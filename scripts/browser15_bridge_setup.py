"""Inspecciona, instala o retira el host nativo del navegador."""

import argparse
from pathlib import Path

from desktop_agent.browser_bridge_install import (
    BrowserBridgeInstallError,
    inspect_native_host,
    install_native_host,
    uninstall_native_host,
)


def _extension_directory() -> Path:
    return (Path(__file__).resolve().parents[1] / "browser_extension").resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--install-host", action="store_true")
    actions.add_argument("--uninstall-host", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.install_host:
            status = install_native_host()
        elif arguments.uninstall_host:
            uninstall_native_host()
            print("Host nativo retirado del usuario actual.")
            return 0
        else:
            status = inspect_native_host()
    except BrowserBridgeInstallError as error:
        print(str(error))
        return 1
    print(f"Host nativo listo: {'sí' if status.ready else 'no'}")
    print(f"Extensión: {_extension_directory()}")
    print(f"ID fijo: {status.extension_id}")
    return 0 if status.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
