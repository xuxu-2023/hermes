"""Tests for Chromium-presence detection in browser_tool.

Regression guard for the "browser tool advertised but Chromium missing"
class of bug — where ``agent-browser`` CLI is discoverable but no
Chromium build is on disk, causing every browser_* tool call to hang
for the full command timeout before surfacing a useless error.
"""

import os

import pytest

from tools import browser_tool as bt


@pytest.fixture(autouse=True)
def _reset_chromium_cache():
    bt._cached_chromium_installed = None
    yield
    bt._cached_chromium_installed = None


class TestChromiumSearchRoots:
    def test_respects_playwright_browsers_path_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        roots = bt._chromium_search_roots()
        assert str(tmp_path) == roots[0]

    def test_ignores_playwright_browsers_path_zero(self, monkeypatch):
        # Playwright treats "0" as "skip browser download" — not a real path.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
        roots = bt._chromium_search_roots()
        assert "0" not in roots

    def test_always_includes_default_ms_playwright_cache(self, monkeypatch):
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        roots = bt._chromium_search_roots()
        home = os.path.expanduser("~")
        assert any(r == os.path.join(home, ".cache", "ms-playwright") for r in roots)


class TestChromiumInstalled:
    def test_true_when_plain_chromium_on_path(self, monkeypatch):
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(
            bt.shutil,
            "which",
            lambda name: "/usr/bin/chromium" if name == "chromium" else None,
        )

        assert bt._chromium_installed() is True

    def test_true_when_chromium_dir_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()
        assert bt._chromium_installed() is True

    def test_true_when_headless_shell_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium_headless_shell-1208").mkdir()
        assert bt._chromium_installed() is True

    def test_true_when_macos_app_bundle_chrome_present(self, monkeypatch):
        """macOS app-bundle Chrome counts even when not on PATH (#48172).

        The installer's find_system_browser() already treats this exact path as
        a usable system browser, but a .app bundle has no PATH entry, so the
        runtime gate used to hide the browser tools on existing/source installs.
        """
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(bt.sys, "platform", "darwin")
        monkeypatch.setattr(bt.shutil, "which", lambda name: None)
        monkeypatch.setattr(bt, "_chromium_search_roots", lambda: [])
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        monkeypatch.setattr(bt.os.path, "isfile", lambda p: p == chrome)

        assert bt._chromium_installed() is True

    def test_true_when_macos_app_bundle_chromium_present(self, monkeypatch):
        """macOS app-bundle Chromium counts even when not on PATH (#48172)."""
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(bt.sys, "platform", "darwin")
        monkeypatch.setattr(bt.shutil, "which", lambda name: None)
        monkeypatch.setattr(bt, "_chromium_search_roots", lambda: [])
        chromium = "/Applications/Chromium.app/Contents/MacOS/Chromium"
        monkeypatch.setattr(bt.os.path, "isfile", lambda p: p == chromium)

        assert bt._chromium_installed() is True

    def test_false_on_macos_without_app_bundle_path_or_cache(self, monkeypatch):
        """No PATH chrome, no .app bundle, no Playwright cache → not installed."""
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(bt.sys, "platform", "darwin")
        monkeypatch.setattr(bt.shutil, "which", lambda name: None)
        monkeypatch.setattr(bt, "_chromium_search_roots", lambda: [])
        monkeypatch.setattr(bt.os.path, "isfile", lambda p: False)

        assert bt._chromium_installed() is False

    def test_app_bundle_paths_ignored_off_darwin(self, monkeypatch):
        """The /Applications fallback must only fire on macOS."""
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(bt.sys, "platform", "linux")
        monkeypatch.setattr(bt.shutil, "which", lambda name: None)
        monkeypatch.setattr(bt, "_chromium_search_roots", lambda: [])
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        monkeypatch.setattr(bt.os.path, "isfile", lambda p: p == chrome)

        assert bt._chromium_installed() is False

    def test_result_cached(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()
        assert bt._chromium_installed() is True
        # Delete after first call — cached True should still return True.
        (tmp_path / "chromium-1208").rmdir()
        assert bt._chromium_installed() is True


class TestCheckBrowserRequirementsChromium:

    def test_local_mode_with_chromium_returns_true(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(bt, "_find_agent_browser", lambda **_kw: "/usr/local/bin/agent-browser")
        monkeypatch.setattr(bt, "_requires_real_termux_browser_install", lambda _: False)
        monkeypatch.setattr(bt, "_get_cloud_provider", lambda: None)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()

        assert bt.check_browser_requirements() is True

    def test_cloud_mode_does_not_require_local_chromium(self, monkeypatch, tmp_path):
        """Cloud browsers (Browserbase etc.) host their own Chromium."""
        class FakeProvider:
            def is_configured(self):
                return True
            def provider_name(self):
                return "browserbase"

        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(bt, "_find_agent_browser", lambda **_kw: "/usr/local/bin/agent-browser")
        monkeypatch.setattr(bt, "_requires_real_termux_browser_install", lambda _: False)
        monkeypatch.setattr(bt, "_get_cloud_provider", lambda: FakeProvider())
        # Point chromium search at an empty dir — should not matter for cloud.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "fakehome"))

        assert bt.check_browser_requirements() is True

    def test_startup_check_uses_lightweight_agent_browser_lookup(self, monkeypatch, tmp_path):
        seen = []

        def fake_find_agent_browser(**kwargs):
            seen.append(kwargs)
            return "/usr/local/bin/agent-browser"

        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(bt, "_find_agent_browser", fake_find_agent_browser)
        monkeypatch.setattr(bt, "_requires_real_termux_browser_install", lambda _: False)
        monkeypatch.setattr(bt, "_get_cloud_provider", lambda: None)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()

        assert bt.check_browser_requirements() is True
        assert seen == [{"validate": False}]

    def test_camofox_mode_does_not_require_chromium(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: True)
        # Even with no chromium on disk, camofox drives its own backend.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "fakehome"))

        assert bt.check_browser_requirements() is True


class TestRunBrowserCommandChromiumGuard:
    """Verify _run_browser_command fails fast (no timeout hang) when
    Chromium is missing in local mode.
    """


