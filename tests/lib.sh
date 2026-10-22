#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# lib.sh - rutas, guards y helpers compartidos del harness de humo de IA-sync.
#
# Este archivo es la UNICA fuente de verdad de:
#   * las rutas del harness (work dir, fixtures, artefactos, snapshots);
#   * la resolucion de los perfiles reales (solo lectura);
#   * los guards de seguridad (invariantes 1 y 2 del ODD);
#   * el texto exacto de la linea NOTA del reporte.
#
# Uso:
#   source tests/lib.sh                    -> define funciones y exporta variables
#   bash tests/lib.sh --print-env          -> imprime KEY=VALUE con todo resuelto
#   bash tests/lib.sh --assert-under-work RUTA
#   bash tests/lib.sh --assert-not-real-profile RUTA
#
# Los scripts Python del harness NO repiten esta logica: consultan a lib.sh
# con --print-env y Calling a los guards con los subcomandos --assert-*.
# ---------------------------------------------------------------------------

# El shell que sourcea este archivo ya usa `set -euo pipefail`; repetirlo aqui
# no cambia nada y protege al uso directo (`bash tests/lib.sh ...`).
set -euo pipefail

# --- Rutas base (overridables por variable de entorno) -----------------------

# Todo lo que el harness escribe vive acá adentro. Override: SMOKE_WORK_DIR.
: "${SMOKE_WORK_DIR:=/tmp/opencode/iasync-smoke}"

# Puerto del CDP para el paso 4. Override: SMOKE_CDP_PORT.
: "${SMOKE_CDP_PORT:=9222}"

# Binario de Brave para el paso 4 (vacío = autodetección con `command -v`).
: "${SMOKE_BROWSER_BIN:=}"

SMOKE_TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_REPO_DIR="$(cd "$SMOKE_TESTS_DIR/.." && pwd)"
: "${SMOKE_TOOL:=$SMOKE_REPO_DIR/bookmarks-merge.py}"

# --- Marcadores del smoke test ----------------------------------------------

SMOKE_MARKER_TITLE="${SMOKE_MARKER_TITLE:-MENU-ROOT-SMOKE-TEST}"
SMOKE_MARKER_URL="${SMOKE_MARKER_URL:-https://smoke-test.invalid/menu-root}"

# Nombre de la raíz `menu` de Firefox. Es el texto que la herramienta usa como
# etiqueta de la carpeta proyectada dentro de 'other'.
SMOKE_MENU_FOLDER_TITLE="${SMOKE_MENU_FOLDER_TITLE:-Menú de marcadores}"

# Línea EXACTA del reporte (mismos acentos, mayúsculas y dos espacios tras NOTA).
# El assert A2 compara contra este texto, byte a byte.
SMOKE_NOTA_LINE="  NOTA  el formato Chromium no tiene raiz '${SMOKE_MENU_FOLDER_TITLE}': 1 marcador del arbol fusionado se proyecto dentro de 'other'"

# --- Rutas derivadas del work dir -------------------------------------------

SMOKE_FIREFOX_DIR="$SMOKE_WORK_DIR/firefox"
SMOKE_FF_STORE="$SMOKE_FIREFOX_DIR/places.sqlite"
SMOKE_BRAVE_DIR="$SMOKE_WORK_DIR/brave/Default"
SMOKE_BRAVE_STORE="$SMOKE_BRAVE_DIR/Bookmarks"
SMOKE_CHROMIUM_DIR="$SMOKE_WORK_DIR/chromium/Default"
SMOKE_CHROMIUM_STORE="$SMOKE_CHROMIUM_DIR/Bookmarks"
SMOKE_OUT_DIR="$SMOKE_WORK_DIR/out"
SMOKE_SNAPSHOTS_DIR="$SMOKE_WORK_DIR/snapshots"

# Un directorio de salida por par: la herramienta escribe SIEMPRE `report.txt`
# en el `--out` recibido, asi que compartirlo entre los dos pares haria que el
# segundo run pisara el reporte del primero.
SMOKE_OUT_FIREFOX_BRAVE="$SMOKE_OUT_DIR/firefox-brave"
SMOKE_OUT_FIREFOX_CHROMIUM="$SMOKE_OUT_DIR/firefox-chromium"

# --- Logging -----------------------------------------------------------------

smoke_heading() {
    printf '\n\033[1m=== %s ===\033[0m\n' "$*"
}

smoke_log() {
    printf '  %s\n' "$*"
}

smoke_ok() {
    printf '  \033[32mOK\033[0m    %s\n' "$*"
}

smoke_warn() {
    printf '  \033[33mAVISO\033[0m %s\n' "$*" >&2
}

smoke_fail() {
    printf '  \033[31mFALLA\033[0m %s\n' "$*" >&2
    exit 1
}

# --- Resolucion de perfiles reales (SOLO LECTURA) ----------------------------
#
# Se replica la logica de `bookmarks-merge.py` para que el harness apunte a
# los mismos perfiles que la herramienta. Un perfil real se puede sobreescribir
# por variable de entorno (SMOKE_FF_PROFILE / SMOKE_BRAVE_PROFILE /
# SMOKE_CHROMIUM_PROFILE) para probar contra otro perfil.

_smoke_ini_sections() {
    # Imprime "SECCION<TAB>CLAVE<TAB>VALOR" por cada par clave=valor del .ini.
    awk '
        /^[[:space:]]*\[/ {
            line = $0
            gsub(/[[:space:]]/, "", line)
            sub(/^\[/, "", line)
            sub(/\]$/, "", line)
            section = line
            next
        }
        index($0, "=") > 0 {
            key = substr($0, 1, index($0, "=") - 1)
            val = substr($0, index($0, "=") + 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
            if (section != "") printf "%s\t%s\t%s\n", section, key, val
        }
    ' "$1"
}

_smoke_resolve_ff_profile() {
    local base="$HOME/.mozilla/firefox"
    local ini="$base/profiles.ini"
    local section key val path candidate=""
    local -a sections=()
    declare -A val_of=()

    if [ -f "$ini" ]; then
        while IFS=$'\t' read -r section key val; do
            if [ -z "${val_of["$section|$key"]+presente}" ]; then
                sections+=("$section")
            fi
            val_of["$section|$key"]="$val"
        done < <(_smoke_ini_sections "$ini")

        # 1) Secciones Install*: su `Default` es la ruta del perfil en uso.
        for section in "${sections[@]}"; do
            case "$section" in
                Install*) ;;
                *) continue ;;
            esac
            path="${val_of["$section|Default"]:-}"
            if [ -n "$path" ] && [ -d "$base/$path" ]; then
                candidate="$base/$path"
                break
            fi
        done

        # 2) Secciones Profile*: la marcada Default=1.
        if [ -z "$candidate" ]; then
            for section in "${sections[@]}"; do
                case "$section" in
                    Profile*) ;;
                    *) continue ;;
                esac
                if [ "${val_of["$section|Default"]:-}" != "1" ]; then
                    continue
                fi
                path="${val_of["$section|Path"]:-}"
                if [ -n "$path" ] && [ -d "$base/$path" ]; then
                    candidate="$base/$path"
                    break
                fi
            done
        fi
    fi

    # 3) Fallback: el perfil *.default-release.
    if [ -z "$candidate" ]; then
        for path in "$base"/*.default-release; do
            if [ -d "$path" ]; then
                candidate="$path"
                break
            fi
        done
    fi

    printf '%s' "$candidate"
}

_smoke_resolve_chromium_family_profile() {
    # El flag --<member>-profile de la herramienta apunta al DIRECTORIO QUE
    # CONTIENE el archivo `Bookmarks` (p.ej. .../Default).
    local base="$1"
    local dir
    if [ -f "$base/Default/Bookmarks" ]; then
        printf '%s' "$base/Default"
        return 0
    fi
    if [ -d "$base" ]; then
        for dir in "$base"/*/; do
            if [ -f "${dir}Bookmarks" ]; then
                printf '%s' "${dir%/}"
                return 0
            fi
        done
    fi
    printf ''
}

smoke_resolve_profiles() {
    if [ -z "${SMOKE_FF_PROFILE:-}" ]; then
        SMOKE_FF_PROFILE="$(_smoke_resolve_ff_profile)"
    fi
    if [ -z "${SMOKE_BRAVE_PROFILE:-}" ]; then
        SMOKE_BRAVE_PROFILE="$(_smoke_resolve_chromium_family_profile \
            "$HOME/.config/BraveSoftware/Brave-Browser")"
    fi
    if [ -z "${SMOKE_CHROMIUM_PROFILE:-}" ]; then
        SMOKE_CHROMIUM_PROFILE="$(_smoke_resolve_chromium_family_profile \
            "$HOME/.config/chromium")"
    fi
    export SMOKE_FF_PROFILE SMOKE_BRAVE_PROFILE SMOKE_CHROMIUM_PROFILE
}

# --- Guards de seguridad ----------------------------------------------------

smoke_realpath() {
    # `realpath -m` normaliza la ruta sin exigir que exista: asi el guard
    # tambien protege archivos que todavia no fueron creados.
    realpath -m -- "$1"
}

smoke_assert_under_work() {
    # INVARIANTE 1: toda escritura ocurre dentro de $SMOKE_WORK_DIR.
    local target="${1:-}"
    local root resolved
    if [ -z "$target" ]; then
        smoke_fail "smoke_assert_under_work: falta la ruta a verificar."
    fi
    root="$(smoke_realpath "$SMOKE_WORK_DIR")"
    resolved="$(smoke_realpath "$target")"
    case "$resolved" in
        "$root"|"$root"/*) return 0 ;;
    esac
    smoke_fail "INVARIANTE 1 violado: '$resolved' esta fuera del work dir '$root'."
}

smoke_assert_not_real_profile() {
    # INVARIANTE 2: los perfiles reales SOLO se leen.
    local target="${1:-}"
    local resolved real
    if [ -z "$target" ]; then
        smoke_fail "smoke_assert_not_real_profile: falta la ruta a verificar."
    fi
    resolved="$(smoke_realpath "$target")"
    for real in "$SMOKE_FF_PROFILE" "$SMOKE_BRAVE_PROFILE" "$SMOKE_CHROMIUM_PROFILE"; do
        if [ -z "$real" ]; then
            continue
        fi
        real="$(smoke_realpath "$real")"
        case "$resolved" in
            "$real"|"$real"/*)
                smoke_fail "INVARIANTE 2 violado: '$resolved' esta dentro del perfil real '$real'."
                ;;
        esac
    done
}

smoke_assert_writable() {
    # Atajo: las dos invariantes juntas, para cada ruta que el harness escribe.
    smoke_assert_not_real_profile "$1"
    smoke_assert_under_work "$1"
}

# --- Utilidades -------------------------------------------------------------

smoke_sha256() {
    sha256sum -- "$1" | cut -d' ' -f1
}

smoke_require_file() {
    if [ ! -f "$1" ]; then
        smoke_fail "No existe el archivo esperado: $1"
    fi
}

smoke_require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        smoke_fail "Falta el comando '$1' en el PATH."
    fi
}

smoke_running_browsers() {
    # Nombres de proceso que usa la herramienta para detectar browsers abiertos
    # (BROWSERS[...]["comms"] en bookmarks-merge.py).
    local comms="firefox firefox-bin firefox-esr brave brave-browser chromium chromium-browser"
    local proc comm found=""
    for proc in /proc/[0-9]*; do
        [ -r "$proc/comm" ] || continue
        comm="$(cat "$proc/comm" 2>/dev/null || true)"
        case " $comms " in
            *" $comm "*) found="$found $comm" ;;
        esac
    done
    printf '%s' "${found# }"
}

smoke_browser_running() {
    # true si algun proceso del browser indicado esta corriendo.
    case " $(smoke_running_browsers) " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}

# --- Modo CLI (para que los scripts Python consulten esta misma lógica) -----

smoke_print_env() {
    printf 'SMOKE_WORK_DIR=%s\n' "$SMOKE_WORK_DIR"
    printf 'SMOKE_CDP_PORT=%s\n' "$SMOKE_CDP_PORT"
    printf 'SMOKE_BROWSER_BIN=%s\n' "$SMOKE_BROWSER_BIN"
    printf 'SMOKE_TOOL=%s\n' "$SMOKE_TOOL"
    printf 'SMOKE_TESTS_DIR=%s\n' "$SMOKE_TESTS_DIR"
    printf 'SMOKE_REPO_DIR=%s\n' "$SMOKE_REPO_DIR"
    printf 'SMOKE_FF_PROFILE=%s\n' "${SMOKE_FF_PROFILE:-}"
    printf 'SMOKE_BRAVE_PROFILE=%s\n' "${SMOKE_BRAVE_PROFILE:-}"
    printf 'SMOKE_CHROMIUM_PROFILE=%s\n' "${SMOKE_CHROMIUM_PROFILE:-}"
    printf 'SMOKE_FIREFOX_DIR=%s\n' "$SMOKE_FIREFOX_DIR"
    printf 'SMOKE_FF_STORE=%s\n' "$SMOKE_FF_STORE"
    printf 'SMOKE_BRAVE_DIR=%s\n' "$SMOKE_BRAVE_DIR"
    printf 'SMOKE_BRAVE_STORE=%s\n' "$SMOKE_BRAVE_STORE"
    printf 'SMOKE_CHROMIUM_DIR=%s\n' "$SMOKE_CHROMIUM_DIR"
    printf 'SMOKE_CHROMIUM_STORE=%s\n' "$SMOKE_CHROMIUM_STORE"
    printf 'SMOKE_OUT_DIR=%s\n' "$SMOKE_OUT_DIR"
    printf 'SMOKE_OUT_FIREFOX_BRAVE=%s\n' "$SMOKE_OUT_FIREFOX_BRAVE"
    printf 'SMOKE_OUT_FIREFOX_CHROMIUM=%s\n' "$SMOKE_OUT_FIREFOX_CHROMIUM"
    printf 'SMOKE_SNAPSHOTS_DIR=%s\n' "$SMOKE_SNAPSHOTS_DIR"
    printf 'SMOKE_MARKER_TITLE=%s\n' "$SMOKE_MARKER_TITLE"
    printf 'SMOKE_MARKER_URL=%s\n' "$SMOKE_MARKER_URL"
    printf 'SMOKE_MENU_FOLDER_TITLE=%s\n' "$SMOKE_MENU_FOLDER_TITLE"
    printf 'SMOKE_NOTA_LINE=%s\n' "$SMOKE_NOTA_LINE"
}

smoke_main() {
    smoke_resolve_profiles
    case "${1:-}" in
        --print-env)
            smoke_print_env
            ;;
        --assert-under-work)
            shift
            smoke_assert_under_work "${1:-}"
            ;;
        --assert-not-real-profile)
            shift
            smoke_assert_not_real_profile "${1:-}"
            ;;
        *)
            printf 'Uso: lib.sh --print-env | --assert-under-work RUTA | --assert-not-real-profile RUTA\n' >&2
            return 2
            ;;
    esac
}

# Solo si este archivo se ejecuta (no si se sourcea).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    smoke_main "$@"
fi
