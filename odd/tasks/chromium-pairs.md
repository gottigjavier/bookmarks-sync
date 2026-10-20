# Feature: chromium-pairs (v2 — 3 navegadores, 3 pares)

## Objetivo

Generalizar `bookmarks-merge.py` para sincronizar **exactamente dos navegadores a la vez**
elegidos entre `{firefox, brave, chromium}`, con los 3 pares posibles:

- `firefox,brave`
- `firefox,chromium`
- `brave,chromium`

El miembro **canónico** (esqueleto de estructura de carpetas) se elige siempre explícitamente
entre los dos miembros del par. **Brave deja de ser el default canónico**: `--canonical`
pasa a ser obligatorio.

## Problema hoy

El tool está construido como "Firefox ⇄ Brave" con roles semánticos hardcodeados:
`--canonical` defaultea a `brave`, `--only {both,brave,firefox}`, perfiles resueltos
incondicionalmente, snapshot/restore/salida/reportes nombrados por navegador, y
`BROWSER_COMMS` sin `chromium` (agujero en el guard de procesos).

## Alcance autorizado (esta parte; la GUI viene después)

- CLI solamente. La interfaz tkinter envolverá este CLI en una feature posterior; la
  superficie CLI debe ser limpia y general.
- Rutas por defecto de cada navegador + overrides por flag (luego la GUI las expondrá
  para portabilidad a otros SO).
- Actualizar README.md y README-EN.md.

Fuera de alcance: GUI, multi-SO (solo se dejan los overrides preparados), sync de a más
de dos navegadores.

## Restricciones

1. Siempre exactamente dos navegadores por ejecución.
2. `--canonical` siempre explícito; sin default.
3. El pair drive TODO: resolución de perfiles (lazy, solo el par), guard de procesos,
   snapshot, restore, emisión, verificación y reporte.
4. Snapshots viejos (`snapshots/<ts>/firefox|brave/...`) deben seguir siendo restaurables.
5. Sin `--apply` salvo autorización explícita del usuario (dry-run por defecto, como hoy).

## Hechos verificados en fuente (del mapeo, no asumidos)

- Brave y Chromium comparten formato JSON Chromium: `read_brave`, `_flatten_brave`,
  `emit_chromium`, `inventory_chromium`, `ch_guid` son genéricos de formato, reutilizables.
- `merge()` (esqueleto Brave + items FF aplanados) y `merge_ff_canonical()` (esqueleto FF +
  items Brave aplanados) implementan el **mismo algoritmo** tree-skeleton + flat-side.
  No hace falta merge tree-vs-tree: `brave,chromium` reutiliza el mismo patrón con
  `_flatten_brave` sobre el lado no canónico.
- `ff_items` tiene forma genérica (`path/title/url/added_us`) + campos Firefox-específicos
  (`root_id` int, filtrado de tags). La forma flatten (`root_key` str) es la genérica.
- Riesgos verificados: `BROWSER_COMMS` (L88) sin chromium; emisión dual incondicional
  (L1035-1041); bloque `--no-dedupe` (L1016-1030) solo implementa semántica
  brave-canónica; `brave_meta` usado como meta del emisor incluso con FF canónico
  (L1010/L1037 — contaminación de root GUIDs); resolución eager de perfiles (L962-965)
  falla si un navegador del que NO forma parte del par no está instalado.

## Tareas

- [x] **T1** — Registro de navegadores + CLI por par: constante `BROWSERS` (fuente única
      de verdad, la GUI podrá enumerarla), resolución lazy por navegador (incl.
      `resolve_chromium_profile` → `~/.config/chromium/Default`), `--pair a,b` +
      `--canonical {a,b}` obligatorio, `--only` restringido al par, `--chromium-profile`,
      comms de chromium en el guard, docstring y versión 2.0.0.
- [x] **T2** — Merge unificado: función genérica esqueleto+lado plano con adaptador de
      items FF (`root_id` → root_key) y flatten del lado no canónico; label
      `Desde <navegador no canónico>`; anclaje asimétrico preservado (chromium-canónico
      → wrapper en bookmark_bar; firefox-canónico → "Desde X" por raíz). Incluye fix de
      bug latente: fallback self-descendant en `merge()` que causaba recursión infinita
      en `assign_keys` (`list(bar)`).
- [x] **T3** — Emisión/verificación/reportes por par: `out/<navegador>-bookmarks.json`,
      `firefox-restore.json` solo si firefox ∈ par, meta del emisor desde el miembro
      chromium-family del par, reporte y `--no-dedupe` pair-aware, write-back: solo la
      familia-chromium se escribe automático (canónico únicamente en pares
      chromium↔chromium; nunca dos stores vivos en una corrida).
- [x] **T4** — Snapshot/restore por par: layout `snapshots/<ts>/<navegador>/...`,
      restore selectivo y backward-compatible con snapshots viejos (`firefox/`+`brave/`;
      navegador ausente → skip con aviso). Verificado con perfiles falsos en /tmp.
- [x] **T5** — Verificación end-to-end de los 6 setups (3 pares × 2 canónicos) en dry-run
      contra datos reales + README.md y README-EN.md actualizados. Verificador independiente
     : 8 checks, todos pass. READMEs: 18 ejemplos extraídos y verificados exit 0.

## Criterios de aceptación

- Los 6 setups (par × canónico) corren dry-run sobre los datos reales, reportan conteos
  coherentes y la verificación interna del script pasa (0 duplicados, exclusivos presentes).
- `--canonical` sin argumento → error claro; par inválido para `--only` → error claro.
- Invocar un par no incluye resolución de perfiles de navegador ajeno al par.
- Chromium abierto → guard bloquea `--apply`.
- README (ES y EN) reflejan la superficie nueva; sin menciones a default brave.

## Verificación aplicable

- TDD: **disabled** (el proyecto no tiene runner de tests; la verificación funcional es
  dry-run end-to-end + aserciones internas del script + releer artefactos con jq).
- Por tarea: dry-run de los setups afectados. Cierre T5: los 6 setups + revisión de
  artefactos emitidos (`jq` sobre JSONs chromium, releo del restore FF).

## Forecast de delivery

~350-450 líneas cambiadas (bookmarks-merge.py + 2 READMEs). Estrategia: `ask-on-risk`
— si supera 400, preguntar split vs `size:exception`.

## Ruta de implementación

- Delegated direct (writer único) — trigger: 2+ archivos no triviales.
- Exploración/mapeo: delegada (completada, 2026-10-20).

## Progreso

- [x] Mapeo de acoplamientos (explore).
- [x] T1–T5 completas y verificadas (writer + verificador independiente + spot check
      del orquestador: `py_compile` + dry-run firefox,chromium/canonical chromium exit 0).

## Evidencia de verificación (2026-10-20)

- 6 setups dry-run: exit 0, verificación interna OK, 0 FALLA, 0 duplicados (~1548–1549 URLs).
- 7 rutas de error: exit 1 con mensajes claros (par/canónico ausentes, fuera del par, `--only` inválido).
- Determinismo: rerun byte-idéntico (`cmp`); par order-insensitive.
- Artefactos pair-aware: `brave,chromium` NO emite `firefox-restore.json`; nombre del
  artefacto chromium = miembro del par.
- jq: JSON Chromium válido (roots bookmark_bar/other/synced, sin checksum).
- Resolución lazy: override de perfil ajeno al par → ignorado, exit 0.
- Guard: chromium en comms; `--apply` bloqueado nombra miembros del par en ejecución.
- Write-back chromium↔chromium: estructuralmente un solo store vivo automático (read estático).
- READMEs: tablas consistentes con `--help`, 18 ejemplos exit 0, sin default brave.

## Riesgos / follow-ups conocidos

- Store de Chromium en esta máquina está vacío (0 bookmarks): el camino esqueleto
  chromium no vacío solo se ejercitó con Brave (mismo formato). Prueba real pendiente
  cuando haya un chromium poblado.
- `--restore` exige `--canonical` aunque no lo usa (ergonomía; documentado).
- El AVISO de "está corriendo" en dry-run lista todos los navegadores, no solo los del
  par (el guard bloqueante de `--apply` sí es pair-scoped; solo cosmético).
- README profile example usa la ruta real del Firefox profile de esta máquina.

## Nota

Directorio **no es repo git**. Las unidades de trabajo se commitearán cuando el usuario
autorice inicializar el repo (pregunta pendiente al llegar al primer commit).
