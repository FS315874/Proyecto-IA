"""Aceptación simulada de v0.16 sin abrir aplicaciones reales ni usar red."""

from desktop_agent.parser import parse_command
from desktop_agent.tools.applications import open_application


def main() -> int:
    scenarios = {
        "abrí Steam": "steam",
        "abrí VoiceMeeter Banana": "voicemeeter",
        "abrí League of Legends": "league_of_legends",
        "abrí God of War Ragnarok": "god_of_war_ragnarok",
    }

    started: list[str] = []
    for command, expected_name in scenarios.items():
        action = parse_command(command)
        if action is None or action.arguments.get("name") != expected_name:
            raise AssertionError(f"Parser inválido para el escenario: {command}")

        result = open_application(
            expected_name,
            path_checker=lambda _path: True,
            finder=lambda _name: None,
            starter=lambda executable, *arguments: started.append(executable),
            process_checker=lambda names: bool(names),
            waiter=lambda _seconds: None,
        )
        if not result.success or "verificada" not in result.message:
            raise AssertionError(f"Lanzamiento simulado inválido: {command}")

    rejected = open_application(
        "aplicacion_inventada",
        starter=started.append,
    )
    if rejected.success or "no está permitida" not in rejected.message:
        raise AssertionError("Una aplicación fuera del catálogo no fue rechazada")
    if len(started) != len(scenarios):
        raise AssertionError("El escenario rechazado produjo un efecto")

    print("APPLICATION-16 QA: OK")
    print("Escenarios permitidos: 4")
    print("Escenarios rechazados: 1")
    print("Efectos reales: 0")
    print("Llamadas de IA: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
