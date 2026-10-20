#!/usr/bin/env python3
"""Tkinter GUI for bookmarks-merge.py.

Integration model: subprocess. Never imports the CLI; builds the exact argv and
captures stdout+stderr in a log panel. The only introspection is
``--list-browsers --json``, whose registry drives every widget.

Pure logic (build_command / self_test) runs without a display:
    python3 bookmarks_gui.py --self-test
"""

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

SCRIPT = Path(__file__).resolve().parent / "bookmarks-merge.py"

# CLI defaults (mirrored only to know when a value is "non-default").
DEFAULT_ATTACH_FOLDER = "Favoritos"
DEFAULT_MAX_EXAMPLES = 15
DEFAULT_OUT = str(SCRIPT.parent / "out")
DEFAULT_SNAPSHOTS = str(SCRIPT.parent / "snapshots")


# ----------------------------------------------------------------- pure logic


def fetch_registry():
    """Ask the CLI for its browser/pair registry (JSON). Raises on failure."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--list-browsers", "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=SCRIPT.parent,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"--list-browsers falló (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def build_command(state):
    """Turn a plain dict state into the full CLI argv.

    state keys: pair, canonical, only, attach_folder, out, snapshots,
    max_examples, no_dedupe, force, apply, profiles {name: path},
    browsers {name: {profile_flag, ...}} (the registry).
    Raises ValueError on an invalid pair/canonical/only.
    """
    browsers = state.get("browsers") or {}
    members = [
        m.strip().lower() for m in str(state.get("pair", "")).split(",") if m.strip()
    ]
    if (len(members) != 2 or len(set(members)) != 2
            or any(m not in browsers for m in members)):
        raise ValueError(f"Par inválido: {state.get('pair')!r}")
    canonical = str(state.get("canonical", "")).strip().lower()
    if canonical not in members:
        raise ValueError(f"--canonical {canonical!r} no pertenece al par {members}")
    only = state.get("only", "both")
    if only not in ("both",) and only not in members:
        raise ValueError(f"--only {only!r} no es válido para el par {members}")

    cmd = [sys.executable, str(SCRIPT),
           "--pair", ",".join(members), "--canonical", canonical]
    if only != "both":
        cmd += ["--only", only]
    attach = str(state.get("attach_folder", "") or "").strip()
    if attach and attach != DEFAULT_ATTACH_FOLDER:
        cmd += ["--attach-folder", attach]
    out = str(state.get("out", "") or "").strip()
    if out:
        cmd += ["--out", out]
    snapshots = str(state.get("snapshots", "") or "").strip()
    if snapshots:
        cmd += ["--snapshots", snapshots]
    if int(state.get("max_examples", DEFAULT_MAX_EXAMPLES)) != DEFAULT_MAX_EXAMPLES:
        cmd += ["--max-examples", str(int(state["max_examples"]))]
    if state.get("no_dedupe"):
        cmd += ["--no-dedupe"]
    if state.get("force"):
        cmd += ["--force"]
    if state.get("apply"):
        cmd += ["--apply"]

    # Profile overrides: only for pair members with a non-empty entry, using the
    # registry's own flag (--ff-profile / --brave-profile / --chromium-profile).
    profiles = state.get("profiles") or {}
    for name in browsers:  # registry order keeps the argv deterministic
        value = str(profiles.get(name, "") or "").strip()
        if name in members and value:
            cmd += [browsers[name]["profile_flag"], value]
    return cmd


def self_test():
    """Assert build_command against the real registry. No Tk, no display."""
    registry = fetch_registry()
    browsers = {b["name"]: b for b in registry["browsers"]}
    base = {
        "browsers": browsers, "pair": "", "canonical": "", "only": "both",
        "attach_folder": DEFAULT_ATTACH_FOLDER, "out": "", "snapshots": "",
        "max_examples": DEFAULT_MAX_EXAMPLES, "no_dedupe": False, "force": False,
        "apply": False, "profiles": {},
    }
    checks = []

    def expect(name, actual, wanted):
        assert actual == wanted, f"{name}\n  esperado: {wanted}\n  obtenido: {actual}"
        checks.append(name)

    def raises(name, bad_state):
        try:
            build_command(bad_state)
        except ValueError:
            checks.append(name)
            return
        raise AssertionError(f"{name}: no lanzó ValueError")

    # Minimal state for each of the 3 valid pairs (canonical = first member).
    for pair in registry["pairs"]:
        first = pair.split(",")[0]
        state = {**base, "pair": pair, "canonical": first}
        expect(f"mínimo {pair} canónico={first}",
               build_command(state),
               [sys.executable, str(SCRIPT), "--pair", pair, "--canonical", first])

    # Canonical = second member swaps the value (every valid pair).
    for pair in registry["pairs"]:
        second = pair.split(",")[1]
        expect(f"canónico = segundo miembro ({pair})",
               build_command({**base, "pair": pair, "canonical": second}),
               [sys.executable, str(SCRIPT), "--pair", pair, "--canonical", second])

    # Every optional flag included.
    expect("todos los flags opcionales",
           build_command({**base, "pair": "firefox,brave", "canonical": "brave",
                          "only": "brave", "attach_folder": "Custom",
                          "out": "/tmp/o", "snapshots": "/tmp/s",
                          "max_examples": 3, "no_dedupe": True, "force": True,
                          "apply": True}),
           [sys.executable, str(SCRIPT), "--pair", "firefox,brave",
            "--canonical", "brave", "--only", "brave", "--attach-folder", "Custom",
            "--out", "/tmp/o", "--snapshots", "/tmp/s", "--max-examples", "3",
            "--no-dedupe", "--force", "--apply"])

    # Defaults are excluded (minimal checks above) plus out/snapshots empty,
    # attach-folder equal to the default and max_examples equal to 15.
    expect("defaults excluidos",
           build_command({**base, "pair": "brave,chromium", "canonical": "chromium",
                          "attach_folder": DEFAULT_ATTACH_FOLDER,
                          "max_examples": DEFAULT_MAX_EXAMPLES}),
           [sys.executable, str(SCRIPT), "--pair", "brave,chromium",
            "--canonical", "chromium"])

    # Profile overrides: only non-empty entries, using the registry's profile_flag.
    expect("solo perfiles no vacíos",
           build_command({**base, "pair": "firefox,brave", "canonical": "firefox",
                          "profiles": {"firefox": "", "brave": "/x/brave",
                                       "chromium": "/x/chromium"}}),
           [sys.executable, str(SCRIPT), "--pair", "firefox,brave",
            "--canonical", "firefox", "--brave-profile", "/x/brave"])
    expect("flag de perfil según registry",
           build_command({**base, "pair": "firefox,chromium", "canonical": "firefox",
                          "profiles": {"firefox": "/x/ff", "chromium": "/x/ch"}}),
           [sys.executable, str(SCRIPT), "--pair", "firefox,chromium",
            "--canonical", "firefox", "--ff-profile", "/x/ff",
            "--chromium-profile", "/x/ch"])

    # Invalid state: canonical not in the pair -> ValueError.
    raises("canónico fuera del par lanza ValueError",
           {**base, "pair": "firefox,brave", "canonical": "chromium"})
    raises("par desconocido lanza ValueError",
           {**base, "pair": "firefox,safari", "canonical": "firefox"})
    raises("--only inválido lanza ValueError",
           {**base, "pair": "firefox,brave", "canonical": "firefox",
            "only": "chromium"})
    raises("miembros duplicados lanza ValueError",
           {**base, "pair": "firefox,firefox", "canonical": "firefox"})
    return checks


# ----------------------------------------------------------------- GUI


class BookmarksGui:
    """Window construction and run orchestration. Never instantiated at import."""

    def __init__(self, root, registry):
        self.root = root
        self.registry = registry
        self.browsers = {b["name"]: b for b in registry["browsers"]}
        self.pairs = registry["pairs"]
        self.proc = None
        self.reader = None
        self.log_queue = queue.Queue()
        self._updating = False
        self._action_buttons = []

        root.title(f"IA-sync — Sincronización de marcadores "
                   f"(v{registry.get('script_version', '?')})")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        if self.pairs:
            first_a, first_b = self.pairs[0].split(",")
            self.a_var.set(first_a)
            self.b_var.set(first_b)
            self._prev_a, self._prev_b = first_a, first_b
        else:
            self._prev_a = self._prev_b = ""
        self._on_pair_change()
        self._set_status(f"Registro cargado: {len(self.browsers)} navegadores, "
                         f"{len(self.pairs)} pares.")
        root.after(100, self._poll)

    # ---- layout ------------------------------------------------------------

    def _build_ui(self):
        # Status bar packed first so it sits at the bottom.
        status_frame = ttk.Frame(self.root, padding=(8, 4))
        status_frame.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(status_frame, textvariable=self.status_var,
                  foreground="#555").pack(fill="x")

        sync = ttk.LabelFrame(self.root, text="Sincronización", padding=8)
        sync.pack(fill="x", padx=8, pady=(8, 4))
        self.a_var, self.b_var = tk.StringVar(), tk.StringVar()
        self.pair_combos = []
        for col, (label, var) in enumerate((("Navegador A", self.a_var),
                                            ("Navegador B", self.b_var))):
            ttk.Label(sync, text=label).grid(row=0, column=col * 2, sticky="w")
            combo = ttk.Combobox(sync, textvariable=var, state="readonly", width=14,
                                 values=list(self.browsers))
            combo.grid(row=0, column=col * 2 + 1, padx=(2, 16))
            combo.bind("<<ComboboxSelected>>",
                       lambda _e, which=col: self._on_browser_change(which))
            self.pair_combos.append(combo)
        ttk.Label(sync, text="Canónico:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.canon_box = ttk.Frame(sync)
        self.canon_box.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.canonical_var = tk.StringVar()
        ttk.Label(sync, text="Aplicar a (--only):").grid(row=2, column=0, sticky="w",
                                                         pady=(6, 0))
        self.only_var = tk.StringVar(value="both")
        self.only_combo = ttk.Combobox(sync, textvariable=self.only_var,
                                       state="readonly", width=14)
        self.only_combo.grid(row=2, column=1, sticky="w", pady=(6, 0))

        profiles = ttk.LabelFrame(self.root, text="Perfiles", padding=8)
        profiles.pack(fill="x", padx=8, pady=4)
        self.profile_rows = {}
        for row, (name, info) in enumerate(self.browsers.items()):
            ttk.Label(profiles, text=info["display"] + ":").grid(
                row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(profiles, width=44)
            entry.grid(row=row, column=1, padx=4, pady=2)
            hint = ttk.Label(profiles, foreground="#777",
                             text=self._profile_hint(info))
            hint.grid(row=row, column=2, sticky="w", padx=4)
            browse = ttk.Button(profiles, text="Examinar…", width=11,
                                command=lambda n=name: self._browse_profile(n))
            browse.grid(row=row, column=3, padx=4, pady=2)
            self.profile_rows[name] = {"entry": entry, "browse": browse, "hint": hint}

        options = ttk.LabelFrame(self.root, text="Opciones", padding=8)
        options.pack(fill="x", padx=8, pady=4)
        self.attach_var = tk.StringVar(value=DEFAULT_ATTACH_FOLDER)
        self.out_var = tk.StringVar(value=DEFAULT_OUT)
        self.snapshots_var = tk.StringVar(value=DEFAULT_SNAPSHOTS)
        self._option_row(options, 0, "Carpeta adjunta (--attach-folder):",
                         self.attach_var, 22)
        self._option_row(options, 1, "Carpeta de salida (--out):",
                         self.out_var, 44, "Carpeta de salida")
        self._option_row(options, 2, "Carpeta de snapshots (--snapshots):",
                         self.snapshots_var, 44, "Carpeta de snapshots")
        ttk.Label(options, text="Ejemplos máximos (--max-examples):").grid(
            row=3, column=0, sticky="w")
        self.max_examples_var = tk.StringVar(value=str(DEFAULT_MAX_EXAMPLES))
        ttk.Spinbox(options, from_=0, to=1000, textvariable=self.max_examples_var,
                    width=6).grid(row=3, column=1, sticky="w", padx=4, pady=2)
        self.no_dedupe_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Sin dedupe (--no-dedupe)",
                        variable=self.no_dedupe_var).grid(
            row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(options, text="Forzar (--force) — peligroso",
                        variable=self.force_var).grid(
            row=5, column=0, columnspan=2, sticky="w")

        actions = ttk.LabelFrame(self.root, text="Acciones", padding=8)
        actions.pack(fill="x", padx=8, pady=4)
        specs = (
            ("Previsualizar (dry-run)", self.on_preview),
            ("Aplicar…", self.on_apply),
            ("Restaurar snapshot…", self.on_restore),
            ("Abrir carpeta de salida", self.on_open_out),
            ("Abrir reporte.txt", self.on_open_report),
        )
        for text, command in specs:
            btn = ttk.Button(actions, text=text, command=command)
            btn.pack(side="left", padx=(0, 6))
            self._action_buttons.append(btn)

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        header = ttk.Frame(log_frame)
        header.pack(fill="x")
        ttk.Button(header, text="Limpiar", command=self._clear_log).pack(side="right")
        self.log = scrolledtext.ScrolledText(log_frame, height=12, state="disabled",
                                             wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    # ---- registry-driven widgets ------------------------------------------

    def _pair_members(self):
        return [self.a_var.get(), self.b_var.get()]

    def _option_row(self, frame, row, label, var, width, browse_title=None):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(frame, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", padx=4, pady=2)
        if browse_title:
            ttk.Button(frame, text="Examinar…", width=11, command=lambda: (
                self._pick_dir(var, browse_title))).grid(row=row, column=2, padx=4)

    def _display(self, name):
        # Degraded registry (fetch failure): unknown names fall back to themselves.
        info = self.browsers.get(name)
        return info["display"] if info else name

    def _profile_hint(self, info):
        # profile_base is the base dir, NOT the profile: the CLI resolves the
        # active Firefox profile from profiles.ini and the chromium Default dir.
        if info["kind"] == "firefox":
            return "Por defecto: perfil activo de profiles.ini"
        return f"Por defecto: {info['profile_base']}/Default"

    def _on_browser_change(self, which):
        if self._updating:
            return
        a, b = self.a_var.get(), self.b_var.get()
        if a == b:
            self._updating = True
            if which == 0:
                self.a_var.set(self._prev_a)
            else:
                self.b_var.set(self._prev_b)
            self._updating = False
            self._set_status("Los dos navegadores deben ser distintos: "
                             "se revierte la selección.")
            return
        self._prev_a, self._prev_b = a, b
        self._on_pair_change()

    def _on_pair_change(self):
        for child in self.canon_box.winfo_children():
            child.destroy()
        if not self.browsers:
            # Degraded registry (fetch failure): no widgets to populate.
            self.only_combo.configure(values=["both"])
            self.only_var.set("both")
            return
        members = self._pair_members()
        # Canónico: only the two pair members.
        if self.canonical_var.get() not in members:
            self.canonical_var.set(members[0])
        for member in members:
            ttk.Radiobutton(self.canon_box, text=self._display(member),
                            variable=self.canonical_var, value=member).pack(
                side="left", padx=(0, 8))
        # --only: both / A / B, rebuilt per pair.
        self.only_combo.configure(values=["both"] + members)
        if self.only_var.get() not in ("both",) + tuple(members):
            self.only_var.set("both")
        # Profile rows: enabled only for the pair's members.
        for name, row in self.profile_rows.items():
            active = name in members
            row["entry"].configure(state="normal" if active else "disabled")
            row["browse"].configure(state="normal" if active else "disabled")
            row["hint"].configure(
                foreground="#777" if active else "#bbb")
        self._set_status(f"Par activo: {members[0]},{members[1]} — "
                         f"canónico: {self._display(self.canonical_var.get())}")

    def _browse_profile(self, name):
        info = self.browsers[name]
        chosen = filedialog.askdirectory(title=f"Perfil de {info['display']}")
        if not chosen:
            return
        store = Path(chosen) / ("places.sqlite" if info["kind"] == "firefox"
                                else "Bookmarks")
        if not store.exists():
            messagebox.showwarning(
                "Store no encontrado",
                f"No se encontró «{store.name}» dentro de {chosen}.\n"
                "La ruta se toma igual; el CLI fallará si el perfil no es válido.")
        entry = self.profile_rows[name]["entry"]
        entry.delete(0, "end")
        entry.insert(0, chosen)

    def _pick_dir(self, var, title):
        chosen = filedialog.askdirectory(title=title)
        if chosen:
            var.set(chosen)

    # ---- state / commands --------------------------------------------------

    def _state(self):
        raw_max = str(self.max_examples_var.get()).strip()
        try:
            max_examples = int(raw_max) if raw_max else DEFAULT_MAX_EXAMPLES
        except ValueError:
            raise ValueError(f"--max-examples debe ser un número, no «{raw_max}»") \
                from None
        return {
            "browsers": self.browsers,
            "pair": ",".join(self._pair_members()),
            "canonical": self.canonical_var.get(),
            "only": self.only_var.get(),
            "attach_folder": self.attach_var.get(),
            "out": self.out_var.get(),
            "snapshots": self.snapshots_var.get(),
            "max_examples": max_examples,
            "no_dedupe": self.no_dedupe_var.get(),
            "force": self.force_var.get(),
            "apply": False,
            "profiles": {name: row["entry"].get()
                         for name, row in self.profile_rows.items()},
        }

    def _apply_summary(self, state):
        """Confirmation summary based on the CLI's documented write-back matrix."""
        # Pair order mirrors the argv so the summary never contradicts the log line.
        members = [m.strip() for m in state["pair"].split(",") if m.strip()]
        canonical, only = state["canonical"], state["only"]
        auto = ([m for m in members if m != "firefox"] if "firefox" in members
                else [canonical])
        auto = [m for m in auto if only in ("both", m)]
        out = Path(state["out"] or DEFAULT_OUT).expanduser()
        lines = [
            "Se ejecutará --apply con estos parámetros:",
            f"Par: {members[0]},{members[1]}   Canónico: "
            f"{self._display(canonical)}   --only: {only}",
            "",
            "Escritura automática: "
            + (", ".join(self._display(m) for m in auto) or "ninguna"),
        ]
        if "firefox" in members and only in ("both", "firefox"):
            lines.append(f"Firefox: restore MANUAL desde {out / 'firefox-restore.json'} "
                         "(desde la Biblioteca)")
        for m in members:
            if m != "firefox" and m not in auto and only in ("both", m):
                lines.append(f"{self._display(m)}: copia manual del artefacto "
                             "sobre su almacén (miembro no canónico)")
        lines += ["", "Se tomará un snapshot antes de escribir.", "¿Continuar?"]
        return "\n".join(lines)

    def _report_input_error(self, exc, dialog):
        msg = f"Entrada inválida: {exc}"
        self._set_status(msg)
        self._append_log(msg)
        if dialog:
            messagebox.showerror("Entrada inválida", str(exc))

    def on_preview(self):
        try:
            cmd = build_command(self._state())
        except ValueError as exc:
            self._report_input_error(exc, dialog=False)
            return
        self._start(cmd)

    def on_apply(self):
        try:
            state = self._state()
            state["apply"] = True
            cmd = build_command(state)
        except ValueError as exc:
            self._report_input_error(exc, dialog=True)
            return
        self._start(cmd, self._apply_summary(state))

    def on_restore(self):
        try:
            state = self._state()
        except ValueError as exc:
            self._report_input_error(exc, dialog=True)
            return
        initial = str(Path(state["snapshots"] or DEFAULT_SNAPSHOTS).expanduser())
        snapshot = filedialog.askdirectory(title="Snapshot a restaurar",
                                           initialdir=initial)
        if not snapshot:
            self._set_status("Restauración cancelada.")
            return
        summary = (f"Se restaurará el snapshot:\n{snapshot}\n"
                   f"sobre los perfiles de {state['pair']} "
                   f"(canónico: {state['canonical']}).\n"
                   "Los archivos actuales se sobrescriben. ¿Continuar?")
        try:
            cmd = build_command(state) + ["--restore", snapshot]
        except ValueError as exc:
            self._report_input_error(exc, dialog=True)
            return
        self._start(cmd, summary)

    def on_open_out(self):
        self._open(Path(self.out_var.get() or DEFAULT_OUT).expanduser(),
                   is_dir=True)

    def on_open_report(self):
        self._open(Path(self.out_var.get() or DEFAULT_OUT).expanduser() / "report.txt")

    def _open(self, path, is_dir=False):
        ok = path.is_dir() if is_dir else path.is_file()
        if not ok:
            self._set_status(f"No existe: {path}")
            return
        subprocess.Popen(["xdg-open", str(path)])

    # ---- process orchestration --------------------------------------------

    def _start(self, cmd, confirm_summary=None):
        if self.proc is not None and self.proc.poll() is None:
            self._set_status("Ya hay un proceso en curso; esperá a que termine.")
            return
        if confirm_summary and not messagebox.askyesno("Confirmar operación",
                                                       confirm_summary):
            self._set_status("Operación cancelada por el usuario.")
            return
        self._clear_log()
        self._append_log("$ " + " ".join(cmd))
        try:
            # errors="replace": one undecodable byte must never kill the stream.
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, cwd=SCRIPT.parent)
        except OSError as exc:
            self.proc = None
            self._append_log(f"Error al lanzar el proceso: {exc}")
            return
        self.reader = threading.Thread(target=self._read_output,
                                       args=(self.proc, self.log_queue),
                                       daemon=True)
        self.reader.start()
        self._set_running(True)
        self._set_status("Proceso en curso…")

    @staticmethod
    def _read_output(proc, log_queue):
        try:
            for line in iter(proc.stdout.readline, ""):
                log_queue.put(line.rstrip("\n"))
        except Exception as exc:  # noqa: BLE001 - never die silently mid-stream
            log_queue.put(f"[error leyendo la salida del proceso: {exc}]")

    def _poll(self):
        try:
            done_draining = False
            while not done_draining:
                try:
                    line = self.log_queue.get_nowait()
                except queue.Empty:
                    done_draining = True
                else:
                    self._append_log(line)
            if (self.proc is not None and self.proc.poll() is not None
                    and (self.reader is None or not self.reader.is_alive())):
                code = self.proc.returncode
                self.proc = None
                self._append_log(f"[proceso terminado — código de salida {code}]")
                self._set_running(False)
                self._set_status(f"Proceso finalizado (exit {code}).")
        except Exception as exc:  # noqa: BLE001 - a failed tick must not kill the timer
            try:
                self._append_log(f"[error interno en el polling: {exc}]")
            except Exception:  # noqa: BLE001 - log pane may be gone during teardown
                pass
        finally:
            self.root.after(100, self._poll)

    def on_close(self):
        if self.proc is not None and self.proc.poll() is None:
            if not messagebox.askyesno(
                    "Salir", "Hay un proceso en curso. ¿Terminarlo y salir?"):
                return
            self.proc.terminate()
            # Reap in the background so the child never lingers as a zombie.
            threading.Thread(target=self.proc.wait, daemon=True).start()
        self.root.destroy()

    # ---- helpers -----------------------------------------------------------

    def _set_running(self, running):
        for btn in self._action_buttons:
            btn.configure(state="disabled" if running else "normal")

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_status(self, text):
        self.status_var.set(text)


# ----------------------------------------------------------------- entrypoint


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv == ["--self-test"]:
        checks = self_test()
        print(f"self_test OK ({len(checks)} comprobaciones)")
        for name in checks:
            print(f"  ok - {name}")
        return 0
    if argv:
        print("Uso: python3 bookmarks_gui.py [--self-test]", file=sys.stderr)
        return 2

    registry_error = None
    try:
        registry = fetch_registry()
    except Exception as exc:  # noqa: BLE001 - dialog + degraded window
        registry_error = str(exc)
        registry = {"script_version": "?", "pairs": [], "browsers": []}

    root = tk.Tk()
    app = BookmarksGui(root, registry)
    if registry_error:
        for btn in app._action_buttons:
            btn.configure(state="disabled")
        for combo in app.pair_combos:
            combo.configure(state="disabled")
        app._set_status("No se pudo leer el registro del CLI: la GUI queda "
                        "inhabilitada.")
        messagebox.showerror(
            "Registro no disponible",
            f"No se pudo obtener --list-browsers --json:\n{registry_error}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
