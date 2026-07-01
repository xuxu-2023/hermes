#!/usr/bin/env python3
"""Generate local reports from the GitHub intelligence vault."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DATA = Path.home() / "Data" / "github-intelligence"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    # Iterate physical file lines instead of str.splitlines(); JSON strings can
    # legally contain unicode line separators that splitlines() treats as record
    # boundaries, which would silently drop valid JSONL records.
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def year(value: str | None) -> str:
    return value[:4] if value and len(value) >= 4 else "unknown"


def repo_from_item(item: dict) -> str:
    repo = item.get("repository_url") or ""
    if "repos/" in repo:
        return repo.split("repos/", 1)[1]
    html = item.get("html_url") or ""
    if "github.com/" in html:
        parts = html.split("github.com/", 1)[1].split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return item.get("repository", {}).get("full_name") or "unknown"


def write_inventory(data: Path, reports: Path, repos: list[dict], orgs: list[dict]) -> None:
    langs = Counter(r.get("language") or "unknown" for r in repos)
    owners = Counter((r.get("full_name") or "/").split("/")[0] for r in repos)
    private = sum(1 for r in repos if r.get("private"))
    archived = sum(1 for r in repos if r.get("archived"))
    lines = [
        "# GitHub Inventory",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Accessible repos collected: `{len(repos)}`",
        f"- Private repos: `{private}`",
        f"- Archived repos: `{archived}`",
        f"- Visible orgs: `{len(orgs)}`",
        "",
        "## Visible Orgs",
        "",
    ]
    for org in sorted(orgs, key=lambda o: o.get("login", "")):
        lines.append(f"- `{org.get('login')}` — {org.get('description') or ''}")
    lines += ["", "## Top Languages", ""]
    for lang, count in langs.most_common(20):
        lines.append(f"- `{lang}`: {count}")
    lines += ["", "## Repos by Owner", ""]
    for owner, count in owners.most_common():
        lines.append(f"- `{owner}`: {count}")
    lines += ["", "## Recently Updated Repos", ""]
    for repo in sorted(repos, key=lambda r: r.get("pushed_at") or "", reverse=True)[:50]:
        lines.append(f"- [{repo.get('full_name')}]({repo.get('html_url')}) — {repo.get('language') or 'unknown'} — pushed `{repo.get('pushed_at')}`")
    (reports / "inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pr_timeline(reports: Path, prs: list[dict], issues: list[dict]) -> None:
    by_year = Counter(year(p.get("created_at")) for p in prs)
    by_repo = Counter(repo_from_item(p) for p in prs)
    issue_year = Counter(year(i.get("created_at")) for i in issues)
    lines = ["# GitHub PR / Issue Timeline", "", f"Generated: `{datetime.now(timezone.utc).isoformat()}`", "", "## Authored PRs by Year", ""]
    for y, c in sorted(by_year.items()):
        lines.append(f"- `{y}`: {c}")
    lines += ["", "## Involved Issues by Year", ""]
    for y, c in sorted(issue_year.items()):
        lines.append(f"- `{y}`: {c}")
    lines += ["", "## Top PR Repos", ""]
    for repo, c in by_repo.most_common(30):
        lines.append(f"- `{repo}`: {c}")
    lines += ["", "## Recent Authored PRs", ""]
    for pr in sorted(prs, key=lambda p: p.get("created_at") or "", reverse=True)[:50]:
        lines.append(f"- [{pr.get('title')}]({pr.get('html_url')}) — `{repo_from_item(pr)}` — `{pr.get('state')}` — `{pr.get('created_at')}`")
    (reports / "pr-timeline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_themes(reports: Path, repos: list[dict], prs: list[dict], issues: list[dict]) -> None:
    words = Counter()
    stack = Counter(r.get("language") or "unknown" for r in repos)
    for item in [*repos, *prs, *issues]:
        text = " ".join(str(item.get(k) or "") for k in ["name", "full_name", "description", "title", "body"])
        for token in text.lower().replace("/", " ").replace("-", " ").replace("_", " ").split():
            if len(token) >= 4 and token not in {"http", "https", "github", "null", "true", "false", "with", "from", "this", "that", "into", "when", "where"}:
                words[token] += 1
    lines = ["# GitHub Project Themes", "", f"Generated: `{datetime.now(timezone.utc).isoformat()}`", "", "## Stack Signals", ""]
    for lang, c in stack.most_common(20):
        lines.append(f"- `{lang}`: {c}")
    lines += ["", "## Recurring Terms", ""]
    for word, c in words.most_common(80):
        lines.append(f"- `{word}`: {c}")
    (reports / "project-themes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dormant(reports: Path, repos: list[dict]) -> None:
    candidates = [r for r in repos if not r.get("archived")]
    candidates.sort(key=lambda r: r.get("pushed_at") or "")
    lines = ["# Dormant Project Candidates", "", f"Generated: `{datetime.now(timezone.utc).isoformat()}`", "", "Repos below are non-archived accessible repos with the oldest push timestamps in the current archive.", ""]
    for r in candidates[:80]:
        lines.append(f"- [{r.get('full_name')}]({r.get('html_url')}) — {r.get('language') or 'unknown'} — pushed `{r.get('pushed_at')}` — {r.get('description') or ''}")
    (reports / "dormant-projects.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_commit_history(reports: Path, commits: list[dict]) -> None:
    by_year = Counter(year(c.get("committed_at") or c.get("authored_at")) for c in commits)
    by_repo = Counter(c.get("repo") or "unknown" for c in commits)
    by_author = Counter((c.get("author_email") or "unknown").lower() for c in commits)
    remote_reachable = sum(1 for c in commits if any("/" in ref and not ref.startswith("tags/") for ref in (c.get("refs") or [])))
    local_only = len(commits) - remote_reachable
    lines = [
        "# GitHub Commit History",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Commit records: `{len(commits)}`",
        f"- Remote-ref reachable records: `{remote_reachable}`",
        f"- Local-only/tag-only/no-ref records: `{local_only}`",
        "",
        "## Commits by Year",
        "",
    ]
    for y, c in sorted(by_year.items()):
        lines.append(f"- `{y}`: {c}")
    lines += ["", "## Top Repos by Commit Records", ""]
    for repo, c in by_repo.most_common(50):
        lines.append(f"- `{repo}`: {c}")
    lines += ["", "## Top Author Emails", ""]
    for email, c in by_author.most_common(30):
        lines.append(f"- `{email}`: {c}")
    lines += ["", "## Recent Commits", ""]
    for commit in sorted(commits, key=lambda c: c.get("committed_at") or "", reverse=True)[:80]:
        refs = ", ".join((commit.get("refs") or [])[:5])
        lines.append(f"- `{commit.get('committed_at')}` `{commit.get('repo')}` `{str(commit.get('sha') or '')[:12]}` — {commit.get('subject') or ''} ({refs})")
    (reports / "commit-history.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_hermes_opportunities(reports: Path, repos: list[dict], prs: list[dict], issues: list[dict]) -> None:
    terms = Counter()
    source_examples: defaultdict[str, list[str]] = defaultdict(list)
    keywords = ["agent", "ai", "bot", "automation", "telegram", "discord", "airtable", "workflow", "github", "memory", "search", "cron", "llm", "cursor", "hermes"]
    for item in [*repos, *prs, *issues]:
        text = " ".join(str(item.get(k) or "") for k in ["name", "full_name", "description", "title", "body"]).lower()
        url = item.get("html_url") or item.get("url") or ""
        for kw in keywords:
            if kw in text:
                terms[kw] += 1
                if url and len(source_examples[kw]) < 5:
                    source_examples[kw].append(url)
    lines = [
        "# Hermes Improvement Opportunities from GitHub History",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "This report is evidence-backed by the local GitHub intelligence vault. It should guide Hermes improvements without copying private code into global memory.",
        "",
        "## Strong Signals",
        "",
    ]
    for kw, c in terms.most_common():
        lines.append(f"- `{kw}`: {c} matching records")
        for url in source_examples[kw][:3]:
            lines.append(f"  - {url}")
    lines += [
        "",
        "## Recommended Hermes Improvements",
        "",
        "1. Use `github_history_query` (local vault tool) before repo/dev/automation tasks — already shipped in Hermes.",
        "2. Run `scripts/github_intelligence_digest.py` after collection to refresh skill-candidates, repo-conventions, and revival-queue reports.",
        "3. Promote abstract skills from `reports/skill-candidates.md` after review — do not copy private code.",
        "4. Use `reports/repo-conventions.md` as orientation, then verify against live repo files.",
        "5. Keep private org details local; promote only abstract durable preferences after review.",
        "",
    ]
    (reports / "hermes-improvement-opportunities.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(data: Path) -> dict[str, int]:
    reports = data / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    repos = read_jsonl(data / "raw" / "repos.jsonl")
    orgs = read_jsonl(data / "raw" / "orgs.jsonl")
    prs_authored = read_jsonl(data / "raw" / "prs-authored.jsonl")
    prs_involved = read_jsonl(data / "raw" / "prs-involved.jsonl")
    issues_involved = read_jsonl(data / "raw" / "issues-involved.jsonl")
    commits = read_jsonl(data / "raw" / "commits.jsonl")
    write_inventory(data, reports, repos, orgs)
    write_pr_timeline(reports, prs_authored, issues_involved)
    write_themes(reports, repos, prs_involved, issues_involved)
    write_dormant(reports, repos)
    write_commit_history(reports, commits)
    write_hermes_opportunities(reports, repos, prs_involved, issues_involved)
    return {"repos": len(repos), "orgs": len(orgs), "prs_authored": len(prs_authored), "prs_involved": len(prs_involved), "issues_involved": len(issues_involved), "commits": len(commits)}


def _run_script(data: Path, script_name: str, timeout: int = 120) -> dict | None:
    """Run a derived-report script and return its JSON-ish status."""
    script = Path(__file__).with_name(script_name)
    if not script.is_file():
        return None
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(script), "--data", str(data)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        return {"ok": False, "stderr": proc.stderr[-500:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": True, "stdout": proc.stdout[-500:]}


def _run_digest(data: Path) -> dict | None:
    """Refresh derived Hermes-facing reports (skill candidates, revival queue, etc.)."""
    return _run_script(data, "github_intelligence_digest.py", timeout=120)


def _run_commit_workflows(data: Path) -> dict | None:
    """Refresh commit-derived workflow classifier reports."""
    return _run_script(data, "github_intelligence_commit_workflows.py", timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument(
        "--skip-digest",
        action="store_true",
        help="Skip github_intelligence_digest.py (skill candidates, revival queue, etc.)",
    )
    parser.add_argument(
        "--skip-commit-workflows",
        action="store_true",
        help="Skip github_intelligence_commit_workflows.py (commit workflow classifier)",
    )
    args = parser.parse_args()
    data = Path(args.data).expanduser().resolve()
    result = analyze(data)
    if not args.skip_digest:
        digest = _run_digest(data)
        if digest is not None:
            result["digest"] = digest
    if not args.skip_commit_workflows:
        commit_workflows = _run_commit_workflows(data)
        if commit_workflows is not None:
            result["commit_workflows"] = commit_workflows
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
