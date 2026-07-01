from hermes_cli import web_server


def test_memory_provider_schema_uses_blank_for_builtin_only():
    field = web_server.CONFIG_SCHEMA["memory.provider"]

    assert field["description"] == "External memory provider plugin (blank = built-in only)"
    assert "" in field["options"]
    assert "hindsight" in field["options"]
    assert "honcho" in field["options"]
    assert "builtin" not in field["options"]
