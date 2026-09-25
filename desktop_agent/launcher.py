"""Entrada sin consola con errores de inicio visibles y redactados."""


def main() -> int:
    try:
        from desktop_agent.tk_app import run_gui
        result = run_gui()
    except Exception:
        result = 1
    if result:
        import tkinter as tk
        from tkinter import messagebox

        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Desktop Agent",
                "Ya hay una ventana de Desktop Agent abierta. Buscala en la barra de tareas."
                if result == 2 else
                "No se pudo iniciar Desktop Agent. Comprobá la instalación de Python y las dependencias; "
                "ejecutá python -m desktop_agent --gui desde la carpeta del proyecto para diagnosticar.",
                parent=root,
            )
            root.destroy()
        except tk.TclError:
            pass
    return result
