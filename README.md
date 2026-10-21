# IA-sync — Herramienta de Convergencia de Marcadores

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Firefox](https://img.shields.io/badge/Firefox-FF7139FF?style=flat-square&logo=firefox&logoColor=white)
![Brave](https://img.shields.io/badge/Brave-FA5A09?style=flat-square&logo=brave&logoColor=white)
![Chromium](https://img.shields.io/badge/Chromium-4285F4?style=flat-square&logo=chromium&logoColor=white)
![Linux](https://img.shields.io/badge/Plataforma-Linux-FCC624?style=flat-square&logo=linux&logoColor=white)
![Licencia](https://img.shields.io/badge/Licencia-BUSL%201.1-8A2BE2?style=flat-square)
![Versión](https://img.shields.io/badge/Versi%C3%B3n-2.0.0-007ec6?style=flat-square)

Converge los marcadores de exactamente dos navegadores (Firefox, Brave y Chromium) en un único árbol deduplicado, con estructura de carpetas **Base** seleccionable y seguridad completa de snapshot/rollback.

> Versión en inglés: [README-EN.md](README-EN.md)

## Tabla de Contenidos

- [Aviso](#aviso)
- [Por qué](#por-qué)
- [Características](#características)
- [Requisitos](#requisitos)
- [Inicio Rápido](#inicio-rápido)
- [Uso](#uso)
- [Interfaz Gráfica (GUI)](#interfaz-gráfica-gui)
- [Cómo Funciona](#cómo-funciona)
- [Artefactos de Salida](#artefactos-de-salida)
- [Snapshots y Rollback](#snapshots-y-rollback)
- [Características de Seguridad](#características-de-seguridad)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Detalles de Formato Verificados](#detalles-de-formato-verificados)
- [Licencia](#licencia)

---

## Aviso

IA-sync es un proyecto **no comercial**, de carácter personal: se distribuye sin fines de lucro, sin soporte dedicado y sin garantías de uso profesional. Se licencia bajo **BUSL 1.1** (`Additional Use Grant: None`): se permite copiar, modificar y redistribuir con **uso no productivo**; cualquier uso comercial requiere una licencia del titular. El `Change Date` es el **21 de octubre de 2030**, fecha en la que la obra pasa a gobernarse por la **GPL v3.0 o posterior**. Ver [Licencia](#licencia).

Las **rutas de configuración dependen del sistema** en el que se ejecute la herramienta. Los valores por defecto asumen una instalación en Linux (`~/.mozilla/firefox`, `~/.config/BraveSoftware/Brave-Browser`, `~/.config/chromium`) y pueden no coincidir con tu instalación, tu usuario o tu distribución de escritorio. Antes de ejecutar `--apply`, configura explícitamente las rutas que correspondan mediante `--ff-profile`, `--brave-profile`, `--chromium-profile`, `--out` y `--snapshots` (o los campos equivalentes de la GUI) y contrasta las rutas resueltas en el reporte (`out/report.txt`).

> Configurar estas rutas es responsabilidad del usuario: la herramienta no adivina ubicaciones fuera de los supuestos de sus valores por defecto y aborta si no puede resolver un perfil.

## Por qué

Los navegadores almacenan marcadores en formatos incompatibles y divergen con el tiempo. Firefox usa SQLite (`places.sqlite` + WAL); Brave y Chromium usan el JSON de Chromium. La herramienta fusiona los dos navegadores elegidos en un solo árbol, elimina duplicados por URL normalizada, preserva los marcadores exclusivos de cada uno y genera artefactos en el formato nativo de cada navegador.

## Características

- **Sincronización por pares**: exactamente dos navegadores por corrida, elegidos con `--pair`: `firefox,brave`, `firefox,chromium` o `brave,chromium` (el orden no importa).
- **Base obligatoria y explícita**: `--canonical` (sin default) elige qué distribución de carpetas es el esqueleto (**Base**), y debe ser un miembro del par. No existe navegador Base implícito.
- **Unión completa**: se preservan los marcadores de ambos miembros del par; solo se eliminan duplicados reales.
- **Dedupe inteligente**: conserva la ocurrencia más superficial de cada URL (un atajo en la barra gana contra una copia enterrada tres carpetas adentro). Los empates se resuelven por orden DFS; la raíz Base se escanea primero.
- **Exclusivos preservados bajo `Desde <otro navegador>`**: la ruta exacta depende de si la Base es de la familia Chromium o Firefox (ver *Modos Base y anclaje*).
- **Determinístico**: mismas entradas → mismas salidas. Correr dos veces produce artefactos idénticos.
- **Snapshot en cada `--apply`**: copia de seguridad de los dos miembros del par antes de cualquier escritura.
- **Guardas de seguridad**: rechaza `--apply` si un miembro del par está corriendo (Chromium reescribe su archivo al cerrar). `--force` lo anula.
- **`--restore`**: rollback de un solo comando desde cualquier snapshot; compatible con los snapshots de la v1.x.
- **`--only`**: limita `--apply` a un miembro del par, dejando intacto al otro.
- **`--write-both`** *(pares Chromium+Chromium)*: escribe EN VIVO en **ambos** navegadores (default: solo la Base). Peligroso si están abiertos.
- **Perfiles configurables**: `--ff-profile`, `--brave-profile` y `--chromium-profile`; solo se resuelven los perfiles de los miembros del par.
- **Interfaz gráfica (GUI)**: `bookmarks_gui.py` — tkinter, solo stdlib, tema oscuro, emojis, diálogo de confirmación explicativo, checkbox "Actualizar ambos navegadores Chromium en vivo" para pares Chromium+Chromium.
- **Cero dependencias**: solo librería estándar de Python 3. Sin instalaciones con pip.

## Requisitos

- Python 3.10+
- Dos navegadores instalados entre Firefox, Brave y Chromium (perfiles detectados automáticamente desde rutas estándar)
- Sin paquetes externos

## Inicio Rápido

```bash
# 1. Dry-run: ver qué cambiaría (no se escribe nada en los navegadores)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# 2. Revisar el reporte y los artefactos en out/
cat out/report.txt

# 3. Aplicar cuando esté satisfecho (cerrar los navegadores del par primero)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply
```

## Uso

### Invocación básica

```bash
python3 bookmarks-merge.py --pair A,B --canonical X [OPCIONES]
```

`--pair` y `--canonical` son obligatorios: sin ellos el script termina con error (no hay Base por defecto). Correr sin `--apply` siempre es seguro: solo lee, escribe previews en `out/` y muestra un reporte.

### Pares válidos

| `--pair` (orden indiferente) | `--canonical` aceptado |
|---|---|
| `firefox,brave` | `firefox`, `brave` |
| `firefox,chromium` | `firefox`, `chromium` |
| `brave,chromium` | `brave`, `chromium` |

### Todas las opciones

| Opción | Default | Descripción |
|---|---|---|
| `--pair A,B` | — (obligatorio) | Par de navegadores a sincronizar, coma separada y orden indiferente. Ver pares válidos. |
| `--canonical NOMBRE` | — (obligatorio) | Navegador cuyo árbol de carpetas es el esqueleto (**Base**); debe ser un miembro del par. Sin default. |
| `--apply` | off | Escribe los cambios en los navegadores. Sin esto: solo dry-run. |
| `--only NOMBRE` | `both` | Limita `--apply` a un miembro del par: `both` o el nombre de un miembro. |
| `--write-both` | off | **Solo pares Chromium+Chromium**: escribe EN VIVO en AMBOS navegadores (default: solo la Base; requiere `--apply`). Peligroso: dos almacenes vivos simultáneos pueden corromperse si los navegadores están abiertos. |
| `--force` | off | Anula la guarda de navegadores abiertos. Peligroso. |
| `--restore SNAPSHOT_DIR` | — | Restaura los archivos del par desde un snapshot y sale. Requiere también `--pair` y `--canonical`. |
| `--ff-profile RUTA` | auto | Ruta explícita del perfil de Firefox (por defecto: el perfil activo de `profiles.ini`). |
| `--brave-profile RUTA` | auto | Ruta explícita del perfil de Brave (por defecto: `~/.config/BraveSoftware/Brave-Browser/Default`). |
| `--chromium-profile RUTA` | auto | Ruta explícita del perfil de Chromium (por defecto: `~/.config/chromium/Default`). |
| `--attach-folder NOMBRE` | `Favoritos` | Carpeta en `bookmark_bar` donde se cuelga `Desde <otro>`. Solo se usa cuando la Base es de la familia Chromium; con `--canonical firefox` se ignora. |
| `--out DIR` / `--out-dir DIR` | `out/` | Directorio de salida para artefactos y reportes. |
| `--snapshots DIR` | `./snapshots` | Directorio para snapshots automáticos. |
| `--max-examples N` | `15` | Cuántos ejemplos de duplicados mostrar en el reporte. |
| `--no-dedupe` | off | Desactiva la deduplicación (solo debug). |
| `-h` / `--help` | — | Muestra la ayuda y sale. |

Solo se resuelven los perfiles de los miembros del par: una corrida `brave,chromium` nunca toca la configuración de Firefox.

### Ejemplos

```bash
# Firefox↔Brave con la estructura de Brave como Base
python3 bookmarks-merge.py --pair firefox,brave --canonical brave

# Firefox↔Chromium con Firefox como Base (--attach-folder se ignora en este modo)
python3 bookmarks-merge.py --pair firefox,chromium --canonical firefox

# Brave↔Chromium: en --apply solo se escribe el almacén de la Base
python3 bookmarks-merge.py --pair brave,chromium --canonical chromium

# Brave↔Chromium: escribir en AMBOS almacenes vivos (requiere --apply, ¡cierra los navegadores!)
python3 bookmarks-merge.py --pair brave,chromium --canonical brave --write-both --apply

# Aplicar solo a un miembro del par (el otro queda intacto)
python3 bookmarks-merge.py --pair firefox,brave --canonical brave --apply --only brave

# Restaurar un snapshot del par firefox,brave
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore snapshots/20261019-185514

# Rutas de perfil explícitas (solo se resuelven los miembros del par;
# reemplazar la ruta de Firefox por la del perfil propio)
python3 bookmarks-merge.py --pair firefox,brave --canonical firefox \
  --ff-profile ~/.mozilla/firefox/14yqiklo.default-release \
  --brave-profile ~/.config/BraveSoftware/Brave-Browser/Default
```

## Interfaz Gráfica (GUI)

`bookmarks_gui.py` ofrece el CLI completo en una ventana tkinter (solo stdlib). No duplica lógica: construye el mismo comando y lo ejecuta por subprocess, y enumera navegadores, pares y defaults desde `--list-browsers --json` (el registry del CLI es la única fuente de verdad). La interfaz usa un tema oscuro de grises (`#1a1a1a` de fondo, tarjetas `#232323` planas sin bordes) con íconos emoji en títulos y botones.

```bash
python3 bookmarks_gui.py              # abre la ventana
python3 bookmarks_gui.py --self-test  # prueba la lógica pura, sin display
```

- **🔀 Sincronización**: par A/B (no pueden ser iguales), Base limitado a los dos miembros y `--only`.
- **👤 Perfiles**: una fila por navegador del registro; solo los miembros del par quedan activos (el resto, deshabilitados) y el campo muestra en gris el perfil por defecto hasta que se lo edita (si queda igual al default, no se envía el flag). "📂 Examinar…" avisa si no encuentra `places.sqlite` (Firefox) o `Bookmarks` (Chromium), pero conserva la ruta elegida.
- **⚙ Opciones**: `--attach-folder`, `--out`, `--snapshots`, `--max-examples`, `--no-dedupe`, `--force` y **`--write-both`** (checkbox "Actualizar ambos navegadores Chromium en vivo — peligroso", solo visible en pares Chromium+Chromium, off por defecto).
- **⚡ Acciones**: previsualización (solo lectura), `--apply` y `--restore` con **diálogo de confirmación explicativo** que muestra:
  - **ESTRUCTURA BASE**: qué navegador provee la jerarquía de carpetas
  - **QUÉ PASA CON CADA NAVEGADOR**: acción explícita por navegador (auto-escritura con contexto de Base, restore manual de Firefox, copia manual, o excluido por `--only`)
  - Advertencia destacada para Firefox (restore manual obligatorio)
  - Acceso directo a `out/` y `report.txt`
  - "↺ Valores por defecto" para volver todos los campos y selecciones al estado inicial
  - Al final de la fila, "✖ Cerrar" sale por el mismo camino seguro que el botón de la ventana
- **Checkbox `--write-both`**: solo aparece cuando el par seleccionado es Chromium+Chromium (Brave+Chromium). Off por defecto. Al marcarlo, el diálogo de confirmación indica "activado por --write-both" para el miembro no-Base.

La GUI no muestra un panel de log: el CLI escribe el detalle completo en `out/report.txt` (accesible con "🧾 Abrir report.txt"). El avance se sigue en la barra de estado inferior —"⏳ Proceso en curso…" con el comando compactado, y "✅ Proceso finalizado (código N)." al terminar—; una salida distinta de cero abre un diálogo con el código y la ruta del reporte. Mientras corre un proceso, los botones de acción quedan deshabilitados (el de cerrar sigue activo); cerrar la ventana con una corrida activa pide confirmación y termina el subprocess.

## Cómo Funciona

### Modelo de merge

1. **Leer** los dos miembros del par: Firefox desde una copia de `places.sqlite` (WAL reproducido en la copia, nunca en el original); Brave y Chromium desde su JSON `Bookmarks`.
2. **Construir esqueleto** desde el árbol de carpetas del navegador Base.
3. **Deduplicar** dentro del esqueleto: para cada URL normalizada, conservar la ocurrencia más cercana a una raíz (gana la menor profundidad; los empates van a orden DFS, raíz Base escaneada primero).
4. **Colocar marcadores exclusivos** del otro miembro bajo `Desde <otro>`, preservando su jerarquía de subcarpetas original.
5. **Emitir un artefacto por miembro del par** en formato nativo:
   - `out/firefox-restore.json` — solo si `firefox` está en el par. JSON legacy de Firefox (restore manual vía Biblioteca → Importar y respaldar → Restaurar → Elegir archivo…)
   - `out/<navegador>-bookmarks.json` — para cada miembro de la familia Chromium: `out/brave-bookmarks.json` y/o `out/chromium-bookmarks.json`. JSON Chromium (escritura directa al almacén vivo según *Qué escribe `--apply`*)
   - `out/report.txt` — reporte completo del merge (también se imprime en stdout)

### Normalización de URLs

Dos URLs se consideran duplicadas cuando normalizan a la misma clave:

- Esquema y host en minúsculas
- `www.` inicial eliminado del host
- Query string y fragmento eliminados
- Slash final eliminado del path

Ejemplos: `https://www.example.com/path/` y `https://example.com/path` son duplicados.

### Mapeo de raíces

| Raíz Firefox | Raíz Chromium (Brave y Chromium) | Notas |
|---|---|---|
| `toolbar` (Barra de marcadores) | `bookmark_bar` | Barra de marcadores |
| `unfiled` (Otros marcadores) | `other` | Otros marcadores |
| `mobile` (Marcadores móviles) | `synced` | Marcadores móviles |
| `menu` (Menú de marcadores) | `menu` | Raíz extra; Chromium la ignora |
| `tags` (Etiquetas) | — | Se salta; las etiquetas son artefactos, no marcadores |

### Modos Base y anclaje

Ambos modos producen la **misma unión** de marcadores; solo difiere la organización de carpetas. El reporte muestra el ancla exacta en la línea `Destino`.

**Base de familia Chromium** (`--canonical brave` o `--canonical chromium`): el árbol de la Base es el esqueleto. Los marcadores exclusivos del otro navegador se cuelgan bajo `bookmark_bar / <attach-folder> / Desde <otro> / <etiqueta raíz> / <ruta original>`. Si la carpeta `<attach-folder>` no existe en la barra, los hijos de la barra se envuelven en una carpeta `Bookmarks bar` y el ancla cuelga ahí.

**`--canonical firefox`**: el árbol de Firefox es el esqueleto. Los marcadores exclusivos del otro navegador se cuelgan en `Desde <otro>` al tope de cada raíz, preservando la ruta original debajo. `--attach-folder` se acepta pero no se usa.

## Artefactos de Salida

Después de un dry-run o `--apply`, el directorio `out/` contiene un artefacto por miembro del par más el reporte:

| Archivo | Emitido cuando | Formato y propósito |
|---|---|---|
| `firefox-restore.json` | `firefox` está en el par | JSON legacy de Firefox; restore manual vía Biblioteca |
| `brave-bookmarks.json` | `brave` está en el par | JSON Chromium para el archivo `Bookmarks` de Brave |
| `chromium-bookmarks.json` | `chromium` está en el par | JSON Chromium para el archivo `Bookmarks` de Chromium |
| `report.txt` | siempre | Reporte completo del merge (también se imprime en stdout) |

### Qué escribe `--apply`

| Par | Escritura automática en el almacén vivo | Siempre manual |
|---|---|---|
| `firefox,brave` | Brave | Restore de Firefox (`firefox-restore.json`) |
| `firefox,chromium` | Chromium | Restore de Firefox (`firefox-restore.json`) |
| `brave,chromium` | Solo el almacén de la Base | Copia manual del artefacto del miembro no-Base (el reporte imprime origen y destino) |

**Con `--write-both` (pares Chromium+Chromium):** se escriben **ambos** almacenes vivos (la Base y el no-Base). Requiere `--apply` y que **ambos** navegadores estén cerrados (o `--force`).

Nunca se escriben dos almacenes vivos en una misma corrida sin `--write-both`. `--only` restringe lo anterior a un solo miembro del par.

### Aplicar a Firefox (manual)

Cuando `firefox` está en el par, su almacén nunca se toca automáticamente: Firefox no tiene una ruta de importación por CLI soportada. El restore debe hacerse manualmente:

1. **Cerrar Firefox.**
2. Abrir Firefox → `Ctrl+Shift+O` (Biblioteca).
3. Importar y respaldar → Restaurar → Elegir archivo…
4. Seleccionar `out/firefox-restore.json`.
5. Aceptar el aviso de que los marcadores actuales serán reemplazados.

> **Importante**: NO borrar marcadores manualmente antes de restaurar. El restore de Firefox reemplaza el árbol completo.

### Aplicar al almacén Chromium (automático)

Cuando el miembro de la familia Chromium es el objetivo automático (el único miembro Chromium del par `firefox,*`, o la Base en `brave,chromium`), `--apply` escribe directamente en su archivo `Bookmarks`, preservando los permisos existentes del archivo (típicamente `0600`). Ese navegador debe estar cerrado — Chromium reescribe su archivo al salir y sobrescribiría el cambio.

En pares `brave,chromium`, el miembro no-Base no se escribe: el reporte imprime la instrucción de copia manual (`out/<navegador>-bookmarks.json` sobre el `Bookmarks` del perfil correspondiente).

**Con `--write-both`**: ambos almacenes Chromium se sobrescriben. El reporte muestra ambas líneas "archivo reemplazado".

## Snapshots y Rollback

Cada `--apply` crea automáticamente un snapshot en `snapshots/<timestamp>/` con un directorio por miembro del par:

- `firefox/places.sqlite` (más `-wal`/`-shm` si existen)
- `brave/Bookmarks` y `Bookmarks.bak` (si existen); para `chromium`, el mismo contenido bajo `chromium/`
- `manifest.json` con hashes SHA-256 de cada archivo

### Restaurar desde snapshot

`--restore` es pair-aware: restaura únicamente los miembros del `--pair` elegido que existan en el snapshot; los miembros ausentes se omiten con un aviso. Los snapshots de la v1.x (solo `firefox/` + `brave/`) se restauran sin problemas con `--pair firefox,brave`. Igual que `--apply`, exige `--pair` y `--canonical`.

```bash
# Listar snapshots disponibles
ls snapshots/

# Restaurar un snapshot del par firefox,brave
python3 bookmarks-merge.py --pair firefox,brave --canonical brave \
  --restore snapshots/20261019-185514
```

Los navegadores deben estar cerrados antes de restaurar (o usar `--force`).

## Características de Seguridad

| Guarda | Comportamiento |
|---|---|
| Chequeo de navegadores abiertos | `--apply` se rechaza si un miembro del par (Firefox, Brave o Chromium) está corriendo. Los demás navegadores solo generan un aviso de análisis de solo lectura. `--force` anula la guarda. |
| Snapshot pre-aplicación | Antes de escribir nada, cada `--apply` snapshottea los dos miembros del par. |
| Un solo almacén vivo por corrida | En pares `brave,chromium` solo se escribe el almacén de la Base; el resto es manual. Con `--write-both` se escriben ambos (requiere ambos cerrados). |
| Preservación de permisos | El archivo `Bookmarks` de la familia Chromium mantiene sus permisos existentes (fallback: `0600`). |
| SQLite copy-on-read | `places.sqlite` nunca se abre en su lugar; se hace una copia y el WAL se reproduce en la copia. |
| GUIDs determinísticos | Derivados con `uuid5` desde un namespace estable, así que re-ejecutar produce artefactos idénticos. |
| Omisión de checksum | `BookmarkCodec::Decode()` de Chromium nunca lee `checksum`; el campo se omite y se recalcula en el próximo guardado del navegador. |
| Puerta de verificación | Los artefactos se verifican (conteo de URLs, cero duplicados) antes de permitir `--apply`. |

## Estructura del Proyecto

```
IA-sync/
├── bookmarks-merge.py          # Script principal (v2.0.0, solo stdlib)
├── bookmarks_gui.py            # Interfaz gráfica (tkinter, solo stdlib)
├── README.md                   # Este archivo (español)
├── README-EN.md                # English version
├── LICENSE                     # BUSL 1.1 license
├── sources.md                  # Enlaces de referencia (mantenido por el usuario)
├── out/                        # Artefactos generados (según el par de la corrida)
│   ├── brave-bookmarks.json    #   JSON Chromium para Brave (si brave está en el par)
│   ├── chromium-bookmarks.json #   JSON Chromium para Chromium (si chromium está en el par)
│   ├── firefox-restore.json    #   JSON legacy Firefox (si firefox está en el par)
│   ├── report.txt              #   Reporte del merge
│   └── prev-*/                 #   Copias manuales de artefactos anteriores
├── snapshots/                  # Snapshots automáticos en --apply
│   └── <timestamp>/
│       ├── firefox/            #   places.sqlite + WAL/SHM
│       ├── brave/              #   Bookmarks + Bookmarks.bak
│       ├── chromium/           #   Bookmarks + Bookmarks.bak (si chromium está en el par)
│       └── manifest.json       #   Hashes SHA-256
├── backups/                    # Copias de protección (manuales)
│   ├── firefox-auto-backup-*.jsonlz4
│   └── firefox-pre-merge-*.json
└── odd/tasks/                  # Documentos de tareas de desarrollo
    └── bookmarks-merge.md
```

## Detalles de Formato Verificados

Estos hechos fueron verificados contra el código fuente upstream, no asumidos:

| Hecho | Fuente |
|---|---|
| `Decode()` de Chromium nunca lee ni valida `checksum` | `components/bookmarks/browser/bookmark_codec.cc` |
| UUIDs de carpetas permanentes están hard-codeados; no se leen del archivo | `bookmark_codec.cc` (~línea 290) |
| GUID inválido/duplicado → se reasigna uno random v4 automáticamente | `bookmark_codec.cc` |
| `importFromFile(path, {replace: true})` de Firefox reemplaza el árbol completo | `toolkit/components/places/BookmarkJSONUtils.sys.mjs` |
| Firefox envuelve el JSON en `[...]` antes de parsear (`JSON.parse("[" + blob + "]")`) | `PlacesUtils.unwrapNodes()` |
| Formato de GUID de Firefox: `/^[a-zA-Z0-9_-]{12}$/` | `toolkit/components/places/PlacesUtils.sys.mjs` |
| `date_added` de Chromium: microsegundos desde 1601-01-01 | Código fuente Chromium |
| `dateAdded` de Firefox: microsegundos desde 1970-01-01 | Código fuente Firefox |

## Licencia

[BUSL 1.1](LICENSE) — Business Source License 1.1. Copyright (c) 2026 gottigjavier.

| Parámetro | Valor |
|---|---|
| Licensor | gottigjavier |
| Licensed Work | IA-sync versión 2.0.0 y posteriores, (c) 2026 gottigjavier |
| Additional Use Grant | `None` — sin uso productivo; el uso comercial requiere licencia del titular |
| Change Date | 2030-10-21 (o el cuarto aniversario de la primera distribución pública, lo que ocurra antes) |
| Change License | GPL v3.0 or later |

Hasta el `Change Date`, BUSL 1.1 **no es una licencia de código abierto aprobada por la OSI**: el código está disponible, pero sin permiso de uso productivo. Desde esa fecha, la obra se rige por la GPL v3.0 o posterior.