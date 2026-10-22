#!/usr/bin/env python3
"""Step 3: aplica los artefactos sobre las COPIAS y verifica el store escrito.

Para cada par (firefox,brave) y (firefox,chromium):

  1. sha256 de la copia de Firefox (places.sqlite, -wal, -shm) ANTES del apply.
  2. `bookmarks-merge.py --apply` sobre las copias del work dir.
  3. A6: la copia de Firefox quedo INTACTA (la herramienta nunca auto-escribe
     Firefox: su artefacto es para import manual).
  4. A5: el store del miembro de la familia Chromium es byte-identico al
     artefacto (sha256) y contiene la proyeccion `other` -> "Menú de marcadores"
     -> marcador.
  5. Se genero un snapshot dentro del work dir.

Por que `--force`: la herramienta aborta el `--apply` si hay un browser del par
corriendo. El harness opera sobre COPIAS, asi que el estado del browser real
del usuario es irrelevante: si esta abierto, al salir reescribiria su store
propio, no la copia. `--force` solo desactiva ese guard.

Uso:
    python3 tests/step3_apply_and_verify.py [--work-dir RUTA]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
LIB = TESTS_DIR / "lib.sh"
sys.path.insert(0, str(TESTS_DIR))
# No dejar `__pycache__` en el arbol del repo al importar assert_artifacts.
sys.dont_write_bytecode = True

import assert_artifacts  # noqa: E402  (necesita TESTS_DIR en sys.path)


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


def guard(action: str, path: Path) -> None:
    proc = subprocess.run(["bash", str(LIB), action, str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.strip() + "\n")
        raise SystemExit(1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def firefox_store_hashes(ff_dir: Path) -> dict:
    hashes = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(ff_dir / "places.sqlite") + suffix)
        if path.is_file():
            hashes[path.name] = sha256_of(path)
    return hashes


def apply_pair(member: str, env: dict, report: Report) -> None:
    print(f"\n  --- par firefox,{member} ---")
    work_dir = Path(env["SMOKE_WORK_DIR"])
    out_dir = Path(env[f"SMOKE_OUT_FIREFOX_{member.upper()}"])
    profile_dir = Path(env[f"SMOKE_{member.upper()}_DIR"])
    store = Path(env[f"SMOKE_{member.upper()}_STORE"])
    ff_dir = Path(env["SMOKE_FIREFOX_DIR"])
    artifact = out_dir / f"{member}-bookmarks.json"

    # Guardas: todo lo que se va a escribir (o sobre lo que se va a escribir)
    # tiene que estar dentro del work dir y fuera de los perfiles reales.
    for path in (out_dir, store):
        guard("--assert-not-real-profile", path)
        guard("--assert-under-work", path)

    if not store.is_file():
        report.fail(f"no existe la copia del store de {member}: {store}")
        return
    if not artifact.is_file():
        report.fail(f"no existe el artefacto {artifact}; corré step2 primero")
        return

    ff_before = firefox_store_hashes(ff_dir)
    store_before = sha256_of(store)
    print(f"  [step3] sha256 places.sqlite (antes) = "
          f"{ff_before.get('places.sqlite', 'n/d')}")
    print(f"  [step3] sha256 {member}/Bookmarks (antes) = {store_before}")

    log_path = out_dir / "apply.log"
    cmd = [
        sys.executable, env["SMOKE_TOOL"],
        "--pair", f"firefox,{member}",
        "--canonical", "firefox",
        "--ff-profile", str(ff_dir),
        f"--{member}-profile", str(profile_dir),
        "--out", str(out_dir),
        "--snapshots", env["SMOKE_SNAPSHOTS_DIR"],
        "--apply",
        "--force",  # ver la docstring: el guard de browsers abiertos no aplica a las copias
    ]
    print(f"  [step3] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        report.fail(f"el --apply del par firefox,{member} termino con exit "
                    f"{proc.returncode} (se esperaba 0). Log: {log_path}")
        return
    report.ok(f"--apply del par firefox,{member} termino con exit 0")

    # A6: la copia de Firefox quedo intacta.
    ff_after = firefox_store_hashes(ff_dir)
    report.check(
        ff_before == ff_after,
        f"la copia de Firefox quedo intacta (A6): {len(ff_after)} archivo(s) sin cambios",
        "la copia de Firefox CAMBIO: la herramienta nunca debe auto-escribir Firefox. "
        f"antes={ff_before} despues={ff_after}",
    )

    # A5: el store escrito es byte-identico al artefacto.
    artifact_hash = sha256_of(artifact)
    store_after = sha256_of(store)
    report.check(
        store_after == artifact_hash,
        f"el store de {member} es byte-identico al artefacto (A5): {store_after}",
        f"el store de {member} NO coincide con el artefacto: store={store_after} "
        f"artefacto={artifact_hash}",
    )

    # A4 sobre el store YA ESCRITO (no solo sobre el artefacto).
    failures = assert_artifacts.check_chromium_artifact(
        store, env["SMOKE_MARKER_TITLE"], env["SMOKE_MARKER_URL"]
    )
    if failures:
        for failure in failures:
            report.fail(f"store de {member}: {failure}")
    else:
        report.ok(f"el store de {member} contiene la proyeccion "
                  f"other -> {env['SMOKE_MENU_FOLDER_TITLE']} -> {env['SMOKE_MARKER_TITLE']}")

    # Snapshot: tiene que estar dentro del work dir.
    snapshots = Path(env["SMOKE_SNAPSHOTS_DIR"])
    stamps = sorted(p for p in snapshots.glob("*") if p.is_dir()) if snapshots.is_dir() else []
    report.check(
        bool(stamps),
        f"se genero un snapshot en {stamps[-1] if stamps else snapshots}",
        f"no se encontro ningun snapshot en {snapshots}",
    )
    if stamps:
        latest = stamps[-1]
        members = sorted(p.name for p in latest.iterdir() if p.is_dir())
        report.check(
            "firefox" in members,
            f"el snapshot incluye los stores de Firefox: {members}",
            f"el snapshot no incluye los stores de Firefox: {members}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica los artefactos y verifica el store.")
    parser.add_argument("--work-dir", help="directorio de trabajo (default: $SMOKE_WORK_DIR)")
    args = parser.parse_args()
    if args.work_dir:
        os.environ["SMOKE_WORK_DIR"] = args.work_dir

    env = lib_env()
    print(f"[step3] work dir: {env['SMOKE_WORK_DIR']}")

    report = Report()
    for member in ("brave", "chromium"):
        profile = env.get(f"SMOKE_{member.upper()}_PROFILE")
        store = Path(env[f"SMOKE_{member.upper()}_STORE"])
        if not profile or not store.is_file():
            print(f"[step3] AVISO: sin fixture de {member}: par omitido.", file=sys.stderr)
            continue
        apply_pair(member, env, report)

    if report.failures:
        print(f"\n[step3] {len(report.failures)} falla(s)")
        return 1
    print("\n[step3] OK: todos los asserts del apply pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
