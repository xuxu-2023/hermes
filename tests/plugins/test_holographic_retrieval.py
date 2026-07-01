from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore


def test_long_multi_token_query_falls_back_to_any_token_candidates(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory_store.db")
    retriever = FactRetriever(store)
    store.add_fact(
        "Claude Code on hermes-server uses DaseinAI relay env ~/.config/claude-code/env; "
        "launchers ~/.local/bin/claude and ~/.hermes/node/bin/claude wrap claude-daseinai. "
        "Non-interactive gateway sessions may need sourcing that env; verify with "
        "`claude auth status --text`.",
        category="tool",
        tags="Claude Code,DaseinAI,hermes-server,relay,env,gateway,tool",
    )

    results = retriever.search("Claude Code DaseinAI relay claude-daseinai", limit=5)

    assert results
    assert results[0]["content"].startswith("Claude Code on hermes-server uses DaseinAI relay")
