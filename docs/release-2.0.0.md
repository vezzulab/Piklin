<details>
<summary>

## English

</summary>

**Piklin 2.0** puts your photos on a map — and lets you place the ones that never recorded where they were.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

**64-bit ARM (arm64):** this release is for 64-bit PCs. The ARM build follows in 2.0.1.

**macOS:** Piklin 2.0 is Linux only. The map needs a component that the Mac build does not carry yet; it is being added, and 2.0.1 will bring the map to macOS. Macs stay on 1.0.8 until then, and nothing in your library changes in the meantime.

## Download

| Your computer | Download |
|---|---|
| 🐧 **Linux PC** — 64-bit Intel or AMD (x86-64) | [**piklin_2.0.0_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_amd64.deb) |
| 🐧 **Try it without installing** — Linux PC (x86-64) | [**Piklin-2.0.0-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-x86_64.AppImage) |

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

If Piklin 1.0.4 or newer is installed, Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

```bash
sudo apt install ./piklin_2.0.0_amd64.deb
```

Without installing:

```bash
chmod +x Piklin-2.0.0-x86_64.AppImage
./Piklin-2.0.0-x86_64.AppImage
```

## What's new

### Your photos on a map

Every photo app can show you where a photo was taken. Piklin turns that around: the map is the way **into** your library. Open it and you see everywhere you have been, and you travel through your memories by place instead of by date.

- **Pins that mean somewhere you went.** Photos taken close together become one pin, and the pins regroup as you zoom — a country, then a city, then a street corner. Each pin says how many albums are behind it.
- **Rest on a pin and its albums slide in** beside the map. The map stays where it is, the panel waits until you choose, and an arrow in the bar brings you back to the map exactly as you left it.
- **Countries and places you have visited,** counted, with the places listed inside each country. Click one and the map flies there.

### Telling Piklin where you were

Most photographs carry no coordinates at all — a camera without a receiver, a phone with location switched off, a picture that went through a messaging app. A map built only on what cameras recorded stays nearly empty, which is why photo apps quietly give up on it.

**So you can say it yourself.** Right-click an album, type the country, the city and, if you like, the name of the place, and pick it from the answers. Every photo in that album lands on the map at once — thousands of photographs find their place in a few clicks, including ones that never carried a coordinate.

- Several albums can be placed together, and a chosen handful of photos can be given their own place when an album covers more than one.
- Placing an album moves **every** photo in it, so nothing is left behind a few streets away.
- Each album shows a dot once it is on the map: filled when it is settled in one place, faint while it is only partly placed.
- What you set by hand is kept in your library, travels with your backup, and a later scan never overwrites it.

### Places named without asking anyone

Piklin carries a list of the world's towns and reads it from your own disk, so turning a pin into "Santo Domingo" never sends a coordinate anywhere. With no connection the map still names its pins.

Searching for a place by name is the one thing that reaches the network, and only the words you type are sent — never a photograph, a filename or a coordinate of yours.

### Clearer about making photos smaller

Preferences › Storage now explains, in plain words, what making a photo smaller actually is: what it does, that Piklin tries each choice on your own photos and measures the result rather than guessing, and that your folders and the photos already in your library are left alone. Each choice says what it costs in room and what it costs in detail.

### Smaller things

- The sidebar no longer counts the library on the interface's thread. On a large library the numbers used to hold everything up for half a second after every import, edit and backup.
- No number beside All Photos or the albums: the footer already carries the library's count.
- Piklin opens filling the screen.
- The grey block standing in for a thumbnail still being made was drawn transparent, so a grid waiting for its photos looked empty rather than busy.

### A note on the map's background

The map is drawn with OpenStreetMap's own tile servers while this feature finds its feet. Those servers are donated infrastructure for OpenStreetMap's website rather than for applications, so Piklin will move to a background of its own — which will also let the map work with no connection at all. If the map ever comes up blank, that is why, and an update will fix it.

## Verify your download

SHA-256:

```
36a3714fef048d468ec050ce46b32aed1b94860001a065e39ccd64205c29fd72  piklin_2.0.0_amd64.deb
62488cecc4a65afd4837662ad7edc574d53ec5a2b9aa7b6abb82618989e3b529  Piklin-2.0.0-x86_64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0** pone tus fotos en un mapa — y te deja colocar las que nunca guardaron dónde fueron tomadas.

### Funciona en

| Sistema | Versiones | Computadoras |
|---|---|---|
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

**ARM de 64 bits (arm64):** esta versión es para PC de 64 bits. La de ARM llega en la 2.0.1.

**macOS:** Piklin 2.0 es solo para Linux. El mapa necesita un componente que la versión de Mac todavía no lleva; se está añadiendo, y la 2.0.1 traerá el mapa a macOS. Hasta entonces las Mac se quedan en la 1.0.8, y tu biblioteca no cambia en nada.

### Descargar

| Tu computadora | Descarga |
|---|---|
| 🐧 **PC con Linux** — Intel o AMD de 64 bits (x86-64) | [**piklin_2.0.0_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_amd64.deb) |
| 🐧 **Pruébalo sin instalar** — PC con Linux (x86-64) | [**Piklin-2.0.0-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-x86_64.AppImage) |

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

Si tienes Piklin 1.0.4 o más nuevo, Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

```bash
sudo apt install ./piklin_2.0.0_amd64.deb
```

Sin instalar:

```bash
chmod +x Piklin-2.0.0-x86_64.AppImage
./Piklin-2.0.0-x86_64.AppImage
```

### Novedades

#### Tus fotos en un mapa

Cualquier app de fotos te enseña dónde se tomó una foto. Piklin le da la vuelta: el mapa es la **puerta de entrada** a tu biblioteca. Lo abres y ves todos los lugares donde has estado, y recorres tus recuerdos por sitio en vez de por fecha.

- **Pines que significan un lugar donde estuviste.** Las fotos tomadas cerca se juntan en un pin, y los pines se reagrupan al acercarte: un país, luego una ciudad, luego una esquina. Cada pin dice cuántos álbumes hay detrás.
- **Apoya el puntero en un pin y sus álbumes entran** al lado del mapa. El mapa no se mueve, el panel espera a que elijas, y una flecha en la barra te devuelve al mapa tal como lo dejaste.
- **Cuántos países y lugares has visitado,** con los lugares dentro de cada país. Pulsa uno y el mapa vuela hasta allí.

#### Decirle a Piklin dónde estuviste

La mayoría de las fotos no llevan coordenadas: una cámara sin receptor, un teléfono con la ubicación apagada, una foto que pasó por una app de mensajería. Un mapa que dependa solo de lo que grabó la cámara se queda casi vacío, y por eso las apps de fotos acaban abandonándolo.

**Así que puedes decirlo tú.** Clic derecho en un álbum, escribe el país, la ciudad y, si quieres, el nombre del lugar, y elige entre las respuestas. Todas las fotos de ese álbum caen en el mapa de una vez — miles de fotos encuentran su sitio en unos pocos clics, incluidas las que nunca llevaron una coordenada.

- Se pueden colocar varios álbumes juntos, y también un grupo de fotos elegidas cuando un álbum abarca más de un lugar.
- Colocar un álbum mueve **todas** sus fotos, para que ninguna se quede a unas calles de distancia.
- Cada álbum muestra un punto cuando está en el mapa: relleno cuando está asentado en un solo sitio, tenue mientras está a medias.
- Lo que pones a mano se guarda en tu biblioteca, viaja con tu copia de seguridad, y un escaneo posterior nunca lo sobrescribe.

#### Lugares con nombre sin preguntarle a nadie

Piklin lleva consigo una lista de los pueblos del mundo y la lee de tu propio disco, así que convertir un pin en "Santo Domingo" no envía ninguna coordenada a ninguna parte. Sin conexión, el mapa sigue nombrando sus pines.

Buscar un lugar por su nombre es lo único que sale a la red, y solo viajan las palabras que escribes — nunca una foto, ni un nombre de archivo, ni una coordenada tuya.

#### Más claro al hacer las fotos más pequeñas

Ajustes › Almacenamiento ahora explica, en palabras llanas, qué es hacer una foto más pequeña: qué hace, que Piklin prueba cada opción sobre tus propias fotos y mide el resultado en vez de adivinar, y que tus carpetas y las fotos que ya están en tu biblioteca no se tocan. Cada opción dice lo que cuesta en espacio y lo que cuesta en detalle.

#### Cosas pequeñas

- El sidebar ya no cuenta la biblioteca en el hilo de la interfaz. En una biblioteca grande, los números frenaban todo medio segundo después de cada importación, cambio y copia de seguridad.
- Sin número al lado de Todas las Fotos ni de los álbumes: el pie ya lleva la cuenta de la biblioteca.
- Piklin se abre ocupando la pantalla.
- El bloque gris que sustituye a una miniatura que aún se está haciendo se dibujaba transparente, así que una cuadrícula esperando sus fotos parecía vacía en vez de ocupada.

#### Una nota sobre el fondo del mapa

El mapa se dibuja con los servidores de OpenStreetMap mientras esta función se asienta. Esos servidores son infraestructura donada para la web de OpenStreetMap, no para aplicaciones, así que Piklin pasará a un fondo propio — que además hará que el mapa funcione sin conexión. Si alguna vez el mapa sale en blanco, es por esto, y una actualización lo arreglará.

Piklin es gratis. Si te sirve, puedes [invitarnos a un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
