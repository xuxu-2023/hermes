"""Tests for agent/insights.py — InsightsEngine analytics and reporting."""

import time
import io
import pytest
from rich.console import Console

from hermes_state import SessionDB
from agent.insights import (
    InsightsEngine,
    _estimate_cost,
    _bar_chart,
)
from agent.usage_pricing import (
    format_duration_compact as _format_duration,
    has_known_pricing as _has_known_pricing,
)


def _render_to_text(renderable) -> str:
    """Helper to render a Rich object to a plain string for assertion."""
    console = Console(file=io.StringIO(), width=100, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.file.getvalue()


@pytest.fixture()
def db(tmp_path):
    """Create a SessionDB with a temp database file."""
    db_path = tmp_path / "test_insights.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


@pytest.fixture()
def populated_db(db):
    """Create a DB with realistic session data for insights testing."""
    now = time.time()
    day = 86400

    # Session 1: CLI, claude-sonnet, ended, 2 days ago
    db.create_session(
        session_id="s1", source="cli",
        model="anthropic/claude-sonnet-4-20250514", user_id="user1",
    )
    # Backdate the started_at
    db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = 's1'", (now - 2 * day,))
    db.end_session("s1", end_reason="user_exit")
    db._conn.execute("UPDATE sessions SET ended_at = ? WHERE id = 's1'", (now - 2 * day + 3600,))
    db.update_token_counts("s1", input_tokens=50000, output_tokens=15000)
    db.append_message("s1", role="user", content="Hello, help me fix a bug")
    db.append_message("s1", role="assistant", content="Sure, let me look into that.")
    db.append_message("s1", role="assistant", content="Let me search the files.",
                      tool_calls=[{"function": {"name": "search_files"}}])
    db.append_message("s1", role="tool", content="Found 3 matches", tool_name="search_files")
    db.append_message("s1", role="assistant", content="Let me read the file.",
                      tool_calls=[{"function": {"name": "read_file"}}])
    db.append_message("s1", role="tool", content="file contents...", tool_name="read_file")

    # Session 2: Telegram, gpt-4o, active, 1 day ago
    db.create_session(
        session_id="s2", source="telegram",
        model="openai/gpt-4o", user_id="user1",
    )
    db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = 's2'", (now - 1 * day,))
    db.update_token_counts("s2", input_tokens=10000, output_tokens=2000)
    db.append_message("s2", role="user", content="What's the weather?")
    db.append_message("s2", role="assistant", content="I'll check the web.",
                      tool_calls=[{"function": {"name": "web_search", "arguments": '{"q": "weather in London"}'}}])
    db.append_message("s2", role="tool", content="It's sunny", tool_name="web_search")

    # Session 3: TUI, gpt-4o, 5 days ago
    db.create_session(
        session_id="s3", source="tui",
        model="openai/gpt-4o", user_id="user1",
    )
    db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = 's3'", (now - 5 * day,))
    db.update_token_counts("s3", input_tokens=5000, output_tokens=1000)

    db._conn.commit()
    return db


class TestHasKnownPricing:
    def test_known_commercial_model(self):
        assert _has_known_pricing("anthropic/claude-sonnet-4-20250514", "anthropic", None) is True
        assert _has_known_pricing("openai/gpt-4o", "openai", None) is True

    def test_unknown_custom_model(self):
        assert _has_known_pricing("my-custom-model", "custom", None) is False
        assert _has_known_pricing("llama3:8b", "ollama", "http://localhost:11434") is False

    def test_heuristic_matched_models_are_not_considered_known(self):
        # Heuristics work for estimation but don't count as "known pricing"
        # for strict labeling purposes.
        assert _has_known_pricing("mistral-small-latest", "mistral", None) is False


class TestEstimateCost:
    def test_basic_cost(self):
        # Claude 3.5 Sonnet: $3/M input, $15/M output
        # 50k input = $0.15, 15k output = $0.225 -> total $0.375
        cost, status = _estimate_cost("anthropic/claude-sonnet-4-20250514", 50000, 15000)
        assert cost == pytest.approx(0.375)
        assert status == "estimated"

    def test_zero_tokens(self):
        cost, status = _estimate_cost("openai/gpt-4o", 0, 0)
        assert cost == 0.0
        assert status == "estimated"

    def test_cache_aware_usage(self):
        # Claude 3.5 Sonnet cache: $0.30/M read, $3.75/M write
        # 1M read = $0.30
        cost, status = _estimate_cost(
            "anthropic/claude-sonnet-4-20250514", 0, 0,
            cache_read_tokens=1000000,
        )
        assert cost == pytest.approx(0.30)


class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        # format_duration_compact hides seconds when >= 60s
        assert _format_duration(125) == "2m"

    def test_hours_with_minutes(self):
        assert _format_duration(3665) == "1h 1m"

    def test_exact_hours(self):
        assert _format_duration(7200) == "2h"

    def test_days(self):
        assert _format_duration(100000) == "1.2d"


class TestBarChart:
    def test_basic_bars(self):
        bars = _bar_chart([10, 5, 0], max_width=10)
        assert bars[0] == "█" * 10
        assert bars[1] == "█" * 5
        assert bars[2] == ""

    def test_empty_values(self):
        assert _bar_chart([]) == []

    def test_all_zeros(self):
        assert _bar_chart([0, 0, 0]) == ["", "", ""]

    def test_single_value(self):
        assert _bar_chart([100], max_width=20) == ["█" * 20]


class TestInsightsEmpty:
    def test_empty_db_returns_empty_report(self, db):
        engine = InsightsEngine(db)
        report = engine.generate(days=30)
        assert report["empty"] is True
        assert report["days"] == 30

    def test_empty_db_terminal_format(self, db):
        engine = InsightsEngine(db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))
        assert "No sessions found" in text

    def test_empty_db_gateway_format(self, db):
        engine = InsightsEngine(db)
        report = engine.generate(days=30)
        text = engine.format_gateway(report)
        assert "No sessions found" in text


class TestInsightsPopulated:
    def test_generate_returns_all_sections(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert not report["empty"]
        assert "overview" in report
        assert "models" in report
        assert "platforms" in report
        assert "tools" in report
        assert "skills" in report
        assert "activity" in report
        assert "top_sessions" in report

    def test_overview_session_count(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert report["overview"]["total_sessions"] == 3

    def test_overview_token_totals(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert report["overview"]["total_input_tokens"] == 65000
        assert report["overview"]["total_output_tokens"] == 18000

    def test_overview_cost_positive(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert report["overview"]["estimated_cost"] > 0

    def test_overview_duration_stats(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        # Session 1 is 1 hour
        assert report["overview"]["total_hours"] >= 1.0
        assert report["overview"]["avg_session_duration"] >= 3600

    def test_model_breakdown(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        models = {m["model"]: m for m in report["models"]}
        assert "claude-sonnet-4-20250514" in models
        assert "gpt-4o" in models
        assert models["gpt-4o"]["sessions"] == 2

    def test_platform_breakdown(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        platforms = {p["platform"]: p for p in report["platforms"]}
        assert "cli" in platforms
        assert "telegram" in platforms
        assert "tui" in platforms

    def test_tool_breakdown(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        tools = {t["tool"]: t for t in report["tools"]}
        assert "search_files" in tools
        assert "read_file" in tools
        assert "web_search" in tools
        assert tools["search_files"]["count"] == 1

    def test_skill_breakdown(self, populated_db):
        # We didn't add skill_view/skill_manage calls to populated_db in the fixture
        # so this is empty by default.
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert report["skills"]["summary"]["total_skill_loads"] == 0

    def test_skill_breakdown_respects_days_filter(self, db):
        now = time.time()
        # Old skill use (40 days ago)
        db.create_session(session_id="old", source="cli")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = 'old'", (now - 40 * 86400,))
        db.append_message("old", role="assistant", tool_calls=[{"function": {"name": "skill_view", "arguments": '{"name": "old-skill"}'}}])
        db._conn.execute("UPDATE messages SET timestamp = ? WHERE session_id = 'old'", (now - 40 * 86400,))
        
        # New skill use (2 days ago)
        db.create_session(session_id="new", source="cli")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = 'new'", (now - 2 * 86400,))
        db.append_message("new", role="assistant", tool_calls=[{"function": {"name": "skill_view", "arguments": '{"name": "new-skill"}'}}])
        db._conn.execute("UPDATE messages SET timestamp = ? WHERE session_id = 'new'", (now - 2 * 86400,))
        db._conn.commit()

        engine = InsightsEngine(db)
        
        # 30 day report
        report30 = engine.generate(days=30)
        skills30 = [s["skill"] for s in report30["skills"]["top_skills"]]
        assert "new-skill" in skills30
        assert "old-skill" not in skills30

        # 60 day report
        report60 = engine.generate(days=60)
        skills60 = [s["skill"] for s in report60["skills"]["top_skills"]]
        assert "new-skill" in skills60
        assert "old-skill" in skills60

    def test_activity_patterns(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert len(report["activity"]["by_day"]) == 7
        assert len(report["activity"]["by_hour"]) == 24
        assert report["activity"]["active_days"] > 0

    def test_top_sessions(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        assert len(report["top_sessions"]) > 0
        labels = [s["label"] for s in report["top_sessions"]]
        assert "Longest session" in labels
        assert "Most messages" in labels

    def test_source_filter_cli(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30, source="cli")
        assert report["overview"]["total_sessions"] == 1
        assert report["platforms"][0]["platform"] == "cli"

    def test_source_filter_telegram(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30, source="telegram")
        assert report["overview"]["total_sessions"] == 1
        assert report["platforms"][0]["platform"] == "telegram"

    def test_source_filter_nonexistent(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30, source="nonexistent")
        assert report["empty"] is True

    def test_days_filter_short(self, populated_db):
        engine = InsightsEngine(populated_db)
        # All test data is within last 10 days, so this should find everything
        report = engine.generate(days=10)
        assert report["overview"]["total_sessions"] == 3

    def test_days_filter_long(self, populated_db):
        engine = InsightsEngine(populated_db)
        # 1 day ago session only
        report = engine.generate(days=1.5)
        assert report["overview"]["total_sessions"] == 1


# =========================================================================
# Formatting
# =========================================================================

class TestTerminalFormatting:
    def test_terminal_format_has_sections(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))

        assert "Hermes Insights" in text
        assert "Overview" in text
        assert "Models Used" in text
        assert "Top Tools" in text
        # These headers might be within the Panel/Table structure
        assert "Activity Patterns" in text
        assert "Notable Sessions" in text

    def test_terminal_format_shows_tokens(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))

        assert "Input tokens" in text
        assert "Output tokens" in text
        # Cache metrics were intentionally hidden in manual formatting, 
        # let's check current Rich implementation (it currently hides them too)
        assert "Cache read" not in text
        assert "Cache write" not in text

    def test_terminal_format_shows_platforms(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))

        # Multi-platform, so Platforms section should show
        assert "Platforms" in text
        assert "cli" in text
        assert "telegram" in text

    def test_terminal_format_shows_bar_chart(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))

        assert "█" in text  # Bar chart characters

    def test_terminal_format_hides_cost_for_custom_models(self, db):
        """Cost display is hidden entirely — custom models no longer show 'N/A' either."""
        db.create_session(session_id="s1", source="cli", model="my-custom-model")
        db.update_token_counts("s1", input_tokens=1000, output_tokens=500)
        db._conn.commit()

        engine = InsightsEngine(db)
        report = engine.generate(days=30)
        text = _render_to_text(engine.format_terminal(report))

        assert "N/A" not in text
        assert "custom/self-hosted" not in text
        # Est. Cost is shown as $0.00 for unknown models now, or locally implemented formatting
        # In our implementation it shows Est. Cost: $0.00


class TestGatewayFormatting:
    def test_gateway_format_is_shorter(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        terminal_text = _render_to_text(engine.format_terminal(report))
        gateway_text = engine.format_gateway(report)

        assert len(gateway_text) < len(terminal_text)

    def test_gateway_format_has_bold(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = engine.format_gateway(report)

        assert "**" in text  # Markdown bold

    def test_gateway_format_hides_cost(self, populated_db):
        """Gateway format omits dollar figures and internal cache details."""
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=30)
        text = engine.format_gateway(report)

        assert "$" not in text
        assert "USD" not in text
        assert "cost" not in text.lower()


class TestEdgeCases:
    def test_session_with_no_tokens(self, db):
        db.create_session(session_id="empty", source="cli")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert report["overview"]["total_tokens"] == 0

    def test_session_with_no_end_time(self, db):
        db.create_session(session_id="active", source="cli")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert report["overview"]["total_hours"] == 0

    def test_session_with_no_model(self, db):
        db.create_session(session_id="nomodel", source="cli")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert report["models"][0]["model"] == "unknown"

    def test_custom_model_shows_zero_cost(self, db):
        db.create_session(session_id="custom", source="cli", model="local-llama")
        db.update_token_counts("custom", input_tokens=1000, output_tokens=1000)
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert report["overview"]["estimated_cost"] == 0

    def test_tool_usage_from_tool_calls_json(self, db):
        db.create_session(session_id="j1", source="cli")
        # Add message with tool_calls JSON but NO tool_name on the response message
        db.append_message("j1", role="assistant", tool_calls=[{"function": {"name": "my_tool"}}])
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert report["tools"][0]["tool"] == "my_tool"

    def test_overview_pricing_sets_are_lists(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate()
        # These should be sorted lists for deterministic JSON/UI
        assert isinstance(report["overview"]["models_with_pricing"], list)
        assert isinstance(report["overview"]["models_without_pricing"], list)

    def test_mixed_commercial_and_custom_models(self, db):
        db.create_session(session_id="m1", source="cli", model="openai/gpt-4o")
        db.update_token_counts("m1", input_tokens=1000, output_tokens=1000)
        db.create_session(session_id="m2", source="cli", model="local-llama")
        db.update_token_counts("m2", input_tokens=1000, output_tokens=1000)
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        # GPT-4o has pricing, local-llama doesn't
        assert "gpt-4o" in report["overview"]["models_with_pricing"][0]
        assert "local-llama" in report["overview"]["models_without_pricing"][0]

    def test_single_session_streak(self, db):
        db.create_session(session_id="s1", source="cli")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        # 1 day is not a "streak" of > 1
        assert report["activity"]["max_streak"] == 1

    def test_no_tool_calls(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="hi")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        assert len(report["tools"]) == 0

    def test_only_one_platform(self, db):
        db.create_session(session_id="s1", source="cli")
        db._conn.commit()
        engine = InsightsEngine(db)
        report = engine.generate()
        # format_terminal only shows Platforms if > 1 or non-CLI
        text = _render_to_text(engine.format_terminal(report))
        assert "Platforms" not in text

    def test_large_days_value(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=365)
        assert report["overview"]["total_sessions"] == 3

    def test_zero_days(self, populated_db):
        engine = InsightsEngine(populated_db)
        report = engine.generate(days=0)
        # Should be empty as cutoff is 'now'
        assert report["empty"] is True
