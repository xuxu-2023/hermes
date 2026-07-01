"""MemoryStore.update_fact must not crash when content collides with another row.

Regression for #43389: ``facts.content`` is ``UNIQUE``. ``add_fact`` already
catches the ``sqlite3.IntegrityError`` and returns the existing id, but
``update_fact`` ran the ``UPDATE`` unguarded, so updating one fact's content to
a value another fact already holds raised an unhandled IntegrityError and
crashed the memory operation (e.g. an LLM-generated correction that happens to
match existing content). The update is now rejected (returns False) instead.
"""

from plugins.memory.holographic.store import MemoryStore


def _store():
    return MemoryStore(":memory:")


def test_update_to_duplicate_content_returns_false_not_crash():
    store = _store()
    store.add_fact("Python is fast", category="lang")
    id_b = store.add_fact("Rust is safe", category="lang")

    # Previously raised sqlite3.IntegrityError: UNIQUE constraint failed.
    assert store.update_fact(id_b, content="Python is fast") is False

    # The rejected update must leave the original content untouched.
    row = store._conn.execute(
        "SELECT content FROM facts WHERE fact_id = ?", (id_b,)
    ).fetchone()
    assert row["content"] == "Rust is safe"


def test_non_conflicting_content_update_still_succeeds():
    store = _store()
    id_b = store.add_fact("Rust is safe", category="lang")
    assert store.update_fact(id_b, content="Rust is memory-safe") is True
    row = store._conn.execute(
        "SELECT content FROM facts WHERE fact_id = ?", (id_b,)
    ).fetchone()
    assert row["content"] == "Rust is memory-safe"


def test_update_unknown_fact_returns_false():
    store = _store()
    assert store.update_fact(99999, content="anything") is False


def test_update_fact_to_its_own_content_is_not_a_conflict():
    # A row keeping its own UNIQUE value must not be treated as a duplicate.
    store = _store()
    id_a = store.add_fact("Python is fast", category="lang")
    assert store.update_fact(id_a, content="Python is fast") is True


def test_non_content_update_unaffected_by_guard():
    # trust/tags/category updates don't touch the UNIQUE column.
    store = _store()
    id_a = store.add_fact("Python is fast", category="lang")
    assert store.update_fact(id_a, trust_delta=0.1, tags="pl") is True
