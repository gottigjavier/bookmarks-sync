# Importar el artefacto de Firefox a mano

El harness deja el archivo de restore listo. **Firefox nunca se auto-escribe**:
la herramienta no tiene forma de escribir el store de Firefox de forma segura,
y el importador de Firefox **reemplaza** el árbol completo de marcadores. Por eso
este paso es manual y lo tiene que hacer una persona.

| Qué | Dónde |
|---|---|
| Artefacto a importar | `/tmp/opencode/iasync-smoke/out/firefox-brave/firefox-restore.json` |
| Alternativa (par con Chromium) | `/tmp/opencode/iasync-smoke/out/firefox-chromium/firefox-restore.json` |
| Snapshot del perfil real | `/tmp/opencode/iasync-smoke/snapshots/<marca-de-tiempo>/firefox/` |

Si corriste el harness con `--work-dir RUTA`, las rutas están bajo `RUTA` en vez
de `/tmp/opencode/iasync-smoke`.

## Antes de empezar

- [ ] **Cerrá Firefox por completo** (incluida la ventana de "reiniciar para
      actualizar"). Firefox mantiene `places.sqlite` abierto y el import
      pisa el árbol en memoria.
- [ ] Verificá que el snapshot existe: es el respaldo del estado anterior del
      perfil. `ls /tmp/opencode/iasync-smoke/snapshots/*/firefox/`
- [ ] Si el perfil real tiene marcadores que no querés perder, sacá tu propia
      copia: `cp ~/.mozilla/firefox/<perfil>/places.sqlite ~/places.sqlite.bak`

## Pasos

1. Abrí Firefox.
2. `Ctrl+Shift+O` → se abre la **Biblioteca** (la biblioteca de marcadores).
3. Menú **Importar y respaldar** → **Restaurar** → **Elegir archivo...**.
4. Elegí `firefox-restore.json` (el archivo que dejó el harness).
5. Aceptá el aviso de que **se borran los marcadores actuales**. Esto es
   normal: el restore reemplaza el árbol entero, no lo fusiona.
6. Revisá la barra de marcadores y el menú **Menú de marcadores**.

## Checklist de verificación

- [ ] La **barra de marcadores** conserva las carpetas que tenías.
- [ ] El menú **Menú de marcadores** tiene el marcador `MENU-ROOT-SMOKE-TEST`
      (es el marcador de prueba que el harness insertó en la raíz `menu`).
- [ ] El marcador apunta a `https://smoke-test.invalid/menu-root`.
      (`.invalid` es un TLD reservado que nunca resuelve: es seguro)
- [ ] En **Otros marcadores** está la carpeta `Desde Brave` (o `Desde Chromium`),
      con los marcadores que aportó el otro navegador.
- [ ] No quedaron marcadores duplicados.

## Después del import

El harness **sigue pasando** después de este import: el fixture normaliza la
raíz `menu` de su copia (ver `README.md`), así que el marcador que quedó en tu
perfil real no lo afecta y la corrida siguiente vuelve a salir en verde sin
cambios.

## Si algo sale mal

El harness no toca el perfil real, así que el restore es reversible. Cerrá
Firefox antes de restaurar los archivos y volvé a abrirlo después:

```bash
SNAP=$(ls -d /tmp/opencode/iasync-smoke/snapshots/*/firefox | tail -1)
cp "$SNAP/places.sqlite"     ~/.mozilla/firefox/<perfil>/places.sqlite
cp "$SNAP/places.sqlite-wal" ~/.mozilla/firefox/<perfil>/places.sqlite-wal 2>/dev/null || true
cp "$SNAP/places.sqlite-shm" ~/.mozilla/firefox/<perfil>/places.sqlite-shm 2>/dev/null || true
```

## Qué NO cubre este paso

- La herramienta **no** verifica el import en Firefox: no hay forma
  automatizable de dispararlo sin reemplazar el árbol del usuario.
- La cobertura automática de render es **solo** de la familia Chromium
  (Brave, vía CDP/AX). Firefox no expone CDP.
- El archivo de restore es un **objeto raíz**, no un array JSON: es lo que
  espera `PlacesUtils.unwrapNodes()`.
