#!/usr/bin/env python3
"""bookmarks-merge - converge bookmarks from exactly two browsers into one deduplicated tree.

A run syncs exactly ONE pair chosen from {firefox, brave, chromium}:

  --pair firefox,brave     --canonical brave | firefox
  --pair firefox,chromium  --canonical firefox | chromium
  --pair brave,chromium    --canonical brave | chromium

--pair and --canonical are BOTH required; there is no default canonical browser.
The canonical member's folder distribution is the skeleton; the other member's
exclusive bookmarks are attached under "Desde <non-canonical browser>":

  * canonical in the Chromium family (brave, chromium): everything hangs under
    "<attach-folder>/Desde <other>/<root label>/<original path>" in bookmark_bar.

  * canonical firefox: one "Desde <other>" folder per root at the root's top
    level (--attach-folder is accepted but never used).

Brave and Chromium share the Chromium JSON bookmark format, so every reader and
writer in the "chromium" family below is generic over both. Artifacts are
pair-aware: out/<browser>-bookmarks.json is emitted for each chromium-family
member of the pair, out/firefox-restore.json only when firefox is in the pair.

Design notes. Every one of these was verified against upstream source; see
odd/tasks/bookmarks-merge.md for the exact file and line references.

* Firefox's live store is places.sqlite (+ -wal/-shm). It is NEVER opened in place:
  a copy is made first and the WAL is replayed on the copy.
* Firefox's "Import and Backup -> Restore -> Choose File..." calls
  BookmarkJSONUtils.importFromFile(path, {replace: true}), i.e. it REPLACES the whole
  bookmark tree, and it accepts an uncompressed .json file. There is no supported
  command line path to trigger that import, so this script prepares the file and the
  human performs the restore.
* Chromium-family stores (Brave, Chromium) are Chromium JSON documents.
  BookmarkCodec::Decode() never reads the "checksum" field, so we omit it;
  Chromium recomputes it on its next save.
* Chromium hard-codes the permanent folders' UUIDs and does not read them from the
  file, so we simply echo back whatever the source file has.
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

SCRIPT_VERSION = "2.0.0"
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

# Firefox/Chromium root -> (firefox root pseudonym, firefox root guid, label, index in places root)
FF_ROOT_TARGETS = (
    ("bookmark_bar", "toolbarFolder", "toolbar_____", "toolbar", 1),
    ("other", "unfiledBookmarksFolder", "unfiled_____", "unfiled", 3),
    ("synced", "mobileFolder", "mobile______", "mobile", 4),
)

# Firefox root id -> generic (chromium-style) root key. The tags root (4) is excluded:
# see FF_TAGS_ROOT.
FF_ROOT_ID_TO_KEY = {2: "menu", 3: "bookmark_bar", 5: "other", 6: "synced"}

CHROMIUM_ROOT_ORDER = ("bookmark_bar", "other", "synced")
CHROMIUM_BAR_FALLBACK_NAME = "Bookmarks bar"

# Short prefixes used by the report's example lines.
BROWSER_TAGS = {"firefox": "ff", "brave": "br", "chromium": "ch"}

# Single source of truth for every browser the tool knows about. The CLI (pairs,
# profile flags, process guard, snapshots, write-back) and the later tkinter GUI
# enumerate their options from this registry.
#   kind : "firefox" (places.sqlite) | "chromium" (Chromium JSON Bookmarks file)
#   comms: /proc/<pid>/comm values that identify a running process of the browser
BROWSERS = {
    "firefox": {
        "display": "Firefox",
        "kind": "firefox",
        "comms": ("firefox", "firefox-bin", "firefox-esr"),
        "profile_base": "~/.mozilla/firefox",
        "profile_flag": "--ff-profile",
    },
    "brave": {
        "display": "Brave",
        "kind": "chromium",
        "comms": ("brave", "brave-browser"),
        "profile_base": "~/.config/BraveSoftware/Brave-Browser",
        "profile_flag": "--brave-profile",
    },
    "chromium": {
        "display": "Chromium",
        "kind": "chromium",
        "comms": ("chromium", "chromium-browser"),
        "profile_base": "~/.config/chromium",
        "profile_flag": "--chromium-profile",
    },
}

# Order used to normalize --pair so that "brave,firefox" == "firefox,brave".
BROWSER_ORDER = ("firefox", "brave", "chromium")
VALID_PAIRS = ("firefox,brave", "firefox,chromium", "brave,chromium")

BROWSER_COMMS = {comm for info in BROWSERS.values() for comm in info["comms"]}
COMM_TO_BROWSER = {comm: name for name, info in BROWSERS.items() for comm in info["comms"]}


def registry_document():
    """Machine-readable registry for tooling like the tkinter GUI (--list-browsers)."""
    return {
        "script_version": SCRIPT_VERSION,
        "pairs": list(VALID_PAIRS),
        "browsers": [
            {
                "name": name,
                "display": info["display"],
                "kind": info["kind"],
                "comms": list(info["comms"]),
                "profile_base": info["profile_base"],
                "profile_flag": info["profile_flag"],
            }
            for name, info in BROWSERS.items()
        ],
    }


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
    base = Path(BROWSERS["firefox"]["profile_base"]).expanduser()
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
    base = Path(BROWSERS["brave"]["profile_base"]).expanduser()
    if (base / "Default" / "Bookmarks").is_file():
        return base / "Default"
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "Bookmarks").is_file():
                return entry
    raise SystemExit("No pude resolver el perfil de Brave. Pasá --brave-profile <ruta>.")


def resolve_chromium_profile(explicit):
    if explicit:
        return Path(explicit).expanduser()
    base = Path(BROWSERS["chromium"]["profile_base"]).expanduser()
    if (base / "Default" / "Bookmarks").is_file():
        return base / "Default"
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "Bookmarks").is_file():
                return entry
    raise SystemExit("No pude resolver el perfil de Chromium. Pasá --chromium-profile <ruta>.")


# --------------------------------------------------------------------------- readers


def read_chromium(bookmarks_path):
    """Returns (roots_meta, roots) where roots maps a root key to a list of Nodes.

    Generic over the Chromium family: this same code reads Brave's and Chromium's
    Bookmarks file.
    """
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
        roots[key] = [_chromium_to_node(child) for child in node.get("children") or []]
    return meta, roots


def _chromium_to_node(node):
    if node.get("type") == "folder":
        return _folder(
            node.get("name") or "",
            int(node.get("date_added") or 0),
            [_chromium_to_node(child) for child in node.get("children") or []],
        )
    url = node.get("url") or ""
    return _bookmark(node.get("name") or url, url, int(node.get("date_added") or 0))


def _flatten_chromium(chromium_roots):
    """Flatten a Chromium-family tree into generic flat items, grouped by root.

    Flat item shape (shared with the Firefox adapter, see _adapt_ff_items):
    {root_key, root_label, path, title, url, added_us}. Chromium roots are already
    in the generic namespace, so root_label == root_key.
    """
    items = []
    for root_key, children in chromium_roots.items():
        for node in children:
            _flatten_chromium_node(node, root_key, [], items)
    return items


def _flatten_chromium_node(node, root_key, path, items):
    if node.kind == "url":
        items.append({
            "root_key": root_key,
            "root_label": root_key,
            "path": list(path),
            "title": node.name,
            "url": node.url,
            "added_us": node.added_us,
        })
    else:
        for child in node.children:
            _flatten_chromium_node(child, root_key, path + [node.name], items)


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


def _build_ff_tree(ff_items):
    """Build a tree from Firefox items, using generic (chromium-style) root keys.

    Maps Firefox roots to their generic equivalents (FF_ROOT_ID_TO_KEY):
      toolbar (id=3) -> bookmark_bar, unfiled (id=5) -> other, mobile (id=6) -> synced
      menu (id=2) -> menu (extra, emitted but Chromium ignores it)
      tags (id=4) -> skipped (artifacts, not real bookmarks)
    """
    roots = {"bookmark_bar": [], "other": [], "synced": [], "menu": []}

    for item in ff_items:
        root_key = FF_ROOT_ID_TO_KEY.get(item["root_id"])
        if not root_key:
            continue

        children = roots[root_key]
        for part in item["path"]:
            folder_name = part or "(sin título)"
            found = None
            for child in children:
                if child.kind == "folder" and child.name == folder_name:
                    found = child
                    break
            if found is None:
                found = _folder(folder_name)
                children.append(found)
            children = found.children

        children.append(_bookmark(item["title"] or item["url"], item["url"], item["added_us"]))

    return roots


# ----------------------------------------------------------------------------- merge


def _adapt_ff_items(ff_items):
    """Normalize Firefox items to the generic flat-item shape used by merge_pair.

    Flat shape: {root_key, root_label, path, title, url, added_us}. Tag-root entries
    are dropped (Firefox discards that root on import, so they must not be mistaken
    for bookmarks that exist "only in Firefox"); they are counted for the report.
    """
    flat, tag_entries = [], 0
    for item in ff_items:
        if item["root_id"] == FF_TAGS_ROOT:
            tag_entries += 1
            continue
        root_key = FF_ROOT_ID_TO_KEY.get(item["root_id"])
        if root_key is None:
            continue
        flat.append({
            "root_key": root_key,
            "root_label": FF_ROOT_LABELS[item["root_id"]],
            "path": item["path"],
            "title": item["title"],
            "url": item["url"],
            "added_us": item["added_us"],
        })
    return flat, tag_entries


def _place_flat_items(merged, items, attach_label, attach_folder, canonical_kind):
    """Attach flat-side items under attach_label; returns the anchor string or None.

    The anchoring styles are deliberately asymmetric (each one preserved from the
    original Firefox<->Brave implementation):

    * chromium-family canonical: everything hangs inside <attach_folder> in
      bookmark_bar, with the item's root label as the first path segment. If the
      attach folder is missing, the bar's children are wrapped in a folder named
      like the bar and the attach label hangs inside it.
    * firefox canonical: one attach_label per root at the root's top level;
      attach_folder is accepted but never used.
    """
    if not items:
        return None
    if canonical_kind == "chromium":
        bar = merged.setdefault("bookmark_bar", [])
        anchor = find_folder_path(bar, attach_folder)
        if anchor is None:
            # Wrap a COPY of the bar's children: aliasing `bar` itself here would
            # make the wrapper its own descendant once inserted into `bar`, and
            # every later tree walk (assign_keys) would recurse forever.
            wrapped = list(bar)
            bar.clear()
            fallback = _folder(CHROMIUM_BAR_FALLBACK_NAME, 0, wrapped)
            bar.append(fallback)
            anchor = [fallback]
        container = find_or_make_folder(anchor[-1].children, attach_label)
        for item in items:
            cursor = container
            for part in [item["root_label"]] + item["path"]:
                cursor = find_or_make_folder(cursor.children, part or "(sin título)")
            cursor.children.append(
                _bookmark(item["title"] or item["url"], item["url"], item["added_us"])
            )
        return " / ".join(node.name for node in anchor) + " / " + attach_label

    by_root = {}
    for item in items:
        by_root.setdefault(item["root_key"], []).append(item)
    for root_key, group in by_root.items():
        root_children = merged.setdefault(root_key, [])
        container = find_or_make_folder(root_children, attach_label)
        for item in group:
            cursor = container
            for part in item["path"]:
                cursor = find_or_make_folder(cursor.children, part or "(sin título)")
            cursor.children.append(
                _bookmark(item["title"] or item["url"], item["url"], item["added_us"])
            )
    return attach_label


def merge_pair(skeleton_roots, flat_items, canonical_kind, attach_label, attach_folder,
               tag_entries=0):
    """Build the unified tree: the canonical skeleton, deduplicated by URL, with the
    other member's exclusive bookmarks attached (tree skeleton + flat side).

    Dedupe inside the skeleton keeps the copy closest to a root. A bookmark loose in
    the bar is a shortcut the user reaches every day; the same URL filed inside a
    folder is a filing. Walking depth-first made the folder copy win whenever the
    folder itself came first in its parent's children list, which silently deleted
    the bar shortcuts. The flat side is then deduped against every URL the skeleton
    keeps; whatever survives is placed via _place_flat_items().
    """
    dropped_skel, dropped_flat = [], []

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

    for children in skeleton_roots.values():
        scan(children, 1)

    # Phase 2: rebuild the tree, dropping every copy that lost its URL.
    def keep(node, trail):
        if node.kind == "url":
            winner = best.get(_norm_url(node.url))
            if winner is None or winner[1] != id(node):
                dropped_skel.append({"trail": trail, "url": node.url, "name": node.name})
                return None
            return _bookmark(node.name, node.url, node.added_us)
        children = []
        for child in node.children:
            kept = keep(child, trail + [child.name])
            if kept is not None:
                children.append(kept)
        return _folder(node.name, node.added_us, children)

    merged = {}
    for root_key, children in skeleton_roots.items():
        kept = []
        for child in children:
            node = keep(child, [root_key, child.name])
            if node is not None:
                kept.append(node)
        merged[root_key] = kept

    # Phase 3: the flat side is deduped against every URL the skeleton keeps.
    seen = {key: None for key in best}
    new_items = []
    for item in flat_items:
        key = _norm_url(item["url"])
        if key in seen:
            dropped_flat.append({
                "trail": [item["root_key"]] + item["path"] + [item["title"]],
                "url": item["url"],
                "name": item["title"],
            })
            continue
        seen[key] = None
        new_items.append(item)

    anchor = _place_flat_items(merged, new_items, attach_label, attach_folder, canonical_kind)

    return {
        "merged": merged,
        "anchor": anchor,
        "placed": new_items,
        "dropped_skel": dropped_skel,
        "dropped_flat": dropped_flat,
        "tag_entries": tag_entries,
    }


# --------------------------------------------------------------------------- writers


def emit_chromium(chromium_meta, merged, target):
    """Emit a valid Chromium-family Bookmarks document (checksum intentionally omitted)."""
    counter = itertools.count(1)
    roots = {}
    for root_key in CHROMIUM_ROOT_ORDER:
        meta = chromium_meta.get(root_key)
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


def take_snapshot(dest_root, profiles, pair):
    """Snapshot every pair member: firefox -> places.sqlite triple, chromium-family
    -> Bookmarks + Bookmarks.bak. Layout: <stamp>/<browser>/... (old snapshots used
    the same firefox/ + brave/ dir names, so they stay restorable)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_root / stamp
    manifest = {"version": 1, "created": stamp, "script_version": SCRIPT_VERSION, "files": {}}

    for member in pair:
        profile = profiles[member]
        member_dir = dest / member
        member_dir.mkdir(parents=True, exist_ok=True)
        if member == "firefox":
            sources = [
                (Path(str(profile / "places.sqlite") + suffix), f"places.sqlite{suffix}")
                for suffix in ("", "-wal", "-shm")
            ]
        else:
            sources = [(profile / name, name) for name in ("Bookmarks", "Bookmarks.bak")]
        for src, name in sources:
            if not src.is_file():
                continue
            dst = member_dir / name
            shutil.copy2(src, dst)
            manifest["files"][str(dst.relative_to(dest))] = {
                "bytes": dst.stat().st_size,
                "sha256": sha256_of(dst),
            }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


# ------------------------------------------------------------------------------- main


def build_report(args, profiles, chromium_roots, ff_items, ff_stats, result, out_dir):
    """Render the human report. Everything is pair-aware: only the pair's members are
    mentioned, and wording/labels key off the chosen canonical browser."""
    pair = args._pair
    canonical = args.canonical
    noncanon = args._noncanon
    display = {name: info["display"] for name, info in BROWSERS.items()}
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
    for member in pair:
        add(f"  {display[member]:<9}: {profiles[member]}")
    add(f"  Par     : {pair[0]},{pair[1]}")
    add(f"  Canonico: {canonical}")
    add(f"  Modo    : {'APPLY' if args.apply else 'DRY-RUN (no se toca ningun browser)'}")
    add("")

    add("-- Estado de origen " + "-" * 57)
    for member in pair:
        if member == "firefox":
            add(f"  Firefox : {len(ff_items):5d} bookmarks, {ff_stats['folders']:4d} carpetas, "
                f"{ff_stats['separators']} separadores")
        else:
            roots = chromium_roots[member]
            add(f"  {display[member]:<9}: {count_urls(roots):5d} bookmarks, "
                f"{count_folders(roots):4d} carpetas")
    if "firefox" in pair:
        if result["tag_entries"]:
            add(f"            ({result['tag_entries']} entradas de etiquetas ignoradas: Firefox descarta el root de tags)")
        if ff_stats["orphans"]:
            add(f"            ({ff_stats['orphans']} bookmarks huerfanos, sin carpeta padre reconocible)")
    add("")

    add("-- Duplicados eliminados " + "-" * 53)
    add(f"  Dentro de {display[canonical]:<10}: {len(result['dropped_skel']):5d}")
    add(f"  {display[noncanon]} (ya estaban en {display[canonical]}): {len(result['dropped_flat']):5d}")
    for item in result["dropped_skel"][: args.max_examples]:
        add(f"     - [{BROWSER_TAGS[canonical]}] {'/'.join(item['trail'][:4])}  ->  {item['url'][:70]}")
    for item in result["dropped_flat"][: args.max_examples]:
        add(f"     - [{BROWSER_TAGS[noncanon]}] {'/'.join(item['trail'][:4])}  ->  {item['url'][:70]}")
    add("")

    title = f"-- Exclusivos de {display[noncanon]} (preservados) "
    add(title + "-" * max(5, 78 - len(title)))
    add(f"  Total reubicado : {len(result['placed'])}")
    if result["anchor"]:
        add(f"  Destino         : {result['anchor']}")
    for item in result["placed"][: args.max_examples]:
        trail = " / ".join([item["root_label"]] + item["path"])
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
            add(f"         FALTAN {len(missing)} de los exclusivos de {display[noncanon]}")
    add(f"  {'OK ' if ok else 'FALLA'}  los dos artefactos coinciden en cantidad")
    add("")

    add("-- Siguiente paso " + "-" * 60)
    if args.apply:
        for member in pair:
            if args.only not in ("both", member):
                continue
            if member == "firefox":
                add("  Firefox : hacer el restore a mano (Firefox REEMPLAZA todo el arbol):")
                add("            1) Cerrá Firefox.")
                add("            2) Abrí Firefox -> Ctrl+Shift+O (Biblioteca).")
                add("            3) Importar y respaldar -> Restaurar -> Elegir archivo...")
                add(f"            4) Elegí: {out_dir / 'firefox-restore.json'}")
                add("            5) Aceptá el aviso de que se borran los marcadores actuales.")
            elif member in args._auto_targets:
                add(f"  {display[member]:<9}: archivo reemplazado. Abrilo y revisa la barra de marcadores.")
            else:
                add(f"  {display[member]:<9}: copia manual (miembro no canonico):")
                add(f"            {out_dir / (member + '-bookmarks.json')}")
                add(f"            sobre {profiles[member] / 'Bookmarks'}")
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


def _normalize_pair(raw):
    """Parse and validate --pair (comma separated, order insensitive)."""
    members = [m.strip().lower() for m in (raw or "").split(",") if m.strip()]
    if len(members) != 2 or len(set(members)) != 2 or any(m not in BROWSERS for m in members):
        raise SystemExit(
            f"'{raw}' no es un --pair valido. Pares validos (orden indiferente): "
            + " | ".join(VALID_PAIRS) + "."
        )
    return tuple(sorted(members, key=BROWSER_ORDER.index))


def _resolve_profile(member, args):
    """Resolve the profile of ONE pair member (lazy: browsers outside the pair are never touched)."""
    if member == "firefox":
        return resolve_firefox_profile(args.ff_profile)
    if member == "brave":
        profile = resolve_brave_profile(args.brave_profile)
    else:
        profile = resolve_chromium_profile(args.chromium_profile)
    bookmarks = profile / "Bookmarks"
    if not bookmarks.is_file():
        raise SystemExit(
            f"No existe {bookmarks} del perfil de {BROWSERS[member]['display']}. "
            f"Pasá --{member}-profile <ruta>."
        )
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Converge los marcadores de exactamente dos navegadores en un unico "
                    "arbol deduplicado, usando como esqueleto la estructura de carpetas "
                    "del navegador canonico (siempre explicito con --canonical)."
    )
    parser.add_argument("--apply", action="store_true",
                        help="escribe los cambios (sin esto: dry-run, no toca ningun browser)")
    parser.add_argument("--pair", metavar="A,B",
                        help="par de navegadores a sincronizar, coma separada y orden "
                             "indiferente: firefox,brave | firefox,chromium | brave,chromium "
                             "(obligatorio)")
    parser.add_argument("--canonical", metavar="NOMBRE",
                        help="navegador cuyo arbol de carpetas es el esqueleto; debe ser uno "
                             "de los dos miembros de --pair (obligatorio, sin default)")
    parser.add_argument("--only", default="both", metavar="NOMBRE",
                        help="limita la aplicacion a un miembro del par: both | <miembro> "
                             "(default: both)")
    parser.add_argument("--force", action="store_true",
                        help="ignora el guard de browsers abiertos (peligroso)")
    parser.add_argument("--restore", metavar="SNAPSHOT_DIR",
                        help="restaura los archivos desde un snapshot y sale")
    parser.add_argument("--ff-profile", help="ruta del perfil de Firefox")
    parser.add_argument("--brave-profile", help="ruta del perfil de Brave")
    parser.add_argument("--chromium-profile",
                        help="ruta del perfil de Chromium (default: ~/.config/chromium/Default)")
    parser.add_argument("--attach-folder", default="Favoritos",
                        help="carpeta del bookmark_bar donde colgar 'Desde <navegador>' "
                             "(solo cuando el canonico es un navegador de la familia "
                             "Chromium; con --canonical firefox se cuelga en la raiz de "
                             "cada root)")
    parser.add_argument("--out", "--out-dir", dest="out", default=str(SCRIPT_DIR / "out"),
                        help="carpeta de salida para previews y artefactos")
    parser.add_argument("--snapshots", default=str(SCRIPT_DIR / "snapshots"),
                        help="carpeta de snapshots")
    parser.add_argument("--max-examples", type=int, default=15,
                        help="cuantas entradas de ejemplo mostrar en el reporte")
    parser.add_argument("--no-dedupe", action="store_true", help="desactiva el dedupe (debug)")
    parser.add_argument("--list-browsers", action="store_true",
                        help="imprime en JSON los navegadores, pares y rutas por defecto "
                             "que conoce la herramienta (consumido por la GUI) y sale")
    parser.add_argument("--json", action="store_true",
                        help="formato JSON de salida (solo valido con --list-browsers)")
    args = parser.parse_args(argv)

    # ---- Registry introspection (used by the tkinter GUI); needs no pair. ------
    if args.list_browsers:
        print(json.dumps(registry_document(), indent=2, ensure_ascii=False))
        return 0
    if args.json:
        raise SystemExit("--json solo tiene sentido con --list-browsers.")

    # ---- CLI validation. The pair drives everything below. ---------------------
    if not args.pair:
        raise SystemExit("Falta --pair. Pares validos (orden indiferente): "
                         + " | ".join(VALID_PAIRS) + ".")
    pair = _normalize_pair(args.pair)
    args._pair = pair
    if not args.canonical:
        raise SystemExit(f"Falta --canonical. Para el par {pair[0]},{pair[1]} los valores "
                         f"validos son: {pair[0]} | {pair[1]}.")
    if args.canonical not in pair:
        raise SystemExit(f"--canonical '{args.canonical}' no pertenece al par "
                         f"{pair[0]},{pair[1]}. Valores validos: {pair[0]} | {pair[1]}.")
    if args.only != "both" and args.only not in pair:
        raise SystemExit(f"--only '{args.only}' no es valido para el par {pair[0]},{pair[1]}. "
                         f"Valores validos: both | {pair[0]} | {pair[1]}.")
    noncanon = pair[0] if args.canonical == pair[1] else pair[1]
    args._noncanon = noncanon
    canonical_kind = BROWSERS[args.canonical]["kind"]
    attach_label = f"Desde {BROWSERS[noncanon]['display']}"

    # Profiles are resolved lazily: only the pair's members are ever looked up.
    profiles = {member: _resolve_profile(member, args) for member in pair}
    live = {
        member: profiles[member] / ("places.sqlite" if member == "firefox" else "Bookmarks")
        for member in pair
    }
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.restore:
        snapshot = Path(args.restore).expanduser()
        if not snapshot.is_dir():
            raise SystemExit(f"Snapshot inexistente: {snapshot}")
        running = running_browsers()
        if running and not args.force:
            raise SystemExit(f"Cerralos primero: {', '.join(sorted(running))} (o usá --force)")
        restored, skipped = [], []
        for member in pair:
            member_dir = snapshot / member
            if not member_dir.is_dir():
                # Old snapshots only carry firefox/ + brave/; never crash on those.
                skipped.append(member)
                continue
            if member == "firefox":
                for suffix in ("", "-wal", "-shm"):
                    src = member_dir / f"places.sqlite{suffix}"
                    if src.is_file():
                        shutil.copy2(src, Path(str(live[member]) + suffix))
                        restored.append(str(live[member]) + suffix)
            else:
                src = member_dir / "Bookmarks"
                if src.is_file():
                    shutil.copy2(src, live[member])
                    restored.append(str(live[member]))
        if restored:
            print("Restaurado:")
            for item in restored:
                print("  " + item)
        else:
            print("Nada que restaurar: el snapshot no contiene archivos para este par.")
        for member in skipped:
            print(f"AVISO: el snapshot no tiene el directorio '{member}'; se omite {member}.")
        return 0

    running = running_browsers()
    pair_comms = {comm for member in pair for comm in BROWSERS[member]["comms"]}
    running_pair = running & pair_comms
    if args.apply and running_pair and not args.force:
        names = ", ".join(sorted(BROWSERS[COMM_TO_BROWSER[comm]]["display"] for comm in running_pair))
        raise SystemExit(
            f"Hay browsers abiertos ({names}). Cerralos antes de --apply "
            f"(al salir reescriben su archivo y pisarian el cambio)."
        )
    if running:
        print(f"AVISO: {', '.join(sorted(running))} esta corriendo. "
              f"El analisis es de solo lectura; el estado puede ser el ultimo guardado.",
              file=sys.stderr)

    # ---- Read the pair's stores. ----------------------------------------------
    ff_items, ff_stats = None, None
    if "firefox" in pair:
        with tempfile.TemporaryDirectory(prefix="bookmarks-merge-") as tmp:
            ff_items, orphans, separators, ff_folder_count = read_firefox(profiles["firefox"], tmp)
        ff_stats = {
            "folders": ff_folder_count,
            "separators": separators,
            "orphans": orphans,
        }

    chromium_roots, chromium_meta = {}, {}
    for member in pair:
        if member != "firefox":
            chromium_meta[member], chromium_roots[member] = read_chromium(live[member])

    # Skeleton = canonical tree. Chromium-emit meta (root names/GUIDs/date_added)
    # comes from the canonical member when it is chromium-family, else from the
    # pair's only chromium-family member.
    if args.canonical == "firefox":
        skeleton_roots = _build_ff_tree(ff_items)
        meta_source = noncanon
    else:
        skeleton_roots = chromium_roots[args.canonical]
        meta_source = args.canonical

    # Flat side = the non-canonical member, normalized to one item shape.
    if noncanon == "firefox":
        flat_items, tag_entries = _adapt_ff_items(ff_items)
    else:
        flat_items, tag_entries = _flatten_chromium(chromium_roots[noncanon]), 0

    result = merge_pair(skeleton_roots, flat_items, canonical_kind, attach_label,
                        args.attach_folder, tag_entries)
    if args.no_dedupe:
        merged_all = {root_key: _clone(children) for root_key, children in skeleton_roots.items()}
        anchor = _place_flat_items(merged_all, flat_items, attach_label,
                                   args.attach_folder, canonical_kind)
        result = {
            "merged": merged_all,
            "anchor": anchor,
            "placed": list(flat_items),
            "dropped_skel": [],
            "dropped_flat": [],
            "tag_entries": tag_entries,
        }

    for root_key, children in result["merged"].items():
        assign_keys(children, root_key)

    # ---- Emit one artifact per pair member (pair-aware). -----------------------
    artifacts = {}
    for member in pair:
        if member == "firefox":
            target = out_dir / "firefox-restore.json"
            emit_firefox(result["merged"], target)
        else:
            target = out_dir / f"{member}-bookmarks.json"
            emit_chromium(chromium_meta[meta_source], result["merged"], target)
        artifacts[member] = target

    inventories, inv_by_member = [], {}
    for member in pair:
        if member == "firefox":
            urls, folders = inventory_firefox(artifacts[member])
            fmt = "legacy json"
        else:
            urls, folders = inventory_chromium(artifacts[member])
            fmt = "chromium"
        label = f"{BROWSERS[member]['display']:<8} ({fmt})"
        inv_by_member[member] = (urls, folders)
        inventories.append((label, artifacts[member], (urls, folders)))

    args._inventories = inventories
    args._expected_urls = len(inv_by_member[meta_source][0])
    args._must_contain = {_norm_url(item["url"]) for item in result["placed"]}

    # Which chromium-family member would --apply auto-write? With a firefox partner
    # the (single) chromium-family member is written directly; with a chromium-family
    # partner only the canonical member is auto-written (never two live stores at once).
    if "firefox" in pair:
        auto_targets = {m for m in pair if m != "firefox"}
    else:
        auto_targets = {args.canonical}
    args._auto_targets = {m for m in auto_targets if args.only in ("both", m)}

    report, ok = build_report(
        args, profiles, chromium_roots, ff_items, ff_stats, result, out_dir,
    )
    print(report)
    (out_dir / "report.txt").write_text(report, encoding="utf-8")

    if not ok:
        print("Verificacion FALLIDA: no se aplica nada.", file=sys.stderr)
        return 2

    if not args.apply:
        print(f"Previews escritos en: {out_dir}")
        return 0

    snapshot = take_snapshot(Path(args.snapshots).expanduser(), profiles, pair)
    print(f"Snapshot: {snapshot}")

    before = {}
    for member in pair:
        if args.only not in ("both", member):
            continue
        if member == "firefox":
            print(f"Firefox: preparado {artifacts['firefox']} - hacé el restore a mano desde la Biblioteca.")
        elif member in args._auto_targets:
            before[str(live[member])] = sha256_of(live[member])
            replace_keeping_mode(artifacts[member], live[member])
            print(f"{BROWSERS[member]['display']} actualizado: {live[member]}")
        else:
            print(f"{BROWSERS[member]['display']}: copia manual (miembro no canonico): "
                  f"{artifacts[member]} -> {live[member]}")
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
