# IA-sync — Bookmark Convergence Tool

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Firefox](https://img.shields.io/badge/Firefox-FF7139FF?style=flat-square&logo=firefox&logoColor=white)
![Brave](https://img.shields.io/badge/Brave-FA5A09?style=flat-square&logo=brave&logoColor=white)
![Chromium](https://img.shields.io/badge/Chromium-4285F4?style=flat-square&logo=chromium&logoColor=white)
![Linux](https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=white)
![License](https://img.shields.io/badge/License-BUSL%201.1-8A2BE2?style=flat-square)
![Version](https://img.shields.io/badge/Version-2.0.0-007ec6?style=flat-square)

Converges the bookmarks of exactly two browsers (Firefox, Brave and Chromium) into a single deduplicated tree, with a selectable **Base** layout and full snapshot/rollback safety.

> Spanish version: [README.md](README.md)

## Table of Contents

- [Notice](#notice)
- [Why](#why)
- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Graphical Interface (GUI)](#graphical-interface-gui)
- [How It Works](#how-it-works)
- [Output Artifacts](#output-artifacts)
- [Snapshots and Rollback](#snapshots-and-rollback)
- [Safety Features](#safety-features)
- [Project Structure](#project-structure)
- [Verified Format Details](#verified-format-details)
- [License](#license)

---

## Notice

IA-sync is a **non-commercial**, personal project: it is provided at no cost, without dedicated support, and without warranties for professional use. It is licensed under **BUSL 1.1** (`Additional Use Grant: None`): copying, modifying and redistributing with **non-production use** are allowed; any commercial use requires a license from the project owner. The `Change Date` is **October 21, 2030**, on which date the work becomes governed by the **GPL v3.0 or later**. See [License](#license).

The **configuration paths depend on the system** the tool runs on. The default values assume a Linux installation (`~/.mozilla/firefox`, `~/.config/BraveSoftware/Brave-Browser`, `~/.config/chromium`) and may not match your installation, your user, or your desktop distribution. Before running `--apply`, set the relevant paths explicitly with `--ff-profile`, `--brave-profile`, `--chromium-profile`, `--out` and `--snapshots` (or the equivalent GUI fields), and check the resolved paths in the report (`out/report.txt`).

> Configuring these paths is the user's responsibility: the tool does not guess locations outside the assumptions of its defaults and aborts when it cannot resolve a profile.

## Why

Browsers store bookmarks in incompatible formats and diverge over time. Firefox uses SQLite (`places.sqlite` + WAL); Brave and Chromium use Chromium JSON. The tool merges the two chosen browsers into one tree, removes duplicates by normalized URL, preserves each browser's exclusive bookmarks, and writes native-format artifacts for each one.

## Features

- **Pair-based sync**: exactly two browsers per run, chosen with `--pair`: `firefox,brave`, `firefox,chromium` or `brave,chromium` (order-insensitive).
- **Explicit, mandatory Base**: `--canonical` (no default) picks whose folder layout is the skeleton (**Base**), and must be one of the pair's members. There is no implicit Base browser.
- **Union merge**: bookmarks from both pair members are preserved; only true duplicates are removed, and both resulting artifacts contain exactly the same bookmarks as the merged tree.
- **Smart dedupe**: within the Base skeleton the shallowest occurrence of each URL wins (a bar shortcut beats a copy buried three folders deep) and ties are resolved by the traversal order of the Base tree. Between the Base and the other browser the Base always wins: the other browser's copy of a URL already present in the Base is dropped, regardless of its depth.
- **Exclusives preserved under `Desde <other browser>`**: the exact path depends on whether the Base is Chromium-family or Firefox (see *Base modes and anchoring*).
- **Deterministic**: same inputs → same outputs. Running twice produces identical artifacts.
- **Snapshot on every `--apply`**: automatic backup of both pair members before any write.
- **Safety guards**: refuses to `--apply` while a pair member is running (Chromium rewrites its file on close). `--force` overrides.
- **`--restore`**: one-command rollback from any snapshot; compatible with v1.x snapshots.
- **`--only`**: restricts `--apply` to one pair member, leaving the other untouched.
- **`--write-both`** *(Chromium+Chromium pairs)*: writes LIVE to **both** browsers (default: only the Base). Dangerous if browsers are open.
- **Configurable profiles**: `--ff-profile`, `--brave-profile` and `--chromium-profile`; only the pair members' profiles are resolved.
- **Graphical Interface (GUI)**: `bookmarks_gui.py` — tkinter, stdlib only, dark theme, emojis, explanatory confirmation dialog, checkbox "Update both Chromium browsers live" for Chromium+Chromium pairs.
- **Zero dependencies**: Python 3 standard library only. No pip installs.

## Requirements

- Python 3.10+
- Two browsers installed among Firefox, Brave and Chromium (profiles auto-detected from standard paths)
- No external packages

## Quick Start

```bash
# 1. Dry-run: see what would change (nothing is written to the browsers)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# 2. Review the report and artifacts in <script dir>/out
cat <script dir>/out/report.txt

# 3. Apply when satisfied (close the pair's browsers first!)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply
```

## Usage

### Basic invocation

```bash
python3 bookmarks-merge.py --pair A,B --canonical X [OPTIONS]
```

`--pair` and `--canonical` are both required: without them the script exits with an error (there is no default Base). Running without `--apply` is always safe: it only reads, writes previews to the output directory (by default `<script dir>/out`), and prints a report.

### Valid pairs

| `--pair` (order-insensitive) | Accepted `--canonical` |
|---|---|
| `firefox,brave` | `firefox`, `brave` |
| `firefox,chromium` | `firefox`, `chromium` |
| `brave,chromium` | `brave`, `chromium` |

### All options

| Option | Default | Description |
|---|---|---|
| `--pair A,B` | — (required) | Pair of browsers to sync, comma-separated and order-insensitive. See valid pairs. |
| `--canonical NAME` | — (required) | Browser whose folder tree is the skeleton (**Base**); must be a member of `--pair`. No default. |
| `--apply` | off | Write changes to browsers. Without it: dry-run only. |
| `--only NAME` | `both` | Restrict `--apply` to one pair member: `both` or a member's name. |
| `--write-both` | off | **Chromium+Chromium pairs only**: write LIVE to BOTH browsers (default: only the Base; requires `--apply`). Dangerous: two live stores can corrupt if browsers are open. |
| `--force` | off | Override the open-browser guard. Dangerous. |
| `--restore SNAPSHOT_DIR` | — | Restore the pair's files from a snapshot and exit. Also requires `--pair` and `--canonical`. |
| `--ff-profile PATH` | auto | Explicit Firefox profile path (default: the active profile from `profiles.ini`). |
| `--brave-profile PATH` | auto | Explicit Brave profile path (default: `~/.config/BraveSoftware/Brave-Browser/Default`). |
| `--chromium-profile PATH` | auto | Explicit Chromium profile path (default: `~/.config/chromium/Default`). |
| `--attach-folder NAME` | `Favoritos` | Folder in `bookmark_bar` where `Desde <other>` is hung. Only used when the Base is Chromium-family; ignored with `--canonical firefox`. |
| `--out DIR` / `--out-dir DIR` | `<script dir>/out` | Output directory for artifacts and reports. |
| `--snapshots DIR` | `<script dir>/snapshots` | Directory for automatic snapshots. |
| `--max-examples N` | `15` | How many duplicate examples to show in the report. |
| `--no-dedupe` | off | Disable deduplication (debug only). |
| `-h` / `--help` | — | Show help and exit. |

By default, both directories live inside the script's folder and are absolute paths; the `out/` and `snapshots/` shorthands used in the examples below mean those default directories.

Only the pair members' profiles are resolved: a `brave,chromium` run never touches Firefox configuration.

### Examples

```bash
# Firefox↔Brave with Brave's structure as the Base
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# Firefox↔Chromium with Firefox as Base (--attach-folder is ignored in this mode)
python3 bookmarks-merge.py --pair firefox,chromium --canonical firefox

# Brave↔Chromium: on --apply only the Base's store is written
python3 bookmarks-merge.py --pair brave,chromium --canonical chromium

# Brave↔Chromium: write to BOTH live stores (requires --apply, close browsers first!)
python3 bookmarks-merge.py --pair brave,chromium --canonical brave --write-both --apply

# Apply to a single pair member (the other one is left untouched)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply --only brave

# Restore a snapshot of the firefox,brave pair
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore <script dir>/snapshots/20261019-185514

# Explicit profile paths (only the pair's members are resolved;
# replace the Firefox path with your own profile)
python3 bookmarks-merge.py --pair firefox,brave --canonical firefox \
  --ff-profile ~/.mozilla/firefox/14yqiklo.default-release \
  --brave-profile ~/.config/BraveSoftware/Brave-Browser/Default
```

## Graphical Interface (GUI)

`bookmarks_gui.py` exposes the full CLI in a tkinter window (stdlib only). It adds no duplicated logic: it builds the same command and runs it via subprocess, and enumerates browsers, pairs, and defaults from `--list-browsers --json` (the CLI's registry is the single source of truth). The interface uses a dark grey theme (`#1a1a1a` background, flat borderless `#232323` cards) with emoji icons in titles and buttons.

```bash
python3 bookmarks_gui.py              # open the window
python3 bookmarks_gui.py --self-test  # test the pure logic, no display needed
```

- **🔀 Sync**: A/B pair (they cannot be the same), Base restricted to the two members, and `--only`.
- **👤 Profiles**: one row per registry browser; only the pair's members are enabled (the rest are disabled), and the field shows the default profile in gray until edited (if it matches the default, no flag is sent). "📂 Browse…" warns if `places.sqlite` (Firefox) or `Bookmarks` (Chromium) is missing, but keeps the chosen path.
- **⚙ Options**: `--attach-folder`, `--out`, `--snapshots`, `--max-examples`, `--no-dedupe`, `--force`, and **`--write-both`** (checkbox "Update both Chromium browsers live — dangerous", only visible for Chromium+Chromium pairs, off by default).
- **⚡ Actions**: preview (read-only), `--apply` and `--restore` with an **explanatory confirmation dialog** showing:
  - **BASE STRUCTURE**: which browser provides the folder hierarchy
  - **WHAT HAPPENS TO EACH BROWSER**: explicit per-browser action (auto-write with Base context, Firefox manual restore, manual copy, or excluded by `--only`)
  - Prominent Firefox warning (manual restore required)
  - Direct access to `out/` and `report.txt`
  - "↺ Valores por defecto" to return every field and selection to its startup state
  - At the end of the row, "✖ Cerrar" exits through the same safe path as the window button
- **Checkbox `--write-both`**: only appears when the selected pair is Chromium+Chromium (Brave+Chromium). Off by default. When checked, the confirmation dialog notes "enabled by --write-both" for the non-Base member.

The GUI shows no log panel: the CLI writes the full detail to `out/report.txt` (reachable via "🧾 Abrir report.txt"). Progress is tracked in the bottom status bar — "⏳ Proceso en curso…" with the compacted command, then "✅ Proceso finalizado (código N)." on exit — and a non-zero exit opens a dialog with the code and the report path. Action buttons are disabled while a process runs (the close button stays enabled); closing the window with an active run asks for confirmation and terminates the subprocess.

## How It Works

### Merge model

1. **Read** both pair members: Firefox from a copy of `places.sqlite` (WAL replayed on the copy, never the original); Brave and Chromium from their `Bookmarks` JSON.
2. **Build skeleton** from the Base browser's folder tree.
3. **Dedupe** within the skeleton: for each normalized URL, keep the occurrence closest to a root (shallowest depth wins; ties go to DFS order, following the traversal order of the Base tree).
4. **Place exclusive bookmarks** from the other member under `Desde <other>`, preserving their original subfolder hierarchy.
5. **Emit one artifact per pair member** in native format:
   - `out/firefox-restore.json` — only when `firefox` is in the pair. Firefox legacy JSON (manual restore via Library → Import and Backup → Restore → Choose File…)
   - `out/<browser>-bookmarks.json` — for each Chromium-family member: `out/brave-bookmarks.json` and/or `out/chromium-bookmarks.json`. Chromium JSON (direct write to the live store per *What `--apply` writes*)
   - `out/report.txt` — full merge report (also printed to stdout)

Each artifact is the merged tree rendered in its browser's native format; with Firefox as the Base, the Chromium-family artifact renders the menu root inside `other`, under a `Menú de marcadores` folder.

### URL normalization

Two URLs are considered duplicates when they normalize to the same key:

- Scheme and host lowercased
- Leading `www.` stripped from host
- Default port dropped (`:80` for http, `:443` for https)
- Query string and fragment **preserved**
- Trailing slash stripped from path, only when there is no query and no fragment

Examples: `https://www.example.com/path/` and `https://example.com/path` are duplicates.

Schemes other than http(s) (`file:`, `about:`, `javascript:`, `chrome:`, …) are compared verbatim and never normalized.

### Root mapping

| Firefox root | Chromium root (Brave and Chromium) | Notes |
|---|---|---|
| `toolbar` (Bookmarks bar) | `bookmark_bar` | Bookmarks bar |
| `unfiled` (Other bookmarks) | `other` | Other bookmarks |
| `mobile` (Mobile bookmarks) | `synced` | Mobile bookmarks |
| `menu` (Bookmarks menu) | `other` (projected) | The Chromium family has no menu root: with Firefox as the Base, its bookmarks are projected into `other` under a `Menú de marcadores` folder, so none of them is lost |
| `tags` (Tags) | — | Skipped; tag entries are artifacts, not bookmarks |

### Base modes and anchoring

Both modes produce the **same union** of bookmarks; only the folder organization differs. The report shows the exact anchor in the `Destino` line.

**Chromium-family Base** (`--canonical brave` or `--canonical chromium`): the Base's tree is the skeleton. The other browser's exclusive bookmarks hang under `bookmark_bar / <attach-folder> / Desde <other> / <root label> / <original path>`. If `<attach-folder>` is missing from the bar, the bar's children are wrapped in a `Bookmarks bar` folder and the anchor hangs inside it.

**`--canonical firefox`**: Firefox's tree is the skeleton. The other browser's exclusive bookmarks hang in a `Desde <other>` folder at the top of each root, preserving the original path underneath. `--attach-folder` is accepted but never used. The Chromium format only has three roots (`bookmark_bar`, `other` and `synced`), so the other browser's artifact cannot host the `Menú de marcadores` root as-is: its bookmarks are projected into `other`, under a `Menú de marcadores` folder (the label is set by the tool, not by the browser's language). With that projection, both resulting artifacts contain exactly the same bookmarks as the merged tree: none is discarded.

## Output Artifacts

After a dry-run or `--apply`, the output directory (by default `<script dir>/out`) contains one artifact per pair member plus the report:

| File | Emitted when | Format and purpose |
|---|---|---|
| `firefox-restore.json` | `firefox` is in the pair | Firefox legacy JSON; manual restore via the Library |
| `brave-bookmarks.json` | `brave` is in the pair | Chromium JSON for Brave's `Bookmarks` file |
| `chromium-bookmarks.json` | `chromium` is in the pair | Chromium JSON for Chromium's `Bookmarks` file |
| `report.txt` | always | Full merge report (also printed to stdout) |

### What `--apply` writes

| Pair | Automatic write to the live store | Always manual |
|---|---|---|
| `firefox,brave` | Brave | Firefox restore (`firefox-restore.json`) |
| `firefox,chromium` | Chromium | Firefox restore (`firefox-restore.json`) |
| `brave,chromium` | Only the Base's store | Manual copy of the non-Base member's artifact (the report prints source and destination) |

**With `--write-both` (Chromium+Chromium pairs):** **both** live stores are written (the Base and the non-Base). Requires `--apply` and **both** browsers closed (or `--force`).

Two live stores are never written in a single run without `--write-both`. `--only` restricts the above to one pair member.

### Applying to Firefox (manual)

When `firefox` is in the pair, its store is never touched automatically: Firefox has no supported CLI import path. The restore must be done manually:

1. **Close Firefox.**
2. Open Firefox → `Ctrl+Shift+O` (Library).
3. Import and Backup → Restore → Choose File…
4. Select `out/firefox-restore.json`.
5. Accept the warning that current bookmarks will be replaced.

> **Important**: Do NOT delete bookmarks manually before restoring. Firefox's restore replaces the entire tree.

### Applying to the Chromium-family store (automatic)

When the Chromium-family member is the automatic target (the only Chromium member of a `firefox,*` pair, or the Base in `brave,chromium`), `--apply` writes directly to its `Bookmarks` file, preserving the file's existing permissions (typically `0600`). That browser must be closed first — Chromium rewrites its file on exit and would overwrite the change.

In `brave,chromium` pairs the non-Base member is not written: the report prints the manual copy instruction (`out/<browser>-bookmarks.json` over the profile's `Bookmarks` file).

**With `--write-both`**: both Chromium stores are overwritten. The report shows both "file replaced" lines.

## Snapshots and Rollback

Every `--apply` automatically creates a snapshot in `<script dir>/snapshots/<timestamp>/` with one directory per pair member:

- `firefox/places.sqlite` (plus `-wal`/`-shm` if present)
- `brave/Bookmarks` and `Bookmarks.bak` (if present); for `chromium`, the same contents under `chromium/`
- `manifest.json` with SHA-256 hashes of every file

### Restore from snapshot

`--restore` is pair-aware: it restores only the chosen `--pair` members present in the snapshot; missing members are skipped with a warning. v1.x snapshots (only `firefox/` + `brave/`) restore fine with `--pair firefox,brave`. Like `--apply`, it requires `--pair` and `--canonical`.

```bash
# List available snapshots
ls <script dir>/snapshots/

# Restore a snapshot of the firefox,brave pair
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore <script dir>/snapshots/20261019-185514
```

Browsers must be closed before restoring (or use `--force`).

## Safety Features

| Guard | Behavior |
|---|---|
| Open-browser check | `--apply` refuses if a pair member (Firefox, Brave or Chromium) is running. Other browsers only trigger a read-only analysis warning. `--force` overrides the guard. |
| Pre-apply snapshot | Before writing anything, every `--apply` snapshots both pair members. |
| One live store per run | In `brave,chromium` pairs only the Base's store is written; the rest is manual. With `--write-both` both are written (requires both closed). |
| Permission preservation | The Chromium-family `Bookmarks` file keeps its existing permissions (fallback: `0600`). |
| SQLite copy-on-read | `places.sqlite` is never opened in place; a copy is made and the WAL is replayed on the copy. |
| Deterministic GUIDs | Derived with `uuid5` from a stable namespace, so re-running produces identical artifacts. |
| Checksum omission | Chromium's `BookmarkCodec::Decode()` never reads `checksum`; the field is omitted and recomputed on the browser's next save. |
| Verification gate | The check runs on every invocation, dry-runs included. Every artifact is compared against the merged tree: it must contain exactly the same set of normalized URLs (none missing, none extra) and zero internal duplicates. If it fails, nothing is written to the browsers' stores and the process exits with code 2; the preview artifacts are still left in the output directory. |

## Project Structure

```
IA-sync/
├── bookmarks-merge.py          # Main script (v2.0.0, stdlib-only)
├── bookmarks_gui.py            # GUI (tkinter, stdlib-only)
├── README.md                   # Spanish version
├── README-EN.md                # This file (English)
├── LICENSE                     # BUSL 1.1 license
├── sources.md                  # Reference links (local material, not versioned)
├── out/                        # Generated artifacts (per the run's pair)
│   ├── brave-bookmarks.json    #   Chromium JSON for Brave (if brave is in the pair)
│   ├── chromium-bookmarks.json #   Chromium JSON for Chromium (if chromium is in the pair)
│   ├── firefox-restore.json    #   Firefox legacy JSON (if firefox is in the pair)
│   └── report.txt              #   Merge report
├── snapshots/                  # Automatic snapshots (created on the first --apply)
│   └── <timestamp>/
│       ├── firefox/            #   places.sqlite + WAL/SHM
│       ├── brave/              #   Bookmarks + Bookmarks.bak
│       ├── chromium/           #   Bookmarks + Bookmarks.bak (if chromium is in the pair)
│       └── manifest.json       #   SHA-256 hashes
└── odd/                        # Development documents (local material, not versioned)
```

`sources.md` and `odd/` are excluded from version control (they are local working material).

## Verified Format Details

These facts were verified against upstream source code, not assumed:

| Fact | Source |
|---|---|
| Chromium `Decode()` never reads or validates `checksum` | `components/bookmarks/browser/bookmark_codec.cc` |
| Permanent folder UUIDs are hard-coded; not read from file | `bookmark_codec.cc` (~line 290) |
| Invalid/duplicate GUID → auto-reassigned as random v4 | `bookmark_codec.cc` |
| Firefox `importFromFile(path, {replace: true})` replaces the whole tree | `toolkit/components/places/BookmarkJSONUtils.sys.mjs` |
| Firefox wraps JSON in `[...]` before parsing (`JSON.parse("[" + blob + "]")`) | `PlacesUtils.unwrapNodes()` |
| Firefox GUID format: `/^[a-zA-Z0-9_-]{12}$/` | `toolkit/components/places/PlacesUtils.sys.mjs` |
| Chromium `date_added`: microseconds since 1601-01-01 | Chromium source |
| Firefox `dateAdded`: microseconds since 1970-01-01 | Firefox source |

## License

[BUSL 1.1](LICENSE) — Business Source License 1.1. Copyright (c) 2026 gottigjavier.

| Parameter | Value |
|---|---|
| Licensor | gottigjavier |
| Licensed Work | IA-sync version 2.0.0 and later, (c) 2026 gottigjavier |
| Additional Use Grant | `None` — no production use; commercial use requires a license from the project owner |
| Change Date | 2030-10-21 (or the fourth anniversary of the first public distribution, whichever comes first) |
| Change License | GPL v3.0 or later |

Until the `Change Date`, BUSL 1.1 **is not an OSI-approved open source license**: the code is available, but production use is not permitted. From that date on, the work is governed by the GPL v3.0 or later.