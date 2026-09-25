"""Vista local del historial: revisar no significa corregir ni borrar un fallo."""
from desktop_agent.error_history import ErrorHistoryHandler, HistoryUnavailable


class ErrorHistoryDialog:
    def __init__(self, root, handler: ErrorHistoryHandler):
        import tkinter as tk
        from tkinter import ttk

        self.handler = handler
        self.window = tk.Toplevel(root)
        self.window.title("Historial de errores — Desktop Agent")
        self.window.geometry("920x520")
        self.window.minsize(700, 420)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=1)
        ttk.Label(self.window, text="Últimos 500 incidentes locales. Sin órdenes, transcripciones, audio, URLs ni claves.",
                  wraplength=850).grid(row=0, column=0, sticky="w", padx=12, pady=8)
        controls = ttk.Frame(self.window)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        self.include_reviewed = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Incluir revisados", variable=self.include_reviewed, command=self.refresh).pack(side="left")
        ttk.Button(controls, text="Actualizar", command=self.refresh).pack(side="left", padx=8)
        self.review_button = ttk.Button(controls, text="Marcar seleccionado como revisado", command=self.mark_reviewed, state="disabled")
        self.review_button.pack(side="left")
        table_frame = ttk.Frame(self.window)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("date", "category", "stage", "code"), show="headings", selectmode="browse")
        for name, title, width in (("date", "Fecha UTC", 195), ("category", "Clasificación preliminar", 175),
                                   ("stage", "Etapa", 145), ("code", "Código", 260)):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=90)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda _: self.show_selected())
        self.details = tk.StringVar(value="")
        ttk.Label(self.window, textvariable=self.details, wraplength=850, justify="left").grid(row=3, column=0, sticky="ew", padx=12, pady=8)
        self.status = tk.StringVar(value="")
        ttk.Label(self.window, textvariable=self.status, wraplength=850).grid(row=4, column=0, sticky="w", padx=12, pady=8)
        self.entries = {}
        self.refresh()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        self.entries = {}
        self.details.set("Revisado sólo indica que se examinó; no confirma una corrección.")
        self.review_button.configure(state="disabled")
        try:
            incidents = self.handler.history.list(include_reviewed=self.include_reviewed.get())
        except HistoryUnavailable as error:
            self.status.set(str(error))
            return
        for incident in incidents:
            item = incident.report()
            self.entries[str(incident.id)] = item
            self.table.insert("", "end", iid=str(incident.id), values=(
                incident.occurred_at, item["category"], incident.stage or incident.source, incident.code))
        warning = (f" No se guardaron {self.handler.dropped_count} incidentes durante esta sesión."
                   if self.handler.dropped_count else "")
        self.status.set(f"{len(incidents)} incidentes {'registrados' if self.include_reviewed.get() else 'sin revisar'}." + warning)

    def show_selected(self):
        selected = self.table.selection()
        entry = self.entries.get(selected[0]) if selected else None
        self.review_button.configure(state="normal" if entry and not entry["reviewed"] else "disabled")
        if not entry:
            return
        self.details.set(f"#{entry['id']} · v{entry['version']} · {entry['source']} · {entry['tool'] or 'sin herramienta'}\n"
                         f"{entry['summary']}\nQué revisar: {entry['next_check']}\n"
                         f"Duración: {entry['duration_ms'] if entry['duration_ms'] is not None else 'no medida'} ms · "
                         f"{'Revisado' if entry['reviewed'] else 'Sin revisar'}. La categoría no prueba la causa.")

    def mark_reviewed(self):
        selected = self.table.selection()
        if not selected or selected[0] not in self.entries:
            return
        try:
            updated = self.handler.history.mark_reviewed(int(selected[0]))
        except HistoryUnavailable as error:
            self.status.set(str(error))
            return
        self.refresh()
        if not updated:
            self.status.set("El incidente ya no está en el historial; pudo salir por retención. No se modificó otro registro.")
