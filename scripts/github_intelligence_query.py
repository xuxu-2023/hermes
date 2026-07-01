#!/usr/bin/env python3
"""Lexically search the local GitHub intelligence vault."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_DATA = Path.home() / "Data" / "github-intelligence"


def iter_records(data: Path):
    for path in list((data / "raw").glob("*.jsonl")) + list((data / "reports").glob("*.md")):
        if path.suffix == ".jsonl":
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                text = json.dumps(obj, ensure_ascii=False)
                yield {"path": str(path), "line": i, "text": text, "url": obj.get("html_url") or obj.get("url"), "title": obj.get("title") or obj.get("full_name") or obj.get("name")}
        else:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                yield {"path": str(path), "line": i, "text": line, "url": None, "title": path.name}


def score(text: str, tokens: list[str]) -> int:
    low = text.lower()
    return sum(low.count(t) for t in tokens)


def search(data: Path, query: str, limit: int = 10) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in query.split() if len(t) >= 2]
    hits = []
    for rec in iter_records(data):
        s = score(rec["text"], tokens)
        if s > 0:
            snippet = rec["text"][:800].replace("\n", " ")
            hits.append({**rec, "score": s, "snippet": snippet})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    hits = search(Path(args.data).expanduser().resolve(), args.query, args.limit)
    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
    else:
        for hit in hits:
            print(f"[{hit['score']}] {hit['path']}:{hit['line']} {hit.get('title') or ''}")
            if hit.get("url"):
                print(f"  {hit['url']}")
            print(f"  {hit['snippet'][:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
