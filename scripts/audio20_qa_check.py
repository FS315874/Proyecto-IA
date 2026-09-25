"""Regresión de v0.20 sin modificar el volumen real de Windows."""

import unittest


def main() -> int:
    modules = (
        "tests.test_output_audio",
        "tests.test_parser",
        "tests.test_interpretation",
        "tests.test_plans",
        "tests.test_openai_provider",
        "tests.test_openai_plan_provider",
        "tests.test_cli",
        "tests.test_diagnostic_workflows",
        "tests.test_error_history",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    print(
        "AUDIO20_QA_OK: dispositivo, límite, verificación, parser, IA, "
        "planes y diagnósticos; sin cambiar volumen real."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
