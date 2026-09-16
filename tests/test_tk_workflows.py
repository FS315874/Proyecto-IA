"""Recorridos de interfaz Tk real con herramientas, voz y credenciales ficticias."""
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_agent.app_settings import AppSettings, AppSettingsStore
from desktop_agent.browser_preferences import BrowserPreferenceStore, PreferredBrowser
from desktop_agent.browser_bridge import BrowserBridgeSnapshot, BrowserBridgeState
from desktop_agent.local_controller import LocalAgentController
from desktop_agent.local_service import DesktopAgentService
from desktop_agent.tk_app import TkDesktopAgentApp, run_gui
from desktop_agent.voice import VoiceState, VoiceUpdate, VoiceBackendResult, VoiceResultStatus
from scripts.ui12_smoke_check import QaProcessor, QaPlayback


@unittest.skipUnless(os.name == "nt", "La interfaz de escritorio está dirigida a Windows")
class TkWorkflowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.service = DesktopAgentService(LocalAgentController(QaProcessor(), QaPlayback(), logging.getLogger("tk-tests")), logging.getLogger("tk-tests"))
        self.voice = SimpleNamespace(state=VoiceState.READY, cancel=Mock(), drain_updates=lambda: (), start=lambda: "voice-1")
        self.callbacks = []
        self.root.report_callback_exception = lambda *error: self.callbacks.append(error)

    def tearDown(self):
        self.service.close(2)
        for identifier in self.root.tk.call("after", "info"):
            self.root.after_cancel(identifier)
        self.root.destroy()
        self.temporary.cleanup()
        self.assertEqual(self.callbacks, [])

    def app(self, *, auto=False, runtime=None, diagnostics=None, diagnostic_logger=None):
        return TkDesktopAgentApp(self.root, self.service, self.voice, runtime,
            settings=AppSettings(auto_send_voice=auto), settings_store=AppSettingsStore(self.path / "settings.json"),
            diagnostics=diagnostics, diagnostic_logger=diagnostic_logger)

    def ready(self, text="Abrir calculadora.", status=VoiceResultStatus.READY):
        return VoiceUpdate("voice-1", VoiceState.READY, VoiceBackendResult(status, text, None, "es-UY", 250, 500), 750, "ready")

    def test_review_mode_never_submits_until_explicit_send(self):
        app = self.app()
        app._voice_capture_id = "voice-1"
        app._apply_voice_update(self.ready())
        self.assertEqual(len(self.service.snapshot.history), 0)
        self.assertEqual(app._transcript.get(), "Abrir calculadora.")
        self.assertTrue(app._submit_voice())
        self.assertEqual(len(self.service.snapshot.history), 1)
        self.assertEqual(app._transcript.get(), "")

    def test_auto_send_is_exactly_once(self):
        app = self.app(auto=True)
        app._voice_capture_id = "voice-1"
        app._apply_voice_update(self.ready())
        app._apply_voice_update(self.ready())
        self.assertEqual(len(self.service.snapshot.history), 1)
        self.assertIn("enviada", app._voice_status_text.get())

    def test_ambiguous_transcript_never_auto_sends(self):
        app = self.app(auto=True)
        app._voice_capture_id = "voice-1"
        app._apply_voice_update(self.ready(status=VoiceResultStatus.AMBIGUOUS))
        self.assertEqual(len(self.service.snapshot.history), 0)
        self.assertIn("ambigua", app._voice_status_text.get())

    def test_stop_discards_result_already_waiting_in_ui_queue(self):
        app = self.app(auto=True)
        app._voice_capture_id = "voice-1"
        app._request_stop()
        app._apply_voice_update(self.ready())
        self.assertEqual(len(self.service.snapshot.history), 0)
        self.assertEqual(app._transcript.get(), "")

    def test_emergency_transcript_with_punctuation_does_not_become_command(self):
        app = self.app(auto=True)
        app._voice_capture_id = "voice-1"
        app._apply_voice_update(self.ready("¡Detener agente!"))
        self.assertEqual(len(self.service.snapshot.history), 0)
        self.assertIn("emergencia", app._voice_status_text.get())

    def test_browser_checkbox_saves_without_extra_guardar_click(self):
        runtime = SimpleNamespace(preferences=BrowserPreferenceStore(self.path / "browser.json"),
                                  snapshot=BrowserBridgeSnapshot(BrowserBridgeState.WAITING, None))
        app = self.app(runtime=runtime)
        app._browser_choice.set("Google Chrome")
        app._browser_preference_changed()
        app._browser_session_check.invoke()
        saved = runtime.preferences.load()
        self.assertIs(saved.browser, PreferredBrowser.CHROME)
        self.assertTrue(saved.use_current_session)

    def test_configuration_dialog_can_save_without_api_or_microphone_capture(self):
        from tkinter import ttk
        app = self.app()
        with patch("desktop_agent.settings_dialog.list_input_devices", return_value=()):
            app._open_settings()
        window = next(child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel")

        def descend(widget):
            for child in widget.winfo_children():
                yield child
                yield from descend(child)

        save = next(child for child in descend(window) if isinstance(child, ttk.Button) and child.cget("text") == "Guardar y aplicar")
        save.invoke()
        self.assertEqual(app.next_settings, AppSettings())
        self.assertEqual(AppSettingsStore(self.path / "settings.json").load(), AppSettings())
        self.assertTrue(app._closing)


    def test_browser_save_error_survives_refresh_and_restores_effective_selection(self):
        from desktop_agent.browser_preferences import BrowserPreference, BrowserPreferenceError
        preference = BrowserPreference(PreferredBrowser.CHROME, True)
        store = SimpleNamespace(load=lambda: preference, save=Mock(side_effect=BrowserPreferenceError("private path")))
        runtime = SimpleNamespace(preferences=store, snapshot=BrowserBridgeSnapshot(BrowserBridgeState.WAITING, None))
        app = self.app(runtime=runtime)
        app._browser_choice.set("Opera GX")
        app._browser_session.set(False)
        app._save_browser_preference()
        app._refresh_browser_status()
        self.assertIn("no se pudo guardar", app._browser_status_text.get())
        self.assertNotIn("private", app._browser_status_text.get())
        self.assertEqual(app._browser_choice.get(), "Google Chrome")
        self.assertTrue(app._browser_session.get())
        store.save.side_effect = None
        app._save_browser_preference()
        self.assertIsNone(app._browser_error)

    def test_error_history_is_visible_and_review_keeps_the_record(self):
        from desktop_agent.diagnostics import emit_failure
        from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler
        handler = ErrorHistoryHandler(ErrorHistory(self.path / "errors.sqlite3"))
        logger = logging.Logger(self.id())
        logger.addHandler(handler)
        emit_failure(logger, "app_missing", "executor", tool="open_application")
        app = self.app(diagnostics=handler, diagnostic_logger=logger)
        app._errors_button.invoke()
        dialog = app._error_dialog
        self.assertEqual(len(dialog.table.get_children()), 1)
        dialog.table.selection_set(dialog.table.get_children()[0])
        dialog.show_selected()
        self.assertIn("Aplicación no encontrada", dialog.details.get())
        dialog.review_button.invoke()
        self.assertEqual(len(dialog.table.get_children()), 0)
        dialog.include_reviewed.set(True)
        dialog.refresh()
        self.assertEqual(len(dialog.table.get_children()), 1)
        app._errors_button.invoke()
        self.assertIs(app._error_dialog, dialog)

    def test_corrupt_history_is_shown_without_crashing_ui(self):
        from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler
        path = self.path / "broken.sqlite3"
        path.write_bytes(b"synthetic broken history")
        app = self.app(diagnostics=ErrorHistoryHandler(ErrorHistory(path)))
        app._errors_button.invoke()
        self.assertIn("No se pudo acceder", app._error_dialog.status.get())
        self.assertEqual(path.read_bytes(), b"synthetic broken history")

    def test_lost_history_warning_survives_a_later_successful_write(self):
        from desktop_agent.diagnostics import emit_failure
        from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler, HistoryUnavailable
        handler = ErrorHistoryHandler(ErrorHistory(self.path / "errors.sqlite3"))
        logger = logging.Logger(self.id())
        logger.addHandler(handler)
        with patch.object(handler.history, "record", side_effect=HistoryUnavailable()), patch("sys.stderr", None):
            emit_failure(logger, "app_missing", "executor")
        emit_failure(logger, "app_missing", "executor")
        app = self.app(diagnostics=handler, diagnostic_logger=logger)
        app._poll()
        self.assertIn("1 sin guardar", app._errors_button.cget("text"))
        app._errors_button.invoke()
        self.assertIn("No se guardaron 1", app._error_dialog.status.get())


class GuiRestartTests(unittest.TestCase):
    def test_saved_configuration_precedes_invalid_environment(self):
        with patch("desktop_agent.tk_app.AppSettingsStore") as store, patch("desktop_agent.tk_app._run_gui_session", return_value=0) as session:
            store.return_value.path.exists.return_value = True
            store.return_value.load.return_value = AppSettings(monthly_budget_usd=.75)
            self.assertEqual(run_gui({"DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD": "invalid"}), 0)
        self.assertEqual(session.call_args.args[1].monthly_budget_usd, .75)

    def test_restart_keeps_ephemeral_key_only_inside_new_session(self):
        configured = AppSettings(api_key="fake-ephemeral-key", ai_enabled=True, monthly_budget_usd=.5)
        calls = []

        def session(environ, settings, store, restart, warning):
            calls.append((dict(environ), settings))
            if len(calls) == 1:
                restart.append(configured)
            return 0

        with patch("desktop_agent.tk_app.AppSettingsStore") as store, patch("desktop_agent.tk_app._run_gui_session", side_effect=session):
            store.return_value.load.return_value = AppSettings()
            self.assertEqual(run_gui({}), 0)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("OPENAI_API_KEY", calls[0][0])
        self.assertEqual(calls[1][0]["OPENAI_API_KEY"], "fake-ephemeral-key")
        self.assertEqual(calls[1][0]["DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD"], "0.5")


if __name__ == "__main__":
    unittest.main()
