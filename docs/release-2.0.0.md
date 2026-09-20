<details>
<summary>

## English

</summary>

**Piklin 2.0** puts your photos on a map — and lets you place the ones that never recorded where they were.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🍎 macOS | 11 Big Sur or newer | Apple Silicon (M1 and later) and Intel Macs |
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

**64-bit ARM (arm64):** this release is for 64-bit PCs. 

**macOS:** Piklin 2.0 for Mac has the map too. One download runs on Apple Silicon and on Intel Macs, macOS 11 Big Sur or newer. The Mac app is not sold through Apple, so macOS asks you to allow it once: see **Install** below.

## Download

Find your computer in the table and download **one** file.

| Your computer | File | What it is |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.0.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.0_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.0-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.0_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.0-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-aarch64.AppImage) | Runs without installing; works on Fedora too |

**Not sure which one?** On a Mac, the first row works for every Mac, new or old. On Linux, a normal PC needs the Intel or AMD file; ARM is for machines like a Raspberry Pi 5 or Linux on Apple Silicon.

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**On a Mac:**
1. Open `Piklin-2.0.0.dmg` and drag Piklin into **Applications**.
2. Open Piklin. macOS says it cannot check it for malware. That is expected: Piklin is free and not sold through Apple. Click **Done** (not *Move to Trash*).
3. Open **System Settings › Privacy & Security**, scroll to **Security**, click **Open Anyway** beside Piklin, enter your password and click **Open**.
4. When macOS asks whether Piklin may use your Pictures or Downloads folder, click **Allow**.

It asks only once. On macOS 11 to 14 you can also hold **Control**, click Piklin and choose **Open**.

**On Linux:** if Piklin 1.0.4 or newer is installed, Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

```bash
sudo apt install ./piklin_2.0.0_amd64.deb     # Intel / AMD
sudo apt install ./piklin_2.0.0_arm64.deb     # ARM
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

### A quieter, clearer map

The map is drawn in soft greys and whites, so your pins are what stands out. Streets, parks, neighbourhoods and places are named in grey — no red crosses on hospitals to be mistaken for a pin. Names appear in the language Piklin is in, and more of them the closer you zoom.

### Finding a place

- Searching a place now asks two free map searches at once and shows each place once, with what kind of place it is — a museum, a neighbourhood, a café.
- If a place is only on Google Maps, paste its link, or its coordinates, in the Place box and the pin lands there. Links from Google Maps, Apple Maps and OpenStreetMap work.
- Only the words you type are sent. Never a photo, a filename or a coordinate from your library.

### Name a group of photos

Placing a selection now lets you name the group: "Norwood Center", "Downtown Boston". Photos placed under the same name stay together inside their album, under that name as a title. The name travels with your backup.

### Search and sort your lists

The albums in the sidebar, the albums at a pin, and the countries and cities on the map can each be searched, and sorted A to Z, Z to A, or by how many photos they hold.

### Export

Any other folder is now chosen in the system's own folder chooser; nobody has to type a path.

### Drag photos out of Piklin

Drag photos to the desktop, a folder or another program and they arrive as files anything can open. A photo kept as HEIC, WebP, AVIF or RAW becomes an ordinary JPEG, with its place and date; an edited photo arrives as it looks now; a JPEG nobody changed arrives untouched.

### Videos, much smaller

Preferences › Storage can now make the videos already in your library smaller with **AV1**, the codec that keeps the most quality for the least room: typically a third to a half of the original size. Each new video is checked against the original before it replaces it, and **the originals are kept for seven days** before they go, in case you change your mind.

### Map zoom that behaves

Pins no longer disappear or flicker while zooming, the wheel zooms on the place under the pointer and stops at the limits instead of sliding the map away, and the + and − buttons go exactly one step.

### Duplicates: everything is read

Finding duplicates now looks through every folder and reads each photo and video from start to end, so only files with identical content are called copies, wherever they are. It shows its progress, can be stopped, and merging double-checks the files before moving anything to Recently Deleted.

### Smaller things



- The sidebar no longer counts the library on the interface's thread. On a large library the numbers used to hold everything up for half a second after every import, edit and backup.
- No number beside All Photos or the albums: the footer already carries the library's count.
- Piklin opens filling the screen.
- The grey block standing in for a thumbnail still being made was drawn transparent, so a grid waiting for its photos looked empty rather than busy.

### A note on the map's background

The map's background comes from OpenFreeMap, a free map service made for applications. Only the part of the map you are looking at is asked for, and nothing about your photos goes with it. Without a connection your pins still show, but the background may not load.

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
| 🍎 macOS | 11 Big Sur o más nuevo | Apple Silicon (M1 y posteriores) y Mac Intel |
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

**ARM de 64 bits (arm64):** incluido en esta versión, como `.deb` y como AppImage.

**macOS:** Piklin 2.0 para Mac también trae el mapa. Una sola descarga funciona en Apple Silicon y en Mac Intel, con macOS 11 Big Sur o más nuevo. La app de Mac no se vende por Apple, así que macOS te pide permitirla una vez: mira **Instalar** más abajo.

### Descargar

Elige la fila de tu computadora y descarga **un** archivo.

| Tu computadora | Archivo | Qué es |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.0.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.0_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.0-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.0_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/piklin_2.0.0_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.0-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.0/Piklin-2.0.0-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

**¿No sabes cuál elegir?** En un Mac, la primera fila sirve para todos, nuevos y viejos. En Linux, si tu computadora es un PC normal, usa la de Intel o AMD; ARM es para máquinas como una Raspberry Pi 5 o un Linux en Apple Silicon.

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**En un Mac:**
1. Abre `Piklin-2.0.0.dmg` y arrastra Piklin a **Aplicaciones**.
2. Abre Piklin. macOS dice que no puede comprobar si tiene malware. Es lo esperado: Piklin es gratis y no se vende por Apple. Pulsa **Listo** (no *Mover a la Papelera*).
3. Abre **Ajustes del Sistema › Privacidad y seguridad**, baja hasta **Seguridad**, pulsa **Abrir igualmente** junto a Piklin, escribe tu contraseña y pulsa **Abrir**.
4. Cuando macOS pregunte si Piklin puede usar tu carpeta Imágenes o Descargas, pulsa **Permitir**.

Lo pide solo una vez. En macOS 11 a 14 también puedes mantener **Control**, hacer clic en Piklin y elegir **Abrir**.

**En Linux:** si tienes Piklin 1.0.4 o más nuevo, Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

```bash
sudo apt install ./piklin_2.0.0_amd64.deb     # Intel / AMD
sudo apt install ./piklin_2.0.0_arm64.deb     # ARM
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

#### Un mapa más tranquilo y claro

El mapa se dibuja en grises y blancos suaves, para que lo que destaque sean tus pines. Las calles, los parques, los barrios y los lugares llevan su nombre en gris, sin cruces rojas en los hospitales que se confundan con un pin. Los nombres salen en el idioma de Piklin, y aparecen más cuanto más te acercas.

#### Encontrar un lugar

- Al buscar un lugar, Piklin consulta dos buscadores de mapas libres a la vez y muestra cada sitio una sola vez, diciendo qué tipo de lugar es: un museo, un barrio, un café.
- Si un lugar solo está en Google Maps, pega su enlace, o sus coordenadas, en la casilla del lugar y el pin cae ahí. Sirven enlaces de Google Maps, Apple Maps y OpenStreetMap.
- Solo se envían las palabras que escribes. Nunca una foto, un nombre de archivo ni una coordenada de tu biblioteca.

#### Ponle nombre a un grupo de fotos

Al colocar una selección ahora puedes ponerle nombre al grupo: "Centro de Norwood", "Downtown Boston". Las fotos colocadas con el mismo nombre se quedan juntas dentro de su álbum, con ese nombre como título. El nombre viaja con tu copia de seguridad.

#### Busca y ordena tus listas

Los álbumes de la barra lateral, los álbumes de un pin y los países y ciudades del mapa se pueden buscar, y ordenar de la A a la Z, de la Z a la A, o por cuántas fotos tienen.

#### Exportar

Cualquier otra carpeta se elige ahora en el selector de carpetas del sistema; nadie tiene que escribir una ruta.

#### Arrastra fotos fuera de Piklin

Arrastra fotos al escritorio, a una carpeta o a otro programa y llegan como archivos que cualquiera abre. Una foto guardada como HEIC, WebP, AVIF o RAW se convierte en un JPEG normal, con su lugar y su fecha; una foto editada llega como se ve ahora; un JPEG que nadie cambió llega intacto.

#### Videos mucho más pequeños

En Preferencias › Almacenamiento ahora puedes hacer más pequeños los videos que ya están en tu biblioteca con **AV1**, el códec que conserva más calidad con menos espacio: normalmente entre un tercio y la mitad del tamaño original. Cada video nuevo se compara con el original antes de reemplazarlo, y **los originales se guardan siete días** antes de borrarse, por si cambias de opinión.

#### Un zoom del mapa que se porta bien

Los pines ya no desaparecen ni parpadean al hacer zoom, la rueda hace zoom sobre el lugar bajo el puntero y se detiene en los límites en vez de deslizar el mapa, y los botones + y − avanzan exactamente un paso.

#### Duplicados: se lee todo

Buscar duplicados ahora revisa todas las carpetas y lee cada foto y video de principio a fin, así que solo se consideran copias los archivos con contenido idéntico, estén donde estén. Muestra su avance, se puede detener, y al combinar se vuelven a comprobar los archivos antes de mover algo a Eliminados recientemente.

#### Cosas pequeñas



- El sidebar ya no cuenta la biblioteca en el hilo de la interfaz. En una biblioteca grande, los números frenaban todo medio segundo después de cada importación, cambio y copia de seguridad.
- Sin número al lado de Todas las Fotos ni de los álbumes: el pie ya lleva la cuenta de la biblioteca.
- Piklin se abre ocupando la pantalla.
- El bloque gris que sustituye a una miniatura que aún se está haciendo se dibujaba transparente, así que una cuadrícula esperando sus fotos parecía vacía en vez de ocupada.

#### Una nota sobre el fondo del mapa

El fondo del mapa viene de OpenFreeMap, un servicio de mapas libre hecho para aplicaciones. Solo se pide la parte del mapa que estás mirando, y nada sobre tus fotos la acompaña. Sin conexión tus pines se ven igual, pero el fondo puede no cargar.

Piklin es gratis. Si te sirve, puedes [invitarnos a un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

