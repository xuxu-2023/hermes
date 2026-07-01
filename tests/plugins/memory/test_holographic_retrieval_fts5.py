"""FactRetriever.search must not silently drop results on FTS5 query syntax.

The memory ``search`` action — the LLM-facing recall path — routes through
``FactRetriever.search`` → ``_fts_candidates``, which runs the query against an
FTS5 ``MATCH``. LLM-issued queries frequently contain FTS5 metasyntax: a
``key:value`` colon, a stray double-quote, a bare ``AND``/``OR``/``NEAR``, or a
parenthesis. ``MATCH`` raises ``sqlite3.OperationalError`` on those, and the
candidate fetch used to swallow the error and return ``[]`` — so the agent got
*no* memories even when relevant facts existed. The fetch now retries with each
token quoted as a literal phrase so recall degrades gracefully to a match.
"""

import pytest

from plugins.memory.holographic.store import MemoryStore
from plugins.memory.holographic.retrieval import FactRetriever, _fts5_safe_query


@pytest.fixture
def retriever():
    store = MemoryStore(":memory:")
    store.add_fact("Rust is memory safe and fast", category="tech")
    store.add_fact("Python is a popular programming language", category="tech")
    return FactRetriever(store)


@pytest.mark.parametrize(
    "query",
    [
        "memory:safe",     # colon → FTS5 column-filter syntax
        '"unterminated',   # stray double-quote
        "rust AND",        # trailing boolean operator
        "(python",         # unbalanced parenthesis
        "NEAR(",           # NEAR with no arguments
        "fast OR",         # trailing OR
        "C++ AND rust",    # operator mixed with content
    ],
)
def test_metasyntax_query_does_not_raise_or_silently_empty(retriever, query):
    # Must never raise sqlite3.OperationalError; always returns a list.
    results = retriever.search(query, min_trust=0.0)
    assert isinstance(results, list)


def test_colon_query_still_recalls_matching_fact(retriever):
    # `memory:safe` is invalid FTS5 (column filter). Before the fix the raw
    # MATCH raised and the fetch returned [], so the agent recalled nothing.
    # The quoted-phrase retry matches "memory safe" in the Rust fact.
    results = retriever.search("memory:safe", min_trust=0.0)
    assert any("memory safe" in r["content"] for r in results), (
        "colon query should still recall the matching fact, not return empty"
    )


def test_boolean_operator_query_still_recalls(retriever):
    # "rust AND" is a dangling operator that FTS5 rejects; the retry quotes
    # both tokens and still finds the Rust fact.
    results = retriever.search("rust AND", min_trust=0.0)
    assert any("Rust" in r["content"] for r in results)


def test_plain_query_unaffected(retriever):
    results = retriever.search("rust", min_trust=0.0)
    assert any("Rust" in r["content"] for r in results)


def test_prefix_query_still_works(retriever):
    # A valid FTS5 prefix query must keep working (raw MATCH is tried first,
    # before any sanitizing retry).
    results = retriever.search("pyth*", min_trust=0.0)
    assert any("Python" in r["content"] for r in results)


def test_fts5_safe_query_helper():
    assert _fts5_safe_query("foo:bar baz") == '"foo:bar" "baz"'
    # An embedded double-quote is doubled (FTS5 phrase escaping).
    assert _fts5_safe_query('a"b') == '"a""b"'
    assert _fts5_safe_query("   ") == ""
