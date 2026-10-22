#!/usr/bin/env python3
"""Step 1: prepara los fixtures minimos del harness de humo.

Que hace:
  1. Copia SOLO los stores de bookmarks de los perfiles reales (solo lectura):
       Firefox   -> $SMOKE_WORK_DIR/firefox/places.sqlite{,-wal,-shm}
       Brave     -> $SMOKE_WORK_DIR/brave/Default/Bookmarks
       Chromium  -> $SMOKE_WORK_DIR/chromium/Default/Bookmarks
  2. Inserta el marcador de prueba dentro de la raiz `menu` de Firefox
     (parent id = 2) en la COPIA, nunca en el perfil real.

Por que NO se copia el perfil completo: el perfil real de Firefox contiene
`logins.json`, `key4.db`, `cookies.sqlite` y `Login Data`. Copiar eso al disco
de trabajo es dejar material de credenciales tirado sin necesidad: la
herramienta solo lee `places.sqlite*` y `Bookmarks`.

Uso:
    python3 tests/step1_prepare_fixtures.py [--work-dir RUTA]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LIB = TESTS_DIR / "lib.sh"

# --- Constantes del fixture ---------------------------------------------------

FF_MENU_PARENT_ID = 2  # id de la raiz `menu` (Menú de marcadores) en moz_bookmarks
FF_ORIGIN_HOST = "smoke-test.invalid"
FF_ORIGIN_PREFIX = "https://"
FF_PLACE_REV_HOST = "invalid.test-smoke"  # rev_host = host al reves
FF_PLACE_GUID = "sm0kePlace01"  # 12 chars del alfabeto [A-Za-z0-9_-]
FF_BOOKMARK_GUID = "sm0keMark001"


# --- Puente con lib.sh --------------------------------------------------------
# Bash resuelve rutas y guards; Python no repite esa logica para que no haya
# dos verdades que puedan divergir.


def lib_env() -> dict:
    proc = subprocess.run(
        ["bash", str(LIB), "--print-env"],
        capture_output=True,
        text=True,
        check=True,
    )
    env = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def guard(action: str, path: Path) -> None:
    """Ejecuta uno de los guards de lib.sh (invariantes 1 y 2)."""
    proc = subprocess.run(
        ["bash", str(LIB), action, str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.strip() + "\n")
        raise SystemExit(1)


def guard_writable(path: Path) -> None:
    guard("--assert-not-real-profile", path)
    guard("--assert-under-work", path)


# --- Utilidades ---------------------------------------------------------------


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_store(source: Path, target: Path) -> None:
    """Copia un store aplicando los guards de seguridad antes de escribir."""
    guard_writable(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


# --- Insercion del marcador ---------------------------------------------------


def insert_marker(db_path: Path, title: str, url: str) -> dict:
    """Inserta el marcador de humo en la raiz `menu` de la copia de Firefox.

    La cadena es de tres inserts porque `moz_bookmarks.fk` apunta a
    `moz_places.id` y `moz_places.origin_id` a `moz_origins.id`. Se abre la
    base con `PRAGMA foreign_keys=ON` para que SQLite valide la cadena, y al
    final `PRAGMA foreign_key_check` debe dar cero filas.
    """
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")

        # Idempotencia: si el paso se re-ejecuta sobre la misma copia, se borra
        # el marcador anterior (hay indices UNIQUE sobre guid) y se reinserta.
        con.execute("DELETE FROM moz_bookmarks WHERE guid = ?", (FF_BOOKMARK_GUID,))
        con.execute("DELETE FROM moz_places WHERE guid = ?", (FF_PLACE_GUID,))

        con.execute(
            "DELETE FROM moz_origins WHERE host = ? AND prefix = ?",
            (FF_ORIGIN_HOST, FF_ORIGIN_PREFIX),
        )

        origin_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM moz_origins").fetchone()[0]
        con.execute(
            "INSERT INTO moz_origins (id, prefix, host, frecency, recalc_frecency, "
            "recalc_alt_frecency) VALUES (?, ?, ?, 0, 0, 0)",
            (origin_id, FF_ORIGIN_PREFIX, FF_ORIGIN_HOST),
        )

        place_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM moz_places").fetchone()[0]
        con.execute(
            "INSERT INTO moz_places (id, url, title, rev_host, visit_count, hidden, typed, "
            "frecency, guid, foreign_count, url_hash, description, preview_image_url, "
            "origin_id) VALUES (?, ?, ?, ?, 0, 0, 0, -1, ?, 0, 0, '', '', ?)",
            (place_id, url, title, FF_PLACE_REV_HOST, FF_PLACE_GUID, origin_id),
        )

        bookmark_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM moz_bookmarks").fetchone()[0]
        position = con.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
            (FF_MENU_PARENT_ID,),
        ).fetchone()[0]
        now_us = int(time.time() * 1_000_000)  # Firefox guarda microsegundos desde epoch
        con.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, "
            "lastModified, guid, syncStatus, syncChangeCounter) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 1, 1)",
            (bookmark_id, place_id, FF_MENU_PARENT_ID, position, title, now_us, now_us,
             FF_BOOKMARK_GUID),
        )

        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SystemExit(f"PRAGMA foreign_key_check devolvio {len(violations)} filas: "
                             f"{violations[:5]}")
        con.commit()

        # Los datos deben quedar en el archivo principal: la herramienta copia el
        # trio places.sqlite{,-wal,-shm} y abre la copia en solo lectura.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()

        row = con.execute(
            "SELECT b.id, b.parent, b.position, b.title, p.url, p.id, o.id "
            "FROM moz_bookmarks b JOIN moz_places p ON p.id = b.fk "
            "JOIN moz_origins o ON o.id = p.origin_id WHERE b.guid = ?",
            (FF_BOOKMARK_GUID,),
        ).fetchone()
        if row is None:
            raise SystemExit("El marcador no se pudo leer despues de insertarlo.")
        return {
            "bookmark_id": row[0],
            "parent": row[1],
            "position": row[2],
            "title": row[3],
            "url": row[4],
            "place_id": row[5],
            "origin_id": row[6],
        }
    finally:
        con.close()


def menu_children_count(db_path: Path) -> int:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM moz_bookmarks WHERE parent = ? AND type = 1",
            (FF_MENU_PARENT_ID,),
        ).fetchone()[0]
    finally:
        con.close()


# --- main ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara los fixtures del smoke test.")
    parser.add_argument("--work-dir", help="directorio de trabajo (default: $SMOKE_WORK_DIR)")
    args = parser.parse_args()
    if args.work_dir:
        import os

        os.environ["SMOKE_WORK_DIR"] = args.work_dir

    env = lib_env()
    work_dir = Path(env["SMOKE_WORK_DIR"])
    title = env["SMOKE_MARKER_TITLE"]
    url = env["SMOKE_MARKER_URL"]

    print(f"[step1] work dir: {work_dir}")

    ff_real = Path(env["SMOKE_FF_PROFILE"]) if env.get("SMOKE_FF_PROFILE") else None
    brave_real = Path(env["SMOKE_BRAVE_PROFILE"]) if env.get("SMOKE_BRAVE_PROFILE") else None
    chromium_real = Path(env["SMOKE_CHROMIUM_PROFILE"]) if env.get("SMOKE_CHROMIUM_PROFILE") else None

    ff_dir = Path(env["SMOKE_FIREFOX_DIR"])
    brave_store = Path(env["SMOKE_BRAVE_STORE"])
    chromium_store = Path(env["SMOKE_CHROMIUM_STORE"])

    # --- Firefox: el trio places.sqlite{,-wal,-shm} ---------------------------
    if ff_real is None:
        print("[step1] AVISO: no se pudo resolver el perfil real de Firefox; "
              "el par firefox,brave queda sin fixture de Firefox.", file=sys.stderr)
        ff_copies = []
    else:
        print(f"[step1] perfil real de Firefox (solo lectura): {ff_real}")
        ff_copies = []
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(ff_real / "places.sqlite") + suffix)
            if not source.is_file():
                continue
            target = ff_dir / f"places.sqlite{suffix}"
            copy_store(source, target)
            ff_copies.append(target)
            print(f"[step1]   copiado {source} -> {target} "
                  f"({target.stat().st_size} bytes)")
        if not ff_copies:
            raise SystemExit(f"No se encontro places.sqlite en {ff_real}")

    # --- Chromium family: solo el archivo Bookmarks ---------------------------
    for label, real, target in (
        ("Brave", brave_real, brave_store),
        ("Chromium", chromium_real, chromium_store),
    ):
        if real is None:
            print(f"[step1] AVISO: no se pudo resolver el perfil real de {label}; "
                  f"el par con {label.lower()} queda sin fixture.", file=sys.stderr)
            continue
        source = real / "Bookmarks"
        if not source.is_file():
            print(f"[step1] AVISO: {label} no tiene Bookmarks en {real}; par omitido.",
                  file=sys.stderr)
            continue
        print(f"[step1] perfil real de {label} (solo lectura): {real}")
        copy_store(source, target)
        print(f"[step1]   copiado {source} -> {target} ({target.stat().st_size} bytes)")

    # --- Marcador en la raiz `menu` de la copia de Firefox --------------------
    ff_store = Path(env["SMOKE_FF_STORE"])
    if not ff_store.is_file():
        raise SystemExit(f"No se pudo preparar la copia de Firefox: falta {ff_store}")

    before = menu_children_count(ff_store)
    info = insert_marker(ff_store, title, url)
    after = menu_children_count(ff_store)

    print(f"[step1] marcador insertado en la raiz `menu` de la COPIA de Firefox:")
    print(f"[step1]   moz_origins.id    = {info['origin_id']}")
    print(f"[step1]   moz_places.id     = {info['place_id']}")
    print(f"[step1]   moz_bookmarks.id  = {info['bookmark_id']} "
          f"(parent={info['parent']}, position={info['position']})")
    print(f"[step1]   titulo = {info['title']!r}")
    print(f"[step1]   url    = {info['url']!r}")
    print(f"[step1]   hijos de la raiz menu en la copia: {before} -> {after}")
    print(f"[step1]   sha256 places.sqlite = {sha256_of(ff_store)}")
    print("[step1] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
