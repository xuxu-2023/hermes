from unittest.mock import Mock

from run_agent import AIAgent, main


def test_chat_returns_error_when_run_conversation_has_no_final_response():
    agent = AIAgent.__new__(AIAgent)
    agent.run_conversation = Mock(return_value={"error": "provider failed"})

    assert agent.chat("hello") == "provider failed"


def test_direct_main_prints_error_result_without_final_response(monkeypatch, capsys):
    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, user_query):
            return {
                "completed": False,
                "api_calls": 3,
                "messages": [],
                "error": "payload too large",
            }

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)

    main(query="hello")

    output = capsys.readouterr().out
    assert "❌ ERROR:" in output
    assert "payload too large" in output
    assert "🎯 FINAL RESPONSE" not in output
