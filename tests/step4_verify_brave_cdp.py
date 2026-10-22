#!/usr/bin/env python3
"""Step 4: verifica que Brave RENDERICE la proyeccion de la raiz `menu`.

Que verifica (A7):
    Other bookmarks -> Menú de marcadores -> MENU-ROOT-SMOKE-TEST

Por que CDP/AX y no el DOM: la pagina `chrome://bookmarks` usa shadow root
cerrado y una lista virtualizada, asi que el DOM no sirve. En cambio
`Accessibility.getFullAXTree` si expone los nombres.

Por que el panel de la derecha no se puede contar: esta virtualizado (se
renderizan ~11-14 filas), asi que las aserciones son por NOMBRE y nunca por
cantidad de filas.

Por que el panel de la izquierda SI hay que expandirlo: no esta virtualizado,
pero las carpetas hijas solo aparecen cuando su padre esta expandido. Para
expandir se resuelve el nodo AX a un elemento del DOM y se despacha un
KeyboardEvent sintetico 'ArrowRight' sobre el <BOOKMARKS-FOLDER-NODE>.

NO usar `Input.dispatchMouseEvent` con `mouseWheel`: cuelga el renderer.

Uso:
    python3 tests/step4_verify_brave_cdp.py [--work-dir RUTA] [--port N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LIB = TESTS_DIR / "lib.sh"

MARKER_TITLE = "MENU-ROOT-SMOKE-TEST"
OTHER_BOOKMARKS = "Other bookmarks"
MENU_FOLDER = "Menú de marcadores"

# Expande la carpeta: busca el <BOOKMARKS-FOLDER-NODE> mas cercano (el elemento
# resuelto puede ser un hijo interno) y le dispara un ArrowRight sintetico.
EXPAND_JS = """
function () {
  let el = this;
  const isFolder = (n) => n && n.tagName === 'BOOKMARKS-FOLDER-NODE';
  let guard = 0;
  while (el && guard++ < 64) {
    if (isFolder(el)) break;
    const root = el.getRootNode ? el.getRootNode() : null;
    if (root && root.host) { el = root.host; continue; }
    el = el.parentElement;
  }
  if (!el || !isFolder(el)) return 'no encontre BOOKMARKS-FOLDER-NODE';
  el.focus();
  el.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'ArrowRight', code: 'ArrowRight', keyCode: 39, which: 39,
    bubbles: true, composed: true, cancelable: true
  }));
  return 'expandido: ' + el.tagName;
}
"""

SCROLL_JS = """
function () {
  let el = this;
  if (el && el.scrollIntoView) { el.scrollIntoView({block: 'center', inline: 'nearest'}); }
  return 'ok';
}
"""


def lib_env() -> dict:
    proc = subprocess.run(
        ["bash", str(LIB), "--print-env"], capture_output=True, text=True, check=True
    )
    env = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


# --- Utilidades de red/procesos ----------------------------------------------


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int) -> tuple[int, str]:
    if port_is_free(preferred):
        return preferred, f"el puerto {preferred} estaba libre"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
    return free, (f"el puerto {preferred} estaba ocupado; se usa el {free} "
                  f"(se puede fijar con SMOKE_CDP_PORT o --port)")


def http_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def wait_for_cdp(port: int, timeout: float = 45.0) -> dict:
    """Espera a que el endpoint de debugging responda."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return http_json(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.4)
    raise SystemExit(f"Brave no respondio en el puerto {port} en {timeout:.0f}s "
                     f"(ultimo error: {last_error})")


def find_brave_binary(env: dict) -> str:
    candidate = env.get("SMOKE_BROWSER_BIN") or ""
    if candidate:
        if not Path(candidate).is_file():
            raise SystemExit(f"SMOKE_BROWSER_BIN no existe: {candidate}")
        return candidate
    for name in ("brave", "brave-browser", "brave-browser-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("No se encontro el binario de Brave en el PATH "
                     "(se puede fijar con SMOKE_BROWSER_BIN).")


# --- Cliente CDP -------------------------------------------------------------


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._mid = 0

    async def send(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._mid += 1
        mid = self._mid
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timeout esperando la respuesta de {method}")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            message = json.loads(raw)
            if message.get("id") != mid:
                continue  # evento del navegador: se ignora
            if "error" in message:
                raise RuntimeError(f"{method} fallo: {message['error']}")
            return message.get("result", {})


def ax_nodes(result: dict) -> list[dict]:
    nodes = []
    for node in result.get("nodes", []):
        name = (node.get("name") or {}).get("value")
        role = (node.get("role") or {}).get("value")
        nodes.append({
            "nodeId": node.get("nodeId"),
            "parentId": node.get("parentId"),
            "backendDOMNodeId": node.get("backendDOMNodeId"),
            "role": role,
            "name": name,
        })
    return nodes


def find_nodes(nodes: list[dict], name: str, role: str | None = None) -> list[dict]:
    return [n for n in nodes
            if n["name"] == name and (role is None or n["role"] == role)]


def ancestor_chain(nodes: list[dict], node: dict) -> list[dict]:
    """Cadena de ancestros via parentId (el AX tree no garantiza orden)."""
    by_id = {n["nodeId"]: n for n in nodes if n["nodeId"] is not None}
    chain, current, seen = [], node, set()
    while current is not None and current.get("nodeId") not in seen:
        seen.add(current.get("nodeId"))
        chain.append(current)
        current = by_id.get(current.get("parentId"))
    return chain


async def call_on_node(cdp: CDP, backend_id: int, function_declaration: str) -> str:
    resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
    object_id = resolved["object"]["objectId"]
    try:
        result = await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": function_declaration,
            "returnByValue": True,
        })
    finally:
        try:
            await cdp.send("Runtime.releaseObject", {"objectId": object_id})
        except Exception:  # noqa: BLE001 - liberar el objeto no es critico
            pass
    return (result.get("result") or {}).get("value", "")


async def expand(cdp: CDP, node: dict) -> str:
    backend_id = node.get("backendDOMNodeId")
    if backend_id is None:
        raise RuntimeError("el nodo AX no tiene backendDOMNodeId")
    return await call_on_node(cdp, backend_id, EXPAND_JS)


async def click(cdp: CDP, node: dict) -> tuple[float, float]:
    backend_id = node.get("backendDOMNodeId")
    if backend_id is None:
        raise RuntimeError("el nodo AX no tiene backendDOMNodeId")

    # Traer el nodo a la vista antes de pedir la caja: si esta fuera del
    # viewport, el clic caeria en otro elemento.
    try:
        await call_on_node(cdp, backend_id, SCROLL_JS)
        await asyncio.sleep(0.4)
    except Exception as exc:  # noqa: BLE001 - el scroll es una ayuda, no un requisito
        print(f"    (scroll omitido: {exc})")

    box = await cdp.send("DOM.getBoxModel", {"backendNodeId": backend_id})
    quad = (box.get("model") or {}).get("content") or []
    if len(quad) < 8:
        raise RuntimeError(f"DOM.getBoxModel devolvio una caja vacia: {box}")
    xs, ys = quad[0::2], quad[1::2]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2

    # OJO: nunca 'mouseWheel' con Input.dispatchMouseEvent (cuelga el renderer).
    for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
        params = {"type": event_type, "x": cx, "y": cy, "button": "left", "clickCount": 1}
        if event_type == "mousePressed":
            params["buttons"] = 1
        if event_type == "mouseReleased":
            params["buttons"] = 0
        await cdp.send("Input.dispatchMouseEvent", params, timeout=15)
    return cx, cy


# --- Verificacion ------------------------------------------------------------


class Report:
    def __init__(self) -> None:
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


async def verify(cdp: CDP, report: Report) -> None:
    await cdp.send("Page.navigate", {"url": "chrome://bookmarks"})
    await asyncio.sleep(3.5)

    tree = await cdp.send("Accessibility.getFullAXTree", timeout=40)
    nodes = ax_nodes(tree)
    print(f"    AX tree: {len(nodes)} nodos")

    # (a) existe el treeitem 'Other bookmarks'
    others = find_nodes(nodes, OTHER_BOOKMARKS, "treeitem")
    if not report.check(
        bool(others),
        f"existe el treeitem {OTHER_BOOKMARKS!r} (a)",
        f"no existe el treeitem {OTHER_BOOKMARKS!r} en el panel de carpetas",
    ):
        return

    # (b) 'Menú de marcadores' cuelga de 'Other bookmarks'
    holders = find_nodes(nodes, MENU_FOLDER, "treeitem")
    expanded = False
    if not holders:
        # El panel de la izquierda no esta virtualizado, pero las carpetas hijas
        # solo existen cuando el padre esta expandido: hay que expandirlo.
        print(f"    {MENU_FOLDER!r} no aparece todavia: se expande {OTHER_BOOKMARKS!r}")
        result = await expand(cdp, others[0])
        print(f"    expansion: {result}")
        expanded = True
        await asyncio.sleep(2.5)
        tree = await cdp.send("Accessibility.getFullAXTree", timeout=40)
        nodes = ax_nodes(tree)
        holders = find_nodes(nodes, MENU_FOLDER, "treeitem")
    else:
        # (d) idempotencia: si ya estaba, no hace falta expandir.
        print(f"    {MENU_FOLDER!r} ya estaba en el AX tree sin expandir (idempotente)")

    if not report.check(
        bool(holders),
        f"existe el treeitem {MENU_FOLDER!r} (b)",
        f"no se encontro el treeitem {MENU_FOLDER!r} "
        f"(expandido={expanded}): el AX tree no lo expone",
    ):
        return

    chain = ancestor_chain(nodes, holders[0])
    chain_labels = [f"{n['role']}:{n['name']}" for n in chain]
    print(f"    cadena de ancestros: {' <- '.join(chain_labels)}")
    report.check(
        any(n["role"] == "treeitem" and n["name"] == OTHER_BOOKMARKS for n in chain),
        f"la cadena de ancestros de {MENU_FOLDER!r} contiene {OTHER_BOOKMARKS!r} (b)",
        f"la cadena de ancestros de {MENU_FOLDER!r} NO contiene {OTHER_BOOKMARKS!r}: "
        f"{chain_labels}",
    )

    # (c) clic en la carpeta: el panel derecho muestra su contenido
    cx, cy = await click(cdp, holders[0])
    print(f"    clic en ({cx:.0f},{cy:.0f})")
    await asyncio.sleep(2.5)

    tree = await cdp.send("Accessibility.getFullAXTree", timeout=40)
    nodes = ax_nodes(tree)
    rows = [n for n in nodes if n["role"] == "row"]
    row_names = [n["name"] for n in rows]
    print(f"    panel derecho: {len(rows)} fila(s) renderizada(s)")
    marker_rows = [n for n in rows if n["name"] == MARKER_TITLE]
    if marker_rows:
        report.ok(f"el panel derecho incluye la fila {MARKER_TITLE!r} (c)")
        return
    other_roles = sorted({n["role"] for n in nodes if n["name"] == MARKER_TITLE})
    report.fail(
        f"el panel derecho NO incluye {MARKER_TITLE!r}. Filas: {row_names}. "
        f"El nombre aparece con otros roles: {other_roles}"
    )


async def run(args, env: dict) -> int:
    work_dir = Path(env["SMOKE_WORK_DIR"])
    brave_bin = find_brave_binary(env)
    report = Report()

    preferred = args.port or int(env.get("SMOKE_CDP_PORT") or 9222)
    port, note = pick_port(preferred)
    print(f"[step4] binario: {brave_bin}")
    print(f"[step4] puerto CDP: {port} ({note})")

    # El perfil de Brave del work dir tiene SOLO Default/Bookmarks: Brave igual
    # carga el store (verificado) y no toca el perfil real.
    user_data_dir = work_dir / "brave"
    log_path = work_dir / "brave-cdp.log"
    cmd = [
        brave_bin,
        "--headless",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    print(f"[step4] $ {' '.join(cmd)}")

    log_file = open(log_path, "wb")
    process = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True
    )
    try:
        version = wait_for_cdp(port)
        print(f"[step4] DevTools listo: {version.get('Browser')} "
              f"({version.get('webSocketDebuggerUrl')})")

        targets = http_json(f"http://127.0.0.1:{port}/json/list")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            print(f"    FALLA no hay ningun target de tipo 'page'")
            report.fail("no hay ningun target de tipo 'page'")
            return 1
        print(f"[step4] target: {page.get('url')} ({page.get('webSocketDebuggerUrl')})")

        import websockets  # import perezoso: solo hace falta en este paso

        async with websockets.connect(
            page["webSocketDebuggerUrl"], max_size=None, open_timeout=20
        ) as websocket:
            cdp = CDP(websocket)
            await verify(cdp, report)
    finally:
        log_file.close()
        _terminate(process)

    if report.failures:
        print(f"\n[step4] {len(report.failures)} falla(s). Log de Brave: {log_path}")
        return 1
    print("\n[step4] OK: Brave renderiza "
          f"{OTHER_BOOKMARKS} -> {MENU_FOLDER} -> {MARKER_TITLE}")
    return 0


def _terminate(process: subprocess.Popen) -> None:
    """Cierra Brave: primero SIGTERM al grupo de procesos, luego SIGKILL."""
    for sig, wait in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
        if process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica el render en Brave via CDP/AX.")
    parser.add_argument("--work-dir", help="directorio de trabajo (default: $SMOKE_WORK_DIR)")
    parser.add_argument("--port", type=int, help="puerto CDP (default: $SMOKE_CDP_PORT o 9222)")
    args = parser.parse_args()
    if args.work_dir:
        os.environ["SMOKE_WORK_DIR"] = args.work_dir

    env = lib_env()
    print(f"[step4] work dir: {env['SMOKE_WORK_DIR']}")
    return asyncio.run(run(args, env))


if __name__ == "__main__":
    sys.exit(main())
