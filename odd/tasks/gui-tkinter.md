# Feature: gui-tkinter

## Objetivo

Interfaz gráfica (tkinter, stdlib sola) que exponga **todas las acciones del CLI**
v2.0.0: selección de par y canónico, overrides de perfiles, dry-run (previsualización),
`--apply` con confirmación, `--restore` de snapshots, `--only`, `--no-dedupe`,
`--attach-folder`, `--max-examples`, y visualización del log/reporte.

## Decisión de integración (usuario, 2026-10-20)

**Subprocess + flag JSON**: la GUI arma el comando `python3 bookmarks-merge.py ...`
y captura stdout/stderr en panel de log (hilo + queue). Un flag nuevo
`--list-browsers --json` permite a la GUI enumerar navegadores/defaults/pares sin
parsear código. La GUI NO importa los internos del CLI. Cualquier flag nuevo del CLI
queda disponible en la GUI casi gratis.

## Restricciones

1. Solo stdlib: `tkinter`, `subprocess`, `threading`, `queue`, `pathlib`, `json`.
2. UI en español neutro (consistente con la salida del CLI).
3. `--apply` y `--restore` NUNCA se ejecutan sin diálogo de confirmación que resuma
   par/canónico/targets.
4. Mientras un subprocess corre, los botones de acción se deshabilitan.
5. La GUI no corre nunca sin display; la verificación sin display usa funciones puras
   (`build_command`) separadas de los widgets.
6. BROWSERS del CLI es la única fuente de verdad (vía `--list-browsers --json`).
7. Commits por etapa en jj (workflow del usuario: `jj describe -m` + `jj bookmark set main -r @`;
   push es manual del usuario, jamás `jj git push`).

## Tareas

- [x] **G1** — CLI: flag `--list-browsers --json`. Sale antes de la validación de
      par/canónico (exit 0). JSON: navegadores {name, display, kind, comms,
      profile_base, profile_flag} + pares + script_version. `--json` solo sin
      `--list-browsers` → error. Defaults movidos al registry (fuente única).
      Commit: `45367fba`.
- [x] **G2** — `bookmarks_gui.py`: skeleton + widgets dirigidos por el registry
      (par A/B, canónico limitado al par, rutas de perfil con "Examinar…", opciones),
      `build_command()` pura (14 checks en `--self-test`, sin display), dry-run con
      streaming de log en hilo (queue + `after` con try/finally, `errors="replace"`).
      Tras revisión independiente: corregido blocker del fallback de registry
      (KeyError → ventana degradada + diálogo), `_poll` exception-safe, ValueError
      de callbacks manejados, reader thread a prueba de decode errors.
- [x] **G3** — Acciones `--apply` y `--restore` con diálogo de confirmación (resumen
      de targets según la matriz de write-back del CLI), `--only`,
      `--attach-folder`, `--no-dedupe`, `--max-examples`, abrir carpeta `out/` y
      abrir `report.txt`. Cierre con confirmación + terminate + wait.
- [ ] **G4** — Verificación + docs: sin display ✓ (compile, self-test 14 checks,
      replay headless del blocker con mocks, dry-run CLI exit 0, READMEs ES/EN con
      sección GUI). **Pendiente: smoke con display real** (usuario:
      `python3 bookmarks_gui.py`) — la máquina no tiene display/xvfb.

## Criterios de aceptación

- `--list-browsers --json` → JSON válido (jq), no rompe invocaciones existentes.
- `build_command()` cubre los 6 setups + flags opcionales (tests inline ejecutables
  sin display).
- Smoke con display real (usuario) o `xvfb-run` si existe: la ventana instanciada
  destruye sin errores.
- Dry-run desde la GUI produce el mismo resultado que el CLI equivalente.
- READMEs documentan la GUI (ES/EN).

## Verificación aplicable

- TDD: **disabled** (sin runner de tests). Funciones puras del GUI testeables por
  import sin display; smoke de widgets solo con display/xvfb.
- Por etapa: `py_compile`, `--list-browsers --json | jq`, `build_command` tests,
  un dry-run real por etapa.

## Forecast de delivery

G1 ~25 líneas; GUI ~400-480 líneas nuevas + sección README. El total puede superar
400 líneas agregadas → si el acumulado lo supera antes del commit, aplicar
`ask-on-risk` (preguntar split vs size:exception).

## Ruta de implementación

- G1: direct inline (mecánico, CLI ya mapeado).
- G2+G3+docs: delegated direct (writer único; trigger: 1 archivo nuevo no trivial + docs).
- G4: verificador independiente + spot check del orquestador.

## Progreso

- [x] Mapeo CLI/registro (feature chromium-pairs, 2026-10-20).
- [x] G1 (commit CLI), G2+G3 (GUI + docs, commit aparte — verificado por escritor +
      verificador independiente + 2 rondas de corrección).
- [x] G4: smoke con display REAL hecho por el usuario (2026-10-20, prueba
      firefox-canónico vía GUI, apply incluido).

## Feedback del usuario (primer test real, 2026-10-20) → G5

- [x] **G5a — fix de orden (CLI, bug)**: en `firefox(canónico),chromium`, carpetas
      de la barra de Firefox quedaban reordenadas al final del artefacto Chromium.
      Diagnóstico: `read_firefox` solo lee type=1 con `ORDER BY b.parent, ...` y
      `_build_ff_tree` creaba carpetas perezosamente al primer bookmark
      referenciado → insertaba la carpeta al FINAL del children list del padre,
      ignorando su `position` real. Caso real: `Favoritos` = position 0 en
      Firefox (toolbar id 3) → salió en index 19 (último de 20). Fix:
      `read_firefox` ahora lleva position de carpetas (dict) y bookmarks (query),
      `_ff_path` devuelve `(title, position)` por segmento, `_build_ff_tree`
      ordena cada children list por position (sort estable, desconocidos al
      final, side table por `id(node)` sin tocar el shape de nodos);
      `_adapt_ff_items` mantiene `path` como strings (forma genérica intacta).
      Verificado: repro antes/después (`Favoritos` de última a primera, 20
      hijos, 12 dentro), conteos idénticos (1548/384/388), determinismo
      (sha256 idéntico), 3 edge cases de canónico vacío OK, dirección
      chromium-canónica intacta. 0 carpetas vacías en el FF del usuario → no
      había pérdida, solo orden. `firefox-restore.json` se corrige con el mismo
      skeleton.
- [x] **G5b — GUI: log más alto**: ScrolledText 12 → 20 filas; el log es el único
      elemento con `expand=True`, así crece con la ventana y el resto queda fijo.
- [x] **G5c — GUI: "Perfiles" a la derecha de "Sincronización"**: fila superior
      de dos columnas (Sincronización izq. / Perfiles der.); Opciones, Acciones
      y log full-width debajo. Labels externos "Por defecto: …" eliminados →
      **placeholder dentro del input** (gris `#9a9a9a`, FocusIn limpia,
      FocusOut restaura si vacío; helper `attach_placeholder`). Placeholders
      derivados del registry (`profile_placeholder`): firefox →
      "Auto (profiles.ini)", chromium-family → `<profile_base>/Default`.
      `build_command` trata placeholder / valor igual al default (literal o
      expandido) como vacío → no emite `profile_flag`. self-test 14 → 17
      checks. README ES/EN: 1 bullet ajustado por precisión (default en gris
      dentro del campo).

Dato aclarado: el Chromium "poblado" (850KB) NO lo puso el usuario — su run de
apply partió de `Chromium: 0 bookmarks` (report.txt 17:10) y el apply copió
Firefox adentro.

Commits de la ronda: `5d8ebd4e chore` (artifacts del test real + snapshots/
gitignored) → `1e5a06e0 fix(cli)` (orden de carpetas, G5a) → commit de este
cambio `feat(gui)` (G5b+G5c + docs).
