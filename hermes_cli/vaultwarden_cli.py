"""CLI handlers for ``hermes secrets vaultwarden ...``.

Subcommands:
    setup    — interactive wizard: verify bw, store session token, pick item, test fetch
    status   — show current config + binary version + session presence
    sync     — run a fetch right now and show what would be applied (dry-run friendly)
    disable  — flip ``secrets.vaultwarden.enabled`` to False
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.secret_sources import vaultwarden as vw
from hermes_cli.config import (
    get_env_path,
    load_config,
    save_config,
    save_env_value,
)
from hermes_cli.secret_prompt import masked_secret_prompt


# ---------------------------------------------------------------------------
# Argparse wiring — called from hermes_cli.main
# ---------------------------------------------------------------------------


def register_cli(parent_parser: argparse.ArgumentParser) -> None:
    """Attach the ``vaultwarden`` subcommand tree to a parent parser."""
    sub = parent_parser.add_subparsers(dest="secrets_vw_command")

    setup = sub.add_parser(
        "setup",
        help="Interactive wizard: verify bw, store session token, pick item",
    )
    setup.add_argument(
        "--item-name",
        help="Vault item name to read secrets from (skips interactive prompt)",
    )
    setup.add_argument(
        "--session",
        help=(
            "Provide the BW_SESSION token non-interactively "
            "(will be stored in .env)"
        ),
    )
    setup.add_argument(
        "--server-url",
        help=(
            "Vaultwarden / Bitwarden server URL (e.g. https://vw.example.com). "
            "Runs `bw config server <url>` when provided.  "
            "Skip to keep the current bw server configuration."
        ),
    )
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser("status", help="Show config + binary + session status")
    status.set_defaults(func=cmd_status)

    sync = sub.add_parser("sync", help="Fetch secrets now and report what changed")
    sync.add_argument(
        "--apply",
        action="store_true",
        help="Export the secrets into the current process env (default: dry-run)",
    )
    sync.set_defaults(func=cmd_sync)

    disable = sub.add_parser("disable", help="Turn off the Vaultwarden integration")
    disable.set_defaults(func=cmd_disable)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    console = Console()
    console.print(
        Panel.fit(
            "[bold]Vaultwarden / Bitwarden Password Manager setup[/bold]\n\n"
            "Prerequisites — install the [cyan]bw[/cyan] CLI (pick one):\n"
            "  npm (recommended):  [cyan]npm install -g @bitwarden/cli[/cyan]\n"
            "  Snap (Linux):       [cyan]sudo snap install bw[/cyan]\n"
            "  Native x64 binary:  https://bitwarden.com/help/cli/\n"
            "    → chmod +x bw && mv bw ~/.local/bin/\n"
            "    → ARM64 users: use npm instead of the native binary\n\n"
            "Then:\n"
            "  2. Log in:   [cyan]bw login[/cyan]\n"
            "  3. Unlock:   [cyan]export BW_SESSION=$(bw unlock --raw)[/cyan]\n\n"
            "Secrets are read from a vault item's [bold]custom fields[/bold].  "
            "Name each field after the env var it should set "
            "(e.g. [cyan]OPENROUTER_API_KEY[/cyan]).",
            border_style="cyan",
        )
    )

    # ------------------------------------------------------------------ binary
    console.print()
    console.print("[bold]Step 1[/bold]  Locate the bw CLI")
    binary = vw.find_bw()
    if binary is None:
        console.print(
            "  [red]bw not found on PATH or in <hermes_home>/bin.[/red]\n\n"
            "  Install it (pick one):\n"
            "    npm (recommended):  [cyan]npm install -g @bitwarden/cli[/cyan]\n"
            "    Snap (Linux):       [cyan]sudo snap install bw[/cyan]\n"
            "    Native x64 binary:  https://bitwarden.com/help/cli/\n"
            "      → chmod +x bw && mv bw ~/.local/bin/\n"
            "      → ARM64 users: use npm instead of the native binary\n\n"
            "  Then re-run this wizard."
        )
        return 1
    version = _bw_version(binary)
    console.print(f"  [green]✓[/green] {binary}  ({version})")

    # -- non-interactive guard --
    if not sys.stdin.isatty():
        missing = []
        if not (args.session and args.session.strip()):
            if not os.environ.get("BW_SESSION", "").strip():
                missing.append("--session")
        if not (args.item_name and args.item_name.strip()):
            missing.append("--item-name")
        if missing:
            console.print(
                f"  [red]Non-interactive mode (no TTY) requires all setup flags.[/red]\n"
                f"  Missing: {', '.join(missing)}\n\n"
                "  Usage:\n"
                "    hermes secrets vaultwarden setup \\\n"
                "      --session '<bw-session-token>' \\\n"
                "      --item-name 'Hermes'"
            )
            return 1

    # ------------------------------------------------------------------ server
    if args.server_url and args.server_url.strip():
        console.print()
        console.print("[bold]Step 2[/bold]  Configure server URL")
        server_url = args.server_url.strip()
        try:
            res = subprocess.run(
                [str(binary), "config", "server", server_url],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()[:200]
                console.print(f"  [red]bw config server failed: {err}[/red]")
                return 1
            console.print(f"  [green]✓[/green] Server set to {server_url}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            console.print(f"  [red]Could not configure server: {exc}[/red]")
            return 1
    else:
        current_server = _bw_current_server(binary)
        if current_server:
            console.print(
                f"\n  [dim]Current bw server: {current_server}[/dim]\n"
                "  To change: [cyan]bw config server <url>[/cyan]"
            )

    # ------------------------------------------------------------------ session
    console.print()
    console.print("[bold]Step 2[/bold]  Provide the BW_SESSION token")
    cfg = load_config()
    secrets_cfg = (cfg.setdefault("secrets", {})
                     .setdefault("vaultwarden", {}))
    session_env = secrets_cfg.get("session_env", "BW_SESSION")

    session = (args.session or "").strip() or os.environ.get(session_env, "").strip()
    if not session:
        console.print(
            f"  Obtain it with: [cyan]export BW_SESSION=$(bw unlock --raw)[/cyan]"
        )
        session = masked_secret_prompt(
            f"  Paste session token ({session_env}): "
        ).strip()
    if not session:
        console.print("  [red]Empty session token, aborting.[/red]")
        return 1

    save_env_value(session_env, session)
    os.environ[session_env] = session
    console.print(f"  [green]✓[/green] stored in {get_env_path()} as {session_env}")

    # ------------------------------------------------------------------ item
    console.print()
    console.print("[bold]Step 3[/bold]  Pick a vault item")

    if args.item_name and args.item_name.strip():
        item_name = args.item_name.strip()
        console.print(f"  Using item: [cyan]{item_name}[/cyan]")
    else:
        items = _list_items(binary, session, console)
        if items is None:
            return 1
        if not items:
            console.print(
                "  [yellow]No items found in the vault (or the session has expired).[/yellow]\n"
                "  Unlock again with [cyan]bw unlock[/cyan] and re-run setup."
            )
            return 1

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Name")
        table.add_column("ID", style="dim")
        for i, item in enumerate(items, 1):
            table.add_row(str(i), item.get("name", "?"), item.get("id", "?"))
        console.print(table)

        while True:
            choice = console.input(f"  Select item [1-{len(items)}]: ").strip()
            if not choice:
                continue
            try:
                idx = int(choice)
            except ValueError:
                console.print("  [red]Enter a number.[/red]")
                continue
            if 1 <= idx <= len(items):
                item_name = items[idx - 1]["name"]
                break
            console.print(f"  [red]Out of range — pick 1-{len(items)}.[/red]")

    # ------------------------------------------------------------------ test
    console.print()
    console.print("[bold]Step 4[/bold]  Test fetch")
    try:
        secrets, warnings = vw.fetch_vaultwarden_secrets(
            session=session,
            item_name=item_name,
            binary=binary,
            use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ Fetch failed: {exc}[/red]")
        return 1

    if not secrets:
        console.print(
            "  [yellow]Fetch succeeded but no usable custom fields found.[/yellow]\n"
            "  Add custom fields to the vault item — name each field after the "
            "env var it should set (e.g. OPENROUTER_API_KEY)."
        )
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Field name", style="cyan")
        table.add_column("Status")
        for key in sorted(secrets):
            if key == session_env:
                status = "[dim]bootstrap token — never overrides itself[/dim]"
            elif os.environ.get(key):
                status = "[yellow]already set in env (will be overwritten)[/yellow]"
            else:
                status = "[green]new[/green]"
            table.add_row(key, status)
        console.print(table)
    for w in warnings:
        console.print(f"  [yellow]warning:[/yellow] {w}")

    # ------------------------------------------------------------------ save
    secrets_cfg["enabled"] = True
    secrets_cfg["item_name"] = item_name
    secrets_cfg.setdefault("session_env", session_env)
    secrets_cfg.setdefault("cache_ttl_seconds", 300)
    secrets_cfg.setdefault("override_existing", True)
    save_config(cfg)

    console.print()
    console.print(
        "[green]✓ Vaultwarden integration is enabled.[/green]  "
        "Secrets will be pulled at the start of every Hermes process."
    )
    console.print(
        "  Status:  [cyan]hermes secrets vaultwarden status[/cyan]\n"
        "  Refresh: [cyan]hermes secrets vaultwarden sync[/cyan]\n"
        "  Disable: [cyan]hermes secrets vaultwarden disable[/cyan]\n\n"
        "  [yellow]Note:[/yellow] the BW_SESSION token expires when your vault locks.\n"
        "  Re-unlock with [cyan]export BW_SESSION=$(bw unlock --raw)[/cyan] and "
        "update .env when that happens."
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    vw_cfg = (cfg.get("secrets") or {}).get("vaultwarden") or {}

    enabled = bool(vw_cfg.get("enabled"))
    session_env = vw_cfg.get("session_env", "BW_SESSION")
    item_name = vw_cfg.get("item_name", "")
    session_set = bool(os.environ.get(session_env))

    binary = vw.find_bw()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Enabled",          _yn(enabled))
    table.add_row("session_env",      session_env)
    table.add_row("session",          "[green]present[/green]" if session_set else "[red]missing[/red]")
    table.add_row("item",             item_name or "[dim](unset)[/dim]")
    table.add_row("Override existing", _yn(bool(vw_cfg.get("override_existing", False))))
    table.add_row("Cache TTL (s)",    str(vw_cfg.get("cache_ttl_seconds", 300)))

    if binary:
        server = _bw_current_server(binary)
        table.add_row("bw binary",   f"{binary} ({_bw_version(binary)})")
        table.add_row("bw server",   server or "[dim]default (bitwarden.com)[/dim]")
    else:
        table.add_row("bw binary",   "[red]not found[/red]")

    console.print(Panel(table, title="Vaultwarden / Bitwarden PM", border_style="cyan"))

    if not enabled:
        console.print("\n  Run [cyan]hermes secrets vaultwarden setup[/cyan] to enable.")
    elif not session_set:
        console.print(
            f"\n  [yellow]Enabled but {session_env} is not set — Hermes will skip "
            "Vaultwarden and warn on next startup.[/yellow]\n"
            "  Unlock: [cyan]export BW_SESSION=$(bw unlock --raw)[/cyan]"
        )
    elif not item_name:
        console.print("\n  [yellow]Enabled but no item_name configured.[/yellow]")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    vw_cfg = (cfg.get("secrets") or {}).get("vaultwarden") or {}
    if not vw_cfg.get("enabled"):
        console.print(
            "[yellow]Vaultwarden integration is disabled.  Run "
            "`hermes secrets vaultwarden setup` first.[/yellow]"
        )
        return 1

    session_env = vw_cfg.get("session_env", "BW_SESSION")
    session = os.environ.get(session_env, "").strip()
    if not session:
        console.print(f"[red]{session_env} is not set.[/red]")
        return 1

    item_name = vw_cfg.get("item_name", "")
    if not item_name:
        console.print("[red]No item_name configured.[/red]")
        return 1

    binary = vw.find_bw()
    if binary is None:
        console.print("[red]bw binary not found.[/red]")
        return 1

    console.print("[dim]Syncing vault from server…[/dim]")
    try:
        res = subprocess.run(
            [str(binary), "sync", "--session", session],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()[:200]
            console.print(f"[yellow]bw sync warning: {err}[/yellow]")
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.print(f"[yellow]bw sync skipped: {exc}[/yellow]")

    try:
        secrets, warnings = vw.fetch_vaultwarden_secrets(
            session=session,
            item_name=item_name,
            binary=binary,
            use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Fetch failed: {exc}[/red]")
        return 1

    if not secrets:
        console.print("[yellow]No usable fields in vault item.[/yellow]")
        return 0

    override = bool(vw_cfg.get("override_existing", False)) or args.apply
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field name", style="cyan")
    table.add_column("Action")
    applied = 0
    for key in sorted(secrets):
        if key == session_env:
            table.add_row(key, "[dim]skip (session token)[/dim]")
            continue
        already = bool(os.environ.get(key))
        if already and not override:
            table.add_row(key, "[dim]skip (already set)[/dim]")
            continue
        if args.apply:
            os.environ[key] = secrets[key]
            applied += 1
            table.add_row(key, "[green]exported[/green]" + (" (overrode)" if already else ""))
        else:
            table.add_row(key, "[green]would export[/green]" + (" (overrides)" if already else ""))

    console.print(table)
    for w in warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")

    if not args.apply:
        console.print(
            "\n  Dry-run — secrets are pulled automatically on the next "
            "[cyan]hermes[/cyan] invocation.  Re-run with [cyan]--apply[/cyan] "
            "to export into the current shell instead."
        )
    else:
        console.print(f"\n  [green]Exported {applied} secret(s) into current process.[/green]")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    vw_cfg = (cfg.setdefault("secrets", {})
                .setdefault("vaultwarden", {}))
    vw_cfg["enabled"] = False
    save_config(cfg)
    console.print(
        "[green]Disabled.[/green]  Vaultwarden secrets will NOT be pulled on the next "
        "Hermes invocation.\n"
        "  Your session token is left in .env — remove it manually if desired."
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yn(b: bool) -> str:
    return "[green]yes[/green]" if b else "[dim]no[/dim]"


def _bw_version(binary: Path) -> str:
    try:
        res = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            return (res.stdout or res.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "version unknown"


def _bw_current_server(binary: Path) -> str:
    """Return the currently configured bw server URL, or empty string."""
    try:
        res = subprocess.run(
            [str(binary), "config", "server"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0:
            return (res.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _list_items(
    binary: Path, session: str, console: Console
) -> Optional[List[dict]]:
    """Call ``bw list items`` and return items that have custom fields."""
    env = os.environ.copy()
    env["BW_SESSION"] = session
    env.setdefault("NO_COLOR", "1")
    try:
        res = subprocess.run(
            [str(binary), "list", "items", "--session", session],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.print(f"  [red]Couldn't list vault items: {exc}[/red]")
        return None

    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()[:300]
        console.print(f"  [red]bw list items failed: {err}[/red]")
        if "session" in err.lower() or "not logged" in err.lower():
            console.print(
                "  [yellow]Session may have expired.  "
                "Re-unlock with [cyan]bw unlock[/cyan] and re-run setup.[/yellow]"
            )
        return None

    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError as exc:
        console.print(f"  [red]bw returned non-JSON: {exc}[/red]")
        return None
    if not isinstance(data, list):
        return []
    # Prefer items with valid env-var custom fields; fall back to all items
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("id") or not item.get("name"):
            continue
        fields = item.get("fields") or []
        has_env_fields = any(
            isinstance(f, dict) and vw._is_valid_env_name(f.get("name", ""))
            for f in fields
        )
        if has_env_fields:
            result.append({"id": item["id"], "name": item["name"]})
    if not result:
        result = [
            {"id": item["id"], "name": item["name"]}
            for item in data
            if isinstance(item, dict) and item.get("id") and item.get("name")
        ]
    return result
