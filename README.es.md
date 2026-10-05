# Invasor

[English](README.md) · **Español**

Panel propio dentro de la interfaz de mando de Steam (**Game Mode y Big Picture**; no
en el cliente de escritorio) con módulos, en la línea de Decky Loader, pero pensado
para **durar sin depender de Steam**:

- No usa componentes, webpack ni React internos de Steam. La interfaz vive en su
  propio shadow DOM, con su propio teclado y sus propios controles.
- Del cliente de Steam solo usa lo imprescindible: la inyección por el depurador
  CEF y los eventos de mando `vgp_*`. Todo lo demás tiene alternativa propia.
- Backend con la **librería estándar de Python**, sin `pip` ni venv. Corre como
  servicio de **usuario** y nunca necesita root.

## Requisitos
- Para usarlo: `python3` (3.9 o superior) y systemd de usuario.
- Para compilar el frontend: Node.js (`esbuild` y `typescript` como dependencias de desarrollo).

## Instalar / desinstalar
```sh
cd frontend && npm install && npm run build && cd ..
./invasor-installation.sh --install              # instala o actualiza, y (re)inicia invasor.service (usuario)
./invasor-installation.sh --uninstall            # quita el servicio y ~/.local/share/invasor
./invasor-installation.sh --uninstall --purge    # ... y también la configuración (~/.config/invasor)
```
Sin argumentos (o abierto con doble clic) pregunta qué hacer en un diálogo, si `zenity` está
instalado; si no, muestra su ayuda.

- **Instalar / actualizar:** primero lo comprueba todo (Python 3.9+, servicios de usuario de systemd,
  Steam, un build del frontend al día), luego copia solo lo que ejecuta el servicio (sin tests ni la
  plantilla `_example`), lo cambia de golpe, y reinicia y comprueba el servicio. Los módulos instalados
  desde un zip se conservan. Si la versión nueva no arranca, se vuelve a poner la anterior.
- **Desinstalar:** el overlay sale de Steam al momento, sin reiniciar Steam. Antes, cada módulo deshace
  lo que dejó fuera de su carpeta (su `uninstall()`). Se borra todo
  `~/.local/share/invasor`, incluidos los módulos instalados desde un zip. La configuración y los ajustes
  de los módulos en `~/.config/invasor` se conservan salvo con `--purge` (el diálogo lo pregunta).

Después de instalar por primera vez, reinicia Steam una vez: el instalador crea
`~/.steam/steam/.cef-enable-remote-debugging`, igual que Decky. Al desinstalar solo se borra si lo creó
el instalador y Decky no está instalado.

### Paquete para otras máquinas
```sh
python3 tools/pack_release.py --out release/   # compila, prueba y empaqueta
```
Genera `invasor-<versión>.tar.gz` (y su `.sha256`): ya compilado, con el instalador, el core y los
módulos incluidos. En la Steam Deck / SteamOS de destino no hace falta Node: se extrae (Ark, o
`tar -xzf`) y se abre `invasor-installation.sh` con doble clic o se ejecuta `./invasor-installation.sh --install`.

### Actualizaciones
**⚙ Settings › Updates** tiene un botón *Check for updates* y otro de instalar. Invasor pregunta a GitHub
(`api.github.com`, la última release de [Gamemode-Invasor/invasor](https://github.com/Gamemode-Invasor/invasor))
y, si su etiqueta es una versión superior a la instalada, se activa el botón de instalar. Instalar
descarga el paquete de la release, lo comprueba con el `.sha256` publicado a su lado y ejecuta su instalador.
El servicio se reinicia y el panel se recarga solo unos segundos después; Steam y cualquier juego en marcha
no se reinician.

Por defecto también comprueba al arrancar y luego cada día, y Steam muestra una notificación una vez por
cada versión nueva. Es la única conexión a Internet que hace Invasor; se desactiva con *Check for updates
automatically* (`update_check` en `config.json`). El checksum detecta una descarga corrupta, no una cuenta
de GitHub comprometida. Los módulos instalados desde un zip no se tocan.

**Canal de actualizaciones:** *Stable* (por defecto) solo ofrece versiones finales (`X.Y.Z`). *Beta (pre-releases)*
(`update_channel`: `"beta"`) ofrece también candidatas (`X.Y.Z-rcN`, por ejemplo `0.1.3-rc1`), que pueden
ser inestables. Al cambiar de canal se busca una actualización al momento. Una versión final es
más nueva que sus candidatas (`0.1.3-rc1` < `0.1.3`) y las versiones nunca bajan, con una excepción: desde
una candidata, elegir *Stable* ofrece la versión estable aunque sea más antigua (*Go back to 0.1.2*); antes
se pide confirmación. Si es la misma versión, no se ofrece nada.

Log: `journalctl --user -u invasor -f`. Configuración opcional:
`~/.config/invasor/config.json` (`open_combo`, `panel_side`, `targets`, `disabled_modules`, `dev_desktop`,
`handle_icon`, `update_check`, `update_channel`; la mayoría también se cambian desde la pestaña **⚙ Settings**).

## Uso con el mando
La pestaña de Invasor (de color, con el icono o con la letra "I") está siempre en la biblioteca. En el menú rápido (···) solo aparece con un
juego en marcha, y el panel de ahí omite los módulos que no pintan nada durante la partida
(`"no_qam": true`, p. ej. Artwork).

| Botón | Acción |
|---|---|
| L3 + R3 | Abrir / cerrar el panel (con un juego abierto: primero ···). Se puede cambiar en ⚙ Settings |
| L1 / R1 | Pestaña anterior / siguiente: cada módulo es una pestaña, y ⚙ Settings la última |
| L2 / R2 | Subpestaña anterior / siguiente, si el módulo tiene |
| Cruceta ↑↓ | Moverse (se salta lo plegado). Si hay mucho contenido, desplaza la vista por páginas |
| Cruceta ←→ | Cambiar el valor del control |
| A / B | Aceptar / cerrar (o cancelar, según el control) |
| X | Plegar / desplegar la sección (desde su título o desde dentro) |
| Y | Valor especial del control, p. ej. volver al valor por defecto |

El panel ocupa el 40 % del ancho de la pantalla en la biblioteca (en Quick Access, la columna visible). Solo se desplaza el contenido; las
sombras arriba y abajo indican que hay más por ver.

Mientras el panel está abierto, Steam no recibe ninguna pulsación del mando. También
se puede abrir con la pestaña azul, con el dedo o con F10.

Se leen tanto los mandos normales (evdev: DualSense, Xbox…) como los de protocolo
Steam Deck: una Steam Deck real, o handhelds como la Legion Go virtualizados por
InputPlumber. Para ver qué botones detecta el servicio: `python3 tools/pad.py`.

**Pestaña ⚙ Settings** (siempre presente, aunque no haya módulos): activar o
desactivar módulos, atajo para abrir el panel, lado del panel, qué muestra la pestaña (el icono, la letra "I" o nada), color de acento (también de la pestaña) y "About" (versión,
estado y mandos detectados). La interfaz de invasor está en inglés; cada módulo
elige su propio idioma.

## Módulos
Invasor trae solo `demo` (una demostración que también usa `tools/smoke.py`) y la plantilla `_example`.
Los módulos de verdad viven en sus propios repositorios y se instalan como zip desde **⚙ Settings › Install
module**:

- [invasor-artwork](../invasor-artwork): arte de la comunidad de steamgriddb.com para tus juegos y accesos directos.
- [invasor-patito](../invasor-patito): generación de fotogramas con lsfg-vk, configurada por juego.

Los módulos instalados viven en `~/.local/share/invasor/user-modules/`. Actualizar Invasor nunca los toca.

## Crear un módulo
El contrato completo está en **[docs/MODULES.md](docs/MODULES.md)** (en inglés; API de módulos 1). En resumen:

```
modules/<id>/
  module.json     obligatorio: api, name, version y el formulario de ajustes
  backend.py      opcional: METHODS, setup(ctx), teardown()
  ui.ts           opcional: defineModule({ render | tabs, hooks })
  dist/ui.js      lo genera `npm run build`
```

- **Los ajustes se declaran en `module.json`.** El backend los valida (tipos, rangos, pasos, opciones) y pone
  los valores por defecto; el panel dibuja el formulario solo, y `backend.py` lee los mismos valores con
  `ctx.settings.get()`. Un módulo con ajustes y sin `ui.ts` tiene una pestaña con solo su formulario.
- **`ui.ts`** añade todo lo demás. El kit:
  - `ui.settingsForm(ctx)`;
  - controles con `get`/`set`/`setDisabled`;
  - secciones, `confirm`, cuadrículas de imágenes;
  - vistas ampliadas (`ctx.openWindow`, `ui.windowButton`).

  Todo funciona con el mando y con el dedo.
- **Aislamiento.** Cada módulo se compila y se carga por separado: un `module.json` inválido, un backend roto
  o una interfaz que falla solo afectan a la pestaña de ese módulo. Los métodos lentos del backend van en su
  propio hilo.

Para empezar, copia `modules/_example/` a `modules/<id>/`. Después:

```sh
cd frontend && npm run build && cd ..
python3 tools/check_module.py modules/<id>
./invasor-installation.sh --install
```

## De qué depende Invasor en Steam
Aquí está todo lo que Invasor toma del cliente de Steam. Cada punto está marcado `STEAM TOUCHPOINT` en el
código. Tras una actualización de Steam, `tools/smoke.py` los comprueba todos en un minuto.
El detalle, cómo puede fallar cada uno y una comparativa con Decky Loader (en inglés): [docs/API-Steam.md](docs/API-Steam.md).
Los módulos también pueden mostrar una notificación como las de los logros de Steam (`ctx.notify`).

| Punto | Para qué | Si Valve lo cambia |
|---|---|---|
| CEF DevTools en el puerto 8080 (`.cef-enable-remote-debugging`) | inyectar el overlay, como Decky | no se inyecta; el log dice que espera a CEF |
| Id del navegador en `/json/version` de CEF | saber que Steam ha arrancado o reiniciado, para `ctx.on_steam_start` de los módulos | `on_steam_start` no se llama; nada más cambia |
| URL de las ventanas: `useragent=Valve%20Steam%20Gamepad`, título `QuickAccess_…` | en qué ventanas va el panel | ajustable sin tocar código con `targets` en `config.json` |
| URL de `SharedJSContext` `/routes/library/app/<id>` | el juego seleccionado | no se muestra el seleccionado; el que está en marcha sí (viene de `/proc`) |
| Portada bajo el cursor en la biblioteca: `document.activeElement` (`role="link"`), la URL de su imagen `/assets/<appid>/` o `/customimages/<appid>`, su nombre en `aria-labelledby` (buscado en `shortcuts.vdf`) | el juego señalado | `highlighted` vale null; en marcha y seleccionado no se ven afectados |
| Eventos `vgp_onbuttondown` y sus números de botón | navegar el panel con el mando | el panel se abre (L3+R3) pero no se navega con el mando; el dedo y F10 siguen funcionando |
| `window.screenX` / `innerWidth` de las ventanas de Steam | ajustar el panel en el menú rápido | usa el ancho aprendido (348 px) |
| Clase CSS `.gpfocus` | devolver el foco tras escribir con teclado físico | no se devuelve el foco; nada más cambia |
| `SteamClient` (opcional), en la ventana o en `SharedJSContext` | `ctx.steam.safeCall` / `ctx.steam_call` para módulos (p. ej. el refresco al momento de Artwork) | las llamadas fallan limpiamente (503) y los módulos tienen plan B (Artwork: el arte nuevo se ve tras reiniciar Steam) |

## Pruebas

```sh
python3 -m unittest discover -s backend/tests -t backend   # backend, sin Steam
(cd frontend && npm run typecheck && npm test)             # tipos y funciones de valores del frontend
python3 tools/check_module.py modules/demo modules/_example # el contrato de módulos
python3 tools/smoke.py                                     # interfaz contra el Steam real
```
El smoke test necesita la interfaz de mando abierta (Game Mode, o Big Picture desde
el escritorio). Para probar desde el cliente de escritorio existe un **modo
desarrollo**: `"dev_desktop": true` en `~/.config/invasor/config.json` y
`systemctl --user restart invasor`. Así invasor se inyecta también en la ventana de
escritorio. En uso normal debe estar apagado; al apagarlo, el servicio retira solo el
panel que hubiera quedado en esa ventana.
`tools/smoke.py` recorre con el mando simulado las pestañas, las subpestañas, las
secciones, todos los controles de Demo, la vista ampliada, el teclado, el diálogo de
confirmación y ⚙ Settings. **Pásalo tras cada actualización de Steam:** en un minuto dice si la
inyección y la navegación siguen funcionando. Al terminar lo deja todo como estaba.

## Licencia
Invasor es software libre bajo la [Licencia Pública General de GNU v3.0 o posterior](LICENSE) (GPL-3.0-or-later).
La plantilla de módulos (`modules/_example/`) y los ejemplos de código de [docs/MODULES.md](docs/MODULES.md)
están bajo la [licencia MIT](LICENSES/MIT.txt): puedes empezar tu propio módulo con ellos y ponerle la licencia que quieras.
