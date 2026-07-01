"""MemoryStore.search_facts must not crash on FTS5 query syntax in user text.

The memory ``search`` action is exposed to the LLM, which routinely emits
queries containing FTS5 metasyntax — a stray double-quote, a ``key:value``
colon, a bare ``AND``/``OR``/``NEAR``, or a parenthesis. Passing those straight
to ``MATCH`` raised an unhandled ``sqlite3.OperationalError`` and crashed the
memory tool. search_facts now retries with each token quoted as a literal
phrase so the search degrades gracefully.
"""

import pytest

from plugins.memory.holographic.store import MemoryStore, _fts5_safe_query


@pytest.fixture
def store():
    s = MemoryStore(":memory:")
    s.add_fact("Python is a programming language", category="tech")
    s.add_fact("Rust is memory safe and fast", category="tech")
    return s


@pytest.mark.parametrize(
    "query",
    [
        '"unterminated',
        "foo:bar",
        "AND",
        "a OR",
        "(python",
        "memory:safe",
        "C++ AND rust",
        'say "hi',
        "NEAR(",
    ],
)
def test_fts5_metasyntax_does_not_crash(store, query):
    # Must return a list, never raise sqlite3.OperationalError.
    result = store.search_facts(query)
    assert isinstance(result, list)


def test_colon_query_still_finds_match_via_safe_path(store):
    # `memory:safe` is invalid FTS5 (column filter), but the quoted-phrase
    # retry matches "memory safe" in the Rust fact.
    results = store.search_facts("memory:safe")
    assert any("memory safe" in r["content"] for r in results)


def test_normal_query_unaffected(store):
    results = store.search_facts("python")
    assert any("Python" in r["content"] for r in results)


def test_prefix_query_still_works(store):
    # A valid FTS5 prefix query must keep working (try-raw before fallback).
    results = store.search_facts("rust*")
    assert any("Rust" in r["content"] for r in results)


def test_empty_after_sanitization_returns_empty(store):
    # A query that's only quotes -> no usable tokens -> [] (no crash).
    assert store.search_facts('"') == []


def test_fts5_safe_query_helper():
    assert _fts5_safe_query("foo:bar baz") == '"foo:bar" "baz"'
    # An embedded double-quote is doubled (FTS5 phrase escaping).
    assert _fts5_safe_query('a"b') == '"a""b"'
    assert _fts5_safe_query("   ") == ""
