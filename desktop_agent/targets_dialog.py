"""Registro explícito de proyectos y documentación desde selectores del sistema."""

from __future__ import annotations

from desktop_agent.approved_targets import ApprovedTargetStore, TargetError, TargetKind


def show_targets(parent, store: ApprovedTargetStore) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    window = tk.Toplevel(parent)
    window.title("Mis proyectos y documentos · Desktop Agent")
    window.transient(parent)
    window.grab_set()
    window.geometry("680x430")
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(3, weight=1)
    ttk.Label(body, text="Destinos que autorizaste", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(
        body,
        text="Elegí una carpeta de proyecto o un archivo .md, .markdown, .txt o .rst. Se abrirán en VS Code.",
        wraplength=630,
    ).grid(row=1, column=0, sticky="w", pady=(4, 12))
    name = tk.StringVar()
    row = ttk.Frame(body)
    row.grid(row=2, column=0, sticky="ew")
    ttk.Label(row, text="Nombre para pedirlo por voz:").pack(side="left")
    ttk.Entry(row, textvariable=name, width=40).pack(side="left", padx=8)
    listing = ttk.Treeview(body, columns=("kind",), show="tree headings", selectmode="browse")
    listing.heading("#0", text="Nombre")
    listing.heading("kind", text="Tipo")
    listing.column("#0", width=430)
    listing.column("kind", width=140)
    listing.grid(row=3, column=0, sticky="nsew", pady=12)
    message = tk.StringVar()
    ttk.Label(body, textvariable=message, foreground="#b91c1c", wraplength=630).grid(row=4, column=0, sticky="w")

    def refresh() -> None:
        listing.delete(*listing.get_children())
        try:
            for item in store.list():
                listing.insert("", "end", text=item.name, values=("Proyecto" if item.kind is TargetKind.PROJECT else "Documento",))
            message.set("")
        except TargetError as error:
            message.set(str(error))

    def add(kind: TargetKind) -> None:
        path = (
            filedialog.askdirectory(parent=window, title="Elegí la carpeta del proyecto")
            if kind is TargetKind.PROJECT
            else filedialog.askopenfilename(
                parent=window,
                title="Elegí un documento de texto",
                filetypes=[("Documentación de texto", "*.md *.markdown *.txt *.rst")],
            )
        )
        if not path:
            return
        try:
            from pathlib import Path

            store.add(name.get(), kind, Path(path))
        except TargetError as error:
            message.set(str(error))
            return
        name.set("")
        refresh()

    def remove() -> None:
        selected = listing.selection()
        if not selected:
            message.set("Seleccioná un destino para quitarlo.")
            return
        chosen = listing.item(selected[0], "text")
        if not messagebox.askyesno("Quitar destino", f"¿Quitar «{chosen}» del catálogo? El archivo no se borra.", parent=window):
            return
        try:
            store.remove(chosen)
        except TargetError as error:
            message.set(str(error))
            return
        refresh()

    buttons = ttk.Frame(body)
    buttons.grid(row=5, column=0, sticky="ew", pady=(12, 0))
    ttk.Button(buttons, text="Agregar proyecto", command=lambda: add(TargetKind.PROJECT)).pack(side="left")
    ttk.Button(buttons, text="Agregar documento", command=lambda: add(TargetKind.DOCUMENT)).pack(side="left", padx=8)
    ttk.Button(buttons, text="Quitar seleccionado", command=remove).pack(side="left")
    ttk.Button(buttons, text="Cerrar", command=window.destroy).pack(side="right")
    refresh()
