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

## Segundo feedback del usuario (2026-10-20) → G6

- [x] **G6a — layout 50/50**: fila superior con `grid` + `columnconfigure(0/1,
      weight=1, uniform="half")` → mitad y mitad exacta. Opciones/Acciones/Salida
      full-width debajo. Padding normalizado a 10; combobox a width 12.
- [x] **G6b — tema oscuro moderno**: base `clam` con fallback seguro; paleta
      central ÚNICA (`PALETTE`, 13 colores, dos tonos de superficie + campo +
      accent + danger + muted) aplicada por una sola `apply_theme()` con wrapper
      `safe()` que descarta opciones no soportadas (nunca crashea). Nombres de
      opciones verificados contra `/usr/lib/tk8.6/ttk/clamTheme.tcl`. Techo
      honesto: messagebox/filedialog siguen nativos — tkinter stdlib no da dark
      nativo; el chrome de la app sí.
- [x] **G6c — español en todo lo visible**: barrido completo (títulos, labels,
      botones, diálogos, filedialog, placeholders, status, valores de combobox).
      Los comboboxes muestran `Firefox/Brave/Chromium/Ambos` mientras el estado
      que llega a `build_command` mantiene los tokens CLI (`token_for`/`only_token`).
      Quedan a propósito: `Default` (carpeta real del perfil), `snapshot`
      (vocabulario del CLI), nombres de flags y de archivos. self-test 17 → 18
      checks (nuevo: etiquetas de pantalla → tokens CLI).
- [x] **G6d — botón "Cerrar"**: al final de la fila Acciones (`side="right"`,
      gap 24px, estilo neutro), misma ruta `on_close` que `WM_DELETE_WINDOW`
      (confirmar + terminate + wait si hay run), fuera de `_action_buttons` →
      queda habilitado durante una corrida.

## Tercer feedback del usuario (2026-10-20, test con display) → G7

- [x] **G7a — BUG "Examinar…" deshabilitados**: causa raíz = regresión de G6c. Al
      pasar los comboboxes de par de tokens a etiquetas de display, el handler
      `_on_pair_change()` seguía comparando `name in members` con `members` en
      etiquetas (`'firefox' in ['Firefox','Brave']` → False para TODOS) → las tres
      filas quedaban deshabilitadas, incluidas las del par activo. Fix: mapear a
      tokens antes de comparar (`token_for(...)`). Matriz de estados verificada
      headless para los 3 pares (2 habilitados + 1 deshabilitado, correcto).
      Lección: cambiar el SIGNIFICADO de una variable rompe silenciosamente a
      todos sus consumidores — el guard durable sería un único accessor
      `_member_tokens()`.
- [x] **G7b — BUG spinbox "Ejemplos Máximos" invisible**: se configuraba
      `TSpinbox background` pero no `fieldbackground` (clam dibuja el área de
      texto desde ahí) → quedaba el default claro con texto casi blanco. Fix:
      `fieldbackground` explícito; delta de luminancia spinbox = entry = 0.833.
- [x] **G7c — "Limpiar" a la cabecera de Salida**: movido junto al título de la
      sección; la tira de botones interna del log se eliminó (más alto útil).
- [x] **G7d — estilo clima**: imitado `/home/javier/programacion/python/clima`
      (`clima_tkinter.py`, tkinter puro). Paleta exacta del referencia (window
      `#1a1a2e`, panel `#16213e`, campo `#1f2b47`, texto `#fff`, muted `#a0a0a0`,
      dim `#666`, accent `#00ff88`, danger `#e94560`) + reglas de uso: cero
      `ttk.LabelFrame` (tarjetas `tk.Frame` planas por contraste, no bordes),
      cero `ttk.Button` (botones planos `bd=0`, `cursor="hand2"`), emojis inline
      como iconos (patrón `f"💧 Humedad: {x}"` del referencia), fuentes Helvetica
      vía `load_fonts()` + familia de emoji resuelta en runtime (`Noto Color
      Emoji` acá, con fallback a la fuente de UI). 14 emojis en total en títulos
      de sección, botones y status. Inputs siguen siendo ttk (mejor edición),
      estilizados al campo oscuro.

Verificado: 18 checks del self-test, 0 `LabelFrame`, 0 `ttk.Button`, 14 literales
de color todos dentro de `PALETTE`, `_on_pair_change` correcto en los 3 pares,
3 rutas de cierre (sin run / con confirmación / cancelar).

Dato aclarado: el Chromium "poblado" (850KB) NO lo puso el usuario — su run de
apply partió de `Chromium: 0 bookmarks` (report.txt 17:10) y el apply copió
Firefox adentro.

Commits de la ronda: `5d8ebd4e chore` (artifacts del test real + snapshots/
gitignored) → `1e5a06e0 fix(cli)` (orden de carpetas, G5a) → `fdbdeceb feat(gui)`
(G5b+G5c + docs) → `d9cbb661 chore` (untrack del .pyc + artifacts del 2do run,
que ya salió con el orden corregido) → `fcf140a6 feat(gui)` (G6a-G6d) →
commit de este cambio `fix(gui)` (G7a-G7d: 2 bugs + layout + estilo clima).
