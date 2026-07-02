"""Regression test: cron scheduler must pass target_model to resolve_runtime_provider.

Symptom (filed upstream as the cron-path analog of #18586): a cron job
configured with `model: deepseek-v4-flash, provider: opencode-go` is
routed to the Anthropic Messages API (api_mode='anthropic_messages',
base_url without `/v1`) when `model.default` is itself an
Anthropic-routed model (e.g. `minimax-m3`). The resolver falls back to
`model.default` when no `target_model` is supplied, then
`opencode_model_api_mode('opencode-go', model.default)` returns
`anthropic_messages` for any `minimax-*` model. The cron job's actual
model is `deepseek-v4-flash` (a chat_completions model on opencode-go)
so the request lands at the wrong endpoint with the wrong payload
shape and 400s with `tools[0].function: missing field 'name'`.

The fix: pass `target_model=model` from the cron scheduler so the
resolver computes api_mode / base_url from the model the job will
actually use, not from the unrelated config default.

Mirrors the delegate_task fix in PR #18605 / #15320 / #18799 — same
class of bug, different call site.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    """Isolate cron job storage."""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


def _stub_scheduler(monkeypatch, observed):
    """Stub out the parts of _run_job_impl that touch the filesystem or network.

    Records every kwargs dict that `resolve_runtime_provider` is called with
    in `observed['resolver_calls']` so the test can assert what was passed.
    """

    class _FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run_conversation(self, *a, **kw):
            return {"final_response": "ok", "messages": []}

        def get_activity_summary(self):
            return {"seconds_since_activity": 0.0}

        def close(self):
            pass

    fake_mod = types.ModuleType("run_agent")
    fake_mod.AIAgent = _FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_mod)

    # Capture resolver invocations
    from hermes_cli import runtime_provider as _rtp

    def _capturing_resolver(**kwargs):
        observed.setdefault("resolver_calls", []).append(kwargs)
        return {
            "provider": kwargs.get("requested") or "test",
            "api_key": "***",
            "base_url": "http://test.local",
            "api_mode": "chat_completions",
        }

    monkeypatch.setattr(_rtp, "resolve_runtime_provider", _capturing_resolver)
    monkeypatch.setattr(_rtp, "format_runtime_provider_error", lambda exc: str(exc))

    import cron.scheduler as sched

    monkeypatch.setattr(sched, "_build_job_prompt", lambda job, prerun_script=None: "hi")
    monkeypatch.setattr(sched, "_resolve_origin", lambda job: None)
    monkeypatch.setattr(sched, "_resolve_delivery_target", lambda job: None)
    monkeypatch.setattr(sched, "_resolve_cron_enabled_toolsets", lambda job, cfg: None)
    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "0")

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: True)


class TestCronPassesTargetModel:
    def test_opencode_go_job_passes_its_model_as_target_model(
        self, tmp_cron_dir, monkeypatch
    ):
        """A cron job pinned to deepseek-v4-flash on opencode-go must propagate
        that model to the resolver, so api_mode / base_url are derived from
        deepseek-v4-flash (chat_completions on /v1/chat/completions) — not
        from the unrelated config default (e.g. minimax-m3 → anthropic_messages
        on /go/chat/completions, which 400s).
        """
        observed = {}
        _stub_scheduler(monkeypatch, observed)

        from cron.scheduler import run_job

        success, _output, _final, _err = run_job(
            {
                "id": "job-opencode-go",
                "name": "opencode-go regression",
                "prompt": "ping",
                "model": "deepseek-v4-flash",
                "provider": "opencode-go",
            }
        )

        assert success is True
        # Exactly one resolver call
        assert len(observed["resolver_calls"]) == 1
        call = observed["resolver_calls"][0]
        # Both the requested provider and the actual job model are forwarded
        assert call.get("requested") == "opencode-go"
        assert call.get("target_model") == "deepseek-v4-flash", (
            "Cron scheduler must pass target_model=job.model so the resolver "
            "computes api_mode / base_url from the job's actual model, not "
            "from model.default. See PR #18605 for the delegate_task fix; "
            "this is the cron-path equivalent."
        )

    def test_no_model_in_job_does_not_pass_target_model(
        self, tmp_cron_dir, monkeypatch
    ):
        """If the cron job itself has no model field, we must not invent one —
        the resolver will use model.default, which is the documented behaviour
        for the main-agent path. The bug only manifests when a job has a
        different model from model.default."""
        observed = {}
        _stub_scheduler(monkeypatch, observed)

        from cron.scheduler import run_job

        run_job(
            {
                "id": "job-no-model",
                "name": "no model",
                "prompt": "ping",
                # no model field
                "provider": "opencode-go",
            }
        )

        call = observed["resolver_calls"][0]
        # The fix must NOT introduce a stray empty-string target_model
        # (that would still be falsy and the resolver would still fall back
        # to model.default — but a contract that says "always pass
        # target_model" would be wrong here).
        assert "target_model" not in call or call["target_model"], (
            "When the job has no model, the scheduler should not pass a "
            "falsy target_model; the resolver is responsible for falling "
            "back to model.default."
        )

    def test_explicit_base_url_still_propagates(
        self, tmp_cron_dir, monkeypatch
    ):
        """Regression guard: adding target_model must not drop the existing
        explicit_base_url plumbing for jobs that override the base URL."""
        observed = {}
        _stub_scheduler(monkeypatch, observed)

        from cron.scheduler import run_job

        run_job(
            {
                "id": "job-custom-url",
                "name": "custom base url",
                "prompt": "ping",
                "model": "deepseek-v4-flash",
                "provider": "opencode-go",
                "base_url": "https://example.test/v9",
            }
        )

        call = observed["resolver_calls"][0]
        assert call.get("target_model") == "deepseek-v4-flash"
        assert call.get("explicit_base_url") == "https://example.test/v9"
