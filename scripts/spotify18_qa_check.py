"""Aceptación simulada de v0.18 sin red externa, cuenta ni reproducción real."""

import unittest


def main() -> int:
    modules = (
        "tests.test_spotify",
        "tests.test_app_settings",
        "tests.test_parser",
        "tests.test_interpretation",
        "tests.test_cli",
        "tests.test_plans",
        "tests.test_openai_provider",
        "tests.test_openai_plan_provider",
        "tests.test_diagnostic_workflows",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    print(
        "SPOTIFY18_QA_OK: OAuth/token simulado, configuración cifrada, "
        "búsqueda, reproducción, controles, dispositivo y verificación; "
        "sin red externa ni efectos reales."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
