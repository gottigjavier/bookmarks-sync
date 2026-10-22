#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# step2_generate_artifacts.sh - dry-run de los pares + asserts del reporte.
#
# Para cada par (firefox,brave) y (firefox,chromium):
#   1. corre `bookmarks-merge.py` en modo dry-run (sin --apply) sobre las COPIAS;
#   2. exige exit 0 (la herramienta devuelve 2 si su propia verificacion falla);
#   3. exige la linea NOTA exacta del reporte (A2);
#   4. corre assert_artifacts.py sobre los artefactos (A3 y A4).
#
# El canonico es SIEMPRE firefox: es el unico modo en que el arbol fusionado
# tiene la raiz `menu`, que es justamente lo que se proyecta dentro de 'other'.
#
# Uso:
#   bash tests/step2_generate_artifacts.sh [--work-dir RUTA]
# ---------------------------------------------------------------------------
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --work-dir)
            SMOKE_WORK_DIR="$2"
            shift 2
            ;;
        *)
            printf 'Uso: step2_generate_artifacts.sh [--work-dir RUTA]\n' >&2
            exit 2
            ;;
    esac
done
export SMOKE_WORK_DIR

# shellcheck source=lib.sh
source "$TESTS_DIR/lib.sh"
smoke_resolve_profiles

smoke_require_cmd python3

# El dry-run no escribe en los perfiles reales: la herramienta abre las copias
# en solo lectura. El unico lugar donde escribe es --out.
smoke_assert_under_work "$SMOKE_OUT_DIR"

run_pair() {
    local member="$1"
    local out_dir="$2"
    local profile_dir="$3"
    local log="$out_dir/dry-run.log"
    local report="$out_dir/report.txt"
    local rc=0

    printf '\n  --- par firefox,%s ---\n' "$member"
    printf '  [step2] out: %s\n' "$out_dir"

    mkdir -p "$out_dir"
    smoke_assert_under_work "$out_dir"

    # Sin --apply: la herramienta solo genera artefactos y reporte.
    # --force NO se necesita en dry-run (el guard de browsers abiertos solo
    # corre con --apply), y no se usa para no relajar las protecciones.
    python3 "$SMOKE_TOOL" \
        --pair "firefox,$member" \
        --canonical firefox \
        --ff-profile "$SMOKE_FIREFOX_DIR" \
        "--$member-profile" "$profile_dir" \
        --out "$out_dir" \
        --snapshots "$SMOKE_SNAPSHOTS_DIR" \
        >"$log" 2>&1 || rc=$?

    if [ "$rc" -ne 0 ]; then
        smoke_fail "par firefox,$member: la herramienta termino con exit $rc (se esperaba 0)."
    fi
    smoke_ok "dry-run del par firefox,$member termino con exit 0"

    smoke_require_file "$report"

    # A2: la linea NOTA, textual y completa. -F (literal) y -x (linea entera).
    if grep -F -x -- "$SMOKE_NOTA_LINE" "$report"; then
        smoke_ok "el reporte contiene la linea NOTA esperada (A2)"
    else
        smoke_fail "el reporte NO contiene la linea NOTA exacta. Se esperaba:
    $SMOKE_NOTA_LINE"
    fi

    # A3 + A4: estructura de los dos artefactos.
    python3 "$TESTS_DIR/assert_artifacts.py" \
        --out "$out_dir" \
        --member "$member" \
        --title "$SMOKE_MARKER_TITLE" \
        --url "$SMOKE_MARKER_URL"
}

smoke_heading "step2: dry-run de los pares y asserts del reporte"

if [ -z "${SMOKE_FF_PROFILE:-}" ] || [ ! -f "$SMOKE_FF_STORE" ]; then
    smoke_fail "falta la copia de Firefox ($SMOKE_FF_STORE). Corré step1 primero."
fi

if [ -n "${SMOKE_BRAVE_PROFILE:-}" ] && [ -f "$SMOKE_BRAVE_STORE" ]; then
    run_pair brave "$SMOKE_OUT_FIREFOX_BRAVE" "$SMOKE_BRAVE_DIR"
else
    smoke_warn "sin perfil real de Brave resoluble: el par firefox,brave queda omitido."
fi

if [ -n "${SMOKE_CHROMIUM_PROFILE:-}" ] && [ -f "$SMOKE_CHROMIUM_STORE" ]; then
    run_pair chromium "$SMOKE_OUT_FIREFOX_CHROMIUM" "$SMOKE_CHROMIUM_DIR"
else
    smoke_warn "sin perfil real de Chromium resoluble: el par firefox,chromium queda omitido."
fi

smoke_ok "step2 terminado"
