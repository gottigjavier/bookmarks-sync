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
- [ ] G4 cerrado: falta smoke con display real del usuario.
