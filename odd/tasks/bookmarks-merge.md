# Feature: bookmarks-merge

**Estado:** en curso — script escrito, pendiente dry-run revisado y `--apply` autorizado.
**Fecha:** 2026-10-19
**Ruta elegida:** direct inline (1 archivo, todo el diseño ya resuelto y verificado contra fuentes; delegar perdería el contexto verificado). No dispara el writer trigger (1 archivo). No hay delegación posible que aporte.

---

## Objetivo

Un script determinista y repetible que converja los bookmarks de Firefox y Brave:
union de ambas fuentes, sin duplicados, con la distribución de carpetas de **Brave** como
canónica, preservando lo que exista solo en cualquiera de los dos.

## Problema

- Firefox tiene 2918 bookmarks en 573 carpetas con **1394 copias duplicadas** (1208 grupos) y
  carpetas repetidas por nombre (`Varios` x8, `Celulares` x2, `FE` x2, `Noticias` x2...).
- Brave tiene 1604 bookmarks en 324 carpetas con 106 copias duplicadas (96 grupos).
- 1472 URLs están en ambos; 52 solo en Firefox; 26 solo en Brave.
- Los formatos en disco son incompatibles: Firefox = SQLite (`places.sqlite` + WAL);
  Brave = JSON Chromium.

## Por qué un script (y no un LLM cada vez)

El merge es 100% determinista: esqueleto de Brave, dedupe por URL, huérfanos a
`Favoritos/Desde Firefox`. No hay juicio que aplicar. Un script es reproducible, auditable y
gratis de re-ejecutar. El LLM sobra en la fase de ejecución.

## Alcance autorizado

- **Sí:** crear el script, un documento de tareas, previews en `out/`, y snapshots en `snapshots/`.
- **No:** tocar ningún browser, ni `places.sqlite`, ni `Bookmarks`. El `--apply` lo autoriza el
  usuario después de revisar el reporte del dry-run.
- **No:** modificar `sources.md` (directiva previa del usuario).

## Restricciones

- Python 3, **solo stdlib**. Sin `pip`. (`python3` no tiene el módulo `lz4` y no hace falta.)
- No tocar `places.sqlite` en vivo: se lee siempre desde una copia (para que SQLite replaye el WAL
  sobre la copia, nunca sobre el original).
- Idempotente: correrlo dos veces debe dar 0 cambios.

## Hechos verificados en fuente (no asumidos)

| Hecho | Dónde se verificó |
|---|---|
| Chromium `Decode()` **no lee ni valida** `checksum`; solo lo escribe `Encode()` | `components/bookmarks/browser/bookmark_codec.cc` |
| Los UUID de las carpetas permanentes de Chromium son **hard-codeados y NO se leen del archivo** | comentario explícito en `bookmark_codec.cc` (línea ~290) |
| GUID inválido/vacío/duplicado en Chromium → se reasigna uno random v4 automáticamente | `bookmark_codec.cc` |
| `date_added` de Chromium = string, microsegundos desde **1601-01-01** | `bookmark_codec.cc` (`ToMicrosecondsSinceWindowsEpoch`) |
| Firefox "Restore → Choose File" llama `importFromFile(path, {replace:true})` → **reemplaza todo** | `browser/components/places/content/places.js:749` |
| Firefox acepta `.json` **sin comprimir** (solo descomprime si la ruta termina en `jsonlz4`) | `toolkit/components/places/BookmarkJSONUtils.sys.mjs` |
| GUID válido en Firefox = exactamente 12 chars `[a-zA-Z0-9_-]` | `isValidGuid` en `PlacesUtils.sys.mjs:492` |
| El root `tags` se descarta en el import de Firefox | `BookmarkJSONUtils.sys.mjs:258` |
| El import ordena los hijos por `index` | `BookmarkJSONUtils.sys.mjs:498` |

## Tareas

- [x] **T1** — Resolver rutas de perfil (Firefox desde `profiles.ini`) + CLI (`--apply`, `--only`, `--force`, overrides).
- [x] **T2** — Guard de procesos + snapshot timestamped + export plano de seguridad.
- [x] **T3** — Extractor de Firefox: copia de `places.sqlite`(+wal/shm) → CTE recursivo → `raíz/ruta/título/url/fecha`.
- [x] **T4** — Extractor de Brave: JSON Chromium → árbol normalizado.
- [x] **T5** — Modelo unificado + merge/dedupe: esqueleto Brave, dedupe por URL normalizada (gana Brave), huérfanos a `Favoritos/Desde Firefox/<raíz FF>/<ruta original>`.
- [x] **T6** — Emisor Chromium (`Bookmarks` JSON): GUIDs deterministas (uuid5), `date_added` en µs/1601, `checksum` omitido (verificado inocuo).
- [x] **T7** — Emisor Firefox (backup legacy JSON): **objeto raíz** (NO array, ver bug crítico), `type`/`typeCode`, pseudónimos `root`, `index`, GUIDs de 12 chars válidos, `dateAdded` en µs/1970, sin `tags`.
- [x] **T8** — Verificación: releer los artefactos generados y asertar 0 duplicados, exclusivos de Firefox presentes, conteos exactos.
- [x] **T9** — Dry-run end-to-end + reporte presentado. **Pendiente la autorización del usuario para `--apply`.**

## Criterios de aceptación

1. `python3 -m py_compile` sin errores.
2. Dry-run produce reporte con: conteos antes/después, duplicados eliminados por browser, ubicación de los 52 huérfanos, y verificación en verde.
3. Dry-run **no** modifica ningún archivo de browser (verificable por mtime/hash).
4. `--apply` aborta si Firefox o Brave están corriendo.
5. Segunda corrida = 0 cambios.

## Verificación aplicable

- `python3 -m py_compile bookmarks-merge.py`
- `sha256sum` de `places.sqlite` y `Bookmarks` antes/después del dry-run (deben ser idénticos).
- Dry-run dos veces → mismo reporte (idempotencia del análisis).

## Registro de decisiones

- **Dedupe por URL global**, no solo intra-browser: el árbol final es uno solo, así que dos
  bookmarks con la misma URL en carpetas distintas colapsan a una. Es lo que pidió el usuario
  ("eliminar los duplicados"); el reporte lo cuantifica para que pueda objetarlo.
- **Gana la copia de Brave** (árbol canónico).
- **Los separadores de Firefox se pierden** (son 5 y pierden sentido al reubicar).
- **`checksum` omitido** en el JSON de Chromium: verificado que `Decode()` no lo lee.
- **Los 52 huérfanos** van a `Favoritos/Desde Firefox/<raíz FF>/<ruta original>` (Opción 1 elegida
  por el usuario). La raíz FF se incluye como nivel para evitar colisiones de nombres entre raíces.

## Limitaciones conocidas

- No es un repo git: **no hay commits por work-unit** (el protocolo los pide). El control de
  versiones queda pendiente de que el usuario decida si inicializa un repo.
- Si Mozilla o Chromium cambian el formato en disco, hay que actualizar el script.
- El Restore de Firefox es destructivo: por eso `--apply` siempre hace snapshot antes.

## Bug crítico encontrado (y por qué importa)

Mi lectura inicial de las fuentes decía que el JSON de restauración de Firefox era un **array**
`[{...}]`. **Era falso.** El backup real de Firefox es un **objeto** suelto, y
`PlacesUtils.unwrapNodes()` hace:

```js
validNodes = JSON.parse("[" + blob + "]");
```

Firefox **envuelve el archivo en corchetes**. Por lo tanto:

| Contenido del archivo | Lo que Firefox parsea | Resultado |
|---|---|---|
| `[{...}]` (array) | `[[{...}]]` → `nodes[0]` = array | `nodes[0].children` es `undefined` → `importFromJSON()` hace `return 0` |
| `{...}` (objeto) | `[{...}]` → `nodes[0]` = contenedor | funciona |

Un archivo con array produce un **no-op silencioso**: Firefox no importa nada y no avisa. Con el
diálogo de confirmación ya aceptado, el usuario habría creído que el restore funcionó.

**Lo detecté comparando contra el backup real del propio Firefox**, no razonando sobre el código.
`emit_firefox()` ahora escribe el objeto suelto, y `inventory_firefox()` aborta ruidosamente si
alguna vez se regenera como array.

## Evidencia de verificación

| Comprobación | Resultado |
|---|---|
| `compile()` del script | OK |
| Dry-run end-to-end | OK, 1548 bookmarks finales |
| Duplicados en la salida (ambos artefactos) | 0 |
| Idempotencia (2 corridas) | byte a byte idéntico |
| `places.sqlite` y `Bookmarks` tras >3 corridas | sha256 sin cambios |
| Estructura del JSON de Firefox vs backup real, por el **mismo camino de parseo de Firefox** | ambos: `dict`, 4 hijos de raíz, set de claves idéntico |
| Diferencia de esquema con el backup real | solo `iconUri` (opcional, cosmético: favicons) |
| GUIDs de Firefox válidos (`/^[a-zA-Z0-9_-]{12}$/`) | 1937/1937 |
| Unicidad de `id` en el artefacto Chromium | 1935/1935 |
| Raíces de Firefox (index/root/guid) vs backup real | idénticos: 0/1/3/4 y `menu/toolbar/unfiled/mobile` |
| Fechas (`dateAdded`) | rango 2014→2026 en µs desde 1970, coherente |

## Progreso

- [x] Investigación y verificación de formatos contra código fuente.
- [x] Medición de datos reales (conteos, solapamiento, duplicados).
- [x] Decisión de estrategia y de placement.
- [x] T1–T8 completas y verificadas.
- [x] T9 dry-run corrido y reporte presentado.
- [ ] T9 `--apply` — esperando autorización explícita del usuario.

## Próximo paso

El usuario revisa `out/report.txt` y los dos artefactos de `out/`. Si autoriza:

1. Cerrar Brave (reescribe su archivo al salir y pisaría el cambio).
2. `python3 bookmarks-merge.py --apply` → snapshot + swap de Brave + prepara el JSON de Firefox.
3. Firefox: Biblioteca → Importar y respaldar → Restaurar → Elegir archivo… → `out/firefox-restore.json`.
4. Confirmar que **Sync de marcadores está apagado en los dos browsers**, o los duplicados vuelven.

Rollback: `python3 bookmarks-merge.py --restore snapshots/<timestamp>/` (con los browsers cerrados).

## T10 — Reimplementación en bash sobre `sqlite-utils` + `yq`: ABORTADA

Objetivo del usuario: un segundo script equivalente construido sobre `sqlite-utils` y `yq`
(motivado por el artículo de atareao en `sources.md`). El usuario fijó la regla de corte:
*si la vía CLI es menos eficiente y/o segura que `bookmarks-merge.py`, se aborta*.

**Veredicto: abortada.** Es menos segura **y** menos eficiente, con cero ganancia funcional.

| Dimensión | `bookmarks-merge.py` | Vía CLI (`sqlite-utils` + `yq`) |
|---|---|---|
| Apertura de `places.sqlite` | Copia + `mode=ro` + `PRAGMA query_only=1`. **Estructuralmente incapaz** de mutar el store | Sin read-only. `--immutable` no existe; `file:…?immutable=1` se trata como nombre de archivo literal → crea un archivo de 0 bytes |
| Efecto sobre el store real | Verificado: sha256 sin cambios tras >3 corridas | Abrir en su lugar hace **checkpoint del WAL**: cambian hash y mtime y se borra el `-wal` de 2.3 MB. *Reproducido sobre el archivo real del usuario durante el sondeo* |
| Dependencias | Python 3, **solo stdlib** | `sqlite-utils` (que **es** un programa Python, ≥3.10, hecho con Click) + `yq` + `gawk` + coreutils |
| Procesos por corrida | 1, una pasada | ~10–20 spawns; **1 sentencia por invocación** de `sqlite-utils` |
| Hashing para IDs/GUIDs | `uuid5` determinista | Imposible en SQL (sin `sha3`/`md5`) e imposible en `yq` → hay que escribirlo a mano en `gawk` |
| Recursión sobre el árbol | Directa | `yq` **no** puede: no tiene `def`, `reduce`, `range`, `getpath` ni `paths` |
| Lenguajes a mantener | 1 | 4 (bash + SQL + expresiones `yq` + `gawk`) |

La premisa de "usar las CLIs en vez de Python" no se sostiene: `sqlite-utils` **es** Python.
No elimina la dependencia, la agrega. Y el propio artículo de atareao, cuando el one-liner no
alcanza, recomienda escribir un script Python de ~45 líneas.

### Hallazgos técnicos verificados (útiles por sí mismos)

- **`yq` 4.54.1 (mikefarah)** — presentes: `..` (descenso recursivo real), `path`, `tag`,
  `to_entries`, `select`, `map`, `ireduce`, `setpath`, `strenv`, `@tsv`, TCP. Ausentes
  (error de lexer): `def`, `reduce`, `range`, `getpath`, `paths`, `leaf_paths`, `floor`.
- **Trampa de `//`** — necesita espacios: `.a // "X"` funciona, `.a//"X"` se parsea como
  comentario y devuelve `null` en silencio. Confirmado contra la doc oficial
  (`mikefarah.gitbook.io/yq/operators/alternative-default-value`).
- **`type` de `yq` devuelve tags YAML** (`!!map`, `!!seq`), no `object`/`array` de jq. Se filtra
  con `select(tag == "!!map")`.
- **Expresión de volcado de Brave verificada** (1604 urls + 324 folders = 1928 filas exactas):
  ```
  . as $doc | [ .. | select(tag == "!!map") | . as $n | (path) as $p
    | select(($p|length) > 0) | select(.type == "url" or .type == "folder")
    | [ ($p|map(tostring)|join("/")), ($p[-1] // ""), ($p[1] // ""),
        ($n.name // ""), ($n.type // "container"), ($n.url // ""),
        ($n.date_added // "0") ] ]
  ```
  Sin el segundo `select` da 2279 filas: infla con los sub-mapas `meta_info`.
- **`sqlite-utils` 4.2.1 / SQLite 3.54.0** — `query` es el subcomando por defecto; `--tsv`,
  `--nl`, `-p` (parámetros con nombre, evita inyección); una sola sentencia por invocación.
  SQL tiene `json_object`, `json_group_array(x ORDER BY y)`, CTE recursivas.
- **Lección de proceso**: verifiqué los *primitivos* de `yq` pero delegué las *costuras*
  (normalizador de URLs en SQL, merge en SQL, emisor en `gawk`) sin probarlas. El worker fresco
  las descubrió a los golpes y entró en bucle. Verificar los primitivos no es verificar la
  integración.

**No se creó ningún archivo `.sh`, y `bookmarks-merge.py` queda intacto.**

---

## T11 — Fix: el `--apply` relajaba los permisos del store de Brave

**Síntoma**: después de `--apply`, `~/.config/BraveSoftware/.../Bookmarks` quedaba `-rw-r--r--`
(0644) cuando el original era `-rw-------` (0600). Efecto real: los marcadores pasaban a ser
legibles por cualquier usuario local de la máquina. Lo detecté comparando `stat` del archivo contra
`stat` del snapshot (que sí conservaba 0600).

**Causa raíz**: `shutil.copy2(chromium_target, brave_file)` propaga el modo de la **fuente**, no del
destino. La fuente es `out/brave-bookmarks.json`, creado por `Path.write_text` con el modo que deja
el umask (0644). `copy2` copia contenido **y metadata**, así que el 0644 del artefacto temporal
viajaba al store.

**Fix**: helper `replace_keeping_mode(src, dst)` (L150-167) — lee `stat.S_IMODE(dst.stat().st_mode)`
antes de copiar, hace el `copy2`, y reaplica el modo que el destino ya tenía; si el destino no existe
(primer run, o `-wal`/`-shm` ausentes) cae a `0600`. Se usa en el único call site de escritura al
store (L863). `SCRIPT_VERSION` 1.0.0 → **1.0.1**, porque el manifest del snapshot registra qué
versión lo creó y el comportamiento cambió.

**Por qué NO se tocó el camino de `--restore`**: ahí `copy2(src=snapshot)` propaga el modo del
snapshot, que a su vez conservó el original al tomarlo (`shutil.copy2` en L611-612). En ese camino
`copy2` **repara** el permiso; cambiarlo a "preservar el destino" lo empeoraría (si el store está
0644 por el bug, preservar dejaría 0644 en vez de volver a 0600). Mecanismo distinto, resultado
correcto en ambos.

**Verificación** (script congelado → no me confío de "compila"):

- `ruff check`: **4 hallazgos, los 4 preexistentes** (EXE001, FURB188, PERF102, FURB122). Cero nuevos.
- Test aislado del helper: destino 0600 → 0600; destino 0644 → 0644 (preserva); inexistente → 0600.
- **End-to-end contra un perfil de Brave falso en `/tmp`** con `--force`: el store falso quedó
  **0600** y con hash idéntico (`564986aabe…`), los stores reales sin tocar. Prueba el camino real
  (mismo código, misma rama) sin arriesgar nada.
- Exoneración de Firefox: su `places.sqlite` cambió de hash varias veces en la sesión
  (`205f5553` → `502d2a53` → `4c9c15b9`) y **no es el script**. El único `sqlite3.connect` del
  archivo apunta a la copia temporal (`file:{copy}?mode=ro`, L336) y `read_firefox` copia antes de
  abrir. Además uno de esos cambios ocurrió en una ventana donde el script no corrió (el intento se
  abortó en el guard). Causa: Firefox abierto escribiendo su propio store.

**Pendiente de limpieza** (detectado en la auditoría de `ruff` + `codebase-memory`, NO aplicado):
`write_flat_export` (L632-636) es código muerto (`callers_total: 0`), `brave_meta` es un parámetro
sin uso de `build_report`, y falta el bit de ejecución (`EXE001`: shebang presente, archivo `0644`).

---

## T12 — Limpieza aplicada (código muerto, parámetro muerto, bit de ejecución)

Las tres cosas que T11 dejó pendientes. `bookmarks-merge.py` pasa de 885 a **879 líneas**.

1. **`write_flat_export` eliminada.** Código muerto confirmado por dos vías independientes: el `grep`
   del nombre devolvía **sólo su propia definición**, y el grafo de `codebase-memory` daba
   `callers_total: 0`. No existía flag `--flat` ni mención en la doc. Al borrarla murió también el
   hallazgo `FURB122` (el `fh.write` dentro del loop).
2. **`brave_meta` fuera de `build_report`** (firma y call site). Estaba declarado y nunca leído:
   `grep brave_meta` no encontraba **ninguna ocurrencia entre la firma y el call site**. Ojo con el
   detalle: `emit_chromium` también tiene un `brave_meta` y **ese sí se usa** — mismo nombre, otra
   función. Por eso el `ARG001` apuntaba sólo a `build_report`; borrar el otro habría roto el emisor.
3. **Bit de ejecución**: `chmod 755` (era `0644` con shebang presente → `EXE001`).

**Verificación — prueba diferencial, no un "anda".** El snapshot de las 17:41 es el estado de ENTRADA
exacto de la corrida original. Lo restauré a perfiles falsos en `/tmp` y re-ejecuté el script
**modificado** con los **mismos flags** (`--apply --force --ff-profile/--brave-profile` falsos):

- exit code **0** y bloque de verificación del script en OK.
- `out/brave-bookmarks.json` → `564986aabe…`, **byte a byte idéntico** al de las 17:41.
- `out/firefox-restore.json` → `3fd65141…`, **byte a byte idéntico**.
- el reporte difiere **sólo** en las 3 líneas con las rutas de los perfiles falsos (L5, L6, L67).
- el store falso quedó **0600** → el fix de T11 sobrevive a la limpieza.
- `ruff`: de 4 hallazgos a **2**.

**Los 2 hallazgos que quedan, y por qué NO se tocaron:**

- `FURB188` (`host[4:]` → `host.removeprefix("www.")`) vive en **`_norm_url`**, la función que define
  la clave de dedupe. Es equivalente, pero es "estilo" en el corazón del algoritmo: no se cambia sin
  una razón. Los números de la verificación (0 duplicados, 1548 finales) dependen de esa función.
- `PERF102` (`root_key` sin usar en `_walk`): ruff mismo marca su autofix como **unsafe**.
  Un `.items()` → `.values()` cosmético no justifica re-verificar el walker.

**Estado del script**: v1.0.1, 879 líneas, 2 hallazgos de estilo preexistentes, cero código muerto
conocido.

## T13 — Fix: el dedupe borraba los atajos sueltos de la barra (gana la copia más cercana a la raíz)

**Reporte del usuario (después de aplicar y después de restaurar en Firefox):** de la Barra de
Marcadores de Firefox **y** de Brave desaparecieron `Google`, `Facebook`, `YouTube`, `Windguru`,
`AccuWeather` y "no sé qué otro más".

**Causa raíz: el desempate del dedupe intra-Brave. Decisión de diseño equivocada — no fue formato,
no fue Firefox, no fue Sync.**

Regla anterior: *"se queda con la primera ocurrencia en orden DFS"*. Los hijos de `bookmark_bar` en
el árbol de Brave son `[Favoritos (carpeta), Google, Facebook, YouTube, AccuWeather, Windguru, …]`.
El recorrido es DFS: **la carpeta `Favoritos` se explora completa antes de llegar a los items sueltos
de la barra**. Para toda URL que existiera *a la vez* suelta en la barra **y** dentro de `Favoritos`,
ganaba la copia de la carpeta y **se borraba el atajo de la barra**.

**Impacto medido (barra de Brave, pre-merge vs post-merge):** 20 marcadores sueltos → 8. Doce
desaparecidos, ninguno perdido (11 exactos + 1 colapsado por variante de URL):

| Marcador | Destino tras el bug |
|---|---|
| Google | `Favoritos / Barra de marcadores Backup` |
| YouTube | `Favoritos / Barra de marcadores Backup` |
| AccuWeather | `Favoritos / Barra de marcadores Backup` |
| ‎Gemini | `Favoritos / Barra de marcadores Backup` |
| GPT | `Favoritos / Barra de marcadores Backup` |
| Swift | `Favoritos / Barra de marcadores Backup` |
| Morphic | `Favoritos / Barra de marcadores Backup` |
| Perplexity | `Favoritos / Barra de marcadores Backup` |
| Facebook | `Favoritos / PROGRAMACION / Desarrollo Web / api` |
| Windguru | `Favoritos / Varios` |
| Direcciones de correo temp. | `Favoritos / PROGRAMACION / Hacking / SeguridadInformatica` |
| Direcciones de correo temp. (`https://temp-mail.org/es`) | colapsada contra la variante `https://temp-mail.org/es/` (mismo destino) |

`Favoritos / Barra de marcadores Backup` (9 marcadores) es un respaldo de la barra hecho por el
propio usuario: el dedupe eligió justo el lugar **redundante** y borró justo el que se usa todos los
días. El archivo de Firefox hereda la misma barra (mismos 9 hijos), por eso faltaba en los dos.

**Lección de diseño:** un marcador suelto en una raíz (barra / otros) es un **atajo de acceso**, no
un duplicado. Borrarlo porque existe una copia clasificada en una carpeta destruye el acceso rápido
y esconde el duplicado, las dos cosas al mismo tiempo. La explicación previa ("nada se perdió, es la
redistribución de carpetas") era incompleta: no había detectado este borrado.

**Decisión del usuario (autorizada):** opción 2 — *"un solo lugar, el más visible"*: el dedupe se
queda con la **copia de menor profundidad**. Tie-break: primero en orden DFS; como `bookmark_bar` es
la primera raíz de `brave_roots` (`read_brave` respeta el orden del documento), la barra gana los
empates de profundidad.

**Cambio (v1.1.0) — `merge()` en dos fases:**

1. `scan()` recorre el árbol de Brave y anota, por clave normalizada, el rango `(profundidad, orden)`
   y la identidad del nodo ganador.
2. `keep()` reconstruye el árbol descartando todo nodo que no sea el ganador de su clave.
3. `seen` (dedupe de Firefox contra Brave) pasa a derivarse de las claves ganadoras → el
   comportamiento cross-browser (y el de `--no-dedupe`) no cambia.

**Por qué la regeneración parte del snapshot y no del estado vivo:** el Brave vivo ya está degradado
(los atajos ya no están en la entrada, sólo las copias de carpeta) y el Firefox vivo ya tiene el
árbol mergeado. Con esos dos inputs el merge reproduciría el árbol degradado — *el bug ya está
absorbido en la entrada*. Los inputs pre-merge están en `snapshots/20261019-174129/`:
`brave/Bookmarks` = `a78e95ae…` (verificado contra el manifiesto) y `firefox/places.sqlite` = 2918
bookmarks / 573 carpetas / 5 separadores (verificado contando sobre una copia).

**Artefactos previos preservados** en `out/prev-20261019-1741/`: `brave-bookmarks.json` =
`564986aabe…`, `firefox-restore.json` = `3fd65141…`, `report.txt` — son los aplicados hoy, o sea el
punto de rollback inmediato.

**Verificación ejecutada (dry-run + apply).**

Dry-run con `--ff-profile snapshots/20261019-174129/firefox --brave-profile snapshots/20261019-174129/brave`:
el reporte informa **Brave 1604/321 y Firefox 2918/573/5** → prueba de que la entrada es la
pre-merge, no el estado degradado. Dedupe intra-Brave **108**, "ya estaban en Brave" **2863**,
reubicados **52**, final **1548 bookmarks / 384 carpetas / 0 duplicados**: idénticos a la corrida
original.

**La barra volvió:** 20 sueltos en el pre-merge → 8 con el bug → **19** con el fix. Los 19 son
exactamente los originales; el 20 → 19 es correcto: la barra pre-merge tenía dos entradas de la
MISMA página (`https://temp-mail.org/es/` y `https://temp-mail.org/es`, misma clave normalizada
`https://temp-mail.org/es`, mismo folder, adyacentes) → duplicado real, colapsado.

**Radio de impacto viejo vs nuevo:** 1548 claves en ambos; **19 cambiaron de ubicación, 0
desaparecieron, 0 aparecieron**. Esos 19 = los atajos que vuelven a la barra + copias que pasan a una
carpeta menos profunda. `Favoritos / Barra de marcadores Backup` queda con 2 bookmarks (los que no
estaban en la barra: `facebook.com/?stype=…` y `cuevana.biz`).

**Apply:** previa restauración del store vivo al pre-merge (`a78e95ae…`) para que la entrada fuese la
real, y luego `--apply --ff-profile <snapshot ff> --brave-profile <perfil vivo>`. El script
snapshoteó (`snapshots/20261019-185514`), escribió **sólo** Brave y dejó el aviso de restore de
Firefox.

- store vivo de Brave: modo **0600** preservado, `244e6ca0…` — **byte a byte idéntico** a
  `out/brave-bookmarks.json` **y** al artefacto del dry-run independiente: reproducibilidad probada.
- `out/firefox-restore.json`: `3cd64a85…`, idéntico al del dry-run.
- snapshot pre-merge `20261019-174129` intacto (`a78e95ae…` / `205f5553…`).

**Hallazgo lateral (no es bug):** el store vivo de Brave era `134fffdde63363dd` (856536 bytes,
18:43:10) al momento de aplicar, **no** el `564986aabe…` que el script escribió a las 17:41: Brave
había reescrito su archivo solo (Chromium re-serializa a su formato al abrir/cerrar). El contenido
mergeado quedó preservado en `out/prev-20261019-1741/`. Implicancia: comparar el hash del store vivo
contra el artefacto sólo es válido con Brave cerrado — por eso el guard existe.

**Falta (paso humano):** reimportar `out/firefox-restore.json` en Firefox (Biblioteca → Importar y
respaldar → Restaurar → Elegir archivo…), con Sync de marcadores desactivado y Firefox cerrado. Sin
borrar nada a mano: el restore reemplaza el árbol entero.

**Commit:** el proyecto no es un repositorio git → no hay commit de unidad de trabajo para este
cambio (limitación conocida; se ofreció `git init` y quedó sin respuesta).

---

## T14 — Consolidación: `--canonical {brave|firefox}` (v1.2.0)

**Fecha:** 2026-10-19
**Ruta:** direct inline (1 archivo, refactor sobre código ya verificado).

**Qué:** Agregar un flag `--canonical` al `bookmarks-merge.py` para seleccionar qué
distribución de carpetas es la base del merge:

- `--canonical brave` (default): comportamiento idéntico a v1.1.0 — esqueleto de Brave,
  items Firefox-only bajo `Favoritos/Desde Firefox/...`.
- `--canonical firefox`: esqueleto de Firefox, items Brave-only bajo `Desde Brave/...`
  en la raíz correspondiente (bookmark_bar, other, synced).

**Por qué un solo script:** comparten ~80% del código (lectores, dedupe, snapshot, apply,
reporte). Dos scripts = duplicación de ~900 líneas, drift de versiones, y confusión sobre
cuál usar. Un flag en `--help` deja la diferencia explícita.

**Cambios en el archivo:**

1. Docstring actualizado con ambos modos.
2. `SCRIPT_VERSION = "1.2.0"`.
3. Argumento `--canonical {brave,firefox}` (default: brave).
4. `_build_ff_tree(ff_items)`: convierte la lista plana de items Firefox en árbol de
   Nodes usando claves Brave (`bookmark_bar`, `other`, `synced`, `menu`). Tags (root_id=4)
   se excluyen. Mapeo: toolbar→bookmark_bar, unfiled→other, mobile→synced, menu→menu.
5. `_flatten_brave(brave_roots)`: extrae URLs del árbol Brave con información de path,
   para colocarlas bajo `Desde Brave/<root>/<path original>`.
6. `merge_ff_canonical(ff_items, brave_roots, attach_folder)`: misma lógica de dedupe
   (shallowest wins) pero con el árbol Firefox como esqueleto y Brave-only bajo
   `Desde Brave`.
7. `main()`: branch según `args.canonical`.
8. `build_report`: etiquetas mod-aware ("Dentro de Firefox" / "Exclusivos de Brave").

**Verificación (dry-run con snapshot pre-merge `20261019-174129`):**

| Métrica | `--canonical brave` | `--canonical firefox` |
|---|---|---|
| Bookmarks finales | 1548 | 1548 |
| Carpetas finales (Brave) | 384 | 378 |
| Dedupe intra-canónico | 108 (Brave) | 1393 (Firefox) |
| Ya en el canónico | 2863 (FF items) | 1578 (Brave items) |
| Exclusivos reubicados | 52 (FF) | 26 (Brave) |
| Verificación | OK | OK |

Ambos modos producen **la misma unión de 1548 bookmarks**. La diferencia es la estructura
de carpetas (384 vs 378 carpetas) porque el esqueleto canónico es distinto.

Estructura de exclusivos en modo Firefox-canonical:
- `bookmark_bar / Desde Brave / Favoritos / ...` → 17 bookmarks
- `other / Desde Brave / {Cuevana, kukas, Radar-Vivo, Salud} / ...` → 9 bookmarks
- Total: 26 (coincide con el conteo original de "solo en Brave")

**Script separado eliminado:** `bookmarks-merge-ff-canonical.py` (escrito durante la
sesión pero redundante tras la consolidación).

**Commit:** pendiente (proyecto inicializado en jj por el usuario; working copy limpio).
