"""Aceptación de v0.17: configuración, voz, cancelación y UI sin API real."""
import unittest


def main() -> int:
    modules = (
        "tests.test_app_settings", "tests.test_audio_capture", "tests.test_audio_devices",
        "tests.test_hotkeys", "tests.test_tk_workflows", "tests.test_local_controller",
        "tests.test_openai_voice", "tests.test_interpretation", "tests.test_browser_adapter",
        "tests.test_browser_runtime", "tests.test_application_tool",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    print("POLISH17_QA_OK: configuración, captura simulada, privacidad, cancelación, "
          "interpretación y Tk real; sin micrófono ni API real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
