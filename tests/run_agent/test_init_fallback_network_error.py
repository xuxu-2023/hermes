"""Regression test for #47321: misleading "no API key" error when the
provider endpoint is unreachable (e.g. DNS outage, Tailscale down).

When the API key env var IS set but resolve_provider_client returns None
because the endpoint can't be reached, the error should say "provider
could not be reached" instead of "no API key was found"."""
import os
import pytest
from unittest.mock import patch, MagicMock
from run_agent import AIAgent


def _make_tool_defs():
    return [{"type": "function", "function": {"name": "web_search",
             "description": "search", "parameters": {"type": "object", "properties": {}}}}]


def test_init_raises_network_error_when_key_present_but_provider_unreachable():
    """When the API key env var is set but resolve_provider_client returns
    None (provider endpoint unreachable), the RuntimeError should say
    'could not be reached', not 'no API key was found'."""
    with patch.dict(os.environ, {"ALIBABA_CODING_PLAN_API_KEY": "sk-test-key-value-123456"}), \
         patch("agent.auxiliary_client.resolve_provider_client", return_value=(None, None)), \
         patch("run_agent.get_tool_definitions", return_value=_make_tool_defs()), \
         patch("run_agent.check_toolset_requirements", return_value={}), \
         patch("run_agent.OpenAI", return_value=MagicMock()):

        with pytest.raises(RuntimeError) as excinfo:
            AIAgent(
                provider="alibaba-coding-plan",
                model="qwen3.6-plus",
                api_key=None,
                base_url=None,
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                fallback_model=None,
            )
        assert "could not be reached" in str(excinfo.value)
        assert "ALIBABA_CODING_PLAN_API_KEY is present in the environment" in str(excinfo.value)
        assert "Check your network" in str(excinfo.value)
        # The old misleading error must NOT appear:
        assert "no API key was found" not in str(excinfo.value)