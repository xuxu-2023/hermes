"""Tests for the #53008 fix — Context Compression Infinite Loop.

When the auxiliary compression model's context window is smaller than the
main model's compression threshold, ``check_compression_model_feasibility``
auto-lowers ``threshold_tokens``.  Three design flaws combine to create an
infinite compression loop (the reporter observed 12–16 consecutive
compressions with the session stuck at ~204K tokens above a 131K threshold):

1. The threshold is lowered to the *full* aux_context with no safety margin.
2. When the compression window exceeds the aux model's context, the aux
   model cannot process it — the API call errors out or is silently
   truncated, producing a near-useless summary.  There is no fallback to
   the main model.
3. Compression can be effective (>10% savings, so anti-thrashing doesn't
   fire) but the post-compression session stays above the threshold
   because the protected tail (large tool outputs) is itself above the
   threshold.  The next turn re-triggers compression — an
   effective-but-ineffective loop.

Three-layer fix:

1. **Safety margin**: the auto-lowered threshold is 80% of aux_context,
   not 100%, leaving headroom for the summary prompt and reducing the
   chance of the window exceeding the aux model's context.

2. **Proactive main-model fallback**: ``compress()`` estimates the token
   count of ``turns_to_summarize`` (the actual content sent to the
   summariser) and compares it with ``_aux_compression_context_length``.
   When the window exceeds the aux model's context, the aux model is
   temporarily swapped out for the main model for that pass.  The aux
   model is restored afterwards (via try/finally) so future passes on a
   smaller session can use it again.

3. **Threshold escape**: if compression was effective (>10% savings) but
   the post-compression session is still above the threshold, the
   threshold is raised to 10% above the current session size (capped at
   the main model's context_length).  This breaks the
   effective-but-ineffective loop — the conversation continues without
   thrashing.
"""

from typing import Any
from unittest.mock import patch

from agent.context_compressor import ContextCompressor, estimate_messages_tokens_rough

Message = dict[str, Any]


def _make_compressor(
    *,
    model: str = "test-model",
    threshold_percent: float = 0.50,
    protect_first_n: int = 2,
    protect_last_n: int = 3,
    quiet_mode: bool = True,
    abort_on_summary_failure: bool = False,
) -> ContextCompressor:
    with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
        return ContextCompressor(
            model=model,
            threshold_percent=threshold_percent,
            protect_first_n=protect_first_n,
            protect_last_n=protect_last_n,
            quiet_mode=quiet_mode,
            abort_on_summary_failure=abort_on_summary_failure,
        )


def _build_session(n_turns: int, chars_per_msg: int = 200) -> list[Message]:
    """Build a multi-turn conversation with controllable size."""
    base = " ".join(["x"] * chars_per_msg)
    messages: list[Message] = [{"role": "system", "content": "You are helpful."}]
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"{base} turn {i}"})
        messages.append({"role": "assistant", "content": f"{base} reply {i}"})
    return messages


# ---------------------------------------------------------------------------
# Layer 1: safety margin (tested via conversation_compression integration)
# The 80% margin is applied in check_compression_model_feasibility, which
# requires a full agent mock.  The unit test for the margin is in
# test_compression_feasibility.py.  Here we test the compressor-side fixes.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layer 2: proactive main-model fallback in compress()
# ---------------------------------------------------------------------------


class TestProactiveMainModelFallback:
    """compress() falls back to the main model when the compression window
    (middle turns sent to the summariser) exceeds the aux model's context."""

    def test_falls_back_when_window_exceeds_aux_context(self):
        comp = _make_compressor()
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        captured = []

        def _capture(*args, **kwargs):
            captured.append(comp.summary_model)
            return "Summary."

        with patch.object(comp, "_generate_summary", side_effect=_capture):
            comp.compress(messages, current_tokens=50_000)

        assert captured == [""], (
            f"summary_model should be empty (main model) when window > aux_context, got {captured}"
        )

    def test_restores_aux_model_after_fallback(self):
        comp = _make_compressor()
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Summary."):
            comp.compress(messages, current_tokens=50_000)

        assert comp.summary_model == "aux-small-model"

    def test_no_fallback_when_window_within_aux_context(self):
        comp = _make_compressor()
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 10_000_000

        messages = _build_session(30, chars_per_msg=200)

        captured = []

        def _capture(*args, **kwargs):
            captured.append(comp.summary_model)
            return "Summary."

        with patch.object(comp, "_generate_summary", side_effect=_capture):
            comp.compress(messages, current_tokens=50_000)

        assert captured == ["aux-small-model"]

    def test_no_fallback_when_no_aux_model(self):
        comp = _make_compressor()
        comp.summary_model = ""
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Summary."):
            comp.compress(messages, current_tokens=50_000)

        assert comp.summary_model == ""

    def test_no_fallback_when_aux_context_not_set(self):
        comp = _make_compressor()
        comp.summary_model = "aux-model"
        comp._aux_compression_context_length = 0

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Summary."):
            comp.compress(messages, current_tokens=50_000)

        assert comp.summary_model == "aux-model"

    def test_no_fallback_when_session_large_but_window_small(self):
        """The comparison is against the compression window, NOT the full
        session.  A session can be much larger than the aux context while
        the window (middle turns) still fits — in that case the aux model
        is used, not the main model."""
        comp = _make_compressor()
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 50_000

        messages = _build_session(30, chars_per_msg=200)

        captured = []

        def _capture(*args, **kwargs):
            captured.append(comp.summary_model)
            return "Summary."

        with patch.object(comp, "_generate_summary", side_effect=_capture):
            comp.compress(messages, current_tokens=200_000)

        assert captured == ["aux-small-model"], (
            "aux model should be used — the window fits even though the "
            f"full session (200K) exceeds the aux context (50K). Got {captured}"
        )

    def test_aux_model_restored_even_when_summary_raises(self):
        """If _generate_summary raises an unexpected exception, the aux
        model is still restored via the finally block."""
        comp = _make_compressor()
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", side_effect=RuntimeError("boom")):
            try:
                comp.compress(messages, current_tokens=50_000)
            except RuntimeError:
                pass

        assert comp.summary_model == "aux-small-model", (
            f"summary_model should be restored even after an exception, got '{comp.summary_model}'"
        )

    def test_aux_model_restored_when_summary_returns_none(self):
        """When _generate_summary returns None (summary failure), the aux
        model is restored before the abort/fallback path runs."""
        comp = _make_compressor(abort_on_summary_failure=True)
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value=None):
            comp.compress(messages, current_tokens=50_000)

        assert comp.summary_model == "aux-small-model"

    def test_overflow_warning_emitted_once(self):
        comp = _make_compressor(quiet_mode=False)
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 500

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Summary."):
            comp.compress(messages, current_tokens=50_000)
            assert comp._aux_context_overflow_warned is True
            comp.compress(messages, current_tokens=50_000)

        assert comp._aux_context_overflow_warned is True


# ---------------------------------------------------------------------------
# Layer 3: threshold escape in compress()
# ---------------------------------------------------------------------------


class TestThresholdEscape:
    """When compression is effective (>10% savings) but the post-compression
    session is still above the threshold, the threshold is raised to break
    the effective-but-ineffective loop."""

    def test_threshold_raised_when_session_stays_above(self):
        """After an effective compression that leaves the session above the
        threshold, the threshold is raised to 110% of the post-compression
        session size."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-model",
                threshold_percent=0.131,  # 131K
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._aux_compression_context_length = 131_000
        comp._threshold_was_auto_lowered = True

        # Build a session with a huge tool output in the tail so the
        # post-compression session stays above 131K
        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(100):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        # Huge tool output in the protected tail
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 400000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        def fake_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.20))

        with patch.object(comp, "_generate_summary", side_effect=fake_summary):
            comp.compress(msgs, current_tokens=estimate_messages_tokens_rough(msgs))

        # Threshold should have been raised above the post-compression session
        assert comp.threshold_tokens > 131_000, (
            f"Threshold should have been raised above 131K, got {comp.threshold_tokens:,}"
        )

    def test_threshold_not_raised_when_session_below_threshold(self):
        """When compression brings the session below the threshold, the
        threshold is not changed."""
        comp = _make_compressor()
        comp._aux_compression_context_length = 131_000
        original_threshold = comp.threshold_tokens

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Small summary."):
            comp.compress(messages, current_tokens=50_000)

        assert comp.threshold_tokens == original_threshold, (
            f"Threshold should not change when session is below it, "
            f"got {comp.threshold_tokens} vs {original_threshold}"
        )

    def test_threshold_not_raised_when_savings_below_10_pct(self):
        """When savings < 10% (ineffective compression), the threshold is
        not raised — anti-thrashing handles that case."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-model",
                threshold_percent=0.131,
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._aux_compression_context_length = 131_000
        original_threshold = comp.threshold_tokens

        # Build a session where the summary is almost as big as the window
        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(100):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 400000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        # Summary = 95% of window → <10% savings
        def big_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.95))

        with patch.object(comp, "_generate_summary", side_effect=big_summary):
            comp.compress(msgs, current_tokens=estimate_messages_tokens_rough(msgs))

        assert comp.threshold_tokens == original_threshold, (
            f"Threshold should not change for ineffective compression, "
            f"got {comp.threshold_tokens} vs {original_threshold}"
        )

    def test_threshold_capped_at_context_length(self):
        """The raised threshold is capped at the main model's context_length.
        To trigger the escape, we need a session that stays above the
        threshold after compression — a huge tool output in the protected
        tail ensures this."""
        with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
            comp = ContextCompressor(
                model="small-main-model",
                threshold_percent=0.50,  # 100K
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._aux_compression_context_length = 100_000
        comp._threshold_was_auto_lowered = True

        # Build a session with a huge tool output in the tail so the
        # post-compression session stays above 100K
        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(80):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        # 120K token tool output in the protected tail
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 240000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        def fake_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.20))

        with patch.object(comp, "_generate_summary", side_effect=fake_summary):
            comp.compress(msgs, current_tokens=estimate_messages_tokens_rough(msgs))

        # The escape should have fired (session stays above 100K due to tail)
        assert comp.threshold_tokens > 100_000, (
            f"Threshold escape should have fired — session stayed above threshold. "
            f"Got threshold={comp.threshold_tokens:,}"
        )
        # And the raised threshold must be capped at context_length (200K)
        assert comp.threshold_tokens <= 200_000, (
            f"Threshold should be capped at context_length (200K), "
            f"got {comp.threshold_tokens:,}"
        )

    def test_threshold_not_raised_without_aux_context(self):
        """The threshold escape only fires when the threshold was auto-lowered
        by the feasibility check (_threshold_was_auto_lowered=True).  Normal
        compression with no aux model doesn't trigger it."""
        comp = _make_compressor()
        comp._aux_compression_context_length = 0  # no feasibility check
        comp._threshold_was_auto_lowered = False
        original_threshold = comp.threshold_tokens

        messages = _build_session(30, chars_per_msg=200)

        with patch.object(comp, "_generate_summary", return_value="Summary."):
            comp.compress(messages, current_tokens=50_000)

        assert comp.threshold_tokens == original_threshold

    def test_threshold_escape_survives_model_switch(self):
        """After update_model (main model switch), _threshold_was_auto_lowered
        must remain True — the aux model didn't change, so the threshold
        escape must still be able to fire.  Resetting it would re-trigger
        the #53008 loop if the new main model has a smaller context."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-model",
                threshold_percent=0.131,
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._threshold_was_auto_lowered = True
        comp._aux_compression_context_length = 131_000

        # Simulate model switch to a smaller-context model
        comp.update_model("smaller-model", context_length=500_000)

        assert comp._threshold_was_auto_lowered is True, (
            "_threshold_was_auto_lowered must survive update_model — the aux "
            "model didn't change, so the threshold escape must still work"
        )

    def test_threshold_not_raised_when_user_configured_low(self):
        """The threshold escape must NOT fire when the user manually set a
        low threshold (without auto-lowering).  Even if an aux model is
        configured and the feasibility check ran, the escape only fires
        when _threshold_was_auto_lowered is True — it must never override
        a user-configured threshold."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-model",
                threshold_percent=0.10,  # 100K — user-configured, not auto-lowered
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        # Feasibility check ran and stored aux context, but did NOT
        # auto-lower (aux_context 131K >= threshold 100K)
        comp._aux_compression_context_length = 131_000
        comp._threshold_was_auto_lowered = False  # NOT auto-lowered
        original_threshold = comp.threshold_tokens

        # Build a session that stays above 100K after compression
        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(100):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 400000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        def fake_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.20))

        with patch.object(comp, "_generate_summary", side_effect=fake_summary):
            comp.compress(msgs, current_tokens=estimate_messages_tokens_rough(msgs))

        assert comp.threshold_tokens == original_threshold, (
            f"Threshold must NOT be raised when user configured it manually "
            f"(not auto-lowered). Got {comp.threshold_tokens} vs {original_threshold}"
        )


# ---------------------------------------------------------------------------
# Integration: the full #53008 scenario end-to-end
# ---------------------------------------------------------------------------


class TestInfiniteLoopPrevention:
    """Simulate the #53008 scenario end-to-end and verify the loop is broken."""

    def test_loop_breaks_with_huge_tail(self):
        """The exact scenario from the bug report: aux context = 131K,
        threshold auto-lowered to 131K, session with a huge tool output
        in the protected tail.  Without the fix, compression runs
        repeatedly without bringing the session below threshold.  With
        the fix, the threshold is raised after the first effective
        compression, breaking the loop."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-main-model",
                threshold_percent=0.131,
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._aux_compression_context_length = 131_000
        comp._threshold_was_auto_lowered = True

        # Session with huge tool output in tail
        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(200):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 400000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        def fake_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.20))

        total_compressions = 0
        current_tokens = estimate_messages_tokens_rough(msgs)

        with patch.object(comp, "_generate_summary", side_effect=fake_summary):
            for turn in range(10):
                # Add new content each turn
                new_content = " ".join(["x"] * 2000)
                msgs.append({"role": "user", "content": f"{new_content} new turn {turn}"})
                msgs.append({"role": "assistant", "content": f"{new_content} new reply {turn}"})
                current_tokens = estimate_messages_tokens_rough(msgs)

                passes = 0
                for p in range(3):
                    if not comp.should_compress(current_tokens):
                        break
                    passes += 1
                    total_compressions += 1
                    msgs = comp.compress(msgs, current_tokens=current_tokens)
                    current_tokens = estimate_messages_tokens_rough(msgs)
                    if not comp.should_compress(current_tokens):
                        break

        # The loop should NOT have run 12+ compressions
        assert total_compressions <= 3, (
            f"Expected at most 3 compressions (loop broken by threshold escape), "
            f"got {total_compressions}"
        )
        # The session should now be below the (raised) threshold
        assert current_tokens <= comp.threshold_tokens, (
            f"Session ({current_tokens:,}) should be below threshold "
            f"({comp.threshold_tokens:,})"
        )
        # The threshold should have been raised from 131K
        assert comp.threshold_tokens > 131_000, (
            f"Threshold should have been raised from 131K, got {comp.threshold_tokens:,}"
        )

    def test_proactive_fallback_used_when_window_exceeds_aux_context(self):
        """When the compression window exceeds the aux model's context,
        the main model is used for the summary (proactive fallback)."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-main-model",
                threshold_percent=0.131,
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp.summary_model = "aux-small-model"
        comp._aux_compression_context_length = 131_000
        comp._threshold_was_auto_lowered = True

        # Build a session large enough that the window > 131K
        msgs = _build_session(360, chars_per_msg=2000)

        used_models = []

        def _track(*args, **kwargs):
            used_models.append(comp.summary_model)
            return "Effective summary. " * 100

        with patch.object(comp, "_generate_summary", side_effect=_track):
            comp.compress(msgs, current_tokens=estimate_messages_tokens_rough(msgs))

        assert used_models == [""], (
            f"Main model should have been used (proactive fallback), got {used_models}"
        )
        assert comp.summary_model == "aux-small-model", (
            "Aux model should be restored after the fallback pass"
        )

    def test_multi_turn_loop_terminates(self):
        """Simulate 10 turns with the multi-pass preflight loop.  The loop
        should terminate quickly (not 12-16 compressions) and the session
        should stay below the (raised) threshold."""
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            comp = ContextCompressor(
                model="big-main-model",
                threshold_percent=0.131,
                protect_first_n=2,
                protect_last_n=3,
                quiet_mode=True,
            )
        comp._aux_compression_context_length = 131_000
        comp._threshold_was_auto_lowered = True

        msgs: list[Message] = [{"role": "system", "content": "You are helpful."}]
        for i in range(200):
            content = " ".join(["x"] * 2000)
            msgs.append({"role": "user", "content": f"{content} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{content} reply {i}"})
        msgs.append({"role": "user", "content": "Run command"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": " ".join(["y"] * 400000), "tool_call_id": "1"})
        msgs.append({"role": "assistant", "content": "Done"})

        def fake_summary(turns, focus_topic=None):
            wt = estimate_messages_tokens_rough(turns)
            return "Summary: " + " ".join(["s"] * int(wt * 4 * 0.20))

        total_compressions = 0
        current_tokens = estimate_messages_tokens_rough(msgs)

        with patch.object(comp, "_generate_summary", side_effect=fake_summary):
            for turn in range(10):
                new_content = " ".join(["x"] * 2000)
                msgs.append({"role": "user", "content": f"{new_content} new turn {turn}"})
                msgs.append({"role": "assistant", "content": f"{new_content} new reply {turn}"})
                current_tokens = estimate_messages_tokens_rough(msgs)

                for p in range(3):
                    if not comp.should_compress(current_tokens):
                        break
                    total_compressions += 1
                    msgs = comp.compress(msgs, current_tokens=current_tokens)
                    current_tokens = estimate_messages_tokens_rough(msgs)
                    if not comp.should_compress(current_tokens):
                        break

        assert total_compressions <= 5, (
            f"Expected at most 5 compressions across 10 turns, got {total_compressions}"
        )
