<details open>
<summary>

## English

</summary>

**Piklin 2.0.7** makes videos smaller without slowing Piklin down, finds duplicates by what the picture is, lets your computers share what they do within minutes, and brings Events and every detail of a photo's metadata.

> **Update every computer that shares a backup,** so they all work the same way.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🍎 macOS | 11 Big Sur or newer | Apple Silicon (M1 and later) and Intel Macs |
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

**macOS:** one download runs on Apple Silicon and on Intel Macs, macOS 11 Big Sur or newer. The Mac app is not sold through Apple, so macOS asks you to allow it once: see **Install or update** below.

## Download

Find your computer in the table and download **one** file.

| Your computer | File | What it is |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.7.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.7_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/piklin_2.0.7_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.7-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.7_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/piklin_2.0.7_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.7-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7-aarch64.AppImage) | Runs without installing; works on Fedora too |

**Not sure which one?** On a Mac, the first row works for every Mac, new or old. On Linux, a normal PC needs the Intel or AMD file; ARM is for machines like a Raspberry Pi 5 or Linux on Apple Silicon.

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0 on a Mac or on Linux:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**On a Mac:**
1. Open `Piklin-2.0.7.dmg` and drag Piklin into **Applications**.
2. Open Piklin. macOS says it cannot check it for malware. That is expected: Piklin is free and not sold through Apple. Click **Done** (not *Move to Trash*).
3. Open **System Settings › Privacy & Security**, scroll to **Security**, click **Open Anyway** beside Piklin, enter your password and click **Open**.
4. When macOS asks whether Piklin may use your Pictures or Downloads folder, click **Allow**.

It asks only once. On macOS 11 to 14 you can also hold **Control**, click Piklin and choose **Open**.

**On Linux:**

```bash
chmod +x Piklin-2.0.7-x86_64.AppImage
./Piklin-2.0.7-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.7_amd64.deb
```

## What's new

### Videos made smaller, without slowing Piklin

- **Piklin stays smooth while it makes videos smaller.** The work is done by a helper of its own, so scrolling, opening photos and typing no longer stall while a long video is processed. When it ends, or you cancel, it gives all its memory back.
- **Your photos come first.** While photos are being copied in, the video being made smaller stops where it is, and goes on when the copy ends. It also uses the disk gently and leaves two of your computer's cores free.
- **It starts at once, one video at a time,** only while the computer is plugged in. Before each video Piklin reads what your other computers did, so a video is never made smaller twice, and every smaller video is checked (length, sound and picture) before it replaces the original.
- **One queue, no repeats.** The videos copied in on import and the videos made smaller later are the same queue now. A video that comes out no smaller, or that fails, is not tried again every few hours.
- **Videos made smaller no longer show as duplicates** of other files.

### Backup and sync

- **The backup waits while photos are copying or videos are being made smaller,** and says why, with numbers: "Backup waits until the videos are made smaller — 70 videos left, about 4 h 40 min", updated after every video.
- **The work on this computer comes first, then the sync, and both matter.** After 20 minutes of waiting, the video in progress finishes and the sync and the backup go; then the videos continue. Your other computers see what this one did in minutes, not hours later.
- **Half-made files are never sent to the backup,** and the ones already there are cleared once a day, only when their finished file is beside them.
- **The log says what the videos are doing,** and why they wait, so a problem can be found.

### Duplicates

- **Duplicates are found by what the picture is, not only by the file.** Piklin looks at every photo and compares it with the others: the same photo saved again, made smaller, sent through a chat or kept as a preview is a duplicate even if the files differ. A shot a moment later, a frame of a burst, an edited version, a screenshot and a RAW next to its JPEG are never taken for the same picture.
- **The same video kept twice is found too:** the camera's original and the smaller copy, or a video imported again with another name. A video you edited and saved as "<name>_edited" is never taken for a copy.
- **It looks by itself,** quietly, a few minutes after opening and then every half hour, and only when the computer is not busy copying, making videos smaller or backing up. What it already looked at is not looked at again.
- **Merging keeps the copy you have invested in** (favourite, edited, in albums, the largest file); the others go to Recently Deleted. Nothing is erased.

### Sidebar and dialogs

- **The Piklin mark is at the very top of the sidebar,** beside the window buttons, and the row it took is for the menus.
- **New Album and New Folder ask where it goes:** "Put it in" lists your folders with their path ("Family › Trips"), and opens the folder it goes into.
- **Every alert follows one formula:** the answer that does the thing is the black pill, the others are soft grey pills with air between them, a short heading and one or two plain sentences.
- **Keep a copy of your photos,** the offer the first time, is built as cards like the first steps.
- **Create** shows **Coming Soon** in the sidebar: it is being made better and cannot be used yet.
- The Support button has its heart.

### Events

- **Events:** a new section in the sidebar. Piklin finds the days and trips in your photos by itself, from when and where they were taken, names them ("Miami · 22 March 2025", "Christmas 2024"), picks a cover photo and shows them as cards by year. Open one to see its photos; right-click to rename it, join it to the earlier or later event, or make an album of it; inside one, "Start a New Event Here" splits it.

### Metadata

- **All the metadata of a photo or video,** open by default in the viewer's Info panel: every EXIF tag with its meaning in words, the full GPS block, IPTC and XMP written by other programs, the colour profile, the RAW sensor data, and for a video its container, every stream and its tags. It can be searched and copied.

### Search

- A folder or an album asked for in other words is found: any order of the words, the beginning of a word, a letter more or less.
- The photo picker browses folders and albums, keeps what you marked while you move between them, and can mark a whole album or folder at once.

### Photo editor

- **Edit several photos together:** they sit in a row under the picture, all take the edit of the first one, and what you change is changed for all.
- The tools are laid out in rows that are always whole.

### Problems

- **A crash is told at the next start,** with where it happened, and a freeze of the window is noted.
- A photo export says its result where it cannot be missed.

## Verify your download

SHA-256:

```
7d00be9ce555748002bfd10e9a737c44312fca34f020c4035d01f03c71ed1e90  Piklin-2.0.7.dmg
804f0f7fb3a3ddb07dbed7ee93a34ea2cf17a9a433ab84b377ba38b4ee625b36  piklin_2.0.7_amd64.deb
0b67c61a0845a0527630ddfd7175131f83d9fbcf07b5de98709337dad2e57ab1  Piklin-2.0.7-x86_64.AppImage
ddbcdc27f2f14e4f25422c8a81f1f06fa92651351d2a83386555dda22bc5ea96  piklin_2.0.7_arm64.deb
4c56bd937c6933f6b46308515c519da68d083387405802af5f2b9cc28b677e75  Piklin-2.0.7-aarch64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.7** hace los videos más pequeños sin frenar a Piklin, encuentra los duplicados por lo que es la imagen, deja que tus computadoras compartan lo que hacen en minutos, y trae Eventos y todos los detalles de los metadatos de una foto.

> **Actualiza todas las computadoras que comparten una copia,** para que todas funcionen igual.

### Funciona en

| Sistema | Versiones | Computadoras |
|---|---|---|
| 🍎 macOS | 11 Big Sur o más nuevo | Macs con Apple Silicon (M1 y posteriores) e Intel |
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

**macOS:** una sola descarga funciona en Apple Silicon y en Macs Intel, con macOS 11 Big Sur o más nuevo. La app de Mac no se vende por Apple, así que macOS te pide permitirla una vez: mira **Instalar o actualizar** más abajo.

### Descargar

Elige la fila de tu computadora y descarga **un** archivo.

| Tu computadora | Archivo | Qué es |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.7.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.7_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/piklin_2.0.7_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.7-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.7_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/piklin_2.0.7_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.7-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.7/Piklin-2.0.7-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

**¿No sabes cuál?** En una Mac, la primera fila sirve para todas, nuevas o viejas. En Linux, una PC normal necesita el archivo Intel o AMD; ARM es para equipos como una Raspberry Pi 5 o Linux en Apple Silicon.

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0 en Mac o en Linux:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**En Mac:**
1. Abre `Piklin-2.0.7.dmg` y arrastra Piklin a **Aplicaciones**.
2. Abre Piklin. macOS dice que no puede comprobar si tiene malware. Es normal: Piklin es gratis y no se vende por Apple. Haz clic en **Aceptar** (no en *Mover a la papelera*).
3. Abre **Ajustes del Sistema › Privacidad y seguridad**, baja hasta **Seguridad**, haz clic en **Abrir de todos modos** junto a Piklin, escribe tu contraseña y haz clic en **Abrir**.
4. Cuando macOS pregunte si Piklin puede usar tu carpeta de Imágenes o Descargas, haz clic en **Permitir**.

Solo lo pregunta una vez. En macOS 11 a 14 también puedes mantener **Control**, hacer clic en Piklin y elegir **Abrir**.

**En Linux:**

```bash
chmod +x Piklin-2.0.7-x86_64.AppImage
./Piklin-2.0.7-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.7_amd64.deb
```

### Novedades

#### Videos más pequeños, sin frenar a Piklin

- **Piklin sigue fluido mientras hace los videos más pequeños.** El trabajo lo hace un ayudante aparte, así que al desplazarte, abrir fotos o escribir ya no hay tirones mientras se procesa un video largo. Al terminar, o si cancelas, devuelve toda su memoria.
- **Tus fotos van primero.** Mientras se copian fotos, el video en curso se detiene donde está y sigue cuando termina la copia. Además usa el disco con suavidad y deja libres dos núcleos de tu computadora.
- **Empieza enseguida, un video a la vez,** y solo con la computadora conectada. Antes de cada video, Piklin lee lo que hicieron tus otras computadoras, así que ningún video se hace más pequeño dos veces, y cada video pequeño se comprueba (duración, sonido e imagen) antes de reemplazar al original.
- **Una sola cola, sin repeticiones.** Los videos copiados al importar y los que se hacen más pequeños después son ahora la misma cola. Un video que no queda más pequeño, o que falla, no se vuelve a intentar cada pocas horas.
- **Los videos que Piklin hizo más pequeños ya no aparecen como duplicados** de otros archivos.

#### Copia de seguridad y sincronización

- **La copia espera mientras se copian fotos o se hacen videos más pequeños,** y dice por qué, con números: "La copia espera a que los videos terminen de hacerse más pequeños — quedan 70 videos, unos 4 h 40 min", actualizado después de cada video.
- **Lo de esta computadora va primero, luego la sincronización, y las dos importan.** Tras 20 minutos de espera, el video en curso termina y se hacen la sincronización y la copia; después los videos continúan. Tus otras computadoras ven lo que hizo esta en minutos, no horas después.
- **Los archivos a medias nunca se envían a la copia,** y los que ya estaban allí se limpian una vez al día, solo cuando su archivo terminado está al lado.
- **El registro dice qué hacen los videos** y por qué esperan, para poder encontrar un problema.

#### Duplicados

- **Los duplicados se encuentran por lo que es la imagen, no solo por el archivo.** Piklin mira cada foto y la compara con las demás: la misma foto guardada otra vez, más pequeña, enviada por chat o guardada como vista previa es un duplicado aunque los archivos sean distintos. Una foto tomada un momento después, un cuadro de una ráfaga, una versión editada, una captura de pantalla y un RAW junto a su JPEG nunca se confunden con la misma imagen.
- **También se encuentra el mismo video guardado dos veces:** el original de la cámara y su copia más pequeña, o un video importado otra vez con otro nombre. Un video que editaste y guardaste como "<nombre>_edited" nunca se toma por una copia.
- **Busca solo,** con calma, unos minutos después de abrir y luego cada media hora, y solo cuando la computadora no está ocupada copiando, haciendo videos más pequeños o haciendo una copia. Lo que ya miró no lo vuelve a mirar.
- **Al unir se queda con la copia en la que has invertido** (favorita, editada, en álbumes, la de mayor tamaño); las demás van a Eliminados recientemente. No se borra nada.

#### Barra lateral y diálogos

- **La marca de Piklin está arriba del todo de la barra lateral,** junto a los botones de la ventana, y la fila que ocupaba queda para los menús.
- **Nuevo álbum y Nueva carpeta preguntan dónde va:** "Ponerlo en" lista tus carpetas con su ruta ("Familia › Viajes"), y abre la carpeta donde queda.
- **Todas las alertas siguen una sola fórmula:** la respuesta que hace la acción es la píldora negra, las otras son píldoras grises suaves con aire entre ellas, un título corto y una o dos frases sencillas.
- **Guarda una copia de tus fotos,** la oferta de la primera vez, está hecha con tarjetas como los primeros pasos.
- **Create** muestra **Próximamente** en la barra lateral: se está mejorando y aún no se puede usar.
- El botón de apoyo tiene su corazón.

#### Eventos

- **Eventos:** una sección nueva en la barra lateral. Piklin encuentra solo los días y los viajes de tus fotos, por cuándo y dónde se tomaron, les pone nombre ("Miami · 22 de marzo de 2025", "Navidad 2024"), elige una foto de portada y los muestra como tarjetas por año. Abre uno para ver sus fotos; clic derecho para renombrarlo, unirlo al evento anterior o al siguiente, o hacer un álbum con él; dentro de uno, "Empezar un nuevo evento aquí" lo divide.

#### Metadatos

- **Todos los metadatos de una foto o un video,** abiertos por defecto en el panel Info del visor: cada etiqueta EXIF con su significado en palabras, el bloque GPS completo, IPTC y XMP escritos por otros programas, el perfil de color, los datos del sensor RAW y, en un video, su contenedor, cada pista y sus etiquetas. Se puede buscar y copiar.

#### Búsqueda

- Se encuentra una carpeta o un álbum pedido con otras palabras: en cualquier orden, el comienzo de una palabra, una letra de más o de menos.
- El selector de fotos explora carpetas y álbumes, conserva lo que marcaste mientras te mueves entre ellos y puede marcar un álbum o una carpeta entera de una vez.

#### Editor de fotos

- **Editar varias fotos juntas:** quedan en una fila bajo la imagen, todas toman la edición de la primera y lo que cambias se cambia en todas.
- Las herramientas están en filas que siempre quedan completas.

#### Problemas

- **Un cierre inesperado se avisa en el siguiente arranque,** con el lugar donde ocurrió, y también se anota cuando la ventana se congela.
- La exportación de fotos dice su resultado donde no se puede pasar por alto.

#### Verifica tu descarga

SHA-256:

```
7d00be9ce555748002bfd10e9a737c44312fca34f020c4035d01f03c71ed1e90  Piklin-2.0.7.dmg
804f0f7fb3a3ddb07dbed7ee93a34ea2cf17a9a433ab84b377ba38b4ee625b36  piklin_2.0.7_amd64.deb
0b67c61a0845a0527630ddfd7175131f83d9fbcf07b5de98709337dad2e57ab1  Piklin-2.0.7-x86_64.AppImage
ddbcdc27f2f14e4f25422c8a81f1f06fa92651351d2a83386555dda22bc5ea96  piklin_2.0.7_arm64.deb
4c56bd937c6933f6b46308515c519da68d083387405802af5f2b9cc28b677e75  Piklin-2.0.7-aarch64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>