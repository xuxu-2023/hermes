"""Vaultwarden / Bitwarden Password Manager (``bw`` CLI) integration.

Hermes pulls API keys from a Vaultwarden (or Bitwarden) vault item at
process startup.  Vaultwarden implements the Bitwarden Password Manager
API — not Bitwarden Secrets Manager — so the ``bws`` CLI used by
``secrets.bitwarden`` does not work with it.  Use this module instead.

Design summary
--------------

* ``bw`` is NOT auto-installed.  Install it from your package manager or
  https://github.com/bitwarden/clients/releases.  Hermes looks for it in
  ``<hermes_home>/bin/bw`` then ``PATH``.
* The session token is stored in ``~/.hermes/.env`` as ``BW_SESSION``
  (or the name chosen in ``secrets.vaultwarden.session_env``).  Obtain it
  with ``export BW_SESSION=$(bw unlock --raw)`` after logging in.
* Secrets come from a single named vault item's custom fields::

      bw get item "<item_name>" --session "$BW_SESSION"

  Every field whose name is a valid env-var identifier is exported to
  ``os.environ``.
* Caching: two-layer (in-process dict + disk JSON) identical to the bws
  module, written to ``<hermes_home>/cache/bw_cache.json``.
* Failures NEVER block Hermes startup.  Missing binary, expired session,
  unknown item — all emit a one-line warning and continue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BW_RUN_TIMEOUT = 30

_CacheKey = Tuple[str, str]  # (session_fingerprint, item_name)
_CACHE: Dict[_CacheKey, "_CachedFetch"] = {}
_DISK_CACHE_BASENAME = "bw_cache.json"


# ---------------------------------------------------------------------------
# Disk cache helpers  (same atomic-rename + chmod 0600 pattern as bws module)
# ---------------------------------------------------------------------------


def _disk_cache_path(home_path: Optional[Path] = None) -> Path:
    if home_path is None:
        home_path = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    return home_path / "cache" / _DISK_CACHE_BASENAME


def _cache_key_str(cache_key: _CacheKey) -> str:
    session_fp, item_name = cache_key
    return f"vw|{session_fp}|{item_name}"


def _read_disk_cache(
    cache_key: _CacheKey, ttl_seconds: float, home_path: Optional[Path] = None
) -> "Optional[_CachedFetch]":
    if ttl_seconds <= 0:
        return None
    path = _disk_cache_path(home_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("key") != _cache_key_str(cache_key):
        return None
    secrets = payload.get("secrets")
    fetched_at = payload.get("fetched_at")
    if not isinstance(secrets, dict) or not isinstance(fetched_at, (int, float)):
        return None
    typed: Dict[str, str] = {
        k: v for k, v in secrets.items() if isinstance(k, str) and isinstance(v, str)
    }
    entry = _CachedFetch(secrets=typed, fetched_at=float(fetched_at))
    return entry if entry.is_fresh(ttl_seconds) else None


def _write_disk_cache(
    cache_key: _CacheKey, entry: "_CachedFetch", home_path: Optional[Path] = None
) -> None:
    path = _disk_cache_path(home_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": _cache_key_str(cache_key),
            "secrets": entry.secrets,
            "fetched_at": entry.fetched_at,
        }
        fd, tmp = tempfile.mkstemp(
            prefix=".bw_cache_", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


@dataclass
class _CachedFetch:
    secrets: Dict[str, str]
    fetched_at: float

    def is_fresh(self, ttl_seconds: float) -> bool:
        if ttl_seconds <= 0:
            return False
        return (time.time() - self.fetched_at) < ttl_seconds


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Outcome of a single vault item pull."""

    secrets: Dict[str, str] = field(default_factory=dict)
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    binary_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def _hermes_bin_dir() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "bin"


def find_bw() -> Optional[Path]:
    """Return a path to a usable ``bw`` binary, or None.

    Resolution order:
      1. ``<hermes_home>/bin/bw``
      2. ``shutil.which("bw")`` (system PATH)

    ``bw`` is not auto-installed — users install it themselves.
    """
    managed = _hermes_bin_dir() / ("bw.exe" if os.name == "nt" else "bw")
    if managed.exists() and os.access(managed, os.X_OK):
        return managed
    system = shutil.which("bw")
    return Path(system) if system else None


# ---------------------------------------------------------------------------
# Secret fetch
# ---------------------------------------------------------------------------


def _session_fingerprint(session: str) -> str:
    return hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]


def fetch_vaultwarden_secrets(
    *,
    session: str,
    item_name: str,
    binary: Optional[Path] = None,
    cache_ttl_seconds: float = 300,
    use_cache: bool = True,
    home_path: Optional[Path] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Pull custom fields from a vault item via ``bw get item``.

    Returns ``(secrets_dict, warnings_list)``.

    Raises :class:`RuntimeError` for fatal conditions (missing binary,
    auth failure, unknown item, unparseable output).
    """
    if not session:
        raise RuntimeError("Vaultwarden session token is empty")
    if not item_name:
        raise RuntimeError("Vaultwarden item_name is empty")

    cache_key: _CacheKey = (_session_fingerprint(session), item_name)
    if use_cache:
        cached = _CACHE.get(cache_key)
        if cached and cached.is_fresh(cache_ttl_seconds):
            return cached.secrets, []
        disk_cached = _read_disk_cache(cache_key, cache_ttl_seconds, home_path)
        if disk_cached is not None:
            _CACHE[cache_key] = disk_cached
            return disk_cached.secrets, []

    bw = binary or find_bw()
    if bw is None:
        raise RuntimeError(
            "bw binary not found.  Install it from your package manager or "
            "https://github.com/bitwarden/clients/releases"
        )

    secrets, warnings = _run_bw_get_item(bw, session, item_name)
    entry = _CachedFetch(secrets=secrets, fetched_at=time.time())
    _CACHE[cache_key] = entry
    if use_cache:
        _write_disk_cache(cache_key, entry, home_path)
    return secrets, warnings


def _run_bw_get_item(
    bw: Path, session: str, item_name: str
) -> Tuple[Dict[str, str], List[str]]:
    cmd = [str(bw), "get", "item", item_name, "--session", session]
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")

    try:
        proc = subprocess.run(  # noqa: S603 — bw path is trusted
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=_BW_RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"bw timed out after {_BW_RUN_TIMEOUT}s fetching item"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"failed to invoke bw: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().replace("\x1b", "")
        raise RuntimeError(f"bw exited {proc.returncode}: {err[:200]}")

    raw = proc.stdout.strip()
    if not raw:
        return {}, ["bw returned no output"]

    try:
        item = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bw returned non-JSON output: {exc}") from exc

    if not isinstance(item, dict):
        raise RuntimeError(f"bw returned unexpected shape: {type(item).__name__}")

    fields = item.get("fields") or []
    if not isinstance(fields, list):
        return {}, ["item has no fields array"]
    if not fields:
        return {}, ["item has no custom fields — add fields named after the env vars you want to export"]

    secrets: Dict[str, str] = {}
    warnings: List[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = f.get("name")
        value = f.get("value")
        if not isinstance(name, str) or value is None:
            continue
        value = str(value)
        if not _is_valid_env_name(name):
            warnings.append(f"Skipping field {name!r}: not a valid env-var name")
            continue
        secrets[name] = value
    return secrets, warnings


def _is_valid_env_name(name: str) -> bool:
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


# ---------------------------------------------------------------------------
# Public entry point — called from hermes_cli.env_loader
# ---------------------------------------------------------------------------


def apply_vaultwarden_secrets(
    *,
    enabled: bool,
    session_env: str = "BW_SESSION",
    item_name: str = "",
    override_existing: bool = False,
    cache_ttl_seconds: float = 300,
    home_path: Optional[Path] = None,
) -> FetchResult:
    """Pull secrets from a vault item and set them on ``os.environ``.

    Defensive — any failure returns a :class:`FetchResult` with ``error``
    set; it never raises.
    """
    result = FetchResult()

    if not enabled:
        return result

    session = os.environ.get(session_env, "").strip()
    if not session:
        result.error = (
            f"secrets.vaultwarden.enabled is true but {session_env} is not set.  "
            "Run `bw unlock --raw` and store the output in your .env file as "
            f"{session_env}=<token>, or run `hermes secrets vaultwarden setup`."
        )
        return result

    if not item_name:
        result.error = (
            "secrets.vaultwarden.item_name is empty.  "
            "Run `hermes secrets vaultwarden setup`."
        )
        return result

    binary = find_bw()
    result.binary_path = binary
    if binary is None:
        result.error = (
            "bw binary not found.  Install it from your package manager or "
            "https://github.com/bitwarden/clients/releases"
        )
        return result

    try:
        secrets, warnings = fetch_vaultwarden_secrets(
            session=session,
            item_name=item_name,
            binary=binary,
            cache_ttl_seconds=cache_ttl_seconds,
            home_path=home_path,
        )
    except RuntimeError as exc:
        result.error = str(exc)
        return result

    result.secrets = secrets
    result.warnings.extend(warnings)

    for key, value in secrets.items():
        if key == session_env:
            result.skipped.append(key)
            continue
        if not override_existing and os.environ.get(key):
            result.skipped.append(key)
            continue
        os.environ[key] = value
        result.applied.append(key)

    return result


# ---------------------------------------------------------------------------
# Test hook
# ---------------------------------------------------------------------------


def _reset_cache_for_tests(home_path: Optional[Path] = None) -> None:
    _CACHE.clear()
    try:
        _disk_cache_path(home_path).unlink()
    except (FileNotFoundError, OSError):
        pass
