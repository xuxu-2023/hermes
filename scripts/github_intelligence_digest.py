#!/usr/bin/env python3
"""Generate Hermes-facing intelligence products from local GitHub vault.

Read-only over raw vault data. Writes derived local reports/indexes only.
"""
from __future__ import annotations
import argparse, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DATA = Path.home() / "Data" / "github-intelligence"
TOPICS = {
    "airtable": ["airtable", "fub", "seniorplace", "automation", "base", "record"],
    "hermes_gateway": ["hermes", "gateway", "telegram", "cron", "launchd", "watchdog"],
    "cursor_agents": ["cursor", "agent", "task_runner", "copilot", "claude", "codex"],
    "memory": ["memory", "simplemem", "oracle", "wiki", "obsidian", "context"],
    "github_actions": ["github actions", "workflow", "ci", "gha", "runner", "deploy"],
    "bots": ["bot", "telegram", "discord", "slack", "message", "webhook"],
    "search_research": ["search", "research", "crawler", "scrape", "dataset", "ingest"],
}
SECRET_RE = re.compile(r"(?i)(sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{20,}|xox[baprs]-[a-z0-9-]{20,}|api[_-]?key\s*[:=]\s*['\"]?[a-z0-9_-]{16,}|token\s*[:=]\s*['\"]?[a-z0-9_-]{16,})")
STOP = {"the","and","for","with","from","this","that","into","your","have","will","are","was","were","not","but","you","all","add","fix","feat","update","github","https","http","com","null","true","false"}

def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path: Path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}
def read_jsonl(path: Path):
    rows=[]
    if not path.exists(): return rows
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip(): continue
            try: rows.append(json.loads(line))
            except Exception: pass
    return rows

def repo_from_item(item):
    for key in ("repo","full_name"):
        if item.get(key): return str(item[key])
    repo_url = item.get("repository_url") or ""
    if "repos/" in repo_url: return repo_url.split("repos/",1)[1]
    html = item.get("html_url") or item.get("url") or ""
    if "github.com/" in html:
        parts=html.split("github.com/",1)[1].split("/")
        if len(parts)>=2: return f"{parts[0]}/{parts[1]}"
    obj=item.get("repository") or {}
    return obj.get("full_name") or "unknown"

def text_of(item):
    vals=[]
    for k in ("full_name","name","description","title","body","subject","repo","language"):
        v=item.get(k)
        if v: vals.append(str(v))
    return " ".join(vals)

def url_of(item): return item.get("html_url") or item.get("url") or ""
def title_of(item): return item.get("title") or item.get("subject") or item.get("full_name") or item.get("name") or repo_from_item(item)
def tokenize(s): return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t)>=4 and t not in STOP]
def has_secret(s): return bool(SECRET_RE.search(s or ""))
def redact(s): return SECRET_RE.sub("[REDACTED_SECRET]", s or "")

def topic_hits(records):
    out={}
    for topic, kws in TOPICS.items():
        scored=[]
        for kind,item in records:
            txt=text_of(item).lower()
            score=sum(txt.count(k) for k in kws)
            if score:
                scored.append({"score":score,"kind":kind,"repo":repo_from_item(item),"title":title_of(item),"url":url_of(item),"snippet":redact(text_of(item)[:500])})
        scored.sort(key=lambda x:x["score"], reverse=True)
        out[topic]=scored
    return out

def infer_repo_conventions(repos, prs, commits):
    by_repo=defaultdict(lambda:{"prs":0,"commits":0,"terms":Counter(),"examples":[],"language":"unknown","url":""})
    for r in repos:
        name=r.get("full_name") or "unknown"; d=by_repo[name]
        d["language"]=r.get("language") or "unknown"; d["url"]=r.get("html_url") or ""; d["description"]=r.get("description") or ""; d["pushed_at"]=r.get("pushed_at") or ""
        d["private"]=bool(r.get("private")); d["archived"]=bool(r.get("archived"))
        d["terms"].update(tokenize(text_of(r)))
    for p in prs:
        name=repo_from_item(p); d=by_repo[name]; d["prs"]+=1; d["terms"].update(tokenize(text_of(p)))
        if url_of(p) and len(d["examples"])<5: d["examples"].append({"title":title_of(p),"url":url_of(p)})
    for c in commits:
        name=repo_from_item(c); d=by_repo[name]; d["commits"]+=1; d["terms"].update(tokenize(text_of(c)))
        if len(d["examples"])<5 and c.get("subject"): d["examples"].append({"title":c.get("subject"),"url":""})
    profiles=[]
    for repo,d in by_repo.items():
        terms=[w for w,_ in d["terms"].most_common(12)]
        confidence = min(100, d["prs"]*2 + d["commits"]//20 + (20 if d.get("language") != "unknown" else 0))
        likely=[]
        t=set(terms)
        if d["language"] in {"Python"} or {"pytest","airtable","script"}&t: likely.append("Python scripts/tests likely; inspect AGENTS.md/runbook before edits")
        if d["language"] in {"TypeScript","JavaScript"} or {"react","next","vite","netlify"}&t: likely.append("JS/TS frontend conventions likely; run package scripts after inspecting package.json")
        if {"airtable","seniorplace","fub"}&t: likely.append("Airtable/APFS automation sensitive; verify env names and scheduler ownership")
        if {"cursor","agent","memory","hermes"}&t: likely.append("Agent/Cursor memory conventions matter; query history before changes")
        profiles.append({"repo":repo,"language":d["language"],"url":d["url"],"description":d.get("description",""),"pushed_at":d.get("pushed_at",""),"archived":d.get("archived",False),"prs":d["prs"],"commits":d["commits"],"top_terms":terms,"likely_conventions":likely,"examples":d["examples"],"confidence":confidence})
    profiles.sort(key=lambda x:(x["confidence"], x["prs"], x["commits"]), reverse=True)
    return profiles

def write_md(path, lines):
    txt="\n".join(lines)+"\n"
    if has_secret(txt): txt=redact(txt)
    path.write_text(txt, encoding="utf-8")

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DEFAULT_DATA)); ap.add_argument("--top", type=int, default=30)
    args=ap.parse_args(); data=Path(args.data).expanduser().resolve(); reports=data/"reports"; state=data/"state"; reports.mkdir(parents=True, exist_ok=True); state.mkdir(parents=True, exist_ok=True)
    repos=read_jsonl(data/"raw/repos.jsonl"); prs_a=read_jsonl(data/"raw/prs-authored.jsonl"); prs_i=read_jsonl(data/"raw/prs-involved.jsonl"); issues=read_jsonl(data/"raw/issues-involved.jsonl")+read_jsonl(data/"raw/issues-authored.jsonl"); commits=read_jsonl(data/"raw/commits.jsonl")
    records=[*(('repo',r) for r in repos),*(('pr',p) for p in prs_i),*(('issue',i) for i in issues),*(('commit',c) for c in commits)]
    topics=topic_hits(records); profiles=infer_repo_conventions(repos, prs_i, commits)
    # skill candidates
    skill_rows=[]
    for topic,hits in topics.items():
        repos_count=Counter(h["repo"] for h in hits)
        value=len(hits)+len(repos_count)*5
        skill_rows.append({"topic":topic,"records":len(hits),"repos":len(repos_count),"score":value,"top_repos":repos_count.most_common(8),"examples":hits[:5]})
    skill_rows.sort(key=lambda x:x["score"], reverse=True)
    # revival queue
    revival=[]
    for p in profiles:
        if p["archived"]: continue
        text=" ".join(p["top_terms"]+[p.get("description") or ""]).lower()
        signal=sum(1 for kws in TOPICS.values() for kw in kws if kw in text)
        if signal and p.get("pushed_at"):
            revival.append({**p,"reuse_score":signal*10+p["confidence"]//5})
    revival.sort(key=lambda x:(x["reuse_score"], x.get("pushed_at") or ""), reverse=True)
    # markdown outputs
    write_md(reports/"repo-conventions.md", ["# Repo Convention Profiles", "", f"Generated: `{now()}`", "", "Derived from local GitHub vault metadata. Verify against live repo files before editing.", ""] + sum(([f"## `{p['repo']}`", "", f"- Language: `{p['language']}`", f"- Confidence: `{p['confidence']}`", f"- PR records: `{p['prs']}`; commit records: `{p['commits']}`", f"- Pushed: `{p.get('pushed_at') or 'unknown'}`", f"- Top terms: {', '.join('`'+t+'`' for t in p['top_terms'][:10])}", "- Likely conventions:"] + [f"  - {c}" for c in (p['likely_conventions'] or ['No strong convention inferred; inspect repo files first.'])] + ["- Evidence examples:"] + [f"  - {e['title']}{(' — '+e['url']) if e.get('url') else ''}" for e in p['examples'][:3]] + [""] for p in profiles[:args.top]), []))
    write_md(reports/"skill-candidates.md", ["# GitHub-Derived Skill Candidates", "", f"Generated: `{now()}`", "", "These are candidates for abstract Hermes skills. Do not copy private code into skills; extract procedures/pitfalls only.", ""] + sum(([f"## `{s['topic']}`", "", f"- Matching records: `{s['records']}`", f"- Repos touched: `{s['repos']}`", f"- Priority score: `{s['score']}`", "- Top repos:"] + [f"  - `{r}`: {c}" for r,c in s['top_repos']] + ["- Evidence examples:"] + [f"  - `{e['repo']}` — {e['title']}{(' — '+e['url']) if e.get('url') else ''}" for e in s['examples']] + ["- Suggested skill shape:", f"  - Trigger: future work involving {s['topic'].replace('_',' ')}", "  - Include: prerequisites, exact commands, pitfalls, verification checks, privacy guardrails", ""] for s in skill_rows), []))
    write_md(reports/"revival-queue.md", ["# Dormant / Reusable Project Revival Queue", "", f"Generated: `{now()}`", "", "Ranked by AI/automation/search/bot/memory signal in local GitHub history. Review before acting; do not auto-modify repos.", ""] + sum(([f"## `{p['repo']}`", "", f"- Reuse score: `{p['reuse_score']}`", f"- Language: `{p['language']}`", f"- Last pushed: `{p.get('pushed_at') or 'unknown'}`", f"- Description: {p.get('description') or ''}", f"- Why it may matter: {', '.join(p['top_terms'][:8])}", "- Next action: inspect repo README/AGENTS/runbook and query vault for exact prior context.", ""] for p in revival[:args.top]), []))
    digest=["# GitHub Intelligence Weekly Digest", "", f"Generated: `{now()}`", "", "## What Hermes should do with the data", "", "1. Query `github_history_query` before repo/dev/automation tasks.", "2. Use `repo-conventions.md` as orientation, then verify against live files.", "3. Promote `skill-candidates.md` items into abstract skills after review.", "4. Use `revival-queue.md` to pick reusable old projects/components.", "", "## Top skill candidates", ""]
    for s in skill_rows[:8]: digest.append(f"- `{s['topic']}` — {s['records']} records across {s['repos']} repos")
    digest += ["", "## Top repo convention profiles", ""]
    for p in profiles[:10]: digest.append(f"- `{p['repo']}` — {p['language']}, confidence {p['confidence']}, terms: {', '.join(p['top_terms'][:5])}")
    digest += ["", "## Top revival candidates", ""]
    for p in revival[:10]: digest.append(f"- `{p['repo']}` — score {p['reuse_score']}, {p['language']}, pushed {p.get('pushed_at') or 'unknown'}")
    commit_workflows = read_json(state/"commit-workflows.json")
    workflow_rows = commit_workflows.get("workflows") or []
    if workflow_rows:
        digest += ["", "## Top commit workflow clusters", ""]
        for wf in workflow_rows[:8]:
            if wf.get("count"):
                digest.append(f"- `{wf.get('key')}` — {wf.get('count')} commits; top repos: {', '.join(r for r,_ in (wf.get('top_repos') or [])[:3])}")
    write_md(reports/"weekly-digest.md", digest)
    index={"generated_at":now(),"data_dir":str(data),"counts":{"repos":len(repos),"prs_involved":len(prs_i),"prs_authored":len(prs_a),"issues":len(issues),"commits":len(commits)},"preflight_policy":{"when":"Before repo/dev/automation work when prior Nicholas patterns may matter","queries":["<repo name> conventions tests deploy", "<topic> automation workflow", "<error message or library>"],"privacy":"Keep raw private details local; promote only abstract procedures."},"top_skill_candidates":skill_rows[:12],"top_repo_profiles":profiles[:30],"top_revival_candidates":revival[:30],"top_commit_workflows":workflow_rows[:12],"report_paths":[str(reports/n) for n in ["repo-conventions.md","skill-candidates.md","revival-queue.md","weekly-digest.md","commit-workflows.md"]]}
    (state/"hermes-preflight-index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    readme=data/"README.md"
    existing=readme.read_text(encoding="utf-8") if readme.exists() else "# GitHub Intelligence Vault\n"
    marker="\n## Hermes Active-Use Runbook\n"
    runbook=marker+"\nThis vault is local/private and is used as Hermes's engineering memory for Nicholas's GitHub work.\n\nGenerated products:\n\n- `reports/repo-conventions.md` — inferred repo orientations; verify against live files.\n- `reports/skill-candidates.md` — repeated workflows to turn into abstract skills.\n- `reports/revival-queue.md` — projects/components worth revisiting.\n- `reports/commit-workflows.md` — commit-derived workflow clusters and repo playbook seeds.\n- `reports/weekly-digest.md` — concise summary for review.\n- `state/hermes-preflight-index.json` — machine-readable preflight index.\n\nOperational rule: before meaningful repo/dev/automation work, query `github_history_query` with the repo/topic/error. Do not copy private code into memory or skills.\n"
    if marker in existing: existing=existing.split(marker)[0].rstrip()+"\n"+runbook
    else: existing=existing.rstrip()+"\n"+runbook
    readme.write_text(existing, encoding="utf-8")
    print(json.dumps({"ok":True,"data":str(data),"counts":index["counts"],"reports":index["report_paths"],"index":str(state/"hermes-preflight-index.json")}, indent=2))
if __name__ == "__main__": main()
