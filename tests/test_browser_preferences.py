import json
import tempfile
import unittest
from pathlib import Path

from desktop_agent.browser_preferences import (
    BrowserPreference,
    BrowserPreferenceError,
    BrowserPreferenceStore,
    PreferredBrowser,
)
from desktop_agent.preferred_browser import PreferredBrowserOpener


class BrowserPreferenceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "browser.json"
        self.store = BrowserPreferenceStore(self.path)

    def test_defaults_to_system_browser_without_session_control(self) -> None:
        self.assertEqual(self.store.load(), BrowserPreference())

    def test_round_trip_is_atomic_and_versioned(self) -> None:
        expected = BrowserPreference(PreferredBrowser.OPERA_GX, True)
        self.store.save(expected)
        self.assertEqual(self.store.load(), expected)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)

    def test_rejects_current_session_for_unknown_default_browser(self) -> None:
        with self.assertRaises(BrowserPreferenceError):
            BrowserPreference(PreferredBrowser.SYSTEM_DEFAULT, True)

    def test_rejects_extra_or_unknown_persisted_fields(self) -> None:
        for value in (
            {
                "schema_version": 1,
                "browser": "unknown",
                "use_current_session": False,
            },
            {
                "schema_version": 1,
                "browser": "chrome",
                "use_current_session": False,
                "extra": True,
            },
        ):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(BrowserPreferenceError):
                    self.store.load()


class PreferredBrowserOpenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = BrowserPreferenceStore(Path(self.temp.name) / "browser.json")

    def test_uses_system_default_by_default(self) -> None:
        opened = []
        opener = PreferredBrowserOpener(
            self.store,
            system_opener=lambda url: opened.append(url) or True,
        )
        self.assertTrue(opener("https://open.spotify.com/"))
        self.assertEqual(opened, ["https://open.spotify.com/"])

    def test_launches_selected_browser_with_url_as_literal_argument(self) -> None:
        self.store.save(BrowserPreference(PreferredBrowser.OPERA_GX, False))
        started = []
        opener = PreferredBrowserOpener(
            self.store,
            system_opener=lambda _url: False,
            path_checker=lambda path: path.endswith(r"Opera GX\opera.exe"),
            finder=lambda _name: None,
            starter=started.append,
        )
        url = "https://www.youtube.com/"
        self.assertTrue(opener(url))
        self.assertEqual(started[0][1], url)
        self.assertEqual(len(started[0]), 2)

    def test_missing_selected_browser_fails_without_system_fallback(self) -> None:
        self.store.save(BrowserPreference(PreferredBrowser.CHROME, False))
        opener = PreferredBrowserOpener(
            self.store,
            system_opener=lambda _url: True,
            path_checker=lambda _path: False,
            finder=lambda _name: None,
        )
        self.assertFalse(opener("https://www.youtube.com/"))
