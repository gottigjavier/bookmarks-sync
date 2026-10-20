# IA-sync — Herramienta de Convergencia de Marcadores

Converge los marcadores de Firefox y Brave en un único árbol deduplicado, con estructura de carpetas canónica seleccionable y seguridad completa de snapshot/rollback.

> Versión en inglés: [README-EN.md](README-EN.md)

## Por qué

Los navegadores almacenan marcadores en formatos incompatibles y divergen con el tiempo. Firefox usa SQLite (`places.sqlite` + WAL), Brave usa JSON de Chromium. Esta herramienta fusiona ambos en un solo árbol, elimina duplicados por URL normalizada, preserva marcadores exclusivos de cada navegador, y genera artefactos en el formato nativo de cada uno.

## Características

- **Unión completa**: se preservan marcadores de ambos navegadores; solo se eliminan duplicados reales.
- **Estructura canónica seleccionable**: `--canonical brave` (default) o `--canonical firefox` controla qué distribución de carpetas es el esqueleto.
- **Dedupe inteligente**: conserva la ocurrencia más superficial de cada URL (un atajo en la barra gana contra una copia enterrada tres carpetas adentro). Los empates se resuelven por orden DFS; la raíz canónica se escanea primero.
- **Marcadores exclusivos de Firefox** → se colocan bajo `Favoritos / Desde Firefox / <raíz FF> / <ruta original>`.
- **Marcadores exclusivos de Brave** → se colocan bajo `Desde Brave / <raíz> / <ruta original>` (modo Firefox-canonical).
- **Determinístico**: mismas entradas → mismas salidas. Correr dos veces produce artefactos idénticos.
- **Snapshot en cada `--apply`**: copia de seguridad automática de ambos almacenes antes de cualquier escritura.
- **Guardas de seguridad**: rechaza `--apply` con navegadores abiertos (Chromium reescribe su archivo al cerrar). `--force` lo anula.
- **`--restore`**: rollback de un solo comando desde cualquier snapshot.
- **`--only`**: aplicar a un solo navegador (`brave` o `firefox`) sin tocar el otro.
- **Cero dependencias**: solo librería estándar de Python 3. Sin instalaciones con pip.

## Requisitos

- Python 3.10+
- Firefox y/o Brave instalados (detectados automáticamente desde rutas estándar)
- Sin paquetes externos

## Inicio Rápido

```bash
# 1. Dry-run: ver qué cambiaría (no se escribe nada)
python3 bookmarks-merge.py

# 2. Revisar el reporte y los artefactos en out/
cat out/report.txt

# 3. Aplicar cuando esté satisfecho (cerrar navegadores primero!)
python3 bookmarks-merge.py --apply
```

## Uso

### Invocación básica

```bash
python3 bookmarks-merge.py [OPCIONES]
```

Correr sin `--apply` siempre es seguro: solo lee, escribe previews en `out/` y muestra un reporte.

### Todas las opciones

| Opción | Default | Descripción |
|---|---|---|
| `--apply` | off | Escribe los cambios en los navegadores. Sin esto: solo dry-run. |
| `--canonical {brave,firefox}` | `brave` | Qué distribución de carpetas es el esqueleto. |
| `--only {both,brave,firefox}` | `both` | Limita `--apply` a un solo navegador. |
| `--force` | off | Anula la guarda de navegadores abiertos. Peligroso. |
| `--restore SNAPSHOT_DIR` | — | Restaura ambos navegadores desde un snapshot y sale. |
| `--ff-profile RUTA` | auto | Ruta explícita del perfil de Firefox. |
| `--brave-profile RUTA` | auto | Ruta explícita del perfil de Brave. |
| `--attach-folder NOMBRE` | `Favoritos` | Carpeta en la barra de Brave donde se cuelga `Desde Firefox`. Solo aplica en `--canonical brave`. |
| `--out DIR` | `./out` | Directorio de salida para artefactos y reportes. |
| `--snapshots DIR` | `./snapshots` | Directorio para snapshots automáticos. |
| `--max-examples N` | `15` | Cuántos ejemplos de duplicados mostrar en el reporte. |
| `--no-dedupe` | off | Desactiva la deduplicación (solo debug). |

### Ejemplos

```bash
# Modo Firefox-canonical: la estructura de carpetas de Firefox es la base
python3 bookmarks-merge.py --canonical firefox

# Aplicar solo a Brave (Firefox queda intacto)
python3 bookmarks-merge.py --apply --only brave

# Restaurar desde un snapshot específico
python3 bookmarks-merge.py --restore snapshots/20261019-185514

# Usar rutas de perfil explícitas
python3 bookmarks-merge.py \
  --ff-profile ~/.mozilla/firefox/abc123.default-release \
  --brave-profile ~/.config/BraveSoftware/Brave-Browser/Default
```

## Cómo Funciona

### Modelo de merge

1. **Leer** ambos navegadores: Firefox desde una copia de `places.sqlite` (WAL reproducido en la copia, nunca en el original), Brave desde su JSON `Bookmarks`.
2. **Construir esqueleto** desde el árbol de carpetas del navegador canónico.
3. **Deduplicar** dentro del esqueleto: para cada URL normalizada, conservar la ocurrencia más cercana a una raíz (gana la menor profundidad; los empates van a orden DFS, raíz canónica escaneada primero).
4. **Colocar marcadores exclusivos** del otro navegador bajo la carpeta designada, preservando su jerarquía de subcarpetas original.
5. **Emitir** dos artefactos en formato nativo:
   - `out/brave-bookmarks.json` — JSON Chromium (escritura directa al almacén de Brave en `--apply`)
   - `out/firefox-restore.json` — JSON legacy de Firefox (restore manual vía Biblioteca → Importar y respaldar → Restaurar → Elegir archivo…)

### Normalización de URLs

Dos URLs se consideran duplicadas cuando normalizan a la misma clave:

- Esquema y host en minúsculas
- `www.` inicial eliminado del host
- Query string y fragmento eliminados
- Slash final eliminado del path

Ejemplos: `https://www.example.com/path/` y `https://example.com/path` son duplicados.

### Mapeo de raíces

| Raíz Firefox | Raíz Brave | Notas |
|---|---|---|
| `toolbar` (Barra de marcadores) | `bookmark_bar` | Barra de marcadores |
| `unfiled` (Otros marcadores) | `other` | Otros marcadores |
| `mobile` (Marcadores móviles) | `synced` | Marcadores móviles |
| `menu` (Menú de marcadores) | `menu` | Raíz extra; Chromium la ignora |
| `tags` (Etiquetas) | — | Se salta; las etiquetas son artefactos, no marcadores |

### Modos canónicos

**`--canonical brave`** (default): el árbol de carpetas de Brave es el esqueleto. Los marcadores exclusivos de Firefox se colocan bajo `Favoritos / Desde Firefox / <etiqueta raíz FF> / <ruta original>`.

**`--canonical firefox`**: el árbol de carpetas de Firefox es el esqueleto. Los marcadores exclusivos de Brave se colocan bajo `Desde Brave / <raíz Brave> / <ruta original>` en la raíz correspondiente.

Ambos modos producen la **misma unión** de marcadores; solo difiere la organización de carpetas.

## Artefactos de Salida

Después de un dry-run o `--apply`, el directorio `out/` contiene:

| Archivo | Formato | Propósito |
|---|---|---|
| `brave-bookmarks.json` | JSON Chromium | Escribir directamente al archivo `Bookmarks` de Brave |
| `firefox-restore.json` | JSON legacy Firefox | Restore manual vía Biblioteca de Firefox |
| `report.txt` | Texto plano | Reporte completo del merge (también se imprime en stdout) |

### Aplicar a Firefox (manual)

Firefox no tiene una ruta de importación por CLI soportada. El restore debe hacerse manualmente:

1. **Cerrar Firefox.**
2. Abrir Firefox → `Ctrl+Shift+O` (Biblioteca).
3. Importar y respaldar → Restaurar → Elegir archivo…
4. Seleccionar `out/firefox-restore.json`.
5. Aceptar el aviso de que los marcadores actuales serán reemplazados.

> **Importante**: NO borrar marcadores manualmente antes de restaurar. El restore de Firefox reemplaza el árbol completo.

### Aplicar a Brave (automático)

En `--apply`, el script escribe directamente al archivo `Bookmarks` de Brave, preservando los permisos existentes del archivo (típicamente `0600`). Brave debe estar cerrado — Chromium reescribe su archivo al salir y pisaría el cambio.

## Snapshots y Rollback

Cada `--apply` crea automáticamente un snapshot en `snapshots/<timestamp>/` que contiene:

- `firefox/places.sqlite` (más `-wal`/`-shm` si existen)
- `brave/Bookmarks` y `Bookmarks.bak` (si existen)
- `manifest.json` con hashes SHA-256 de cada archivo

### Restaurar desde snapshot

```bash
# Listar snapshots disponibles
ls snapshots/

# Restaurar un snapshot específico (ambos navegadores)
python3 bookmarks-merge.py --restore snapshots/20261019-185514
```

Los navegadores deben estar cerrados antes de restaurar (o usar `--force`).

## Características de Seguridad

| Guarda | Comportamiento |
|---|---|
| Chequeo de navegadores abiertos | `--apply` se rechaza si Firefox o Brave está corriendo. Usar `--force` para anular. |
| Preservación de permisos | El archivo `Bookmarks` de Brave mantiene sus permisos existentes (fallback: `0600`). |
| SQLite copy-on-read | `places.sqlite` nunca se abre en su lugar; se hace una copia y el WAL se reproduce en la copia. |
| GUIDs determinísticos | Derivados con `uuid5` desde un namespace estable, así que re-ejecutar produce artefactos idénticos. |
| Omisión de checksum | `BookmarkCodec::Decode()` de Chromium nunca lee `checksum`; el campo se omite y se recalcula en el próximo guardado de Brave. |
| Puerta de verificación | Los artefactos se verifican (conteo de URLs, cero duplicados) antes de permitir `--apply`. |

## Estructura del Proyecto

```
IA-sync/
├── bookmarks-merge.py          # Script principal (v1.2.0, solo stdlib)
├── README.md                   # Este archivo (español)
├── README-EN.md                # English version
├── sources.md                  # Enlaces de referencia (mantenido por el usuario)
├── out/                        # Artefactos generados
│   ├── brave-bookmarks.json    #   JSON Chromium para Brave
│   ├── firefox-restore.json    #   JSON legacy Firefox para restore
│   ├── report.txt              #   Reporte del merge
│   └── prev-*/                 #   Artefactos anteriores (rollback)
├── snapshots/                  # Snapshots automáticos en --apply
│   └── <timestamp>/
│       ├── firefox/            #   places.sqlite + WAL/SHM
│       ├── brave/              #   Bookmarks + Bookmarks.bak
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

Proyecto personal. Sin archivo de licencia.
