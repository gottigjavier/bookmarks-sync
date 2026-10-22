#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_smoke_test.sh - orquestador del harness de humo de IA-sync.
#
# Corre los cuatro pasos en orden y corta en el primero que falle:
#   1. step1_prepare_fixtures.py   -> copias minimas + marcador en la raiz menu
#   2. step2_generate_artifacts.sh -> dry-run de los pares + asserts del reporte
#   3. step3_apply_and_verify.py   -> --apply sobre las copias + asserts del store
#   4. step4_verify_brave_cdp.py   -> Brave renderiza la proyeccion (CDP/AX)
#
# Al principio registra el estado de los perfiles REALES (sha256 de los stores y
# cantidad de hijos de la raiz `menu`) y al final lo compara: el harness no
# puede haber modificado los perfiles del usuario (A8).
#
# Uso:
#   bash tests/run_smoke_test.sh [--keep] [--work-dir RUTA]
# ---------------------------------------------------------------------------
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Si el usuario no pasa --work-dir, el work dir tiene que estar bajo /tmp para
# que el `rm -rf` de limpieza sea aceptable.
WORK_DIR_EXPLICIT=0
WORK_DIR_FROM_ENV="${SMOKE_WORK_DIR:-}"
KEEP=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep)
            KEEP=1
            shift
            ;;
        --work-dir)
            SMOKE_WORK_DIR="$2"
            WORK_DIR_EXPLICIT=1
            shift 2
            ;;
        -h|--help)
            printf 'Uso: run_smoke_test.sh [--keep] [--work-dir RUTA]\n'
            exit 0
            ;;
        *)
            printf 'Uso: run_smoke_test.sh [--keep] [--work-dir RUTA]\n' >&2
            exit 2
            ;;
    esac
done
if [ -n "$WORK_DIR_FROM_ENV" ]; then
    WORK_DIR_EXPLICIT=1
fi
export SMOKE_WORK_DIR

# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"
smoke_resolve_profiles

BASELINE_DIR=""
BASELINE_MENU=""

# --- Utilidades del orquestador ---------------------------------------------

smoke_assert_cleanable() {
    # `rm -rf` solo puede tocar el work dir, y nunca una ruta general.
    local target="$1"
    local resolved
    resolved="$(smoke_realpath "$target")"
    case "$resolved" in
        /|/tmp|/var/tmp|/usr|/etc|/opt|/home|"$HOME")
            smoke_fail "refuso limpiar '$resolved': ruta demasiado general."
            ;;
    esac
    if [ "$WORK_DIR_EXPLICIT" -eq 0 ]; then
        case "$resolved" in
            /tmp/*|/var/tmp/*) : ;;
            *)
                smoke_fail "refuso limpiar '$resolved': sin --work-dir explicito solo se "
                smoke_fail "limpian rutas bajo /tmp."
                ;;
        esac
    fi
}

real_store_paths() {
    # Los stores reales que existen, uno por linea: "<browser> <ruta>".
    local path
    for spec in \
        "firefox:${SMOKE_FF_PROFILE:-}/places.sqlite" \
        "brave:${SMOKE_BRAVE_PROFILE:-}/Bookmarks" \
        "chromium:${SMOKE_CHROMIUM_PROFILE:-}/Bookmarks"
    do
        path="${spec#*:}"
        if [ -n "${spec%%:*}" ] && [ -f "$path" ]; then
            printf '%s %s\n' "${spec%%:*}" "$path"
        fi
    done
}

real_menu_children() {
    # Cantidad de hijos de la raiz `menu` (id=2) del perfil real de Firefox.
    # Se lee sobre una COPIA del trio places.sqlite*: abrir el archivo real en
    # modo escritura mientras Firefox corre podria interferir con el.
    local profile="$1"
    if [ -z "$profile" ] || [ ! -f "$profile/places.sqlite" ]; then
        printf '(sin perfil)\n'
        return 0
    fi
    SMOKE_FF_PROFILE_PATH="$profile" python3 - <<'PY'
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

profile = Path(os.environ["SMOKE_FF_PROFILE_PATH"])
with tempfile.TemporaryDirectory(prefix="smoke-menu-count-") as tmp:
    copy = Path(tmp) / "places.sqlite"
    shutil.copy2(profile / "places.sqlite", copy)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(profile / "places.sqlite") + suffix)
        if extra.is_file():
            shutil.copy2(extra, str(copy) + suffix)
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        print(con.execute(
            "SELECT COUNT(*) FROM moz_bookmarks WHERE parent = 2"
        ).fetchone()[0])
    finally:
        con.close()
PY
}

record_baseline() {
    BASELINE_DIR="$SMOKE_WORK_DIR/baseline"
    mkdir -p "$BASELINE_DIR"
    real_store_paths | while read -r browser path; do
        printf '%s %s %s\n' "$browser" "$(smoke_sha256 "$path")" "$path"
    done >"$BASELINE_DIR/real-stores.sha256"
    BASELINE_MENU="$(real_menu_children "${SMOKE_FF_PROFILE:-}")"
    printf '%s\n' "$BASELINE_MENU" >"$BASELINE_DIR/real-menu-children"
    smoke_log "baseline de los perfiles reales:"
    sed 's/^/    /' "$BASELINE_DIR/real-stores.sha256"
    smoke_log "hijos de la raiz 'menu' real: $BASELINE_MENU"
}

verify_baseline() {
    # A8: los perfiles reales no pueden haber cambiado por el harness.
    #
    # Salvedad: si el browser del usuario esta corriendo, el store puede
    # cambiar SOLO por el navegador (el harness abre los perfiles reales en
    # solo lectura y nunca escribe en ellos). En ese caso se avisa en vez de
    # fallar: un falso positivo seria peor que un falso negativo.
    local failures=0
    local browser before_hash before_path now_hash running

    smoke_heading "A8: los perfiles reales quedaron intactos"

    while read -r browser before_hash before_path; do
        [ -n "$browser" ] || continue
        if [ ! -f "$before_path" ]; then
            smoke_fail "el store real de $browser desaparecio: $before_path"
            failures=1
            continue
        fi
        now_hash="$(smoke_sha256 "$before_path")"
        if [ "$now_hash" = "$before_hash" ]; then
            smoke_ok "$browser: sha256 sin cambios ($before_path)"
            continue
        fi
        running=""
        if smoke_browser_running "$browser"; then
            running=" (el browser esta corriendo: el cambio puede ser suyo)"
        fi
        smoke_fail "$browser: el store real CAMBIO$running
      antes=$before_hash
      despues=$now_hash"
        failures=1
    done <"$BASELINE_DIR/real-stores.sha256"

    local after_menu
    after_menu="$(real_menu_children "${SMOKE_FF_PROFILE:-}")"
    if [ "$after_menu" = "$BASELINE_MENU" ]; then
        smoke_ok "la raiz 'menu' real sigue con $after_menu hijo(s) (antes: $BASELINE_MENU)"
    else
        smoke_fail "la raiz 'menu' real cambio: antes $BASELINE_MENU, despues $after_menu"
        failures=1
    fi
    return "$failures"
}

run_step() {
    local number="$1" label="$2"
    shift 2
    smoke_heading "Paso $number: $label"
    local rc=0
    "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
        printf '\n' >&2
        smoke_fail "FALLO en el paso $number ($label): exit $rc. No se sigue con los pasos siguientes."
    fi
    smoke_ok "paso $number ($label) OK"
}

print_summary() {
    smoke_heading "Resumen"
    printf '  Work dir   : %s\n' "$SMOKE_WORK_DIR"
    printf '  Herramienta: %s\n' "$SMOKE_TOOL"
    printf '  Firefox real (solo lectura): %s\n' "${SMOKE_FF_PROFILE:-<sin resolver>}"
    printf '  Brave real   (solo lectura): %s\n' "${SMOKE_BRAVE_PROFILE:-<sin resolver>}"
    printf '  Chromium real(solo lectura): %s\n' "${SMOKE_CHROMIUM_PROFILE:-<sin resolver>}"
    printf '\n  Artefactos generados:\n'
    for file in \
        "$SMOKE_OUT_FIREFOX_BRAVE/firefox-restore.json" \
        "$SMOKE_OUT_FIREFOX_BRAVE/brave-bookmarks.json" \
        "$SMOKE_OUT_FIREFOX_BRAVE/report.txt" \
        "$SMOKE_OUT_FIREFOX_CHROMIUM/firefox-restore.json" \
        "$SMOKE_OUT_FIREFOX_CHROMIUM/chromium-bookmarks.json" \
        "$SMOKE_OUT_FIREFOX_CHROMIUM/report.txt"
    do
        if [ -f "$file" ]; then
            printf '    %s\n' "$file"
        fi
    done
    printf '\n  Snapshots:\n'
    if [ -d "$SMOKE_SNAPSHOTS_DIR" ]; then
        find "$SMOKE_SNAPSHOTS_DIR" -mindepth 1 -maxdepth 1 -type d -print | sed 's/^/    /'
    else
        printf '    (ninguno)\n'
    fi
}

print_cleanup_notice() {
    printf '\n'
    printf '  AVISO: el work dir contiene COPIAS de los stores de marcadores de los\n'
    printf '  navegadores (places.sqlite* y Bookmarks) y, en `brave/`, el perfil\n'
    printf '  PROPIO que crea Brave headless en el paso 4 (Local State, Default,\n'
    printf '  cachés). NO contiene credenciales del usuario: nunca se copian\n'
    printf '  logins.json, key4.db, cookies.sqlite ni Login Data.\n'
    printf '  Para borrarlo:\n'
    printf '      rm -rf %s\n' "$SMOKE_WORK_DIR"
}

# --- main ---------------------------------------------------------------------

printf '\033[1m### Smoke test IA-sync: raiz menu de Firefox proyectada en Chromium ###\033[0m\n'
printf 'work dir: %s\n' "$SMOKE_WORK_DIR"

smoke_heading "Preparacion"
smoke_assert_cleanable "$SMOKE_WORK_DIR"
if [ "$KEEP" -eq 1 ]; then
    smoke_log "--keep: no se limpia el work dir."
else
    smoke_log "limpio el work dir para que la corrida sea reproducible..."
    rm -rf -- "$SMOKE_WORK_DIR"
fi
mkdir -p "$SMOKE_WORK_DIR"
smoke_assert_under_work "$SMOKE_WORK_DIR"
smoke_assert_not_real_profile "$SMOKE_WORK_DIR"
smoke_ok "work dir listo"

record_baseline

run_step 1 "preparar fixtures minimos + marcador en la raiz menu" \
    python3 "$TESTS_DIR/step1_prepare_fixtures.py" --work-dir "$SMOKE_WORK_DIR"

run_step 2 "generar artefactos (dry-run) y verificar el reporte" \
    bash "$TESTS_DIR/step2_generate_artifacts.sh" --work-dir "$SMOKE_WORK_DIR"

run_step 3 "aplicar sobre las copias y verificar el store escrito" \
    python3 "$TESTS_DIR/step3_apply_and_verify.py" --work-dir "$SMOKE_WORK_DIR"

run_step 4 "verificar que Brave renderiza la proyeccion (CDP/AX)" \
    python3 "$TESTS_DIR/step4_verify_brave_cdp.py" --work-dir "$SMOKE_WORK_DIR"

if ! verify_baseline; then
    smoke_fail "A8 fallo: los perfiles reales fueron modificados."
fi

print_summary

smoke_heading "Pasos manuales para Firefox (A9)"
cat "$TESTS_DIR/FIREFOX_MANUAL_STEPS.md"

print_cleanup_notice

printf '\n\033[32m### SMOKE TEST OK ###\033[0m\n'
