#!/usr/bin/env python3
"""Read-only GitHub intelligence collector for Nicholas's accessible GitHub history.

This script only performs read operations through `gh`. It writes raw API/search
payloads into a local data vault and never mutates GitHub state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUT = Path.home() / "Data" / "github-intelligence"
TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}(['\"]?)"),
]

# Repos excluded from the local GitHub data vault. Reasons:
#   - read-only access: shouldn't be auto-updated
#   - not relevant to agent context
# Format: "owner/name" (matches GitHub API `full_name` and `repository_url`'s
# /repos/<owner>/<repo> suffix). Keep in sync with EXCLUDED_REPOS in the
# ghembed script so the vector index stays consistent with the raw vault.
EXCLUDED_REPOS = frozenset({
    "onederful-finance/oneder",   # read-only — no auto PRs/commits/issues
})


def _repo_full_name_from_url(url: str) -> str:
    """Extract 'owner/name' from a GitHub API URL like
    'https://api.github.com/repos/owner/name' or '.../repos/owner/name/issues/1'.
    Returns '' if it doesn't match."""
    if not url:
        return ""
    marker = "/repos/"
    if marker in url:
        tail = url.split(marker, 1)[1].strip("/")
        # Take just owner/name (first two path segments); ignore /issues/N, /pulls/N, etc.
        parts = tail.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return ""


def _is_excluded(d: dict) -> bool:
    """True if a GitHub API row references an excluded repo (PR/issue/repo/etc)."""
    # Repos list: has `full_name`. PR/issue: has `repository_url`.
    # Commit records (collected from local clones) have a top-level `repo` field.
    name = (
        d.get("full_name")
        or d.get("repo")
        or _repo_full_name_from_url(d.get("repository_url", ""))
    )
    return name in EXCLUDED_REPOS


def redact(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        for pat in TOKEN_PATTERNS:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}REDACTED{m.group(4)}" if m.lastindex and m.lastindex >= 4 else "REDACTED", text)
        return text
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def run_gh(args: list[str], timeout: int = 120) -> tuple[int, str, str]:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def gh_json(args: list[str], timeout: int = 120) -> Any:
    rc, out, err = run_gh(args, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {err.strip()[-1000:]}")
    if not out.strip():
        return None
    return json.loads(out)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_github_reset(error_text: str) -> str | None:
    """Return a UTC ISO reset hint parsed from GitHub/gh rate-limit errors."""
    match = re.search(r"timestamp ([0-9-]{10} [0-9:]{8}) UTC", error_text)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
    match = re.search(r"x-ratelimit-reset:\s*(\d+)", error_text, flags=re.I)
    if match:
        return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc).isoformat()
    return None


def atomic_write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = write_jsonl(tmp, rows)
    tmp.replace(path)
    return count


def load_existing_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def merge_unique(existing: Iterable[dict], new_rows: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for item in [*existing, *new_rows]:
        key = str(item.get("id") or item.get("node_id") or item.get("html_url") or json.dumps(item, sort_keys=True))
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def ensure_layout(out: Path) -> None:
    for rel in ["raw", "state", "reports", "index"]:
        (out / rel).mkdir(parents=True, exist_ok=True)
    readme = out / "README.md"
    if not readme.exists():
        readme.write_text(
            "# GitHub Intelligence Vault\n\n"
            "Local read-only archive of GitHub metadata accessible to the authenticated `gh` account.\n\n"
            "Raw private/org data should stay local. Promote only reviewed, stable summaries into Hermes memory.\n",
            encoding="utf-8",
        )


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(obj), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(redact(row), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def append_error(out: Path, stage: str, error: Exception | str) -> None:
    rec = {"stage": stage, "error": str(error), "at": iso_now(), "reset_hint": parse_github_reset(str(error))}
    with (out / "state" / "errors.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(redact(rec), sort_keys=True) + "\n")


def paginated_api(endpoint: str, max_pages: int | None = None) -> list[Any]:
    cmd = ["api", "--paginate", endpoint]
    # gh emits one JSON document per page for REST pagination; use jq to flatten
    cmd.extend(["--jq", ".[]"])
    rc, out, err = run_gh(cmd, timeout=600)
    if rc != 0:
        raise RuntimeError(err.strip())
    rows: list[Any] = []
    for line in out.splitlines():
        if line.strip():
            rows.append(json.loads(line))
            if max_pages and len(rows) >= max_pages * 100:
                break
    return rows


def build_search_query(kind: str, login: str, relation: str, since: str | None = None) -> str:
    if relation == "authored":
        q = f"author:{login}"
    elif relation == "involved":
        q = f"involves:{login}"
    elif relation == "reviewed":
        q = f"reviewed-by:{login}"
    else:
        raise ValueError(f"unknown relation: {relation}")
    if kind == "pr":
        q += " is:pr"
    elif kind == "issue":
        q += " is:issue"
    else:
        raise ValueError(f"unknown kind: {kind}")
    if since:
        q += f" updated:>={since}"
    return q


class RateLimitExhausted(RuntimeError):
    """Raised when retry-on-rate-limit budget is exhausted (or the reset window
    is unreasonably far in the future). The caller (`collect()`) catches this
    in its `try/except` around `partitioned_search_issues()` and writes a
    record to `state/errors.jsonl` via `append_error`, so partial failures
    are visible in the manifest and the health check rather than silently
    producing a year with incomplete data.

    When raised from `partitioned_search_issues`, `partial_rows` carries
    whatever rows were collected from successful year-partitions before the
    failure. The caller should merge these with on-disk data (via
    `merge_unique`) so a single bad year doesn't lose the other years'
    progress — the next run will fill in the rest.
    """
    def __init__(self, msg: str, partial_rows: list | None = None):
        super().__init__(msg)
        self.partial_rows = partial_rows or []


def _wait_for_rate_reset(err_text: str, attempt: int) -> float:
    """Return seconds to sleep before retrying after a rate-limit error.

    Tries (in order):
      1. x-ratelimit-reset: <unix-ts>  → sleep until then (+2s buffer)
      2. timestamp YYYY-MM-DD HH:MM:SS UTC → same
      3. exponential backoff capped at 120s: 2^attempt + jitter

    Raises RateLimitExhausted immediately if the parsed reset window is
    unreasonably far in the future (>10 min). Retrying into such a window
    would just busy-loop against the same cap and burn the retry budget.
    Failing fast lets the caller surface the error and try again on the
    next scheduled run, by which time the window is more likely open.
    """
    reset_iso = parse_github_reset(err_text)
    if reset_iso:
        try:
            from datetime import datetime as _dt
            reset_dt = _dt.fromisoformat(reset_iso)
            now = datetime.now(timezone.utc)
            wait = (reset_dt - now).total_seconds()
            # If the window is far in the future, don't bother retrying.
            # Caller surfaces the error; next cron tick can try again.
            if wait > 600:
                raise RateLimitExhausted(
                    f"rate limit reset is {wait:.0f}s away; not retrying this run"
                )
            # If the window is already in the past, the server gave us stale
            # info (or we lost a race). A short sleep + retry may work, but
            # sleeping 2s blindly burns the budget; fall through to backoff
            # which at least grows exponentially.
            if wait <= 0:
                # fall through to backoff below
                raise StopIteration  # local control flow, caught below
            # 0 < wait <= 600: honor the window, with a small buffer.
            return wait + 2.0
        except RateLimitExhausted:
            raise
        except StopIteration:
            pass
        except Exception:
            pass
    # exponential backoff with jitter
    base = min(120.0, 2 ** min(attempt, 7))  # 2,4,8,16,32,64,120,120
    jitter = base * 0.2 * (0.5 - (time.time() % 1))  # ±10%
    return max(2.0, base + jitter)


def search_issues(query: str, max_pages: int | None = None, per_page: int = 100) -> list[dict]:
    """Search issues/PRs with retry-on-rate-limit.

    On 422 (1000-result cap) we stop — that partition is done.
    On 429 / "rate limit exceeded" / "secondary rate limit" we wait for the
    reset window (or back off) and retry the same page, up to MAX_RETRIES
    times. If retries are exhausted, raises RateLimitExhausted so the
    caller can record the partial failure (the function returns nothing
    in that case — partial rows are lost, which is the correct trade-off
    when a year is incomplete: the next run will resume from disk via
    merge_unique and the user sees the failure in errors.jsonl).

    Note: the retry budget is per-page, not per-call. A 100-page call that
    rate-limits on every other page will use up to MAX_RETRIES sleeps per
    rate-limited page. This is intentional — a global cap of 5 would mean
    a single bad page could abort 99 successful pages of work.
    """
    MAX_RETRIES = 5
    rows: list[dict] = []
    page = 1
    retries_left = MAX_RETRIES
    while True:
        endpoint = f"search/issues?q={quote(query)}&per_page={per_page}&page={page}"
        try:
            data = gh_json(["api", endpoint], timeout=180)
        except RateLimitExhausted as exc:
            # Fail fast: window too far in the future. Carry the rows we
            # already collected from successful pages so the caller can
            # preserve them (a year with 6 of 7 pages is still better
            # than 0 of 7).
            raise RateLimitExhausted(str(exc), partial_rows=rows) from exc
        except RuntimeError as exc:
            msg = str(exc)
            if "Only the first 1000 search results are available" in msg:
                print(f"  search cap reached at page {page}; keeping {len(rows)} partial results ({query})", flush=True)
                break
            if "API rate limit exceeded" in msg or "secondary rate limit" in msg.lower():
                if retries_left <= 0:
                    raise RateLimitExhausted(
                        f"rate limit retries exhausted at page {page} of query '{query}' "
                        f"after {MAX_RETRIES} retries; surfacing to caller with {len(rows)} "
                        f"rows from successful pages",
                        partial_rows=rows,
                    )
                attempt = MAX_RETRIES - retries_left + 1
                wait = _wait_for_rate_reset(msg, attempt)
                print(f"  search rate limit hit at page {page} (attempt {attempt}/{MAX_RETRIES}); sleeping {wait:.1f}s then retrying", flush=True)
                time.sleep(wait)
                retries_left -= 1
                continue
            raise
        # success — refill retry budget, accept results
        retries_left = MAX_RETRIES
        items = data.get("items", []) if isinstance(data, dict) else []
        rows.extend(items)
        print(f"  search page {page}: {len(items)} items ({query})", flush=True)
        if len(items) < per_page or (max_pages and page >= max_pages):
            break
        page += 1
        time.sleep(2.2)
    return rows


def partitioned_search_issues(base_query: str, max_pages: int | None = None, per_page: int = 100) -> list[dict]:
    """Search issues/PRs, partitioning by updated year to avoid GitHub's 1000-result cap.

    GitHub Search only exposes the first 1000 results for a query. For full runs,
    split broad PR/issue queries by updated year. Smoke tests (`max_pages`) keep
    the simple one-query behavior for speed.

    If individual year-partitions raise RateLimitExhausted mid-partition, the
    partial rows for that year are discarded (we don't return incomplete years
    to the caller — better to record the failure and let the next run resume
    from disk). A per-year error is recorded; the loop continues to the next
    year so a single bad year doesn't block the rest. After all years, if any
    year was partial, raises a single RateLimitExhausted with a summary so the
    outer caller's `try/except` records it in `state/errors.jsonl`.
    """
    if max_pages:
        return search_issues(base_query, max_pages=max_pages, per_page=per_page)

    current_year = datetime.now(timezone.utc).year
    # GitHub launched in 2008; include current year and an older catch-all.
    ranges = [f"updated:{year}-01-01..{year}-12-31" for year in range(current_year, 2007, -1)]
    ranges.append("updated:<2008-01-01")
    seen: set[str] = set()
    rows: list[dict] = []
    partial_years: list[str] = []
    for qualifier in ranges:
        query = f"{base_query} {qualifier}"
        try:
            part = search_issues(query, max_pages=None, per_page=per_page)
        except RateLimitExhausted as exc:
            print(f"  partition {qualifier} partial: {exc}; continuing to next year", flush=True)
            partial_years.append(qualifier)
            time.sleep(2.2)
            continue
        print(f"  partition {qualifier}: {len(part)} items", flush=True)
        for item in part:
            key = str(item.get("id") or item.get("node_id") or item.get("html_url"))
            if key not in seen:
                seen.add(key)
                rows.append(item)
        time.sleep(2.2)
    if partial_years:
        raise RateLimitExhausted(
            f"{len(partial_years)} year-partition(s) had partial failures "
            f"({len(rows)} complete items collected from other years, "
            f"carried in exc.partial_rows for the caller to merge): "
            f"{', '.join(partial_years[:5])}"
            f"{' ...' if len(partial_years) > 5 else ''}",
            partial_rows=rows,
        )
    return rows


def collect(out: Path, max_pages: int | None = None, since: str | None = None, resume: bool = True) -> dict[str, Any]:
    ensure_layout(out)
    manifest: dict[str, Any] = {"started_at": iso_now(), "out": str(out), "files": {}, "errors": [], "resume": resume}

    print("checking gh auth...", flush=True)
    rc, auth_out, auth_err = run_gh(["auth", "status"], timeout=60)
    if rc != 0:
        raise RuntimeError(auth_err or auth_out)

    user = gh_json(["api", "user"], timeout=60)
    login = user["login"]
    write_json(out / "raw" / "user.json", user)
    manifest["login"] = login
    print(f"authenticated as {login}", flush=True)

    stages = [
        ("orgs", lambda: paginated_api("user/orgs?per_page=100", max_pages=max_pages), out / "raw" / "orgs.jsonl"),
        ("repos", lambda: paginated_api("user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=updated", max_pages=max_pages), out / "raw" / "repos.jsonl"),
        ("gists", lambda: paginated_api("gists?per_page=100", max_pages=max_pages), out / "raw" / "gists.jsonl"),
        ("events", lambda: paginated_api(f"users/{login}/events/public?per_page=100", max_pages=max_pages), out / "raw" / "events.jsonl"),
    ]
    for name, fn, path in stages:
        try:
            print(f"collecting {name}...", flush=True)
            rows = fn()
            if name == "repos":
                before = len(rows)
                rows = [r for r in rows if not _is_excluded(r)]
                skipped = before - len(rows)
                if skipped:
                    print(f"    excluded {skipped} repos in EXCLUDED_REPOS", flush=True)
            merged = merge_unique(load_existing_jsonl(path), rows) if resume else rows
            manifest["files"][str(path)] = atomic_write_jsonl(path, merged)
        except Exception as exc:
            append_error(out, name, exc)
            manifest["errors"].append({"stage": name, "error": str(exc)})

    searches = [
        ("prs-authored", build_search_query("pr", login, "authored", since), out / "raw" / "prs-authored.jsonl"),
        ("prs-involved", build_search_query("pr", login, "involved", since), out / "raw" / "prs-involved.jsonl"),
        ("prs-reviewed", build_search_query("pr", login, "reviewed", since), out / "raw" / "reviews.jsonl"),
        ("issues-authored", build_search_query("issue", login, "authored", since), out / "raw" / "issues-authored.jsonl"),
        ("issues-involved", build_search_query("issue", login, "involved", since), out / "raw" / "issues-involved.jsonl"),
    ]
    for name, query, path in searches:
        rows: list = []
        try:
            print(f"collecting {name}: {query}", flush=True)
            rows = partitioned_search_issues(query, max_pages=max_pages)
        except RateLimitExhausted as exc:
            # Partial failure: record the error (so it's visible in
            # manifest + health card) AND keep any rows from successful
            # year-partitions so they're merged with on-disk data. Next
            # run will fill in the missing year(s).
            append_error(out, name, exc)
            manifest["errors"].append({"stage": name, "error": str(exc)})
            rows = list(exc.partial_rows) if exc.partial_rows else []
            print(f"  {name}: keeping {len(rows)} partial rows from successful years; "
                  f"will continue on next run", flush=True)
        except Exception as exc:
            append_error(out, name, exc)
            manifest["errors"].append({"stage": name, "error": str(exc)})
            rows = []
        before = len(rows)
        rows = [r for r in rows if not _is_excluded(r)]
        skipped = before - len(rows)
        if skipped:
            print(f"    excluded {skipped} rows in EXCLUDED_REPOS", flush=True)
        merged = merge_unique(load_existing_jsonl(path), rows) if resume else rows
        manifest["files"][str(path)] = atomic_write_jsonl(path, merged)

    manifest["finished_at"] = iso_now()
    write_json(out / "state" / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--max-pages", type=int, default=None, help="Limit pages per paginated collection/search for smoke tests")
    parser.add_argument("--since", default=None, help="Only search PR/issues updated on/after YYYY-MM-DD")
    parser.add_argument("--no-resume", action="store_true", help="Overwrite stage files instead of merging with existing JSONL records")
    args = parser.parse_args()
    manifest = collect(Path(args.out).expanduser().resolve(), max_pages=args.max_pages, since=args.since, resume=not args.no_resume)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if not manifest.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
