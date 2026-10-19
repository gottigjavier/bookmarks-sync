#!/usr/bin/env python3
"""bookmarks-merge - converge Firefox and Brave bookmarks into one deduplicated tree.

The merged tree uses Brave's folder distribution as the canonical layout. Bookmarks
that exist in only one browser are preserved: Brave's stay where they are, Firefox's
are attached under "<attach-folder>/Desde Firefox/<firefox root>/<original path>".

Design notes. Every one of these was verified against upstream source; see
odd/tasks/bookmarks-merge.md for the exact file and line references.

* Firefox's live store is places.sqlite (+ -wal/-shm). It is NEVER opened in place:
  a copy is made first and the WAL is replayed on the copy.
* Firefox's "Import and Backup -> Restore -> Choose File..." calls
  BookmarkJSONUtils.importFromFile(path, {replace: true}), i.e. it REPLACES the whole
  bookmark tree, and it accepts an uncompressed .json file. There is no supported
  command line path to trigger that import, so this script prepares the file and the
  human performs the restore.
* Brave's store is a Chromium JSON document. BookmarkCodec::Decode() never reads the
  "checksum" field, so we omit it; Chromium recomputes it on its next save.
* Chromium hard-codes the permanent folders' UUIDs and does not read them from the
  file, so we simply echo back whatever the current file has.
* Chromium date_added = microseconds since 1601-01-01.
* Firefox dateAdded   = microseconds since 1970-01-01.
* Firefox GUIDs must match /^[a-zA-Z0-9_-]{12}$/.

The merge is a pure function of its two inputs, and GUIDs are derived with uuid5, so
running the tool twice produces the same artifacts and the second --apply is a no-op.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCRIPT_VERSION = "1.1.0"
SCRIPT_DIR = Path(__file__).resolve().parent

# Seconds between 1601-01-01 (Windows/Chromium epoch) and 1970-01-01, in microseconds.
WINDOWS_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000

# Stable namespace so that derived GUIDs never change between runs.
UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "bookmarks-merge")

# Firefox GUID alphabet: exactly 64 symbols, matching /^[a-zA-Z0-9_-]{12}$/.
FF_GUID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"

# moz_bookmarks ids of the Firefox root folders (id 1 is the places root itself).
FF_ROOT_LABELS = {
    2: "Menú de marcadores",
    3: "Barra de marcadores",
    4: "Etiquetas",
    5: "Otros marcadores",
    6: "Marcadores móviles",
}
# Tag folders live under the tags root. Firefox discards that root on import, so tag
# entries must not be mistaken for bookmarks that exist "only in Firefox".
FF_TAGS_ROOT = 4
FF_ORPHAN_ROOT = 1

# Brave root -> (firefox root pseudonym, firefox root guid, label, index in places root)
FF_ROOT_TARGETS = (
    ("bookmark_bar", "toolbarFolder", "toolbar_____", "toolbar", 1),
    ("other", "unfiledBookmarksFolder", "unfiled_____", "unfiled", 3),
    ("synced", "mobileFolder", "mobile______", "mobile", 4),
)

CHROMIUM_ROOT_ORDER = ("bookmark_bar", "other", "synced")
BRAVE_ONLY_FALLBACK_NAME = "Bookmarks bar"

BROWSER_COMMS = {"firefox", "firefox-bin", "firefox-esr", "brave", "brave-browser"}


# --------------------------------------------------------------------------- model


@dataclass
class Node:
    """One bookmark or folder in the unified tree."""

    name: str
    kind: str  # "url" | "folder"
    url: str = ""
    added_us: int = 0  # microseconds since the Chromium/Windows epoch (1601)
    children: list = field(default_factory=list)
    key: str = ""  # stable identity, assigned right before emission


def _folder(name, added_us=0, children=None):
    return Node(name=name, kind="folder", added_us=added_us, children=children or [])


def _bookmark(name, url, added_us=0):
    return Node(name=name, kind="url", url=url, added_us=added_us)


# --------------------------------------------------------------------------- utils


def _norm_url(url):
    """Deduplication key for a URL.

    Only http(s) is normalised (lowercase scheme+host, drop a leading "www." and a
    default port, drop trailing slashes when there is no query or fragment).
    Everything else (file:, about:, javascript:, chrome:, ...) is compared verbatim,
    because normalising those would break them.
    """
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return raw
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        try:
            port = parts.port
        except ValueError:
            port = None
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = f"{host}:{port}" if port and not default_port else host
        path = parts.path or ""
        if not parts.query and not parts.fragment:
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))
    except ValueError:
        return raw


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_keeping_mode(src, dst):
    """Copy src over an existing file, keeping that file's permission bits.

    shutil.copy2 propagates the *source* mode, and here the source is a freshly
    written artifact that the umask leaves at 0644. A plain copy2 would therefore
    relax the browser store's permissions (Chromium keeps its Bookmarks private at
    0600). Preserve whatever the target already had and fall back to 0600 when it
    does not exist yet.
    """
    dst = Path(dst)
    try:
        mode = stat.S_IMODE(dst.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    shutil.copy2(src, dst)
    dst.chmod(mode)


def ch_guid(key):
    """Deterministic Chromium GUID. Chromium only requires a valid, unique UUID."""
    return str(uuid.uuid5(UUID_NS, "chromium:" + key))


def ff_guid(key):
    """Deterministic Firefox GUID: 12 chars of the alphabet Firefox validates."""
    value = uuid.uuid5(UUID_NS, "firefox:" + key).int
    return "".join(FF_GUID_ALPHABET[(value >> (6 * i)) & 0x3F] for i in range(12))


def to_unix_us(windows_us):
    return max(0, int(windows_us) - WINDOWS_EPOCH_OFFSET_US)


def find_folder_path(children, name):
    """Depth-first search for a folder by name; returns the node path down to it."""
    for child in children:
        if child.kind == "folder" and child.name == name:
            return [child]
    for child in children:
        if child.kind == "folder":
            deeper = find_folder_path(child.children, name)
            if deeper:
                return [child] + deeper
    return None


def find_or_make_folder(children, name):
    for child in children:
        if child.kind == "folder" and child.name == name:
            return child
    node = _folder(name)
    children.append(node)
    return node


def assign_keys(children, prefix):
    counts = {}
    for child in children:
        counts[child.name] = counts.get(child.name, 0) + 1
        seen = counts[child.name]
        suffix = "" if seen == 1 else f"~{seen}"
        child.key = f"{prefix}/{child.name}{suffix}"
        if child.kind == "folder":
            assign_keys(child.children, child.key)


# --------------------------------------------------------------- environment probes


def running_browsers():
    procs = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return procs
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if comm in BROWSER_COMMS:
            procs.add(comm)
    return procs


def resolve_firefox_profile(explicit):
    if explicit:
        return Path(explicit).expanduser()
    base = Path.home() / ".mozilla" / "firefox"
    ini = base / "profiles.ini"
    if ini.is_file():
        sections, current = {}, None
        for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                sections.setdefault(current, {})
            elif "=" in line and current:
                key, value = line.split("=", 1)
                sections[current][key.strip()] = value.strip()
        # An [Install*] section's Default= is the profile actually in use.
        for name, kv in sections.items():
            if name.startswith("Install") and kv.get("Default"):
                candidate = base / kv["Default"]
                if candidate.is_dir():
                    return candidate
        for name, kv in sections.items():
            if name.startswith("Profile") and kv.get("Default") == "1" and kv.get("Path"):
                candidate = base / kv["Path"]
                if candidate.is_dir():
                    return candidate
    for candidate in sorted(base.glob("*.default-release")):
        return candidate
    raise SystemExit("No pude resolver el perfil de Firefox. Pasá --ff-profile <ruta>.")


def resolve_brave_profile(explicit):
    if explicit:
        return Path(explicit).expanduser()
    base = Path.home() / ".config" / "BraveSoftware" / "Brave-Browser"
    if (base / "Default" / "Bookmarks").is_file():
        return base / "Default"
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "Bookmarks").is_file():
                return entry
    raise SystemExit("No pude resolver el perfil de Brave. Pasá --brave-profile <ruta>.")


# --------------------------------------------------------------------------- readers


def read_brave(bookmarks_path):
    """Returns (roots_meta, roots) where roots maps a root key to a list of Nodes."""
    document = json.loads(bookmarks_path.read_text(encoding="utf-8"))
    meta, roots = {}, {}
    for key, node in (document.get("roots") or {}).items():
        if node.get("type") != "folder":
            continue
        meta[key] = {
            "name": node.get("name") or key,
            "guid": node.get("guid") or "",
            "added_us": int(node.get("date_added") or 0),
        }
        roots[key] = [_brave_to_node(child) for child in node.get("children") or []]
    return meta, roots


def _brave_to_node(node):
    if node.get("type") == "folder":
        return _folder(
            node.get("name") or "",
            int(node.get("date_added") or 0),
            [_brave_to_node(child) for child in node.get("children") or []],
        )
    url = node.get("url") or ""
    return _bookmark(node.get("name") or url, url, int(node.get("date_added") or 0))


def _ff_path(folder_id, folders):
    """Walk up to a known Firefox root. Returns (root_id, [folder titles...])."""
    parts, current = [], folder_id
    for _ in range(500):
        if current in FF_ROOT_LABELS:
            return current, list(reversed(parts))
        if current == FF_ORPHAN_ROOT or current not in folders:
            return None, []
        parent, title = folders[current]
        parts.append(title)
        current = parent
    return None, []


def read_firefox(profile, workdir):
    """Read bookmarks out of a COPY of places.sqlite so the live file is untouched."""
    source = profile / "places.sqlite"
    if not source.is_file():
        raise SystemExit(f"No existe {source}")
    copy = Path(workdir) / "places.sqlite"
    shutil.copy2(source, copy)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(source) + suffix)
        if extra.is_file():
            shutil.copy2(extra, str(copy) + suffix)

    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = 1")
        folders = {
            fid: (parent, title or "")
            for fid, parent, title in con.execute(
                "SELECT id, parent, title FROM moz_bookmarks WHERE type = 2"
            )
        }
        rows = con.execute(
            """
            SELECT b.parent, b.title, p.url, b.dateAdded
            FROM moz_bookmarks b
            JOIN moz_places p ON p.id = b.fk
            WHERE b.type = 1
            ORDER BY b.parent, b.position, b.id
            """
        ).fetchall()
        separators = con.execute(
            "SELECT COUNT(*) FROM moz_bookmarks WHERE type = 3"
        ).fetchone()[0]
    finally:
        con.close()

    items, orphans = [], 0
    for parent, title, url, added in rows:
        if not url:
            continue
        root_id, parts = _ff_path(parent, folders)
        if root_id is None:
            orphans += 1
            continue
        items.append(
            {
                "root_id": root_id,
                "path": parts,
                "title": title or "",
                "url": url,
                "added_us": int(added or 0) + WINDOWS_EPOCH_OFFSET_US,
            }
        )
    return items, orphans, separators, len(folders)


# ----------------------------------------------------------------------------- merge


def merge(brave_roots, ff_items, attach_folder):
    """Build the unified tree: Brave's layout, deduplicated by URL, Firefox-only added.

    Dedupe inside Brave keeps the copy closest to a root. A bookmark loose in the bar is a
    shortcut the user reaches every day; the same URL filed inside a folder is a filing. Walking
    depth-first made the folder copy win whenever the folder itself came first in its parent's
    children list, which silently deleted the bar shortcuts.
    """
    dropped_brave, dropped_ff = [], []

    # Phase 1: for every URL, find the occurrence closest to a root. Ties go to DFS order, and
    # bookmark_bar is scanned first, so the bar also wins depth ties.
    best, counter = {}, itertools.count()

    def scan(children, depth):
        for node in children:
            if node.kind == "url":
                key = _norm_url(node.url)
                rank = (depth, next(counter))
                current = best.get(key)
                if current is None or rank < current[0]:
                    best[key] = (rank, id(node))
            else:
                scan(node.children, depth + 1)

    for children in brave_roots.values():
        scan(children, 1)

    # Phase 2: rebuild the tree, dropping every copy that lost its URL.
    def keep(node, trail):
        if node.kind == "url":
            winner = best.get(_norm_url(node.url))
            if winner is None or winner[1] != id(node):
                dropped_brave.append({"trail": trail, "url": node.url, "name": node.name})
                return None
            return _bookmark(node.name, node.url, node.added_us)
        children = []
        for child in node.children:
            kept = keep(child, trail + [child.name])
            if kept is not None:
                children.append(kept)
        return _folder(node.name, node.added_us, children)

    # Firefox is deduped against every URL Brave keeps.
    seen = {key: None for key in best}

    merged = {}
    for root_key, children in brave_roots.items():
        kept = []
        for child in children:
            node = keep(child, [root_key, child.name])
            if node is not None:
                kept.append(node)
        merged[root_key] = kept

    # Tag entries are artifacts of Firefox's tag folders, not placed bookmarks.
    candidates = [item for item in ff_items if item["root_id"] != FF_TAGS_ROOT]
    new_items = []
    for item in candidates:
        key = _norm_url(item["url"])
        if key in seen:
            dropped_ff.append(item)
            continue
        seen[key] = None
        new_items.append(item)

    container = None
    anchor = None
    if new_items:
        bar = merged.setdefault("bookmark_bar", [])
        anchor = find_folder_path(bar, attach_folder)
        if anchor is None:
            fallback = _folder(BRAVE_ONLY_FALLBACK_NAME, 0, bar)
            bar.insert(0, fallback)
            anchor = [fallback]
        container = find_or_make_folder(anchor[-1].children, "Desde Firefox")
        for item in new_items:
            cursor = container
            for part in [FF_ROOT_LABELS[item["root_id"]]] + item["path"]:
                cursor = find_or_make_folder(cursor.children, part or "(sin título)")
            cursor.children.append(
                _bookmark(item["title"] or item["url"], item["url"], item["added_us"])
            )

    return {
        "merged": merged,
        "anchor": " / ".join(node.name for node in anchor) if new_items else None,
        "placed": new_items,
        "dropped_brave": dropped_brave,
        "dropped_ff": dropped_ff,
        "tag_entries": len(ff_items) - len(candidates),
    }


# --------------------------------------------------------------------------- writers


def emit_chromium(brave_meta, merged, target):
    counter = itertools.count(1)
    roots = {}
    for root_key in CHROMIUM_ROOT_ORDER:
        meta = brave_meta.get(root_key)
        if meta is None:
            continue
        roots[root_key] = {
            "date_added": str(meta["added_us"]),
            "guid": meta["guid"] or ch_guid("root:" + root_key),
            "id": str(next(counter)),
            "name": meta["name"],
            "type": "folder",
            "children": [_chromium_node(node, counter) for node in merged.get(root_key, [])],
        }
    document = {"version": 1, "roots": roots}
    target.write_text(json.dumps(document, ensure_ascii=False, indent=3) + "\n", encoding="utf-8")


def _chromium_node(node, counter):
    if node.kind == "url":
        return {
            "date_added": str(node.added_us),
            "guid": ch_guid(node.key),
            "id": str(next(counter)),
            "name": node.name,
            "type": "url",
            "url": node.url,
        }
    return {
        "date_added": str(node.added_us),
        "guid": ch_guid(node.key),
        "id": str(next(counter)),
        "name": node.name,
        "type": "folder",
        "children": [_chromium_node(child, counter) for child in node.children],
    }


def emit_firefox(merged, target):
    counter = itertools.count(1)
    children = [_ff_folder("menu", "menu________", "bookmarksMenuFolder", 0, [], counter)]
    for root_key, pseudo, guid, label, index in FF_ROOT_TARGETS:
        children.append(
            _ff_folder(label, guid, pseudo, index, merged.get(root_key, []), counter)
        )
    root = {
        "title": "",
        "id": next(counter),
        "guid": "root________",
        "index": 0,
        "dateAdded": 0,
        "lastModified": 0,
        "typeCode": 2,
        "type": "text/x-moz-place-container",
        "root": "placesRoot",
        "children": children,
    }
    # The tags root is intentionally absent: Firefox filters it out on import anyway.
    #
    # CRITICAL: the file must be the BARE root container object, never a JSON array.
    # PlacesUtils.unwrapNodes() parses it as JSON.parse("[" + blob + "]"), so an array
    # file would unwrap to [[...]], nodes[0].children would be undefined, and
    # importFromJSON() would silently import 0 bookmarks while reporting success.
    target.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ff_folder(name, guid, pseudo, index, node_children, counter):
    added = min((c.added_us for c in node_children if c.added_us), default=0)
    out = {
        "title": name,
        "id": next(counter),
        "guid": guid,
        "index": index,
        "dateAdded": to_unix_us(added),
        "lastModified": to_unix_us(added),
        "typeCode": 2,
        "type": "text/x-moz-place-container",
    }
    if pseudo:
        out["root"] = pseudo
    kids = []
    for position, child in enumerate(node_children):
        if child.kind == "url":
            entry = {
                "title": child.name,
                "id": next(counter),
                "guid": ff_guid(child.key),
                "index": position,
                "dateAdded": to_unix_us(child.added_us),
                "lastModified": to_unix_us(child.added_us),
                "typeCode": 1,
                "type": "text/x-moz-place",
                "uri": child.url,
            }
        else:
            entry = _ff_folder(child.name, ff_guid(child.key), None, position, child.children, counter)
        kids.append(entry)
    out["children"] = kids
    return out


# ------------------------------------------------------------------------------ read


def inventory_chromium(target):
    document = json.loads(target.read_text(encoding="utf-8"))
    urls, folders = [], []

    def walk(node):
        if node.get("type") == "folder":
            folders.append(node.get("name") or "")
            for child in node.get("children") or []:
                walk(child)
        else:
            urls.append(node.get("url") or "")

    for root_key, node in (document.get("roots") or {}).items():
        for child in node.get("children") or []:
            walk(child)
    return urls, folders


def inventory_firefox(target):
    document = json.loads(target.read_text(encoding="utf-8"))
    # Guard the trap described in emit_firefox(): Firefox wraps the blob in brackets
    # before parsing, so a JSON array here would be a silent no-op import.
    if not isinstance(document, dict) or not isinstance(document.get("children"), list):
        raise SystemExit(
            "Artefacto de Firefox invalido: debe ser el objeto raiz con 'children', "
            "no un array (PlacesUtils.unwrapNodes hace JSON.parse('[' + blob + ']'))."
        )
    urls, folders = [], []

    def walk(node):
        if node.get("type") == "text/x-moz-place-container":
            folders.append(node.get("title") or "")
            for child in node.get("children") or []:
                walk(child)
        else:
            urls.append(node.get("uri") or "")

    for child in document.get("children") or []:
        walk(child)
    return urls, folders


# --------------------------------------------------------------------------- snapshot


def take_snapshot(dest_root, ff_profile, brave_profile):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_root / stamp
    (dest / "firefox").mkdir(parents=True, exist_ok=True)
    (dest / "brave").mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "created": stamp, "script_version": SCRIPT_VERSION, "files": {}}

    sources = []
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(ff_profile / "places.sqlite") + suffix)
        if src.is_file():
            sources.append((src, dest / "firefox" / src.name))
    for name in ("Bookmarks", "Bookmarks.bak"):
        src = brave_profile / name
        if src.is_file():
            sources.append((src, dest / "brave" / name))

    for src, dst in sources:
        shutil.copy2(src, dst)
        manifest["files"][str(dst.relative_to(dest))] = {
            "bytes": dst.stat().st_size,
            "sha256": sha256_of(dst),
        }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


# ------------------------------------------------------------------------------- main


def build_report(args, ff_profile, brave_profile, ff_items, ff_stats, brave_roots, result, out_dir):
    lines = []
    add = lines.append

    def count_urls(roots):
        return sum(1 for children in roots.values() for node in _walk(children) if node.kind == "url")

    def count_folders(roots):
        return sum(1 for children in roots.values() for node in _walk(children) if node.kind == "folder")

    add("")
    add("=" * 78)
    add("  bookmarks-merge  -  reporte")
    add("=" * 78)
    add(f"  Firefox : {ff_profile}")
    add(f"  Brave   : {brave_profile}")
    add(f"  Modo    : {'APPLY' if args.apply else 'DRY-RUN (no se toca ningun browser)'}")
    add("")

    add("-- Estado de origen " + "-" * 57)
    add(f"  Brave   : {count_urls(brave_roots):5d} bookmarks, {count_folders(brave_roots):4d} carpetas")
    add(f"  Firefox : {len(ff_items):5d} bookmarks, {ff_stats['folders']:4d} carpetas, "
        f"{ff_stats['separators']} separadores")
    if result["tag_entries"]:
        add(f"            ({result['tag_entries']} entradas de etiquetas ignoradas: Firefox descarta el root de tags)")
    if ff_stats["orphans"]:
        add(f"            ({ff_stats['orphans']} bookmarks huerfanos, sin carpeta padre reconocible)")
    add("")

    add("-- Duplicados eliminados " + "-" * 53)
    add(f"  Dentro de Brave          : {len(result['dropped_brave']):5d}")
    add(f"  Firefox (ya estaban en Brave): {len(result['dropped_ff']):5d}")
    for item in result["dropped_brave"][: args.max_examples]:
        add(f"     - [br] {'/'.join(item['trail'][:4])}  ->  {item['url'][:70]}")
    add("")

    add("-- Exclusivos de Firefox (preservados) " + "-" * 39)
    add(f"  Total reubicado : {len(result['placed'])}")
    if result["anchor"]:
        add(f"  Destino         : {result['anchor']} / Desde Firefox")
    for item in result["placed"][: args.max_examples]:
        trail = " / ".join([FF_ROOT_LABELS[item["root_id"]]] + item["path"])
        add(f"     - {trail[:60]}  ->  {item['url'][:60]}")
    add("")

    add("-- Resultado " + "-" * 65)
    add(f"  Bookmark finales : {count_urls(result['merged'])}")
    add(f"  Carpetas finales : {count_folders(result['merged'])}")
    add("")

    add("-- Verificacion " + "-" * 62)
    ok = True
    for label, path, inv in args._inventories:
        urls, folders = inv
        keys = [_norm_url(u) for u in urls]
        dupes = len(keys) - len(set(keys))
        status = "OK " if (len(urls) == args._expected_urls and dupes == 0) else "FALLA"
        if status == "FALLA":
            ok = False
        add(f"  {status}  {label}: {len(urls)} bookmarks (esperado {args._expected_urls}), "
            f"{len(folders)} carpetas, {dupes} duplicados")
        missing = args._must_contain - set(keys)
        if missing:
            ok = False
            add(f"         FALTAN {len(missing)} de los exclusivos de Firefox")
    add(f"  {'OK ' if ok else 'FALLA'}  los dos artefactos coinciden en cantidad")
    add("")

    add("-- Siguiente paso " + "-" * 60)
    if args.apply:
        add("  Brave   : archivo reemplazado. Abrilo y revisa la barra de marcadores.")
        add("  Firefox : hacer el restore a mano (Firefox REEMPLAZA todo el arbol):")
        add("            1) Cerrá Firefox.")
        add("            2) Abrí Firefox -> Ctrl+Shift+O (Biblioteca).")
        add("            3) Importar y respaldar -> Restaurar -> Elegir archivo...")
        add(f"            4) Elegí: {out_dir / 'firefox-restore.json'}")
        add("            5) Aceptá el aviso de que se borran los marcadores actuales.")
    else:
        add("  Nada fue modificado. Cuando revises este reporte y los archivos de out/,")
        add("  autorizá la aplicacion con:  --apply")
    add("")
    return "\n".join(lines), ok


def _walk(children):
    for node in children:
        yield node
        if node.kind == "folder":
            yield from _walk(node.children)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Converge Firefox and Brave bookmarks into one deduplicated tree "
                    "using Brave's folder distribution."
    )
    parser.add_argument("--apply", action="store_true",
                        help="escribe los cambios (sin esto: dry-run, no toca ningun browser)")
    parser.add_argument("--only", choices=("both", "brave", "firefox"), default="both",
                        help="limita la aplicacion a un browser")
    parser.add_argument("--force", action="store_true",
                        help="ignora el guard de browsers abiertos (peligroso)")
    parser.add_argument("--restore", metavar="SNAPSHOT_DIR",
                        help="restaura los archivos desde un snapshot y sale")
    parser.add_argument("--ff-profile", help="ruta del perfil de Firefox")
    parser.add_argument("--brave-profile", help="ruta del perfil de Brave")
    parser.add_argument("--attach-folder", default="Favoritos",
                        help="carpeta (dentro de la barra de Brave) donde colgar 'Desde Firefox'")
    parser.add_argument("--out", default=str(SCRIPT_DIR / "out"),
                        help="carpeta de salida para previews y artefactos")
    parser.add_argument("--snapshots", default=str(SCRIPT_DIR / "snapshots"),
                        help="carpeta de snapshots")
    parser.add_argument("--max-examples", type=int, default=15,
                        help="cuantas entradas de ejemplo mostrar en el reporte")
    parser.add_argument("--no-dedupe", action="store_true", help="desactiva el dedupe (debug)")
    args = parser.parse_args(argv)

    ff_profile = resolve_firefox_profile(args.ff_profile)
    brave_profile = resolve_brave_profile(args.brave_profile)
    ff_places = ff_profile / "places.sqlite"
    brave_file = brave_profile / "Bookmarks"
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.restore:
        snapshot = Path(args.restore).expanduser()
        if not snapshot.is_dir():
            raise SystemExit(f"Snapshot inexistente: {snapshot}")
        running = running_browsers()
        if running and not args.force:
            raise SystemExit(f"Cerralos primero: {', '.join(sorted(running))} (o usá --force)")
        restored = []
        for suffix in ("", "-wal", "-shm"):
            src = snapshot / "firefox" / f"places.sqlite{suffix}"
            if src.is_file():
                shutil.copy2(src, Path(str(ff_places) + suffix))
                restored.append(str(ff_places) + suffix)
        src = snapshot / "brave" / "Bookmarks"
        if src.is_file():
            shutil.copy2(src, brave_file)
            restored.append(str(brave_file))
        print("Restaurado:")
        for item in restored:
            print("  " + item)
        return 0

    running = running_browsers()
    if running:
        if args.apply and not args.force:
            raise SystemExit(
                f"Hay browsers abiertos ({', '.join(sorted(running))}). Cerralos antes de --apply "
                f"(Brave reescribe su archivo al salir y pisaria el cambio)."
            )
        print(f"AVISO: {', '.join(sorted(running))} esta corriendo. "
              f"El analisis es de solo lectura; el estado puede ser el ultimo guardado.",
              file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="bookmarks-merge-") as tmp:
        ff_items, orphans, separators, ff_folder_count = read_firefox(ff_profile, tmp)
    ff_stats = {
        "folders": ff_folder_count,
        "separators": separators,
        "orphans": orphans,
    }

    brave_meta, brave_roots = read_brave(brave_file)

    result = merge(brave_roots, ff_items, args.attach_folder)
    if args.no_dedupe:
        merged_all = {}
        for root_key, children in brave_roots.items():
            merged_all[root_key] = _clone(children)
        for item in ff_items:
            bar = merged_all.setdefault("bookmark_bar", [])
            anchor = find_folder_path(bar, args.attach_folder) or []
            target = anchor[-1] if anchor else find_or_make_folder(bar, args.attach_folder)
            container = find_or_make_folder(target.children, "Desde Firefox")
            cursor = container
            for part in [FF_ROOT_LABELS[item["root_id"]]] + item["path"]:
                cursor = find_or_make_folder(cursor.children, part or "(sin título)")
            cursor.children.append(_bookmark(item["title"] or item["url"], item["url"], item["added_us"]))
        result["merged"] = merged_all
        result["dropped_brave"], result["dropped_ff"] = [], []

    for root_key, children in result["merged"].items():
        assign_keys(children, root_key)

    chromium_target = out_dir / "brave-bookmarks.json"
    firefox_target = out_dir / "firefox-restore.json"
    emit_chromium(brave_meta, result["merged"], chromium_target)
    emit_firefox(result["merged"], firefox_target)

    ch_urls, ch_folders = inventory_chromium(chromium_target)
    ff_urls, ff_folders = inventory_firefox(firefox_target)
    expected = len(ch_urls)
    ff_keys = {_norm_url(item["url"]) for item in result["placed"]}

    args._inventories = [
        ("Brave   (chromium)", chromium_target, (ch_urls, ch_folders)),
        ("Firefox (legacy json)", firefox_target, (ff_urls, ff_folders)),
    ]
    args._expected_urls = expected
    args._must_contain = ff_keys

    report, ok = build_report(
        args, ff_profile, brave_profile, ff_items, ff_stats, brave_roots,
        result, out_dir,
    )
    print(report)
    (out_dir / "report.txt").write_text(report, encoding="utf-8")

    if not ok:
        print("Verificacion FALLIDA: no se aplica nada.", file=sys.stderr)
        return 2

    if not args.apply:
        print(f"Previews escritos en: {out_dir}")
        return 0

    snapshot = take_snapshot(Path(args.snapshots).expanduser(), ff_profile, brave_profile)
    print(f"Snapshot: {snapshot}")

    before = {str(brave_file): sha256_of(brave_file)}
    if args.only in ("both", "brave"):
        replace_keeping_mode(chromium_target, brave_file)
        print(f"Brave actualizado: {brave_file}")
    if args.only in ("both", "firefox"):
        print(f"Firefox: preparado {firefox_target} - hacé el restore a mano desde la Biblioteca.")
    print(f"hashes previos: {before}")
    if running:
        print("Recordá: los browsers estaban abiertos; reinicialos para ver los cambios.",
              file=sys.stderr)
    return 0


def _clone(children):
    out = []
    for node in children:
        if node.kind == "folder":
            out.append(_folder(node.name, node.added_us, _clone(node.children)))
        else:
            out.append(_bookmark(node.name, node.url, node.added_us))
    return out


if __name__ == "__main__":
    sys.exit(main())
