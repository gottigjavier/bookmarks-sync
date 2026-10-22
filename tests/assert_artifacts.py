#!/usr/bin/env python3
"""Asserts estructurales de los artefactos que emite `bookmarks-merge.py`.

Dos artefactos, dos contratos:

  firefox-restore.json  (restore manual de Firefox)
      Objeto raiz con `children` = las 4 raices de Firefox. Cada raiz es
      {title, index, guid, children}:
          menu    -> indice 0, guid "menu________"
          toolbar -> indice 1, guid "toolbar_____"
          unfiled -> indice 3, guid "unfiled_____"
          mobile  -> indice 4, guid "mobile______"
      Los hijos de `menu` son marcadores {title, uri, type: "text/x-moz-place"}.

      TRAMPA: el archivo tiene que ser el OBJETO raiz, nunca un array JSON.
      PlacesUtils.unwrapNodes() hace JSON.parse("[" + blob + "]"), asi que un
      array se desempaqueta como [[...]] y el import importa 0 marcadores
      reportando exito.

  <member>-bookmarks.json  (store de la familia Chromium)
      `roots` = {bookmark_bar, other, synced}. La raiz `menu` de Firefox no
      existe en este formato, asi que sus marcadores se proyectan dentro de
      `other` en una carpeta llamada "Menú de marcadores".

Uso:
    python3 tests/assert_artifacts.py --out DIRECTORIO --member brave
    python3 tests/assert_artifacts.py --firefox RUTA --chromium RUTA
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Raices de Firefox que emite la herramienta: (titulo, indice, guid, pseudonimo).
# OJO: el `title` de cada raiz es la clave generica (`menu`, `toolbar`, ...), no
# la etiqueta humana ("Menú de marcadores"): esas se usan solo en el formato
# Chromium, donde la raiz `menu` se proyecta como una carpeta.
FF_ROOTS = (
    ("menu", 0, "menu________", "bookmarksMenuFolder"),
    ("toolbar", 1, "toolbar_____", "toolbarFolder"),
    ("unfiled", 3, "unfiled_____", "unfiledBookmarksFolder"),
    ("mobile", 4, "mobile______", "mobileFolder"),
)

CHROMIUM_ROOTS = ("bookmark_bar", "other", "synced")

FF_MENU_ROOT_TITLE = "menu"
MENU_FOLDER_TITLE = "Menú de marcadores"


class Checker:
    """Acumula fallas y las imprime una por una (mas util que un unico assert)."""

    def __init__(self, label: str):
        self.label = label
        self.failures: list[str] = []

    def ok(self, message: str) -> None:
        print(f"    OK    {message}")

    def fail(self, message: str) -> None:
        print(f"    FALLA {message}")
        self.failures.append(message)

    def check(self, condition: bool, ok_message: str, fail_message: str) -> bool:
        if condition:
            self.ok(ok_message)
        else:
            self.fail(fail_message)
        return bool(condition)


def _load_json(path: Path, checker: Checker) -> dict | list | None:
    if not path.is_file():
        checker.fail(f"no existe el artefacto {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        checker.fail(f"{path} no es JSON valido: {exc}")
        return None


def check_firefox_artifact(path: Path, title: str, url: str) -> list[str]:
    """Verifica el artefacto de restore de Firefox (A3)."""
    checker = Checker(f"firefox-restore.json: {path}")
    document = _load_json(path, checker)
    if document is None:
        return checker.failures

    if not isinstance(document, dict):
        checker.fail("el archivo debe ser el objeto raiz, no un array JSON "
                     "(unwrapNodes parsea '[' + blob + ']')")
        return checker.failures
    checker.ok("el archivo es el objeto raiz (no un array JSON)")

    children = document.get("children")
    if not isinstance(children, list):
        checker.fail("el objeto raiz no tiene 'children' como lista")
        return checker.failures

    by_title = {}
    for node in children:
        if isinstance(node, dict) and isinstance(node.get("title"), str):
            by_title.setdefault(node["title"], node)

    for root_title, index, guid, pseudo in FF_ROOTS:
        node = by_title.get(root_title)
        if node is None:
            checker.fail(f"falta la raiz {root_title!r}")
            continue
        problems = []
        if node.get("index") != index:
            problems.append(f"index {node.get('index')!r} != {index}")
        if node.get("guid") != guid:
            problems.append(f"guid {node.get('guid')!r} != {guid!r}")
        if node.get("root") != pseudo:
            problems.append(f"root {node.get('root')!r} != {pseudo!r}")
        if not isinstance(node.get("children"), list):
            problems.append("sin lista 'children'")
        if problems:
            checker.fail(f"raiz {root_title!r}: " + "; ".join(problems))
        else:
            checker.ok(f"raiz {root_title!r} (index {index}, guid {guid})")

    menu = by_title.get(FF_MENU_ROOT_TITLE)
    if menu is None:
        checker.fail(f"no se puede verificar el marcador: falta la raiz "
                     f"{FF_MENU_ROOT_TITLE!r}")
        return checker.failures

    matches = [
        child for child in menu.get("children") or []
        if isinstance(child, dict)
        and child.get("title") == title
        and child.get("uri") == url
    ]
    if not matches:
        titles = [c.get("title") for c in menu.get("children") or [] if isinstance(c, dict)]
        checker.fail(f"el marcador {title!r} no esta dentro de la raiz "
                     f"{FF_MENU_ROOT_TITLE!r} (hijos: {titles})")
        return checker.failures
    checker.ok(f"el marcador {title!r} esta dentro de la raiz {FF_MENU_ROOT_TITLE!r}")
    if matches[0].get("type") != "text/x-moz-place":
        checker.fail(f"el marcador tiene type {matches[0].get('type')!r}, "
                     "se esperaba 'text/x-moz-place'")
    else:
        checker.ok("el marcador es de tipo 'text/x-moz-place'")
    return checker.failures


def check_chromium_artifact(path: Path, title: str, url: str) -> list[str]:
    """Verifica el store de la familia Chromium (A4)."""
    checker = Checker(f"bookmarks: {path}")
    document = _load_json(path, checker)
    if document is None:
        return checker.failures

    roots = document.get("roots")
    if not isinstance(roots, dict):
        checker.fail("el documento no tiene 'roots' como objeto")
        return checker.failures

    for root_key in CHROMIUM_ROOTS:
        if root_key in roots:
            checker.ok(f"existe la raiz {root_key!r}")
        else:
            checker.fail(f"falta la raiz {root_key!r}")

    other = roots.get("other") or {}
    other_children = other.get("children") or []
    holders = [
        child for child in other_children
        if isinstance(child, dict)
        and child.get("type") == "folder"
        and child.get("name") == MENU_FOLDER_TITLE
    ]
    if not holders:
        names = [c.get("name") for c in other_children if isinstance(c, dict)]
        checker.fail(f"dentro de 'other' no hay una carpeta {MENU_FOLDER_TITLE!r} "
                     f"(hijos: {names})")
        return checker.failures
    checker.ok(f"dentro de 'other' esta la carpeta {MENU_FOLDER_TITLE!r}")

    holder = holders[0]
    matches = [
        child for child in holder.get("children") or []
        if isinstance(child, dict)
        and child.get("name") == title
        and child.get("url") == url
    ]
    if not matches:
        names = [c.get("name") for c in holder.get("children") or [] if isinstance(c, dict)]
        checker.fail(f"la carpeta {MENU_FOLDER_TITLE!r} no contiene el marcador "
                     f"{title!r} (hijos: {names})")
        return checker.failures
    checker.ok(f"la carpeta {MENU_FOLDER_TITLE!r} contiene el marcador {title!r}")
    return checker.failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Asserts estructurales de los artefactos.")
    parser.add_argument("--out", help="directorio con los artefactos")
    parser.add_argument("--member", choices=("brave", "chromium"),
                        help="miembro de la familia Chromium a verificar")
    parser.add_argument("--firefox", help="ruta explícita del artefacto de Firefox")
    parser.add_argument("--chromium", help="ruta explícita del store Chromium")
    parser.add_argument("--title", default="MENU-ROOT-SMOKE-TEST")
    parser.add_argument("--url", default="https://smoke-test.invalid/menu-root")
    args = parser.parse_args()

    failures: list[str] = []

    firefox_path = None
    chromium_path = None
    if args.out:
        out_dir = Path(args.out)
        firefox_path = out_dir / "firefox-restore.json"
        if args.member:
            chromium_path = out_dir / f"{args.member}-bookmarks.json"
    if args.firefox:
        firefox_path = Path(args.firefox)
    if args.chromium:
        chromium_path = Path(args.chromium)

    if firefox_path is None and chromium_path is None:
        print("assert_artifacts: indicar --out o --firefox/--chromium", file=sys.stderr)
        return 2

    if firefox_path is not None:
        print(f"  [assert] artefacto Firefox: {firefox_path}")
        failures += check_firefox_artifact(firefox_path, args.title, args.url)
    if chromium_path is not None:
        print(f"  [assert] artefacto Chromium: {chromium_path}")
        failures += check_chromium_artifact(chromium_path, args.title, args.url)

    if failures:
        print(f"  [assert] {len(failures)} falla(s)")
        return 1
    print("  [assert] todos los asserts ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
