# IA-sync — Bookmark Convergence Tool

Converges the bookmarks of exactly two browsers (Firefox, Brave and Chromium) into a single deduplicated tree, with a selectable canonical layout and full snapshot/rollback safety.

## Why

Browsers store bookmarks in incompatible formats and diverge over time. Firefox uses SQLite (`places.sqlite` + WAL); Brave and Chromium use Chromium JSON. The tool merges the two chosen browsers into one tree, removes duplicates by normalized URL, preserves each browser's exclusive bookmarks, and writes native-format artifacts for each one.

## Features

- **Pair-based sync**: exactly two browsers per run, chosen with `--pair`: `firefox,brave`, `firefox,chromium` or `brave,chromium` (order-insensitive).
- **Explicit, mandatory canonical**: `--canonical` (no default) picks whose folder layout is the skeleton, and must be one of the pair's members. There is no implicit canonical browser.
- **Union merge**: bookmarks from both pair members are preserved; only true duplicates are removed.
- **Smart dedupe**: keeps the shallowest occurrence of each URL (a bookmark-bar shortcut beats a copy buried three folders deep). Ties break by DFS order; the canonical root is scanned first.
- **Exclusives preserved under `Desde <other browser>`**: the exact path depends on whether the canonical is Chromium-family or Firefox (see *Canonical modes and anchoring*).
- **Deterministic**: same inputs → same outputs. Running twice produces identical artifacts.
- **Snapshot on every `--apply`**: automatic backup of both pair members before any write.
- **Safety guards**: refuses to `--apply` while a pair member is running (Chromium rewrites its file on close). `--force` overrides.
- **`--restore`**: one-command rollback from any snapshot; compatible with v1.x snapshots.
- **`--only`**: restricts `--apply` to one pair member, leaving the other untouched.
- **Configurable profiles**: `--ff-profile`, `--brave-profile` and `--chromium-profile`; only the pair members' profiles are resolved.
- **Zero dependencies**: Python 3 standard library only. No pip installs.

## Requirements

- Python 3.10+
- Two browsers installed among Firefox, Brave and Chromium (profiles auto-detected from standard paths)
- No external packages

## Quick Start

```bash
# 1. Dry-run: see what would change (nothing is written to the browsers)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# 2. Review the report and artifacts in out/
cat out/report.txt

# 3. Apply when satisfied (close the pair's browsers first!)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply
```

## Usage

### Basic invocation

```bash
python3 bookmarks-merge.py --pair A,B --canonical X [OPTIONS]
```

`--pair` and `--canonical` are both required: without them the script exits with an error (there is no default canonical). Running without `--apply` is always safe: it only reads, writes previews to `out/`, and prints a report.

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
| `--canonical NAME` | — (required) | Browser whose folder tree is the skeleton; must be a member of `--pair`. No default. |
| `--apply` | off | Write changes to browsers. Without it: dry-run only. |
| `--only NAME` | `both` | Restrict `--apply` to one pair member: `both` or a member's name. |
| `--force` | off | Override the open-browser guard. Dangerous. |
| `--restore SNAPSHOT_DIR` | — | Restore the pair's files from a snapshot and exit. Also requires `--pair` and `--canonical`. |
| `--ff-profile PATH` | auto | Explicit Firefox profile path (default: the active profile from `profiles.ini`). |
| `--brave-profile PATH` | auto | Explicit Brave profile path (default: `~/.config/BraveSoftware/Brave-Browser/Default`). |
| `--chromium-profile PATH` | auto | Explicit Chromium profile path (default: `~/.config/chromium/Default`). |
| `--attach-folder NAME` | `Favoritos` | Folder in `bookmark_bar` where `Desde <other>` is hung. Only used when the canonical is Chromium-family; ignored with `--canonical firefox`. |
| `--out DIR` / `--out-dir DIR` | `out/` | Output directory for artifacts and reports. |
| `--snapshots DIR` | `./snapshots` | Directory for automatic snapshots. |
| `--max-examples N` | `15` | How many duplicate examples to show in the report. |
| `--no-dedupe` | off | Disable deduplication (debug only). |
| `-h` / `--help` | — | Show help and exit. |

Only the pair members' profiles are resolved: a `brave,chromium` run never touches Firefox configuration.

### Examples

```bash
# Firefox↔Brave with Brave's structure as the skeleton
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# Firefox↔Chromium with Firefox canonical (--attach-folder is ignored in this mode)
python3 bookmarks-merge.py --pair firefox,chromium --canonical firefox

# Brave↔Chromium: on --apply only the canonical's store is written
python3 bookmarks-merge.py --pair brave,chromium --canonical chromium

# Apply to a single pair member (the other one is left untouched)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply --only brave

# Restore a snapshot of the firefox,brave pair
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore snapshots/20261019-185514

# Explicit profile paths (only the pair's members are resolved;
# replace the Firefox path with your own profile)
python3 bookmarks-merge.py --pair firefox,brave --canonical firefox \
  --ff-profile ~/.mozilla/firefox/14yqiklo.default-release \
  --brave-profile ~/.config/BraveSoftware/Brave-Browser/Default
```

## Graphical Interface (GUI)

`bookmarks_gui.py` exposes the full CLI in a tkinter window (stdlib only). It adds no duplicated logic: it builds the same command and runs it via subprocess, and enumerates browsers, pairs, and defaults from `--list-browsers --json` (the CLI's registry is the single source of truth).

```bash
python3 bookmarks_gui.py              # open the window
python3 bookmarks_gui.py --self-test  # test the pure logic, no display needed
```

- **Sync**: A/B pair (they cannot be the same), canonical restricted to the two members, and `--only`.
- **Profiles**: one row per registry browser; only the pair's members are enabled (the rest are disabled), and the field shows the default profile in gray until edited (if it matches the default, no flag is sent). "Browse…" warns if `places.sqlite` (Firefox) or `Bookmarks` (Chromium) is missing, but keeps the chosen path.
- **Options**: `--attach-folder`, `--out`, `--snapshots`, `--max-examples`, `--no-dedupe`, and `--force`.
- **Actions**: live-logged preview (dry-run), `--apply` and `--restore` with a confirmation dialog summarizing the pair, the canonical, and which store is written automatically vs. manually, plus direct access to `out/` and `report.txt`.

Action buttons are disabled while a process is running; closing the window with an active run asks for confirmation and terminates the subprocess.

## How It Works

### Merge model

1. **Read** both pair members: Firefox from a copy of `places.sqlite` (WAL replayed on the copy, never the original); Brave and Chromium from their `Bookmarks` JSON.
2. **Build skeleton** from the canonical browser's folder tree.
3. **Dedupe** within the skeleton: for each normalized URL, keep the occurrence closest to a root (shallowest depth wins; ties go to DFS order, canonical root scanned first).
4. **Place exclusive bookmarks** from the other member under `Desde <other>`, preserving their original subfolder hierarchy.
5. **Emit one artifact per pair member** in native format:
   - `out/firefox-restore.json` — only when `firefox` is in the pair. Firefox legacy JSON (manual restore via Library → Import and Backup → Restore → Choose File…)
   - `out/<browser>-bookmarks.json` — for each Chromium-family member: `out/brave-bookmarks.json` and/or `out/chromium-bookmarks.json`. Chromium JSON (direct write to the live store per *What `--apply` writes*)
   - `out/report.txt` — full merge report (also printed to stdout)

### URL normalization

Two URLs are considered duplicates when they normalize to the same key:

- Scheme and host lowercased
- Leading `www.` stripped from host
- Query string and fragment removed
- Trailing slash stripped from path

Examples: `https://www.example.com/path/` and `https://example.com/path` are duplicates.

### Root mapping

| Firefox root | Chromium root (Brave and Chromium) | Notes |
|---|---|---|
| `toolbar` (Barra de marcadores) | `bookmark_bar` | Bookmarks bar |
| `unfiled` (Otros marcadores) | `other` | Other bookmarks |
| `mobile` (Marcadores móviles) | `synced` | Mobile bookmarks |
| `menu` (Menú de marcadores) | `menu` | Extra root; Chromium ignores it |
| `tags` (Etiquetas) | — | Skipped; tag entries are artifacts, not bookmarks |

### Canonical modes and anchoring

Both modes produce the **same union** of bookmarks; only the folder organization differs. The report shows the exact anchor in the `Destino` line.

**Chromium-family canonical** (`--canonical brave` or `--canonical chromium`): the canonical's tree is the skeleton. The other browser's exclusive bookmarks hang under `bookmark_bar / <attach-folder> / Desde <other> / <root label> / <original path>`. If `<attach-folder>` is missing from the bar, the bar's children are wrapped in a `Bookmarks bar` folder and the anchor hangs inside it.

**`--canonical firefox`**: Firefox's tree is the skeleton. The other browser's exclusive bookmarks hang in a `Desde <other>` folder at the top of each root, preserving the original path underneath. `--attach-folder` is accepted but never used.

## Output Artifacts

After a dry-run or `--apply`, the `out/` directory contains one artifact per pair member plus the report:

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
| `brave,chromium` | Only the canonical's store | Manual copy of the non-canonical member's artifact (the report prints source and destination) |

Two live stores are never written in a single run. `--only` restricts the above to one pair member.

### Applying to Firefox (manual)

When `firefox` is in the pair, its store is never touched automatically: Firefox has no supported CLI import path. The restore must be done manually:

1. **Close Firefox.**
2. Open Firefox → `Ctrl+Shift+O` (Library).
3. Import and Backup → Restore → Choose File…
4. Select `out/firefox-restore.json`.
5. Accept the warning that current bookmarks will be replaced.

> **Important**: Do NOT delete bookmarks manually before restoring. Firefox's restore replaces the entire tree.

### Applying to the Chromium-family store (automatic)

When the Chromium-family member is the automatic target (the only Chromium member of a `firefox,*` pair, or the canonical in `brave,chromium`), `--apply` writes directly to its `Bookmarks` file, preserving the file's existing permissions (typically `0600`). That browser must be closed first — Chromium rewrites its file on exit and would overwrite the change.

In `brave,chromium` pairs the non-canonical member is not written: the report prints the manual copy instruction (`out/<browser>-bookmarks.json` over the profile's `Bookmarks` file).

## Snapshots and Rollback

Every `--apply` automatically creates a snapshot in `snapshots/<timestamp>/` with one directory per pair member:

- `firefox/places.sqlite` (plus `-wal`/`-shm` if present)
- `brave/Bookmarks` and `Bookmarks.bak` (if present); for `chromium`, the same contents under `chromium/`
- `manifest.json` with SHA-256 hashes of every file

### Restore from snapshot

`--restore` is pair-aware: it restores only the chosen `--pair` members present in the snapshot; missing members are skipped with a warning. v1.x snapshots (only `firefox/` + `brave/`) restore fine with `--pair firefox,brave`. Like `--apply`, it requires `--pair` and `--canonical`.

```bash
# List available snapshots
ls snapshots/

# Restore a snapshot of the firefox,brave pair
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore snapshots/20261019-185514
```

Browsers must be closed before restoring (or use `--force`).

## Safety Features

| Guard | Behavior |
|---|---|
| Open-browser check | `--apply` refuses if a pair member (Firefox, Brave or Chromium) is running. Other browsers only trigger a read-only analysis warning. `--force` overrides the guard. |
| Pre-apply snapshot | Before writing anything, every `--apply` snapshots both pair members. |
| One live store per run | In `brave,chromium` pairs only the canonical's store is written; the rest is manual. |
| Permission preservation | The Chromium-family `Bookmarks` file keeps its existing permissions (fallback: `0600`). |
| SQLite copy-on-read | `places.sqlite` is never opened in place; a copy is made and the WAL is replayed on the copy. |
| Deterministic GUIDs | Derived with `uuid5` from a stable namespace, so re-running produces identical artifacts. |
| Checksum omission | Chromium's `BookmarkCodec::Decode()` never reads `checksum`; the field is omitted and recomputed on the browser's next save. |
| Verification gate | Artifacts are checked for URL count and zero duplicates before `--apply` is allowed. |

## Project Structure

```
IA-sync/
├── bookmarks-merge.py          # Main script (v2.0.0, stdlib-only)
├── README.md                   # Spanish version
├── README-EN.md                # This file (English)
├── sources.md                  # Reference links (user-maintained)
├── out/                        # Generated artifacts (per the run's pair)
│   ├── brave-bookmarks.json    #   Chromium JSON for Brave (if brave is in the pair)
│   ├── chromium-bookmarks.json #   Chromium JSON for Chromium (if chromium is in the pair)
│   ├── firefox-restore.json    #   Firefox legacy JSON (if firefox is in the pair)
│   ├── report.txt              #   Merge report
│   └── prev-*/                 #   Manual copies of previous artifacts
├── snapshots/                  # Automatic snapshots on --apply
│   └── <timestamp>/
│       ├── firefox/            #   places.sqlite + WAL/SHM
│       ├── brave/              #   Bookmarks + Bookmarks.bak
│       ├── chromium/           #   Bookmarks + Bookmarks.bak (if chromium is in the pair)
│       └── manifest.json       #   SHA-256 hashes
├── backups/                    # Protective copies (manual)
│   ├── firefox-auto-backup-*.jsonlz4
│   └── firefox-pre-merge-*.json
└── odd/tasks/                  # Development task documents
    └── bookmarks-merge.md
```

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

Personal project. No license file.
