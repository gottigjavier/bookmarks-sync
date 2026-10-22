# Smoke test: raíz `menu` de Firefox proyectada en Chromium

Harness reproducible que verifica que `bookmarks-merge.py` **emite** la raíz
`menu` de Firefox ("Menú de marcadores") y que la familia Chromium la recibe
dentro de `other`, tanto en el artefacto como en el render real de Brave.

Sustituye la verificación manual que se había hecho con scripts sueltos en
`/tmp`: ahora el mismo veredicto se reproduce con un solo comando.

## Quick path

```bash
cd /home/javier/programacion/browsers/IA-sync
bash tests/run_smoke_test.sh
```

Ese comando:

1. limpia el work dir (para que la corrida sea reproducible);
2. copia **solo** los stores de bookmarks de los perfiles reales;
3. corre el dry-run de los pares `firefox,brave` y `firefox,chromium`;
4. aplica los artefactos **sobre las copias**;
5. abre Brave headless y verifica el render vía CDP/AX;
6. comprueba que los perfiles reales no cambiaron;
7. imprime el checklist manual del import en Firefox.

Salida esperada: exit `0` y la línea `### SMOKE TEST OK ###`.

## Flags y variables de entorno

| Flag / variable | Default | Para qué |
|---|---|---|
| `--work-dir RUTA` | `/tmp/opencode/iasync-smoke` | Dónde se escriben los fixtures, artefactos y snapshots |
| `--keep` | (off) | No limpia el work dir al empezar |
| `SMOKE_WORK_DIR` | `/tmp/opencode/iasync-smoke` | Mismo que `--work-dir` |
| `SMOKE_FF_PROFILE` | perfil en uso de Firefox | Perfil real de Firefox (solo lectura) |
| `SMOKE_BRAVE_PROFILE` | `~/.config/BraveSoftware/Brave-Browser/Default` | Perfil real de Brave (solo lectura) |
| `SMOKE_CHROMIUM_PROFILE` | `~/.config/chromium/Default` | Perfil real de Chromium (solo lectura) |
| `SMOKE_CDP_PORT` | `9222` | Puerto del debugging de Brave (si está ocupado se elige otro libre) |
| `SMOKE_BROWSER_BIN` | detección en el `PATH` | Binario de Brave para el paso 4 |
| `SMOKE_TOOL` | `../bookmarks-merge.py` | Ruta de la herramienta a probar |

Los perfiles reales se pueden **leer** pero nunca escribir. El harness opera
siempre sobre copias dentro del work dir.

## Archivos

| Archivo | Qué hace |
|---|---|
| `run_smoke_test.sh` | Orquestador: limpia, corre los 4 pasos en orden, verifica A8, imprime el resumen y el checklist manual |
| `lib.sh` | Rutas, resolución de perfiles reales, guards de seguridad, helpers. Es la única fuente de verdad (los scripts Python la consultan con `--print-env`) |
| `step1_prepare_fixtures.py` | Copia `places.sqlite*` y `Bookmarks` e inserta el marcador `MENU-ROOT-SMOKE-TEST` en la raíz `menu` de la **copia** |
| `step2_generate_artifacts.sh` | Dry-run de los dos pares + assert de la línea `NOTA` + asserts estructurales |
| `assert_artifacts.py` | Asserts de estructura de los dos artefactos (lo usan step2 y step3) |
| `step3_apply_and_verify.py` | `--apply` sobre las copias + asserts del store escrito (A5, A6) |
| `step4_verify_brave_cdp.py` | Abre Brave headless y verifica `Other bookmarks → Menú de marcadores → MENU-ROOT-SMOKE-TEST` vía CDP/AX (A7) |
| `FIREFOX_MANUAL_STEPS.md` | Pasos manuales + checklist del import en Firefox (lo imprime el orquestador, A9) |

Cada script corre standalone:

```bash
bash tests/step2_generate_artifacts.sh --work-dir /tmp/opencode/iasync-smoke
python3 tests/step1_prepare_fixtures.py --work-dir /tmp/opencode/iasync-smoke
python3 tests/assert_artifacts.py --out /tmp/opencode/iasync-smoke/out/firefox-brave --member brave
```

## Criterios de aceptación y dónde se verifican

| ID | Criterio | Dónde |
|---|---|---|
| A1 | El harness corre de punta a punta y termina en exit 0 | `run_smoke_test.sh` |
| A2 | El reporte contiene la línea `NOTA ... 1 marcador ... dentro de 'other'` | `step2_generate_artifacts.sh` (`grep -Fx` sobre `report.txt`) |
| A3 | El artefacto de Firefox tiene el marcador dentro de la raíz `menu` | `assert_artifacts.py::check_firefox_artifact` |
| A4 | El artefacto Chromium tiene `other → Menú de marcadores → marcador` | `assert_artifacts.py::check_chromium_artifact` |
| A5 | Tras `--apply`, el store de Brave es byte-idéntico al artefacto (sha256) | `step3_apply_and_verify.py` |
| A6 | Tras `--apply`, la copia de `places.sqlite` de Firefox queda intacta | `step3_apply_and_verify.py` |
| A7 | Brave renderiza `Other bookmarks → Menú de marcadores → marcador` | `step4_verify_brave_cdp.py` |
| A8 | Los perfiles reales no quedan modificados | `run_smoke_test.sh::verify_baseline` |
| A9 | El orquestador deja el instructivo manual de Firefox | `run_smoke_test.sh` (imprime `FIREFOX_MANUAL_STEPS.md`) |

## Verificación negativa: el harness detecta el bug

Un smoke test que no puede fallar no prueba nada. Para comprobar que los
asserts detectan la regresión, se puede **simular el bug** sobre una corrida
terminada y ver que el harness la rechaza:

```bash
# 1) Simular que la raíz `menu` NO se proyecta dentro de 'other'
cp -a /tmp/opencode/iasync-smoke /tmp/opencode/neg
python3 - <<'PY'
import json
p = '/tmp/opencode/neg/brave/Default/Bookmarks'
d = json.load(open(p))
other = d['roots']['other']
other['children'] = [c for c in other['children'] if c.get('name') != 'Menú de marcadores']
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=3)
PY

# 2) El assert estructural tiene que fallar (exit 1)
python3 tests/assert_artifacts.py --out /tmp/opencode/neg/out/firefox-brave --member brave

# 3) El assert de render (CDP/AX) tiene que fallar (exit 1)
python3 tests/step4_verify_brave_cdp.py --work-dir /tmp/opencode/neg --port 9333
```

Los dos tienen que salir con código `1` y el mensaje correspondiente
(`dentro de 'other' no hay una carpeta 'Menú de marcadores'` y
`no se encontro el treeitem 'Menú de marcadores'`).

Lo mismo aplica a la línea `NOTA` de A2: es un `grep -Fx` literal, así que
cualquier cambio de texto, de acentos o de espacios la hace fallar.

## Contrato de la herramienta que el harness respeta

- El flag `--brave-profile` / `--chromium-profile` apunta al **directorio que
  contiene** `Bookmarks` (p.ej. `.../Default`), no al archivo.
- Firefox se lee desde `<perfil>/places.sqlite` (+ `-wal`, `-shm`).
- Dry-run: exit `0` si la verificación interna pasó, exit `2` si falló.
- `--apply` reescribe el store Chromium-family y crea un snapshot.
  **Firefox nunca se auto-escribe.**
- La línea que se asserta en A2 es textual:

  ```
    NOTA  el formato Chromium no tiene raiz 'Menú de marcadores': 1 marcador del arbol fusionado se proyecto dentro de 'other'
  ```

  (Dos espacios tras `NOTA`; acentos solo en `Menú de marcadores`.)

- El canónico es **siempre Firefox**: es el único modo en que el árbol
  fusionado tiene la raíz `menu`, que es justo lo que se proyecta.

## Invariantes de seguridad

1. **Toda escritura ocurre dentro de `$SMOKE_WORK_DIR`.**
   Guard `smoke_assert_under_work` (en `lib.sh`), aplicado a cada ruta que el
   harness escribe.
2. **Los perfiles reales solo se leen.**
   Guard `smoke_assert_not_real_profile` (en `lib.sh`).
3. **No se copia material de credenciales.** Del perfil de Firefox solo se
   copian `places.sqlite`, `places.sqlite-wal` y `places.sqlite-shm`; de la
   familia Chromium solo `Bookmarks`. Nunca `logins.json`, `key4.db`,
   `cookies.sqlite` ni `Login Data`.
4. **Los scripts no ejecutan comandos de versionado** (`git`, `jj`) ni hacen
   push. El `rm -rf` del orquestador solo toca el work dir, y se niega a
   hacerlo si la ruta no está bajo `/tmp` o no fue indicada explícitamente.
5. **`--force` solo se usa en el `--apply`**, y únicamente porque el harness
   opera sobre copias: el browser real del usuario puede estar abierto y su
   estado es irrelevante para la copia.

Los guards viven **una sola vez**, en `lib.sh`. Los scripts Python los invocan
como subcomandos (`bash tests/lib.sh --assert-under-work RUTA`), así que bash y
Python no pueden discrepar sobre qué ruta es segura.

## Layout del work dir

```
/tmp/opencode/iasync-smoke/
  firefox/places.sqlite{,-wal,-shm}     copia del store de Firefox (+ marcador)
  brave/Default/Bookmarks               copia del store de Brave
  chromium/Default/Bookmarks            copia del store de Chromium
  brave/...                             perfil PROPIO que crea Brave headless (paso 4)
  out/firefox-brave/                    firefox-restore.json, brave-bookmarks.json, report.txt
  out/firefox-chromium/                 firefox-restore.json, chromium-bookmarks.json, report.txt
  snapshots/<marca>/<browser>/...       snapshot que crea --apply
  baseline/real-stores.sha256           estado de los perfiles reales (A8)
  baseline/real-menu-children           cantidad de hijos de la raiz `menu` real (A8)
  brave-cdp.log                         salida de Brave del paso 4
```

Cada par tiene su propio directorio de salida porque la herramienta escribe
siempre `report.txt` en el `--out` recibido: compartirlo haría que el segundo
par pisara el reporte del primero.

Ojo: `brave/` no es solo la copia del store. El paso 4 lanza Brave headless con
`--user-data-dir=<work>/brave`, así que ahí Brave crea su propio perfil
(`Local State`, `Default/`, cachés). Es estado de navegador desechable, no una
copia del perfil real del usuario, y no contiene credenciales.

## Troubleshooting

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `no se pudo resolver el perfil real de Firefox` | `profiles.ini` sin sección `Install*` y sin `*.default-release` | `SMOKE_FF_PROFILE=/ruta bash tests/run_smoke_test.sh` |
| `FALLA en el paso 4` | Brave no arrancó o cambió el layout del AX tree | mirar `/tmp/opencode/iasync-smoke/brave-cdp.log` |
| `el puerto 9222 estaba ocupado` | hay otro debugging remoto activo | `SMOKE_CDP_PORT=9333 bash tests/run_smoke_test.sh` (o se elige solo uno libre) |
| `INVARIANTE 1 violado` | se intentó escribir fuera del work dir | es un bug del harness: reportalo, no lo esquives |
| El par `firefox,chromium` queda omitido | no hay Chromium instalado | es esperable: el par `firefox,brave` es el que tiene que pasar |

## Qué NO cubre este harness

- **El import en Firefox real.** La UI no expone un importador automatizable y
  el restore reemplaza el árbol completo del usuario. Queda como paso manual
  documentado en `FIREFOX_MANUAL_STEPS.md`.
- **El replay del WAL.** Si el `-wal` real está vacío, el escenario no se
  ejercita.
- **`--restore`.** Requiere un estado de snapshot coherente; no está en el
  alcance de la prueba de humo.
- **La familia Chromium con `--write-both`.** El harness prueba el caso de un
  solo auto-target.

## Siguiente paso

Si el harness pasa, el fix de la raíz `menu` está verificado de punta a punta:
artefacto, store escrito y render real. Lo que queda para cerrar el círculo es
el import manual en Firefox.

