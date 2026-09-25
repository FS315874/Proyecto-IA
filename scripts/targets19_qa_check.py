"""Regresión acotada de v0.19 sin abrir VS Code ni leer rutas personales."""

import unittest


def main() -> int:
    modules = (
        "tests.test_approved_targets",
        "tests.test_parser",
        "tests.test_interpretation",
        "tests.test_plans",
        "tests.test_openai_provider",
        "tests.test_openai_plan_provider",
        "tests.test_cli",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    print("TARGETS19_QA_OK: registro, rechazo, parser, IA, planes y CLI; sin aperturas reales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
