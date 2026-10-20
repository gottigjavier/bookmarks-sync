# IA-sync — Bookmark Convergence Tool

Converge Firefox and Brave bookmarks into a single deduplicated tree, selectable canonical layout, with full snapshot/rollback safety.

## Why

Browsers store bookmarks in incompatible formats and diverge over time. Firefox uses SQLite (`places.sqlite` + WAL), Brave uses Chromium JSON. This tool merges both into one tree, removes duplicates by normalized URL, preserves browser-exclusive bookmarks, and writes native-format artifacts for each browser.

## Features

- **Union merge**: bookmarks from both browsers are preserved; only true duplicates are removed.
- **Selectable canonical layout**: `--canonical brave` (default) or `--canonical firefox` controls which browser's folder structure is the skeleton.
- **Smart dedupe**: keeps the shallowest occurrence of each URL (a bookmark-bar shortcut beats a copy buried three folders deep). Ties break by DFS order; the canonical root is scanned first.
- **Firefox-exclusive bookmarks** → placed under `Favoritos / Desde Firefox / <FF root> / <original path>`.
- **Brave-exclusive bookmarks** → placed under `Desde Brave / <root> / <original path>` (Firefox-canonical mode).
- **Deterministic**: same inputs → same outputs. Running twice produces identical artifacts.
- **Snapshot on every `--apply`**: automatic backup of both browser stores before any write.
- **Safety guards**: refuses to `--apply` while browsers are open (Chromium rewrites its file on close). `--force` overrides.
- **`--restore`**: one-command rollback from any snapshot.
- **`--only`**: apply to a single browser (`brave` or `firefox`) while leaving the other untouched.
- **Zero dependencies**: Python 3 standard library only. No pip installs.

## Requirements

- Python 3.10+
- Firefox and/or Brave installed (auto-detected from standard paths)
- No external packages

## Quick Start

```bash
# 1. Dry-run: see what would change (nothing is written)
python3 bookmarks-merge.py

# 2. Review the report and artifacts in out/
cat out/report.txt

# 3. Apply when satisfied (closes browsers first!)
python3 bookmarks-merge.py --apply
```

## Usage

### Basic invocation

```bash
python3 bookmarks-merge.py [OPTIONS]
```

Running without `--apply` is always safe: it only reads, writes previews to `out/`, and prints a report.

### All options

| Option | Default | Description |
|---|---|---|
| `--apply` | off | Write changes to browsers. Without it: dry-run only. |
| `--canonical {brave,firefox}` | `brave` | Which browser's folder layout is the skeleton. |
| `--only {both,brave,firefox}` | `both` | Limit `--apply` to a single browser. |
| `--force` | off | Override the open-browser guard. Dangerous. |
| `--restore SNAPSHOT_DIR` | — | Restore both browsers from a snapshot and exit. |
| `--ff-profile PATH` | auto | Explicit Firefox profile path. |
| `--brave-profile PATH` | auto | Explicit Brave profile path. |
| `--attach-folder NAME` | `Favoritos` | Folder in Brave's bar where `Desde Firefox` is hung. Only used in `--canonical brave`. |
| `--out DIR` | `./out` | Output directory for artifacts and reports. |
| `--snapshots DIR` | `./snapshots` | Directory for automatic snapshots. |
| `--max-examples N` | `15` | How many duplicate examples to show in the report. |
| `--no-dedupe` | off | Disable deduplication (debug only). |

### Examples

```bash
# Firefox-canonical mode: Firefox's folder structure is the base
python3 bookmarks-merge.py --canonical firefox

# Apply only to Brave (Firefox left untouched)
python3 bookmarks-merge.py --apply --only brave

# Restore from a specific snapshot
python3 bookmarks-merge.py --restore snapshots/20261019-185514

# Use explicit profile paths
python3 bookmarks-merge.py \
  --ff-profile ~/.mozilla/firefox/abc123.default-release \
  --brave-profile ~/.config/BraveSoftware/Brave-Browser/Default
```

## How It Works

### Merge model

1. **Read** both browsers: Firefox from a copy of `places.sqlite` (WAL replayed on the copy, never the original), Brave from its `Bookmarks` JSON.
2. **Build skeleton** from the canonical browser's folder tree.
3. **Dedupe** within the skeleton: for each normalized URL, keep the occurrence closest to a root (shallowest depth wins; ties go to DFS order, canonical root scanned first).
4. **Place exclusive bookmarks** from the other browser under the designated folder, preserving their original subfolder hierarchy.
5. **Emit** two native-format artifacts:
   - `out/brave-bookmarks.json` — Chromium JSON (direct write to Brave's store on `--apply`)
   - `out/firefox-restore.json` — Firefox legacy JSON (manual restore via Library → Import and Backup → Restore → Choose File…)

### URL normalization

Two URLs are considered duplicates when they normalize to the same key:

- Scheme and host lowercased
- Leading `www.` stripped from host
- Query string and fragment removed
- Trailing slash stripped from path

Examples: `https://www.example.com/path/` and `https://example.com/path` are duplicates.

### Root mapping

| Firefox root | Brave root | Notes |
|---|---|---|
| `toolbar` (Barra de marcadores) | `bookmark_bar` | Bookmarks bar |
| `unfiled` (Otros marcadores) | `other` | Other bookmarks |
| `mobile` (Marcadores móviles) | `synced` | Mobile bookmarks |
| `menu` (Menú de marcadores) | `menu` | Extra root; Chromium ignores it |
| `tags` (Etiquetas) | — | Skipped; tag entries are artifacts, not bookmarks |

### Canonical modes

**`--canonical brave`** (default): Brave's folder tree is the skeleton. Firefox-exclusive bookmarks are placed under `Favoritos / Desde Firefox / <FF root label> / <original path>`.

**`--canonical firefox`**: Firefox's folder tree is the skeleton. Brave-exclusive bookmarks are placed under `Desde Brave / <Brave root> / <original path>` in the corresponding root.

Both modes produce the **same union** of bookmarks; only the folder organization differs.

## Output Artifacts

After a dry-run or `--apply`, the `out/` directory contains:

| File | Format | Purpose |
|---|---|---|
| `brave-bookmarks.json` | Chromium JSON | Write directly to Brave's `Bookmarks` file |
| `firefox-restore.json` | Firefox legacy JSON | Manual restore via Firefox Library |
| `report.txt` | Plain text | Full merge report (also printed to stdout) |

### Applying to Firefox (manual)

Firefox has no supported CLI import path. The restore must be done manually:

1. **Close Firefox.**
2. Open Firefox → `Ctrl+Shift+O` (Library).
3. Import and Backup → Restore → Choose File…
4. Select `out/firefox-restore.json`.
5. Accept the warning that current bookmarks will be replaced.

> **Important**: Do NOT delete bookmarks manually before restoring. Firefox's restore replaces the entire tree.

### Applying to Brave (automatic)

On `--apply`, the script writes directly to Brave's `Bookmarks` file, preserving the file's existing permissions (typically `0600`). Brave must be closed first — Chromium rewrites its file on exit and would overwrite the change.

## Snapshots and Rollback

Every `--apply` automatically creates a snapshot in `snapshots/<timestamp>/` containing:

- `firefox/places.sqlite` (plus `-wal`/`-shm` if present)
- `brave/Bookmarks` and `Bookmarks.bak` (if present)
- `manifest.json` with SHA-256 hashes of every file

### Restore from snapshot

```bash
# List available snapshots
ls snapshots/

# Restore a specific snapshot (both browsers)
python3 bookmarks-merge.py --restore snapshots/20261019-185514
```

Browsers must be closed before restoring (or use `--force`).

## Safety Features

| Guard | Behavior |
|---|---|
| Open-browser check | `--apply` refuses if Firefox or Brave is running. Use `--force` to override. |
| Mode preservation | Brave's `Bookmarks` file keeps its existing permissions (fallback: `0600`). |
| SQLite copy-on-read | `places.sqlite` is never opened in place; a copy is made and the WAL is replayed on the copy. |
| Deterministic GUIDs | Derived with `uuid5` from a stable namespace, so re-running produces identical artifacts. |
| Checksum omission | Chromium's `BookmarkCodec::Decode()` never reads `checksum`; the field is omitted and recomputed on Brave's next save. |
| Verification gate | Artifacts are checked for URL count and zero duplicates before `--apply` is allowed. |

## Project Structure

```
IA-sync/
├── bookmarks-merge.py          # Main script (v1.2.0, stdlib-only)
├── README.md                   # This file
├── sources.md                  # Reference links (user-maintained)
├── out/                        # Generated artifacts
│   ├── brave-bookmarks.json    #   Chromium JSON for Brave
│   ├── firefox-restore.json    #   Firefox legacy JSON for restore
│   ├── report.txt              #   Merge report
│   └── prev-*/                 #   Previous artifacts (rollback)
├── snapshots/                  # Automatic snapshots on --apply
│   └── <timestamp>/
│       ├── firefox/            #   places.sqlite + WAL/SHM
│       ├── brave/              #   Bookmarks + Bookmarks.bak
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
