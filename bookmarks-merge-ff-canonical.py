#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bookmarks-merge-ff-canonical.py
Converge Firefox y Brave bookmarks usando la distribución de carpetas de FIREFOX como canónica.

- Esqueleto: árbol completo de Firefox (toolbar, unfiled, mobile, menu, tags)
- Brave se fusiona dentro: bookmark_bar→toolbar, other→unfiled, synced→mobile
- Ítems solo-en-Brave → carpeta "Desde Brave" en la raíz Firefox correspondiente
- Dedupe por URL normalizada: gana la copia de Firefox (canónica)
- Dedupe intra-Firefox: gana la copia de menor profundidad (atajos de barra primero)
- Salida: Firefox legacy JSON (restore manual) + Chromium JSON (apply a Brave)
"""

import argparse
import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SCRIPT_VERSION = "1.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent

# --- Constantes de raíces Firefox (places.sqlite) ---
FF_ROOT_IDS = {
    "menu": 2,
    "toolbar": 3,
    "unfiled": 5,
    "tags": 4,
    "mobile": 6,  # se detecta dinámicamente
}

FF_ROOT_LABELS = {
    2: "Barra de marcadores",
    3: "Barra de marcadores",
    5: "Otros marcadores",
    4: "Etiquetas",
}

# Mapeo Brave → Firefox
BRAVE_TO_FF_ROOT = {
    "bookmark_bar": "toolbar",
    "other": "unfiled",
    "synced": "mobile",
}

# Orden de emisión Chromium (para salida Brave)
CHROMIUM_ROOT_ORDER = ["bookmark_bar", "other", "synced", "menu", "tags"]

# Carpeta para ítems solo-en-Brave
ATTACH_FOLDER_NAME = "Desde Brave"

# Fallback si no hay carpeta ancla en la raíz Firefox
BRAVE_ONLY_FALLBACK_NAME = "Favoritos"


# --------------------------------------------------------------------------- model
@dataclass
class Node:
    """Un bookmark o carpeta en el árbol unificado."""
    name: str
    kind: str  # "url" | "folder"
    url: str = ""
    added_us: int = 0
    children: list = field(default_factory=list)
    key: str = ""  # identidad estable, asignada antes de emitir


def _folder(name: str, added_us: int = 0, children: list | None = None) -> Node:
    return Node(name=name, kind="folder", added_us=added_us, children=children or [])


def _bookmark(name: str, url: str, added_us: int = 0) -> Node:
    return Node(name=name, kind="url", url=url, added_us=added_us)


# --------------------------------------------------------------------------- utils
def _norm_url(url: str) -> str:
    """Normaliza URL para dedupe: esquema+host+path sin www., sin query/fragment, sin trailing slash."""
    if not url:
        return ""
    try:
        u = urlsplit(url)
        host = u.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = u.path.rstrip("/")
        return urlunsplit((u.scheme.lower(), host, path, "", ""))
    except Exception:
        return url.lower().rstrip("/")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_keeping_mode(src: Path, dst: Path) -> None:
    """Escribe dst desde src preservando el modo de dst si existe, si no 0o600."""
    mode = dst.stat().st_mode & 0o777 if dst.exists() else 0o600
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.chmod(mode)
    tmp.replace(dst)


def ch_guid(key: str) -> str:
    """Genera GUID estilo Chromium (16 hex chars)."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def ff_guid(key: str) -> str:
    """Genera GUID estilo Firefox (32 hex chars + 4-4-4-4-12)."""
    h = hashlib.sha256(key.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def to_unix_us(windows_us: int) -> int:
    """Epoch Windows (1601) → Unix (1970) en microsegundos."""
    return windows_us // 10 - 11644473600_000_000


def find_folder_path(children: list[Node], name: str) -> list[Node] | None:
    """Busca una carpeta por nombre (primera coincidencia DFS). Devuelve la ruta de nodos."""
    for child in children:
        if child.kind == "folder" and child.name == name:
            return [child]
        if child.kind == "folder":
            sub = find_folder_path(child.children, name)
            if sub:
                return [child] + sub
    return None


def find_or_make_folder(children: list[Node], name: str) -> Node:
    for child in children:
        if child.kind == "folder" and child.name == name:
            return child
    new = _folder(name)
    children.append(new)
    return new


def assign_keys(children: list[Node], prefix: str) -> None:
    """Asigna claves estables a todos los nodos (para GUIDs determinísticos)."""
    counter = itertools.count(1)
    for child in children:
        _assign_keys_rec(child, f"{prefix}/{next(counter)}")


def _assign_keys_rec(node: Node, key: str) -> None:
    node.key = key
    for i, child in enumerate(node.children, 1):
        _assign_keys_rec(child, f"{key}/{i}")


# --------------------------------------------------------------- environment probes
def running_browsers() -> set[str]:
    """Devuelve conjunto de navegadores en ejecución (para guard)."""
    running = set()
    try:
        out = os.popen("ps -eo comm=").read()
        for line in out.splitlines():
            comm = line.strip().lower()
            if comm in ("firefox", "firefox-bin", "brave", "brave-browser"):
                running.add(comm)
    except Exception:
        pass
    return running


def resolve_firefox_profile(explicit: str | None) -> Path:
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


def resolve_brave_profile(explicit: str | None) -> Path:
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
def read_firefox(profile: Path, workdir: Path) -> tuple[list[dict], int, int, int]:
    """
    Lee places.sqlite y devuelve:
    - lista de items (dicts con url, title, root_id, path[], added_us)
    - count de carpetas, separadores, orphans
    """
    places = profile / "places.sqlite"
    if not places.is_file():
        raise SystemExit(f"No existe places.sqlite en {profile}")

    # Copia + WAL/SHM para lectura consistente sin bloquear Firefox
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(places) + suffix)
        if src.is_file():
            shutil.copy2(src, workdir / src.name)

    copy_path = workdir / "places.sqlite"
    con = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Detectar root mobile (suele ser id=6 pero no garantizado)
    mobile_root_id = None
    for row in con.execute("select id, title from moz_bookmarks where parent=1 and type=2"):
        if row["title"].lower() in ("mobile", "marcadores móviles", "marcadores moviles"):
            mobile_root_id = row["id"]
            break
    if mobile_root_id is None:
        # fallback: el que no sea 2,3,4,5
        for row in con.execute("select id from moz_bookmarks where parent=1 and type=2 and id not in (2,3,4,5)"):
            mobile_root_id = row["id"]
            break
    FF_ROOT_IDS["mobile"] = mobile_root_id or 6

    # Construir mapa id → (parent_id, title, type, position, date_added)
    nodes = {}
    for row in con.execute("select id, parent, title, type, position, dateAdded from moz_bookmarks"):
        nodes[row["id"]] = {
            "parent": row["parent"],
            "title": row["title"] or "",
            "type": row["type"],
            "position": row["position"],
            "added_us": row["dateAdded"] or 0,
        }

    # Raíces reales (hijos de PlacesRoot id=1)
    root_ids = [2, 3, 4, 5, FF_ROOT_IDS["mobile"]]

    # Recorrer cada raíz y recolectar bookmarks (type=1) con su path de carpetas
    items = []
    folders_count = 0
    separators_count = 0

    def walk_folder(folder_id: int, path: list[str], root_id: int):
        nonlocal folders_count, separators_count
        for child_id in sorted(
            (cid for cid, n in nodes.items() if n["parent"] == folder_id),
            key=lambda cid: nodes[cid]["position"]
        ):
            n = nodes[child_id]
            if n["type"] == 2:  # folder
                folders_count += 1
                walk_folder(child_id, path + [n["title"]], root_id)
            elif n["type"] == 1:  # url
                items.append({
                    "url": n.get("title", ""),  # placeholder, se llena abajo
                    "title": n["title"],
                    "root_id": root_id,
                    "path": list(path),
                    "added_us": n["added_us"],
                })
            elif n["type"] == 3:  # separator
                separators_count += 1

    # Primero obtener URLs de moz_places
    places_urls = {}
    for row in con.execute("select id, url from moz_places"):
        places_urls[row["id"]] = row["url"]

    # Ahora recorrer desde cada raíz real
    for root_id in root_ids:
        root_title = nodes[root_id]["title"] if root_id in nodes else ""
        # urls sueltas directamente bajo la raíz
        for child_id in sorted(
            (cid for cid, n in nodes.items() if n["parent"] == root_id),
            key=lambda cid: nodes[cid]["position"]
        ):
            n = nodes[child_id]
            if n["type"] == 1:  # url suelta en la raíz
                # Firefox guarda la url en moz_places, no en moz_bookmarks
                # Necesitamos buscar el place_id... en moz_bookmarks no está directo
                # En Firefox, los bookmarks tipo 1 tienen un fk a moz_places via moz_bookmarks.id? No.
                # La relación es: moz_bookmarks.id (para type=1) → moz_places.id via moz_bookmarks.fk? No hay fk directo.
                # En realidad: moz_bookmarks.type=1 tiene un campo 'fk' que apunta a moz_places.id
                pass
            elif n["type"] == 2:
                walk_folder(child_id, [], root_id)

    # La forma correcta: join moz_bookmarks (type=1) con moz_places via moz_bookmarks.fk
    for root_id in root_ids:
        for row in con.execute(
            "select b.id, b.title, b.parent, b.position, b.dateAdded, p.url "
            "from moz_bookmarks b join moz_places p on b.fk = p.id "
            "where b.type=1 and b.parent=? order by b.position",
            (root_id,)
        ):
            # Reconstruir path de carpetas subiendo por parent HASTA la raíz actual (root_id), no hasta PlacesRoot
            path = []
            current = row["parent"]
            while current and current != root_id and current in nodes and nodes[current]["type"] == 2:
                path.insert(0, nodes[current]["title"])
                current = nodes[current]["parent"]
            items.append({
                "url": row["url"],
                "title": row["title"] or row["url"],
                "root_id": root_id,
                "path": path,
                "added_us": row["dateAdded"] or 0,
            })

    # Carpetas y separadores totales (para stats)
    for row in con.execute("select type from moz_bookmarks"):
        if row["type"] == 2:
            folders_count += 1
        elif row["type"] == 3:
            separators_count += 1

    # Separadores y carpetas ya contados arriba en walk_folder, pero el conteo total incluye raíces
    # Re-contar limpio:
    folders_count = sum(1 for n in nodes.values() if n["type"] == 2)
    separators_count = sum(1 for n in nodes.values() if n["type"] == 3)

    # Tags folder (root_id=4) items se ignoran para merge (son artifacts)
    tag_entries = sum(1 for i in items if i["root_id"] == 4)

    con.close()
    return items, folders_count, separators_count, tag_entries


def read_brave(bookmarks_path: Path) -> tuple[dict, dict]:
    """
    Lee Bookmarks de Brave (Chromium JSON).
    Returns: (roots_meta, roots) donde roots mapea root_key → lista de Nodes.
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
        roots[key] = [_brave_to_node(child) for child in node.get("children") or []]
    return meta, roots


def _brave_to_node(node: dict) -> Node:
    if node.get("type") == "folder":
        return _folder(
            node.get("name") or "",
            int(node.get("date_added") or 0),
            [_brave_to_node(child) for child in node.get("children") or []],
        )
    url = node.get("url") or ""
    return _bookmark(node.get("name") or url, url, int(node.get("date_added") or 0))


# ----------------------------------------------------------------------------- merge
def merge(ff_roots: dict[str, list[Node]], brave_roots: dict[str, list[Node]], attach_folder: str) -> dict:
    """
    Fusiona Brave dentro del árbol canónico de Firefox.
    ff_roots: dict root_key → lista de Nodes (árbol Firefox ya parseado)
    brave_roots: dict root_key → lista de Nodes (árbol Brave parseado)
    """
    dropped_brave, dropped_ff = [], []

    # Phase 1: escanear árbol Firefox y elegir ganador por URL (menor profundidad, tie-break DFS)
    best, counter = {}, itertools.count()

    def scan_ff(children: list[Node], depth: int):
        for node in children:
            if node.kind == "url":
                key = _norm_url(node.url)
                rank = (depth, next(counter))
                current = best.get(key)
                if current is None or rank < current[0]:
                    best[key] = (rank, id(node), "ff")
            else:
                scan_ff(node.children, depth + 1)

    for children in ff_roots.values():
        scan_ff(children, 1)

    # Phase 2: reconstruir árbol Firefox, descartando duplicados internos
    def keep_ff(node: Node, trail: list[str]) -> Node | None:
        if node.kind == "url":
            winner = best.get(_norm_url(node.url))
            if winner is None or winner[1] != id(node):
                dropped_ff.append({"trail": trail, "url": node.url, "name": node.name})
                return None
            return _bookmark(node.name, node.url, node.added_us)
        children = []
        for child in node.children:
            kept = keep_ff(child, trail + [child.name])
            if kept is not None:
                children.append(kept)
        return _folder(node.name, node.added_us, children)

    merged = {}
    for root_key, children in ff_roots.items():
        kept = []
        for child in children:
            node = keep_ff(child, [root_key, child.name])
            if node is not None:
                kept.append(node)
        merged[root_key] = kept

    # Phase 3: procesar items de Brave, mapeando a raíces Firefox (SOLO a nivel raíz)
    # Primero, construir set de URLs que ya ganaron en Firefox
    seen = {key: None for key in best}

    # Para cada raíz Brave, mapear a raíz Firefox y agregar items solo-en-Brave bajo "Desde Brave"
    for brave_root, brave_children in brave_roots.items():
        ff_root = BRAVE_TO_FF_ROOT.get(brave_root)
        if ff_root is None or ff_root not in merged:
            ff_root = "unfiled"

        target_root_children = merged[ff_root]

        # Recorrer todo el árbol Brave y extraer URLs que no estén en Firefox
        _collect_brave_only(brave_children, target_root_children, ff_root, seen, dropped_brave, attach_folder)

    return {
        "merged": merged,
        "dropped_brave": dropped_brave,
        "dropped_ff": dropped_ff,
    }


def _collect_brave_only(
    brave_children: list[Node],
    target_children: list[Node],
    ff_root: str,
    seen: dict,
    dropped_brave: list,
    attach_folder: str,
    trail: list[str] | None = None,
):
    """Recorre árbol Brave y agrega items que no existen en Firefox bajo 'Desde Brave' en la raíz destino."""
    if trail is None:
        trail = []

    for node in brave_children:
        if node.kind == "url":
            key = _norm_url(node.url)
            if key in seen:
                dropped_brave.append({"trail": trail + [node.name], "url": node.url, "name": node.name})
                continue
            seen[key] = None
            _add_brave_only_item(target_children, node, attach_folder)
        else:
            # Carpeta: NO fusionar recursivamente por nombre. Solo recorrer para extraer URLs.
            _collect_brave_only(node.children, target_children, ff_root, seen, dropped_brave, attach_folder, trail + [node.name])


def _add_brave_only_item(target_children: list[Node], brave_node: Node, attach_folder: str):
    """Agrega un item solo-en-Brave bajo carpeta 'Desde Brave' en el NIVEL RAÍZ del root Firefox."""
    container = find_or_make_folder(target_children, attach_folder)
    container.children.append(_bookmark(brave_node.name, brave_node.url, brave_node.added_us))


# --------------------------------------------------------------------------- writers
def emit_chromium(brave_meta: dict, merged: dict[str, list[Node]], target: Path) -> None:
    """Emite JSON estilo Chromium (para Brave). Mapea raíces Firefox → Brave."""
    counter = itertools.count(1)
    roots = {}

    # Mapeo Firefox root_key → Brave root_key
    ff_to_brave_root = {
        "toolbar": "bookmark_bar",
        "unfiled": "other",
        "mobile": "synced",
        "menu": "menu",
        "tags": "tags",
    }

    for ff_root, brave_root in ff_to_brave_root.items():
        meta = brave_meta.get(brave_root)
        if meta is None:
            meta = {"name": brave_root, "guid": ch_guid(brave_root), "added_us": 0}

        children_nodes = merged.get(ff_root, [])
        children_json = [_node_to_chromium(child, counter) for child in children_nodes]

        roots[brave_root] = {
            "name": meta["name"],
            "guid": meta["guid"] or ch_guid(brave_root),
            "date_added": str(meta["added_us"]),
            "children": children_json,
            "type": "folder",
        }

    document = {
        "checksum": "",  # Brave lo recalcula
        "roots": roots,
        "version": 1,
    }
    target.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _node_to_chromium(node: Node, counter: itertools.count) -> dict:
    base = {
        "guid": ch_guid(node.key) if node.key else ch_guid(node.name + node.url),
        "name": node.name,
        "date_added": str(node.added_us),
        "type": "folder" if node.kind == "folder" else "url",
    }
    if node.kind == "url":
        base["url"] = node.url
    else:
        base["children"] = [_node_to_chromium(child, counter) for child in node.children]
    return base


def emit_firefox(merged: dict[str, list[Node]], target: Path) -> None:
    """
    Emite JSON legacy de Firefox (para restore manual via Biblioteca → Restaurar → Elegir archivo).
    Estructura: raíz 'placesRoot' con children = [bookmarksMenuFolder, toolbarFolder, unfiledBookmarksFolder, mobileFolder, tagsFolder]
    """
    counter = itertools.count(1)

    # Orden canónico Firefox
    ff_root_order = ["menu", "toolbar", "unfiled", "tags", "mobile"]
    ff_root_ids = {"menu": 1, "toolbar": 2, "unfiled": 3, "tags": 4, "mobile": 5}

    children_roots = []
    for root_key in ff_root_order:
        if root_key not in merged:
            continue
        root_id = ff_root_ids[root_key]
        root_label = FF_ROOT_LABELS.get(root_id, root_key)
        root_node = {
            "type": "text/x-moz-place-container",
            "root": root_key + "Folder",
            "title": root_label,
            "id": root_id,
            "children": [_node_to_firefox(child, counter) for child in merged[root_key]],
        }
        children_roots.append(root_node)

    document = {
        "type": "text/x-moz-place-container",
        "root": "placesRoot",
        "title": "",
        "id": 0,
        "children": children_roots,
    }
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _node_to_firefox(node: Node, counter: itertools.count) -> dict:
    base = {
        "guid": ff_guid(node.key) if node.key else ff_guid(node.name + node.url),
        "title": node.name,
        "index": next(counter),
        "dateAdded": node.added_us,
        "lastModified": node.added_us,
        "type": "text/x-moz-place-container" if node.kind == "folder" else "text/x-moz-place",
    }
    if node.kind == "url":
        base["uri"] = node.url
    else:
        base["children"] = [_node_to_firefox(child, counter) for child in node.children]
    return base


# --------------------------------------------------------------------------- inventory / verification
def inventory_chromium(path: Path) -> tuple[int, int]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    urls = folders = 0
    for root in doc.get("roots", {}).values():
        for node in _walk_chromium(root.get("children") or []):
            if node["type"] == "url":
                urls += 1
            else:
                folders += 1
    return urls, folders


def _walk_chromium(children: list[dict]):
    for node in children:
        yield node
        if node.get("type") == "folder":
            yield from _walk_chromium(node.get("children") or [])


def inventory_firefox(path: Path) -> tuple[int, int]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    urls = folders = 0
    for root in doc.get("children", []):
        for node in _walk_firefox(root.get("children") or []):
            if node["type"] == "text/x-moz-place":
                urls += 1
            elif node["type"] == "text/x-moz-place-container":
                folders += 1
    return urls, folders


def _walk_firefox(children: list[dict]):
    for node in children:
        yield node
        if node.get("type") == "text/x-moz-place-container":
            yield from _walk_firefox(node.get("children") or [])


# ------------------------------------------------------------------------------- main
def build_report(
    args, ff_profile, brave_profile, ff_items, ff_stats, brave_roots, result, out_dir
) -> tuple[str, bool]:
    lines = []
    add = lines.append

    def count_urls(roots):
        return sum(1 for children in roots.values() for node in _walk_merged(children) if node.kind == "url")

    def count_folders(roots):
        return sum(1 for children in roots.values() for node in _walk_merged(children) if node.kind == "folder")

    add("")
    add("=" * 78)
    add("  bookmarks-merge-ff-canonical  -  reporte")
    add("=" * 78)
    add(f"  Firefox : {ff_profile}")
    add(f"  Brave   : {brave_profile}")
    add(f"  Modo    : {'APPLY' if args.apply else 'DRY-RUN (no se toca ningun browser)'}")
    add("")

    add("-- Estado de origen " + "-" * 57)
    add(f"  Firefox : {len(ff_items):5d} bookmarks, {ff_stats['folders']:4d} carpetas, {ff_stats['separators']:4d} separadores")
    add(f"            ({ff_stats['tag_entries']} entradas de etiquetas ignoradas)")
    add(f"  Brave   : {sum(len(c) for c in brave_roots.values()):5d} nodos raíz, {sum(len(c) for c in brave_roots.values())} raíces")

    add("")
    add("-- Duplicados eliminados " + "-" * 51)
    add(f"  Dentro de Firefox       : {len(result['dropped_ff']):5d}")
    add(f"  Brave (ya estaban en FF): {len(result['dropped_brave']):5d}")
    for ex in result["dropped_ff"][:args.max_examples]:
        add(f"     - [ff] {' / '.join(ex['trail'])}  ->  {ex['url']}")
    for ex in result["dropped_brave"][:args.max_examples]:
        add(f"     - [br] {' / '.join(ex['trail'])}  ->  {ex['url']}")

    add("")
    add("-- Exclusivos de Brave (preservados) " + "-" * 39)
    # Contar items agregados bajo "Desde Brave" - aproximado
    add("  (ver artefactos para detalle)")

    add("")
    add("-- Resultado " + "-" * 63)
    total_urls = count_urls(result["merged"])
    total_folders = count_folders(result["merged"])
    add(f"  Bookmarks finales : {total_urls:5d}")
    add(f"  Carpetas finales  : {total_folders:4d}")

    add("")
    add("-- Verificación " + "-" * 60)
    ch_urls, ch_folders = inventory_chromium(out_dir / "brave-bookmarks.json")
    ff_urls, ff_folders = inventory_firefox(out_dir / "firefox-restore.json")
    ok = True
    add(f"  {'OK' if ch_urls == total_urls else 'FAIL'}   Brave   (chromium): {ch_urls:5d} bookmarks (esperado {total_urls}), {ch_folders:4d} carpetas")
    if ch_urls != total_urls: ok = False
    add(f"  {'OK' if ff_urls == total_urls else 'FAIL'}   Firefox (legacy json): {ff_urls:5d} bookmarks (esperado {total_urls}), {ff_folders:4d} carpetas")
    if ff_urls != total_urls: ok = False
    add(f"  {'OK' if ch_urls == ff_urls else 'FAIL'}   los dos artefactos coinciden en cantidad")
    if ch_urls != ff_urls: ok = False

    add("")
    add("-- Siguiente paso " + "-" * 58)
    if not args.apply:
        add("  Nada fue modificado. Cuando revises este reporte y los archivos de out,")
        add("  autorizá la aplicación con:  --apply")
    else:
        add("  Brave   : archivo reemplazado. Abrilo y revisa la barra de marcadores.")
        add("  Firefox : preparado firefox-restore.json - hacé el restore a mano desde la Biblioteca.")

    return "\n".join(lines), ok


def _walk_merged(children: list[Node]):
    for node in children:
        yield node
        if node.kind == "folder":
            yield from _walk_merged(node.children)


def take_snapshot(dest_root: Path, ff_profile: Path, brave_profile: Path) -> Path:
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Converge Firefox y Brave bookmarks usando la distribución de carpetas de Firefox como canónica."
    )
    parser.add_argument("--apply", action="store_true", help="escribe los cambios (sin esto: dry-run)")
    parser.add_argument("--only", choices=("both", "brave", "firefox"), default="both",
                        help="limita la aplicación a un browser")
    parser.add_argument("--force", action="store_true", help="ignora el guard de browsers abiertos")
    parser.add_argument("--restore", metavar="SNAPSHOT_DIR", help="restaura los archivos desde un snapshot y sale")
    parser.add_argument("--ff-profile", help="ruta del perfil de Firefox")
    parser.add_argument("--brave-profile", help="ruta del perfil de Brave")
    parser.add_argument("--attach-folder", default=ATTACH_FOLDER_NAME,
                        help="carpeta (dentro de Favoritos de Firefox) donde colgar 'Desde Brave'")
    parser.add_argument("--out", default=str(SCRIPT_DIR / "out"),
                        help="carpeta de salida para previews y artefactos")
    parser.add_argument("--snapshots", default=str(SCRIPT_DIR / "snapshots"),
                        help="carpeta de snapshots")
    parser.add_argument("--max-examples", type=int, default=15,
                        help="cuantas entradas de ejemplo mostrar en el reporte")
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
                f"(Brave reescribe su archivo al salir y pisaría el cambio)."
            )
        print(f"AVISO: {', '.join(sorted(running))} esta corriendo. "
              f"El análisis es de solo lectura; el estado puede ser el último guardado.",
              file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="bookmarks-merge-ff-") as tmp:
        ff_items, ff_folders, ff_seps, ff_tags = read_firefox(ff_profile, Path(tmp))
    ff_stats = {"folders": ff_folders, "separators": ff_seps, "tag_entries": ff_tags, "orphans": 0}

    # Construir árbol Firefox canónico (Nodes)
    ff_roots = _build_ff_tree(ff_items)

    brave_meta, brave_roots = read_brave(brave_file)

    result = merge(ff_roots, brave_roots, args.attach_folder)

    for root_key, children in result["merged"].items():
        assign_keys(children, root_key)

    chromium_target = out_dir / "brave-bookmarks.json"
    firefox_target = out_dir / "firefox-restore.json"
    emit_chromium(brave_meta, result["merged"], chromium_target)
    emit_firefox(result["merged"], firefox_target)

    report, ok = build_report(
        args, ff_profile, brave_profile, ff_items, ff_stats, brave_roots, result, out_dir
    )
    print(report)
    (out_dir / "report.txt").write_text(report, encoding="utf-8")

    if not ok:
        print("Verificación FALLIDA: no se aplica nada.", file=sys.stderr)
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


def _build_ff_tree(ff_items: list[dict]) -> dict[str, list[Node]]:
    """Convierte la lista plana de items Firefox en árbol de Nodes por raíz."""
    roots = {k: [] for k in ["menu", "toolbar", "unfiled", "tags", "mobile"]}
    root_id_to_key = {v: k for k, v in FF_ROOT_IDS.items()}

    # Mapa global (root_key, path_tuple) -> Node para reutilizar carpetas y detectar ciclos
    folder_nodes = {}

    for item in ff_items:
        root_key = root_id_to_key.get(item["root_id"])
        if not root_key:
            continue
        if root_key == "tags":
            continue

        path_tuple = ()  # tupla inmutable de nombres de carpetas desde la raíz

        for part in item["path"]:
            folder_name = part or "(sin título)"
            path_tuple = path_tuple + (folder_name,)
            
            # Si ya existe esta carpeta en esta ruta exacta, reutilizarla
            node = folder_nodes.get((root_key, path_tuple))
            if node is None:
                # Verificar si agregar esta carpeta crearía un ciclo
                # path_tuple ya incluye el nuevo folder_name; el padre es path_tuple[:-1]
                parent_path = path_tuple[:-1]
                if _would_create_cycle(folder_nodes, root_key, parent_path, folder_name):
                    break  # cortar para evitar ciclo
                node = _folder(folder_name)
                folder_nodes[(root_key, path_tuple)] = node
                # Encontrar padre y agregar
                if parent_path:
                    parent_node = folder_nodes.get((root_key, parent_path))
                    if parent_node:
                        parent_node.children.append(node)
                    else:
                        # Padre no existe (raro), agregar a raíz
                        roots[root_key].append(node)
                else:
                    roots[root_key].append(node)

        # Agregar el bookmark al final del path
        if path_tuple:
            parent_node = folder_nodes.get((root_key, path_tuple))
            if parent_node:
                parent_node.children.append(_bookmark(item["title"] or item["url"], item["url"], item["added_us"]))
            else:
                roots[root_key].append(_bookmark(item["title"] or item["url"], item["url"], item["added_us"]))
        else:
            roots[root_key].append(_bookmark(item["title"] or item["url"], item["url"], item["added_us"]))

    return roots


def _would_create_cycle(folder_nodes: dict, root_key: str, parent_path: tuple, new_folder_name: str) -> bool:
    """Verifica si agregar new_folder_name como hijo de parent_path crearía un ciclo."""
    new_path = parent_path + (new_folder_name,)
    # Si ya existe un nodo con este path exacto, no es ciclo (reutilizar)
    if (root_key, new_path) in folder_nodes:
        return False
    # Verificar si new_folder_name ya aparece en parent_path (ciclo inmediato: A->B->A)
    if new_folder_name in parent_path:
        return True
    # Verificar si el nuevo path sería ancestro de algún nodo existente
    # (es decir, si algún nodo existente tiene path que empieza con new_path)
    for (rk, p), node in folder_nodes.items():
        if rk == root_key and len(p) >= len(new_path) and p[:len(new_path)] == new_path:
            return True
    return False


if __name__ == "__main__":
    sys.exit(main())