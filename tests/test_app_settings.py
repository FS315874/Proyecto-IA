import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from desktop_agent.app_settings import AppSettings, AppSettingsStore, SettingsError
from desktop_agent.windows_secrets import WindowsDpapiProtector


class FakeProtector:
    def protect(self, value):
        return bytes(byte ^ 0x73 for byte in value)

    def unprotect(self, value):
        return self.protect(value)


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "settings.json"
        self.store = AppSettingsStore(self.path, FakeProtector())

    def test_default_budget_and_opt_ins_are_safe(self):
        settings = self.store.load()
        self.assertEqual(settings.monthly_budget_usd, 1)
        for name in ("ai_enabled", "voice_enabled", "auto_send_voice", "remember_key", "voice_hotkey"):
            self.assertFalse(getattr(settings, name))
        self.assertFalse(self.path.exists())

    def test_encrypted_roundtrip_does_not_expose_key_in_file_or_repr(self):
        settings = AppSettings(api_key="fake-private-test-key", remember_key=True, ai_enabled=True,
                               voice_enabled=True, microphone="WASAPI::Microphone", voice_hotkey=True)
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded, settings)
        self.assertEqual(loaded.api_key, settings.api_key)
        self.assertNotIn(settings.api_key, self.path.read_text())
        self.assertNotIn(settings.api_key, repr(loaded))
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_not_remembered_key_is_only_in_session_and_removes_old_key(self):
        settings = AppSettings(api_key="fake-private-test-key", remember_key=True)
        self.store.save(settings)
        self.store.save(replace(settings, remember_key=False))
        self.assertIsNone(self.store.load().api_key)
        self.assertIsNone(json.loads(self.path.read_text())["protected_key"])
        self.assertEqual(settings.environment({})["OPENAI_API_KEY"], settings.api_key)

    def test_environment_is_not_mutated_and_cannot_reenable_disabled_features(self):
        original = {"OPENAI_API_KEY": "old-private-key", "DESKTOP_AGENT_AI_ENABLED": "true", "OTHER": "ok"}
        result = AppSettings(monthly_budget_usd=.25).environment(original)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertEqual(result["DESKTOP_AGENT_AI_ENABLED"], "false")
        self.assertEqual(result["DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD"], "0.25")
        self.assertEqual(original["OPENAI_API_KEY"], "old-private-key")

    def test_invalid_budget_and_types_are_rejected(self):
        for budget in (0, -1, float("nan"), float("inf"), True, "1", 1001):
            with self.subTest(budget=budget), self.assertRaises(SettingsError):
                AppSettings(monthly_budget_usd=budget)
        with self.assertRaises(SettingsError):
            AppSettings(ai_enabled="true")
        with self.assertRaises(SettingsError):
            AppSettings(api_key="secret with spaces")

    def test_corrupt_file_fails_closed_with_redacted_message(self):
        self.path.write_text('{"private-secret": 1}', encoding="utf-8")
        with self.assertRaises(SettingsError) as error:
            self.store.load()
        self.assertNotIn("private-secret", str(error.exception))

    def test_failed_protection_preserves_previous_settings(self):
        self.store.save(AppSettings(monthly_budget_usd=.5))
        original = self.path.read_bytes()
        with patch.object(self.store._protector, "protect", side_effect=RuntimeError("SECRET")):
            with self.assertRaises(SettingsError) as error:
                self.store.save(AppSettings(api_key="fake-private-key", remember_key=True))
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(self.path.read_bytes(), original)

    @unittest.skipUnless(__import__("os").name == "nt", "DPAPI requiere Windows")
    def test_windows_dpapi_roundtrip_with_synthetic_key(self):
        store = AppSettingsStore(self.path, WindowsDpapiProtector())
        store.save(AppSettings(api_key="not-a-real-key", remember_key=True))
        self.assertEqual(store.load().api_key, "not-a-real-key")
        self.assertNotIn("not-a-real-key", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
