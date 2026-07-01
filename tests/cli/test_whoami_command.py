"""Tests for the /whoami CLI command dispatch and handler.

The /whoami command is registered in COMMAND_REGISTRY (and advertised by
/help, tab-completion, and the tips system) but had no dispatch branch in
HermesCLI.process_command — so typing it in the CLI/TUI/Desktop printed
"Unknown command: /whoami".  These tests lock in the dispatch wiring and
the handler behavior.  See issue #51009.
"""

import unittest
from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = None
    cli_obj._pending_input = MagicMock()
    return cli_obj


class TestWhoamiDispatch(unittest.TestCase):
    """/whoami must route to its handler — not fall through to "Unknown"."""

    def test_whoami_dispatches_to_handler(self):
        cli_obj = _make_cli()
        with patch.object(cli_obj, "_handle_whoami_command") as mock_handler:
            result = cli_obj.process_command("/whoami")

        mock_handler.assert_called_once()
        self.assertTrue(result)

    def test_whoami_is_not_unknown_command(self):
        cli_obj = _make_cli()
        with patch("cli._cprint") as mock_cprint:
            result = cli_obj.process_command("/whoami")

        printed = " ".join(str(c) for c in mock_cprint.call_args_list)
        self.assertNotIn("Unknown command", printed)
        self.assertTrue(result)


class TestHandleWhoamiCommand(unittest.TestCase):
    """Handler should report owner-tier access for the CLI context."""

    def test_output_contains_owner_tier(self):
        cli_obj = _make_cli()
        with (
            patch("hermes_cli.profiles.get_active_profile_name", return_value="default"),
            patch("builtins.print") as mock_print,
        ):
            HermesCLI._handle_whoami_command(cli_obj)

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("owner", printed)

    def test_output_contains_cli_surface(self):
        cli_obj = _make_cli()
        with (
            patch("hermes_cli.profiles.get_active_profile_name", return_value="default"),
            patch("builtins.print") as mock_print,
        ):
            HermesCLI._handle_whoami_command(cli_obj)

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("CLI", printed)

    def test_output_shows_profile_name(self):
        cli_obj = _make_cli()
        with (
            patch("hermes_cli.profiles.get_active_profile_name", return_value="test-profile"),
            patch("builtins.print") as mock_print,
        ):
            HermesCLI._handle_whoami_command(cli_obj)

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("test-profile", printed)


class TestWhoamiRegistry(unittest.TestCase):
    def test_whoami_in_registry(self):
        from hermes_cli.commands import COMMAND_REGISTRY

        names = [c.name for c in COMMAND_REGISTRY]
        self.assertIn("whoami", names)

    def test_whoami_not_gateway_only(self):
        from hermes_cli.commands import COMMAND_REGISTRY

        whoami = next(c for c in COMMAND_REGISTRY if c.name == "whoami")
        self.assertFalse(whoami.gateway_only, "/whoami should be available on all surfaces")

    def test_whoami_category_is_info(self):
        from hermes_cli.commands import COMMAND_REGISTRY

        whoami = next(c for c in COMMAND_REGISTRY if c.name == "whoami")
        self.assertEqual(whoami.category, "Info")


if __name__ == "__main__":
    unittest.main()
