import logging
import sys
from collections.abc import Callable, Sequence

from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.logging_config import configure_logging
from desktop_agent.parser import parse_command
from desktop_agent.tools.applications import open_application
from desktop_agent.tools.browser import open_url

Output = Callable[[str], None]


def process_command(
    command: str,
    executor: ActionExecutor,
    logger: logging.Logger,
    output: Output = print,
) -> bool:
    logger.info("User: %s", command)
    output("Entendiendo comando...")

    action = parse_command(command)
    if action is None:
        logger.info("Status: UNSUPPORTED_COMMAND")
        output("Comando no soportado todavía.")
        return False

    logger.info("Intent: %s", action.intent.value)
    if "url" in action.arguments:
        logger.info("URL: %s", action.arguments["url"])
    if "name" in action.arguments:
        logger.info("Application: %s", action.arguments["name"])

    output(f"Ejecutando {action.tool_name}...")
    try:
        result = executor.execute(action)
    except ActionExecutionError as error:
        output(f"Error: {error}")
        return False

    output(result.message)
    return True


def _run_interactive(
    executor: ActionExecutor,
    logger: logging.Logger,
    output: Output = print,
) -> int:
    output("Desktop Agent v0.2 — escribí 'salir' para terminar.")
    while True:
        try:
            command = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            output("\nHasta luego.")
            return 0

        if command.casefold() in {"salir", "exit"}:
            output("Hasta luego.")
            return 0
        if not command:
            continue

        process_command(command, executor, logger, output)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        logger = configure_logging()
    except OSError as error:
        print(f"No se pudo crear el archivo de log: {error}", file=sys.stderr)
        return 1

    executor = ActionExecutor(
        tools={
            "open_url": open_url,
            "open_application": open_application,
        },
        logger=logger,
    )

    if arguments:
        command = " ".join(arguments)
        return 0 if process_command(command, executor, logger) else 1

    return _run_interactive(executor, logger)
