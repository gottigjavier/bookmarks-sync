#!/usr/bin/env python3
"""Tkinter GUI for bookmarks-merge.py.

Integration model: subprocess. Never imports the CLI; builds the exact argv and
runs it non-blocking, reporting progress and outcomes in a status bar (the CLI
writes its full report to out/report.txt, reachable from the window). The only
introspection is ``--list-browsers --json``, whose registry drives every widget.

Pure logic (build_command / self_test) runs without a display:
    python3 bookmarks_gui.py --self-test
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCRIPT = Path(__file__).resolve().parent / "bookmarks-merge.py"

# CLI defaults (mirrored only to know when a value is "non-default").
DEFAULT_ATTACH_FOLDER = "Favoritos"
DEFAULT_MAX_EXAMPLES = 15
DEFAULT_OUT = str(SCRIPT.parent / "out")
DEFAULT_SNAPSHOTS = str(SCRIPT.parent / "snapshots")


def option_defaults():
    """Declarative source of truth for every optional-flag widget default.

    Used by the widget builders, by build_command's no-flag comparison and by
    the reset button, so those three can never drift apart. Keys mirror the
    widget attribute suffixes (``<name>_var``) plus the checkboxes.
    """
    return {
        "attach": DEFAULT_ATTACH_FOLDER,
        "out": DEFAULT_OUT,
        "snapshots": DEFAULT_SNAPSHOTS,
        "max_examples": str(DEFAULT_MAX_EXAMPLES),
        "no_dedupe": False,
        "force": False,
        "write_both": False,
    }

# Single source of truth for every UI color. Neutral dark-grey ramp: the
# window < card < field steps are lightness-only (no blue tint), so surfaces
# separate by value instead of hue.
PALETTE = {
    "window": "#1a1a1a",    # app background (darkest step)
    "panel": "#232323",     # card / surface background
    "field": "#2e2e2e",     # inputs (lighter than the card)
    "field_border": "#3d3d3d",
    "text": "#ffffff",      # primary text
    "muted": "#a0a0a0",     # secondary text, status bar
    "dim": "#666666",       # tertiary text, placeholders
    "accent": "#3ddc84",    # positive / read-only actions (toned-down green)
    "accent_hover": "#34bd71",
    "danger": "#e94560",    # destructive actions
    "danger_hover": "#c7304a",
    "on_accent": "#12211a", # dark text on the accent green (AA at 8.5:1)
    "select_bg": "#e94560",
    "select_fg": "#ffffff",
}

# Fonts are created lazily (a Tk root must exist first).
FONT_SIZES = {"title": 18, "section": 13, "label": 11, "small": 9, "emoji": 13}

# Emoji font candidates in preference order; first installed one wins.
EMOJI_FONT_CANDIDATES = (
    "Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji",
    "Symbola", "DejaVu Sans",
)

# Display-only strings (state reaching build_command keeps CLI tokens).
ONLY_BOTH = "Ambos"


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


def profile_placeholder(info):
    """In-field hint naming the CLI's default profile for this browser.

    profile_base in the registry is the base dir, NOT the profile: the CLI
    resolves the active Firefox profile from profiles.ini and uses the Default
    dir for chromium-family browsers.
    """
    if info["kind"] == "firefox":
        return "Automático (profiles.ini)"
    return f"{info['profile_base']}/Default"


def token_for(browsers, value):
    """Map a browser display label (or raw name) back to its CLI token.

    Comboxes show display names (Firefox); build_command needs tokens
    (firefox). Unknown values pass through unchanged (degraded registry).
    """
    for name, info in browsers.items():
        if value == info.get("display"):
            return name
    return value


def only_label(browsers, token):
    """Map an --only CLI token to its combobox display label."""
    if token == "both":
        return ONLY_BOTH
    info = browsers.get(token)
    return info["display"] if info else token


def only_token(browsers, label):
    """Map an --only combobox display label back to its CLI token."""
    if label == ONLY_BOTH:
        return "both"
    for name, info in browsers.items():
        if label == info.get("display"):
            return name
    return label


def _is_default_profile(value, info):
    """True when a profile entry shows its placeholder or the default path.

    Those cases must NOT emit a profile flag: default == explicit same value.
    """
    try:
        placeholder = profile_placeholder(info)
    except KeyError:  # degraded/partial registry entry
        return False
    if value == placeholder:
        return True
    try:
        return value == str(Path(placeholder).expanduser())
    except (RuntimeError, OSError, ValueError):
        return False


# Migrated into PALETTE (single source of truth for colors).
PLACEHOLDER_FG = PALETTE["muted"]
REAL_FG = PALETTE["text"]


def attach_placeholder(entry, placeholder):
    """Show `placeholder` as gray hint text while `entry` is empty.

    Focusing clears the hint and restores the normal text color; blurring
    restores the hint when the field was left empty.
    """

    def show_hint():
        entry.delete(0, "end")
        entry.insert(0, placeholder)
        entry.configure(foreground=PLACEHOLDER_FG)

    def on_focus_in(_event):
        if entry.get() == placeholder:
            entry.delete(0, "end")
        entry.configure(foreground=REAL_FG)

    def on_focus_out(_event):
        if not entry.get().strip():
            show_hint()

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    show_hint()


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
    # --write-both: only for chromium+chromium pairs, writes to both live stores.
    if state.get("write_both"):
        is_chromium_pair = all(m != "firefox" for m in members)
        if is_chromium_pair:
            cmd += ["--write-both"]

    # Profile overrides: only for pair members with a non-empty entry, using the
    # registry's own flag (--ff-profile / --brave-profile / --chromium-profile).
    # Placeholder/default values count as empty (no flag emitted).
    profiles = state.get("profiles") or {}
    for name in browsers:  # registry order keeps the argv deterministic
        info = browsers[name]
        value = str(profiles.get(name, "") or "").strip()
        if name in members and value and not _is_default_profile(value, info):
            cmd += [info["profile_flag"], value]
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

    # Placeholder/default values read from the profile fields count as empty.
    ff_ph = profile_placeholder(browsers["firefox"])
    ch_ph = profile_placeholder(browsers["chromium"])
    expect("placeholders no emiten flag de perfil",
           build_command({**base, "pair": "firefox,brave", "canonical": "brave",
                          "profiles": {"firefox": ff_ph,
                                       "brave": profile_placeholder(browsers["brave"])}}),
           [sys.executable, str(SCRIPT), "--pair", "firefox,brave",
            "--canonical", "brave"])
    expect("valor igual al default (literal) omite el flag",
           build_command({**base, "pair": "brave,chromium", "canonical": "chromium",
                          "profiles": {"chromium": ch_ph}}),
           [sys.executable, str(SCRIPT), "--pair", "brave,chromium",
            "--canonical", "chromium"])
    expect("valor igual al default (expandido) omite el flag",
           build_command({**base, "pair": "brave,chromium", "canonical": "chromium",
                          "profiles": {"chromium": str(Path(ch_ph).expanduser())}}),
           [sys.executable, str(SCRIPT), "--pair", "brave,chromium",
            "--canonical", "chromium"])

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

    # write_both flag: only emitted for chromium+chromium pairs.
    expect("write_both emitido en par chromium+chromium",
           build_command({**base, "pair": "brave,chromium", "canonical": "brave",
                          "write_both": True}),
           [sys.executable, str(SCRIPT), "--pair", "brave,chromium",
            "--canonical", "brave", "--write-both"])
    expect("write_both NO emitido en par con Firefox",
           build_command({**base, "pair": "firefox,brave", "canonical": "brave",
                          "write_both": True}),
           [sys.executable, str(SCRIPT), "--pair", "firefox,brave",
            "--canonical", "brave"])
    expect("write_both NO emitido si False",
           build_command({**base, "pair": "brave,chromium", "canonical": "brave",
                          "write_both": False}),
           [sys.executable, str(SCRIPT), "--pair", "brave,chromium",
            "--canonical", "brave"])

    # Display layer (Spanish labels) must round-trip to CLI tokens: the state
    # that reaches build_command keeps tokens even though combos show labels.
    expect("etiquetas de pantalla → tokens CLI",
           (token_for(browsers, browsers["firefox"]["display"]),
            token_for(browsers, "brave"),
            only_label(browsers, "both"),
            only_label(browsers, "chromium"),
            only_token(browsers, ONLY_BOTH),
            only_token(browsers, browsers["brave"]["display"])),
           ("firefox", "brave", ONLY_BOTH, "Chromium", "both", "brave"))

    # The declarative defaults must be indistinguishable from "no optional
    # flags": the reset button and build_command share this one source.
    defaults = option_defaults()
    default_state = {
        "browsers": browsers, "pair": "firefox,brave", "canonical": "firefox",
        "only": "both", "attach_folder": defaults["attach"],
        "out": "", "snapshots": "",
        "max_examples": int(defaults["max_examples"]),
        "no_dedupe": defaults["no_dedupe"], "force": defaults["force"],
        "apply": False,
        # Profiles reset to their placeholders (no override emitted).
        "profiles": {name: profile_placeholder(info)
                     for name, info in browsers.items()},
    }
    expect("defaults de la UI → argv mínimo",
           build_command(default_state),
           [sys.executable, str(SCRIPT), "--pair", "firefox,brave",
            "--canonical", "firefox"])
    return checks


# ----------------------------------------------------------------- GUI


def apply_theme(root, style):
    """Apply PALETTE to the whole UI through one ttk style. Headless-safe.

    Base: clam with a safe fallback (the app keeps working without it).
    Unsupported options are skipped silently (see `safe`).
    """
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # clam unavailable: current theme kept, per-option skips below

    def safe(fn, *args, **kw):
        """Apply style options; on TclError retry one-by-one, dropping failures."""
        try:
            fn(*args, **kw)
        except tk.TclError:
            for key, value in kw.items():
                try:
                    fn(*args, **{key: value})
                except tk.TclError:
                    pass  # not supported by this theme: skip silently

    p = PALETTE
    root.configure(background=p["window"])
    # Best effort: classic Tk dialogs (msgbox/filedialog) on X11.
    root.option_add("*Dialog.msg*Background", p["panel"])
    root.option_add("*Dialog.msg*Foreground", p["text"])
    root.option_add("*TCombobox*Listbox*Background", p["field"])
    root.option_add("*TCombobox*Listbox*Foreground", p["text"])
    root.option_add("*TCombobox*Listbox*selectBackground", p["select_bg"])
    root.option_add("*TCombobox*Listbox*selectForeground", p["select_fg"])

    # Base style. Sections/cards are flat tk.Frame, so the ttk chrome that
    # remains is only for inputs, indicators and the popdown listbox.
    safe(style.configure, ".", background=p["panel"], foreground=p["text"],
         troughcolor=p["panel"], selectbackground=p["select_bg"],
         selectforeground=p["select_fg"])
    safe(style.configure, "TFrame", background=p["panel"])
    safe(style.configure, "TLabel", background=p["panel"], foreground=p["text"])

    # Inputs: flat dark fields, no bevel, accent focus ring.
    safe(style.configure, "TEntry", fieldbackground=p["field"],
         background=p["field"], foreground=p["text"],
         insertbackground=p["text"], selectbackground=p["select_bg"],
         selectforeground=p["select_fg"], bordercolor=p["field_border"],
         lightcolor=p["field"], darkcolor=p["field"], relief="flat", borderwidth=0)
    safe(style.map, "TEntry",
         fieldbackground=[("disabled", p["panel"])],
         background=[("disabled", p["panel"])],
         foreground=[("disabled", p["dim"])],
         bordercolor=[("focus", p["accent"])])
    safe(style.configure, "TCombobox", fieldbackground=p["field"],
         background=p["field"], foreground=p["text"], arrowcolor=p["text"],
         insertbackground=p["text"], bordercolor=p["field_border"],
         lightcolor=p["field"], darkcolor=p["field"], relief="flat", borderwidth=0)
    safe(style.map, "TCombobox",
         fieldbackground=[("readonly", p["field"]), ("disabled", p["panel"])],
         background=[("readonly", p["field"]), ("disabled", p["panel"])],
         foreground=[("readonly", p["text"]), ("disabled", p["dim"])],
         arrowcolor=[("disabled", p["dim"])],
         bordercolor=[("focus", p["accent"])])
    safe(style.configure, "ComboboxPopdownFrame", background=p["field"],
         bordercolor=p["field_border"], relief="flat")
    safe(style.configure, "TSpinbox", fieldbackground=p["field"],
         background=p["field"], foreground=p["text"], arrowcolor=p["text"],
         insertbackground=p["text"], selectbackground=p["select_bg"],
         selectforeground=p["select_fg"], bordercolor=p["field_border"],
         lightcolor=p["field"], darkcolor=p["field"], relief="flat", borderwidth=0)
    safe(style.map, "TSpinbox",
         fieldbackground=[("readonly", p["field"]), ("disabled", p["panel"])],
         background=[("readonly", p["field"]), ("disabled", p["panel"])],
         foreground=[("disabled", p["dim"])],
         arrowcolor=[("disabled", p["dim"])],
         bordercolor=[("focus", p["accent"])])

    # Indicators: keep them ttk (flat, card-background, accent marks).
    safe(style.configure, "TCheckbutton", background=p["panel"],
         foreground=p["text"], indicatorbackground=p["field"],
         indicatorforeground=p["accent"], focuscolor=p["panel"])
    safe(style.map, "TCheckbutton",
         indicatorbackground=[("pressed", p["field_border"]),
                              ("disabled", p["panel"])],
         foreground=[("disabled", p["dim"])])
    safe(style.configure, "TRadiobutton", background=p["panel"],
         foreground=p["text"], indicatorbackground=p["field"],
         indicatorforeground=p["accent"], focuscolor=p["panel"])
    safe(style.map, "TRadiobutton",
         indicatorbackground=[("pressed", p["field_border"]),
                              ("disabled", p["panel"])],
         foreground=[("disabled", p["dim"])])

    safe(style.configure, "Vertical.TScrollbar", background=p["field_border"],
         troughcolor=p["panel"], bordercolor=p["panel"], arrowcolor=p["text"],
         lightcolor=p["field_border"], darkcolor=p["field_border"])
    safe(style.map, "Vertical.TScrollbar",
         background=[("active", p["accent"])],
         arrowcolor=[("disabled", p["dim"])])


def load_fonts(root):
    """Build the clima-style font set once a root exists.

    Returns (fonts, emoji_family): fonts is a dict of tkfont.Font objects and
    emoji_family is an installed emoji font, or None to inherit the UI font.
    """
    from tkinter import font as tkfont

    families = set(tkfont.families(root))
    emoji_family = next(
        (f for f in EMOJI_FONT_CANDIDATES if f in families), None)
    fonts = {
        "title": tkfont.Font(root, family="Helvetica", size=FONT_SIZES["title"],
                             weight="bold"),
        "section": tkfont.Font(root, family="Helvetica",
                               size=FONT_SIZES["section"], weight="bold"),
        "label": tkfont.Font(root, family="Helvetica", size=FONT_SIZES["label"]),
        "small": tkfont.Font(root, family="Helvetica", size=FONT_SIZES["small"]),
    }
    return fonts, emoji_family


def flat_button(parent, text, command, *, accent=False, danger=False):
    """A flat tk.Button consistent with the clima card style.

    ttk buttons keep the native look the user finds dated, so actions are
    plain tk widgets with explicit colors. Remains keyboard-focusable
    (takefocus) and readable (high-contrast fg on the bright faces).
    """
    p = PALETTE
    if accent:
        bg, hover, fg = p["accent"], p["accent_hover"], p["on_accent"]
    elif danger:
        bg, hover, fg = p["danger"], p["danger_hover"], p["text"]
    else:
        bg, hover, fg = p["field"], p["field_border"], p["text"]
    return tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg,
        activebackground=hover, activeforeground=fg, disabledforeground=p["dim"],
        relief="flat", bd=0, highlightthickness=0, cursor="hand2",
        padx=14, pady=6, takefocus=True,
    )


class BookmarksGui:
    """Window construction and run orchestration. Never instantiated at import."""

    def __init__(self, root, registry):
        self.root = root
        self.registry = registry
        self.browsers = {b["name"]: b for b in registry["browsers"]}
        self.pairs = registry["pairs"]
        self.proc = None
        self._updating = False
        self._action_buttons = []

        root.title(f"IA-sync — Sincronización de marcadores "
                   f"(v{registry.get('script_version', '?')})")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        apply_theme(root, ttk.Style(root))
        self.fonts, self.emoji_font = load_fonts(root)

        self._build_ui()
        if self.pairs:
            first_a, first_b = self.pairs[0].split(",")
            # Comboboxes hold display labels; state maps them back to tokens.
            self.a_var.set(self._display(first_a))
            self.b_var.set(self._display(first_b))
            self._prev_a, self._prev_b = self._display(first_a), self._display(first_b)
        else:
            self._prev_a = self._prev_b = ""
        self._on_pair_change()
        # Capture the settled startup UI once; reset_to_defaults() re-applies
        # this exact snapshot, so the reset button can never drift or hardcode
        # a second copy of the defaults.
        self._defaults = self._default_state_snapshot()
        self._set_status(f"📄 Registro cargado: {len(self.browsers)} navegadores, "
                         f"{len(self.pairs)} pares.")
        self._size_window()
        root.after(100, self._poll)

    def _size_window(self):
        """Size the window to its content (no log pane, so no tall geometry).

        The layout is now a fixed vertical stack of cards plus a status bar,
        so the natural height IS the right height. We let Tk compute it, set a
        modest minimum so the fixed-width inputs and the action row never
        clip, and keep the window resizable — width still has slack for the
        long paths in the Opciones card.
        """
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, height)
        self.root.resizable(True, True)

    # ---- layout ------------------------------------------------------------

    def _card(self, parent, *, fill="x", grid=None, **pack):
        """Flat surface card (clima style): no border, contrast + margin only.

        Pass `grid=` as a dict to place it with grid instead of pack (the top
        row needs two cards sharing a 50/50 grid).
        """
        card = tk.Frame(parent, bg=PALETTE["panel"], padx=20, pady=15)
        if grid is not None:
            card.grid(**grid)
        else:
            card.pack(fill=fill, **pack)
        return card

    def _section_title(self, card, text, *, accent=False):
        """Section heading inside a card; accent reserved for emphasis."""
        return tk.Label(
            card, text=text, font=self.fonts["section"], anchor="w",
            fg=PALETTE["danger"] if accent else PALETTE["text"],
            bg=PALETTE["panel"],
        )

    def _text_label(self, parent, text, *, kind="label", fg=None, bg=None):
        return tk.Label(parent, text=text, font=self.fonts[kind],
                        fg=fg or PALETTE["text"], bg=bg or PALETTE["panel"],
                        anchor="w")

    def _build_ui(self):
        # Status bar packed first so it sits at the bottom.
        status_card = tk.Frame(self.root, bg=PALETTE["window"], padx=20, pady=6)
        status_card.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        tk.Label(status_card, textvariable=self.status_var, font=self.fonts["small"],
                 fg=PALETTE["muted"], bg=PALETTE["window"], anchor="w").pack(fill="x")

        # Header card: app title + registry metadata line.
        header = self._card(self.root, fill="x", padx=15, pady=(15, 5))
        tk.Label(header, text="🔖 IA-sync — Sincronización de marcadores",
                 font=self.fonts["title"], fg=PALETTE["text"],
                 bg=PALETTE["panel"]).pack(anchor="w")
        tk.Label(header,
                 text=f"🔄 {len(self.browsers)} navegadores · "
                      f"{len(self.pairs)} pares · CLI v{self.registry.get('script_version', '?')}",
                 font=self.fonts["small"], fg=PALETTE["dim"],
                 bg=PALETTE["panel"]).pack(anchor="w", pady=(4, 0))

        top = tk.Frame(self.root, bg=PALETTE["window"])
        top.pack(fill="x", padx=15, pady=(5, 0))
        # 50/50 split: equal weights + uniform group force both sections to
        # exactly half the row, regardless of each one's natural width.
        top.columnconfigure(0, weight=1, uniform="half")
        top.columnconfigure(1, weight=1, uniform="half")

        sync = self._card(top, grid=dict(row=0, column=0, sticky="nsew",
                                        padx=(0, 5)))
        self._section_title(sync, "🔀 Sincronización").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.a_var, self.b_var = tk.StringVar(), tk.StringVar()
        self.pair_combos = []
        for col, (label, var) in enumerate((("Navegador A", self.a_var),
                                            ("Navegador B", self.b_var))):
            self._text_label(sync, label).grid(
                row=1, column=col * 2, sticky="w", pady=2)
            # Combobox values are display labels; _state maps them to tokens.
            combo = ttk.Combobox(sync, textvariable=var, state="readonly", width=12,
                                 values=[info["display"]
                                         for info in self.browsers.values()])
            combo.grid(row=1, column=col * 2 + 1, padx=(2, 16), pady=2, sticky="w")
            combo.bind("<<ComboboxSelected>>",
                       lambda _e, which=col: self._on_browser_change(which))
            self.pair_combos.append(combo)
        self._text_label(sync, "Base:").grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.canon_box = tk.Frame(sync, bg=PALETTE["panel"])
        self.canon_box.grid(row=2, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.canonical_var = tk.StringVar()
        self._text_label(sync, "Aplicar a (--only):").grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self.only_var = tk.StringVar(value=ONLY_BOTH)
        self.only_combo = ttk.Combobox(sync, textvariable=self.only_var,
                                       state="readonly", width=12)
        self.only_combo.grid(row=3, column=1, sticky="w", pady=(6, 0))

        profiles = self._card(top, grid=dict(row=0, column=1, sticky="nsew",
                                            padx=(5, 0)))
        self._section_title(profiles, "👤 Perfiles").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.profile_rows = {}
        for row, (name, info) in enumerate(self.browsers.items(), start=1):
            self._text_label(profiles, info["display"] + ":").grid(
                row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(profiles, width=30)
            entry.grid(row=row, column=1, padx=(2, 4), pady=2, sticky="w")
            attach_placeholder(entry, profile_placeholder(info))
            browse = flat_button(profiles, "📂 Examinar…",
                                 lambda n=name: self._browse_profile(n))
            browse.grid(row=row, column=2, padx=(0, 2), pady=2, sticky="w")
            self.profile_rows[name] = {"entry": entry, "browse": browse}

        options = self._card(self.root, fill="x", padx=15, pady=(10, 5))
        self._section_title(options, "⚙ Opciones").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        opts = option_defaults()
        self.attach_var = tk.StringVar(value=opts["attach"])
        self.out_var = tk.StringVar(value=opts["out"])
        self.snapshots_var = tk.StringVar(value=opts["snapshots"])
        self._option_row(options, 1, "Carpeta adjunta (--attach-folder):",
                         self.attach_var, 22)
        self._option_row(options, 2, "Carpeta de salida (--out):",
                         self.out_var, 44, "Carpeta de salida")
        self._option_row(options, 3, "Carpeta de snapshots (--snapshots):",
                         self.snapshots_var, 44, "Carpeta de snapshots")
        self._text_label(options, "Ejemplos máximos (--max-examples):").grid(
            row=4, column=0, sticky="w", pady=2)
        self.max_examples_var = tk.StringVar(value=opts["max_examples"])
        ttk.Spinbox(options, from_=0, to=1000, textvariable=self.max_examples_var,
                    width=6).grid(row=4, column=1, sticky="w", padx=4, pady=2)
        self.no_dedupe_var = tk.BooleanVar(value=opts["no_dedupe"])
        self.force_var = tk.BooleanVar(value=opts["force"])
        self.write_both_var = tk.BooleanVar(value=opts.get("write_both", False))
        ttk.Checkbutton(options, text="Sin deduplicación (--no-dedupe)",
                        variable=self.no_dedupe_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(options, text="Forzar (--force) — peligroso",
                        variable=self.force_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=2)
        # write_both: only for chromium+chromium pairs; created hidden, shown in _on_pair_change
        self.write_both_cb = ttk.Checkbutton(
            options,
            text="Actualizar ambos navegadores Chromium en vivo (--write-both) — peligroso",
            variable=self.write_both_var,
        )
        # Initially hidden; _on_pair_change will show/hide based on pair

        # Actions card: last one in the column, so it carries the bottom of the
        # vertical rhythm now that the log pane is gone.
        actions = self._card(self.root, fill="x", padx=15, pady=(10, 10))
        self._section_title(actions, "⚡ Acciones").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        buttons_row = tk.Frame(actions, bg=PALETTE["panel"])
        buttons_row.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        # (label, handler, kind): accent = positive/read action, danger =
        # destructive. Neutral buttons stay on the card surface.
        specs = (
            ("▶️ Previsualizar (solo lectura)", self.on_preview, "accent"),
            ("✅ Aplicar…", self.on_apply, "accent"),
            ("♻️ Restaurar snapshot…", self.on_restore, "danger"),
            ("📂 Abrir carpeta de salida", self.on_open_out, "neutral"),
            ("🧾 Abrir report.txt", self.on_open_report, "neutral"),
            # Reset is non-destructive to disk (nothing is written), so it is a
            # plain action: no confirmation dialog, only a status-bar message.
            ("↺ Valores por defecto", self.reset_to_defaults, "neutral"),
        )
        for text, command, kind in specs:
            btn = flat_button(buttons_row, text, command,
                              accent=kind == "accent", danger=kind == "danger")
            btn.pack(side="left", padx=(0, 8))
            self._action_buttons.append(btn)
        # Close button: same safe-close path as WM_DELETE_WINDOW, neutral style,
        # deliberately separated from the action cluster and never disabled
        # while a run is active (it exists to end the run safely).
        self.close_button = flat_button(buttons_row, "✖ Cerrar", self.on_close)
        self.close_button.pack(side="right", padx=(24, 0))

    # ---- registry-driven widgets ------------------------------------------

    def _pair_members(self):
        # Combobox vars hold display labels (Firefox); tokens are mapped in _state.
        return [self.a_var.get(), self.b_var.get()]

    def _option_row(self, frame, row, label, var, width, browse_title=None):
        self._text_label(frame, label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", padx=4, pady=2)
        if browse_title:
            flat_button(frame, "📂 Examinar…",
                        lambda: self._pick_dir(var, browse_title)).grid(
                row=row, column=2, padx=4, pady=2, sticky="w")

    def _display(self, name):
        # Degraded registry (fetch failure): unknown names fall back to themselves.
        info = self.browsers.get(name)
        return info["display"] if info else name

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
            self._set_status("⚠️ Los dos navegadores deben ser distintos: "
                             "se revierte la selección.")
            return
        self._prev_a, self._prev_b = a, b
        self._on_pair_change()

    def _on_pair_change(self):
        for child in self.canon_box.winfo_children():
            child.destroy()
        if not self.browsers:
            # Degraded registry (fetch failure): no widgets to populate.
            self.only_combo.configure(values=[ONLY_BOTH])
            self.only_var.set(ONLY_BOTH)
            # Hide write_both checkbox (no pair selected)
            if hasattr(self, "write_both_cb"):
                self.write_both_cb.grid_remove()
            return
        members = self._pair_members()  # display labels
        # Base: only the two pair members (values are display labels too).
        if self.canonical_var.get() not in members:
            self.canonical_var.set(members[0])
        for member in members:
            ttk.Radiobutton(self.canon_box, text=member,
                            variable=self.canonical_var, value=member).pack(
                side="left", padx=(0, 8))
        # --only: Ambos / A / B, rebuilt per pair (display labels only).
        self.only_combo.configure(values=[ONLY_BOTH] + members)
        if self.only_var.get() not in (ONLY_BOTH,) + tuple(members):
            self.only_var.set(ONLY_BOTH)
        # Profile rows: enabled only for the pair's members. `members` holds
        # display labels, so the registry token (`name`) must be mapped before
        # comparing — otherwise every row reads as a non-member and all of
        # them (Examinar buttons included) get disabled.
        member_tokens = {token_for(self.browsers, m) for m in members}
        for name, row in self.profile_rows.items():
            active = name in member_tokens
            row["entry"].configure(state="normal" if active else "disabled")
            row["browse"].configure(state="normal" if active else "disabled")
        # write_both checkbox: only visible for chromium+chromium pairs
        if hasattr(self, "write_both_cb"):
            pair_tokens = [token_for(self.browsers, m) for m in members]
            is_chromium_pair = all(t != "firefox" for t in pair_tokens)
            if is_chromium_pair:
                # Place it after the force checkbox (row 7)
                self.write_both_cb.grid(row=7, column=0, columnspan=2, sticky="w", pady=2)
            else:
                self.write_both_cb.grid_remove()
        self._set_status(f"🔀 Par activo: {members[0]},{members[1]} — "
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
                "Archivo del perfil no encontrado",
                f"No se encontró «{store.name}» dentro de {chosen}.\n"
                "La ruta se toma igual; el CLI fallará si el perfil no es válido.")
        entry = self.profile_rows[name]["entry"]
        entry.delete(0, "end")
        entry.insert(0, chosen)
        entry.configure(foreground=REAL_FG)  # a picked path is a real value

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
            # Display labels are mapped back to CLI tokens (firefox, both…).
            "pair": ",".join(token_for(self.browsers, m)
                             for m in self._pair_members()),
            "canonical": token_for(self.browsers, self.canonical_var.get()),
            "only": only_token(self.browsers, self.only_var.get()),
            "attach_folder": self.attach_var.get(),
            "out": self.out_var.get(),
            "snapshots": self.snapshots_var.get(),
            "max_examples": max_examples,
            "no_dedupe": self.no_dedupe_var.get(),
            "force": self.force_var.get(),
            "write_both": self.write_both_var.get(),
            "apply": False,
            "profiles": {name: row["entry"].get()
                         for name, row in self.profile_rows.items()},
        }

    def _default_state_snapshot(self):
        """Capture every UI value the reset button must restore.

        Derived from the live widgets (not a second hardcoded copy of the
        defaults), so nothing can drift from what the app started with.
        """
        return {
            "a": self.a_var.get(),
            "b": self.b_var.get(),
            "canonical": self.canonical_var.get(),
            "only": self.only_var.get(),
            "options": {name: var.get() for name, var in self.option_vars().items()},
            # Profile entries reset to their placeholder (empty-path state).
            "profiles": {name: profile_placeholder(info)
                         for name, info in self.browsers.items()},
        }

    def option_vars(self):
        """Map the declarative option names to their live tk variables."""
        return {
            "attach": self.attach_var,
            "out": self.out_var,
            "snapshots": self.snapshots_var,
            "max_examples": self.max_examples_var,
            "no_dedupe": self.no_dedupe_var,
            "force": self.force_var,
            "write_both": self.write_both_var,
        }

    def reset_to_defaults(self):
        """Restore every UI value captured at startup (no disk side effects).

        Profile fields go back to their placeholder hint, so no profile
        override is emitted; pair/canonical/--only go back to the startup
        selection; option fields and checkboxes return to their CLI defaults.
        """
        d = self._defaults
        self.a_var.set(d["a"])
        self.b_var.set(d["b"])
        self._prev_a, self._prev_b = d["a"], d["b"]
        for name, var in self.option_vars().items():
            var.set(d["options"][name])
        self.canonical_var.set(d["canonical"])
        self.only_var.set(d["only"])
        for name, row in self.profile_rows.items():
            entry = row["entry"]
            entry.delete(0, "end")
            entry.insert(0, d["profiles"][name])
            entry.configure(foreground=PLACEHOLDER_FG)
        # Rebuild canonical/--only for the restored pair and re-sync row states.
        self._on_pair_change()
        self._set_status("↺ Valores restaurados a por defecto.")

    def _apply_summary(self, state):
        """Confirmation summary based on the CLI's documented write-back matrix.

        This dialog is the only pre-flight detail the user gets (there is no
        log pane to inspect afterwards), so it restates the pair, the canonical
        and the exact write-back per member, and points at out/report.txt.
        """
        # Pair order mirrors the argv so the order here never surprises.
        members = [m.strip() for m in state["pair"].split(",") if m.strip()]
        canonical, only = state["canonical"], state["only"]
        noncanon = [m for m in members if m != canonical][0]
        # Base auto-write logic: firefox pair -> all chromium members;
        # chromium+chromium -> only canonical UNLESS write_both=True.
        auto = ([m for m in members if m != "firefox"] if "firefox" in members
                else [canonical])
        write_both = state.get("write_both", False)
        if write_both and "firefox" not in members:
            # Both are chromium-family: add the non-canonical member too.
            noncanon_member = [m for m in members if m != canonical][0]
            auto.append(noncanon_member)
        auto = [m for m in auto if only in ("both", m)]
        out = Path(state["out"] or DEFAULT_OUT).expanduser()

        # Build explanation lines
        lines = [
            "ESTRUCTURA BASE:",
            f"  La jerarquía de carpetas se toma de {self._display(canonical)} (Base).",
            f"  Los marcadores de {self._display(noncanon)} se fusionan dentro de esa estructura.",
            "",
            "QUÉ PASA CON CADA NAVEGADOR:",
        ]

        # Firefox is special: never auto-written
        if "firefox" in members:
            if canonical == "firefox":
                lines.append(
                    f"  Firefox (Base): se genera firefox-restore.json con la estructura "
                    f"fusionada. NO se aplica en vivo — debés restaurarlo a mano."
                )
            else:
                lines.append(
                    f"  Firefox: se genera firefox-restore.json con la estructura fusionada "
                    f"(Base = {self._display(canonical)}). NO se aplica en vivo — "
                    f"debés restaurarlo a mano."
                )

        # Chromium-family members
        for m in members:
            if m == "firefox":
                continue
            if m in auto:
                if m == canonical:
                    lines.append(
                        f"  {self._display(m)} (Base): se ACTUALIZA EN VIVO con la estructura "
                        f"fusionada (sobrescribe su archivo Bookmarks)."
                    )
                else:
                    # Non-canonical chromium member being auto-written
                    if write_both and "firefox" not in members:
                        lines.append(
                            f"  {self._display(m)}: se ACTUALIZA EN VIVO con la estructura fusionada "
                            f"(Base = {self._display(canonical)}). Sobrescribe su archivo Bookmarks "
                            f"— activado por --write-both."
                        )
                    else:
                        lines.append(
                            f"  {self._display(m)}: se ACTUALIZA EN VIVO con la estructura fusionada "
                            f"(Base = {self._display(canonical)}). Sobrescribe su archivo Bookmarks."
                        )
            elif only in ("both", m):
                lines.append(
                    f"  {self._display(m)}: NO se actualiza automáticamente — "
                    f"copiá manualmente out/{m}-bookmarks.json a su almacén."
                )
            else:
                lines.append(
                    f"  {self._display(m)}: excluido por --only={only}."
                )

        lines += [
            "",
            "Se tomará un snapshot antes de escribir.",
            f"El detalle completo queda en {out / 'report.txt'}.",
            "¿Continuar?",
        ]

        if "firefox" in members and only in ("both", "firefox"):
            # Firefox NUNCA se escribe en vivo — esta línea DEBE leerse.
            # Primera línea = header (color emoji), resto = cuerpo limpio.
            lines = [
                "⚠️  FIREFOX: RESTORE MANUAL OBLIGATORIO  ⚠️",
                "",
                f"Archivo: firefox-restore.json  (carpeta: {out.name})",
                "",
                "Pasos:",
                "  Biblioteca (Ctrl+Shift+O) → ⚙ Importar y respaldar",
                "  → Importar datos → Desde archivo",
                "  → seleccionar el JSON de arriba.",
                "",
            ] + lines

        return "\n".join(lines)

    def _report_input_error(self, exc, dialog):
        msg = f"⚠️ Entrada inválida: {exc}"
        self._set_status(msg)
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
            self._set_status("↩️ Restauración cancelada.")
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
        # Detach fully: the opener's own stderr (e.g. Gtk editor plugin
        # warnings) belongs to the opener, not to this app, and must not
        # land in the terminal that launched the GUI.
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # ---- process orchestration --------------------------------------------

    def _confirm(self, title: str, body: str) -> bool:
        """Custom confirmation dialog with monospace body + color emoji header.

        Returns True for Yes, False for No. Mirrors messagebox.askyesno signature.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.configure(bg=PALETTE["panel"])

        # Split body: first line (header with emojis) + rest (monospace box)
        lines = body.split("\n")
        header = lines[0] if lines else ""
        rest = "\n".join(lines[1:]) if len(lines) > 1 else ""

        # Header label with color emoji font (if available)
        if header:
            emoji_font = self.fonts.get("emoji")
            if emoji_font:
                hdr = tk.Label(
                    dlg, text=header, bg=PALETTE["panel"], fg=PALETTE["accent"],
                    font=(emoji_font, FONT_SIZES["emoji"], "bold"),
                    anchor="center", justify="center",
                )
            else:
                hdr = tk.Label(
                    dlg, text=header, bg=PALETTE["panel"], fg=PALETTE["accent"],
                    font=("monospace", FONT_SIZES["label"], "bold"),
                    anchor="center", justify="center",
                )
            hdr.pack(padx=16, pady=(16, 4))

        # Body text widget with monospace font (box-drawing chars align)
        if rest:
            txt = tk.Text(
                dlg, wrap="word", height=20, width=90,
                bg=PALETTE["panel"], fg=PALETTE["text"],
                font=("monospace", FONT_SIZES["label"]),
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground=PALETTE["field_border"],
                insertbackground=PALETTE["accent"],
            )
            txt.insert("1.0", rest.lstrip("\n"))
            txt.configure(state="disabled")
            txt.pack(padx=16, pady=(0, 16))

        # Buttons
        btn_frame = tk.Frame(dlg, bg=PALETTE["panel"])
        btn_frame.pack(pady=(0, 16))

        result = {"value": False}

        def on_yes():
            result["value"] = True
            dlg.destroy()

        def on_no():
            result["value"] = False
            dlg.destroy()

        yes_btn = tk.Button(
            btn_frame, text="✅  Sí", command=on_yes,
            bg=PALETTE["accent"], fg=PALETTE["on_accent"],
            activebackground=PALETTE["accent_hover"],
            activeforeground=PALETTE["on_accent"],
            relief="flat", bd=0, padx=24, pady=8,
            font=(self.fonts.get("label", "Helvetica"), FONT_SIZES["label"], "bold"),
            cursor="hand2",
        )
        no_btn = tk.Button(
            btn_frame, text="✖  No", command=on_no,
            bg=PALETTE["panel"], fg=PALETTE["text"],
            activebackground=PALETTE["field"], activeforeground=PALETTE["text"],
            relief="flat", bd=0, padx=24, pady=8,
            font=(self.fonts.get("label", "Helvetica"), FONT_SIZES["label"]),
            cursor="hand2",
        )
        yes_btn.pack(side="left", padx=8)
        no_btn.pack(side="left", padx=8)

        # Keyboard shortcuts
        dlg.bind("<Return>", lambda e: on_yes())
        dlg.bind("<Escape>", lambda e: on_no())
        yes_btn.focus_set()

        # Position: centered horizontally, ~35% from top (avoids clashing with Acciones)
        dlg.update_idletasks()
        w, h = dlg.winfo_width(), dlg.winfo_height()
        cx = self.root.winfo_rootx() + (self.root.winfo_width() // 2)
        cy = self.root.winfo_rooty() + int(self.root.winfo_height() * 0.35)
        dlg.geometry(f"+{cx - w // 2}+{cy - h // 2}")
        self.root.wait_window(dlg)
        return result["value"]

    def _start(self, cmd, confirm_summary=None):
        if self.proc is not None and self.proc.poll() is None:
            self._set_status("⏳ Ya hay un proceso en curso; esperá a que termine.")
            return
        if confirm_summary and not self._confirm("Confirmar operación", confirm_summary):
            self._set_status("Operación cancelada por el usuario.")
            return
        try:
            # Non-blocking: output is discarded (the CLI writes out/report.txt),
            # but the child must not stall on a full pipe, so stdout+stderr are
            # consumed by a daemon thread rather than left unread.
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, cwd=SCRIPT.parent)
        except OSError as exc:
            self.proc = None
            self._set_status(f"❌ No se pudo lanzar el proceso: {exc}")
            messagebox.showerror(
                "No se pudo lanzar el proceso",
                f"No se pudo ejecutar el CLI:\n{exc}")
            return
        self._drainer = threading.Thread(target=self._drain_output,
                                          args=(self.proc,), daemon=True)
        self._drainer.start()
        self._set_running(True)
        self._set_status(f"⏳ Proceso en curso… {self._compact_cmd(cmd)}")

    @staticmethod
    def _drain_output(proc):
        """Consume the child's pipe so it never blocks; output is not shown."""
        try:
            for _line in iter(proc.stdout.readline, ""):
                pass
        except Exception:  # noqa: BLE001 - drain thread must never raise
            pass

    @staticmethod
    def _compact_cmd(cmd):
        """Short, layout-safe form of the argv for the status bar."""
        # Drop the interpreter + script path (the same for every run) and
        # collapse the absolute paths so the line stays readable in one row.
        args = cmd[2:]
        home = str(Path.home())
        args = [a.replace(home, "~") for a in args]
        text = " ".join(args)
        return text if len(text) <= 90 else text[:87] + "…"

    def _poll(self):
        try:
            if self.proc is not None and self.proc.poll() is not None:
                code = self.proc.returncode
                self.proc = None
                self._set_running(False)
                if code == 0:
                    self._set_status("✅ Proceso finalizado (código 0).")
                else:
                    self._set_status(f"❌ Proceso finalizado con código {code}.")
                    messagebox.showerror(
                        "El proceso terminó con errores",
                        f"El proceso terminó con código {code}.\n"
                        f"Revisá el reporte en {self._report_path()}.")
        except Exception as exc:  # noqa: BLE001 - a failed tick must not kill the timer
            self._set_status(f"❌ Error interno en el seguimiento del proceso: {exc}")
        finally:
            self.root.after(100, self._poll)

    def _report_path(self):
        return Path(self.out_var.get() or DEFAULT_OUT).expanduser() / "report.txt"

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
