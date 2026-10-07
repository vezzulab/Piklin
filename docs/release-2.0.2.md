<details>
<summary>

## English

</summary>

**Piklin 2.0.2** lets you edit the info of one photo or a whole group, shows thumbnails the moment you scroll, keeps photos under their real dates after a restore, and makes backups calmer.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🍎 macOS | 11 Big Sur or newer | Apple Silicon (M1 and later). Intel Macs: the Intel build is coming; stay on 2.0.1 until it is here |
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

## Download

Find your computer in the table and download **one** file.

| Your computer | File | What it is |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) | [**Piklin-2.0.2.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/Piklin-2.0.2.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.2_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/piklin_2.0.2_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.2-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/Piklin-2.0.2-x86_64.AppImage) | Runs without installing; works on Fedora too |

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0.1:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**If you have Piklin 2.0.0:** install this one by hand, once (download it below). Pressing Install inside 2.0.0 says it could not verify the update, because the key that signs updates was replaced. From 2.0.1 on, updates install themselves.

```bash
chmod +x Piklin-2.0.2-x86_64.AppImage
./Piklin-2.0.2-x86_64.AppImage
```

On a Mac, open `Piklin-2.0.2.dmg` and drag Piklin into **Applications**, replacing the old one.

```bash
sudo apt install ./piklin_2.0.2_amd64.deb
```

## What's new

### Photo info

- **Edit Info for one photo or many.** Right-click a photo, or a group you selected, and choose **Edit Info…**: title, caption, keywords, date and time, and a button to choose the place on the map. For a group, a field left empty keeps what each photo has; keywords can be added to the ones a photo already has or replace them; a date can be set for every photo or moved with their spacing kept. Your photo files are not changed: what you type is kept in Piklin and in your backup.

### Dates

- **No more "Yesterday" after a restore.** A photo with no date inside it was dated by its file, which after a restore or a copy is the moment it was written. Piklin now reads the date from the file name wherever it is in it (a Pixel burst cover, a screenshot, a WhatsApp image), and for a photo kept in a dated folder it uses that day.
- **Photos already in your library with a wrong date are corrected** when Piklin opens. A date read from the photo or set by hand is never changed.
- **A restore can no longer replace a photo's time** by the day it ran: the time of a photo that carries none is kept in the backup.

### Thumbnails, the map and the menus

- **Thumbnails come first for what you are looking at.** After scrolling past hundreds of photos, the ones you stopped on used to wait behind all the ones you passed.
- **Choosing a place on the map:** the wheel no longer slides the map away when you zoom right in, and the picker has the same round zoom buttons as the main map.
- **A right-click menu fits in the window** wherever you click, without a scrolling strip. Near the bottom it opens upward.

### Backups to a NAS

- **A restore or sync cut short no longer makes the next backup send everything again.**
- **When nothing is new, no backup is made at all.**
- **Two computers sharing a NAS no longer back up at once:** the second says who has the turn and waits.

### The sidebar and Linux

- **"Albums & Folders".** The heading over your albums and folders says what it holds, and it no longer folds away. The folders inside it still open and close.
- **The AppImage and the .deb now find their icon** on GNOME, KDE and X11: the launcher names the window class Piklin really announces.

## Verify your download

SHA-256:

```
27d8c4a57d157bd44536c5948857f2f264085ed6e92e5a0d7eadfd2597e4e6e2  Piklin-2.0.2.dmg
6dd9b72ffac4d55e140eee66ee8cebb2e91731e066816d0c3cd60effe64dc9a3  piklin_2.0.2_amd64.deb
d47da5f270525746956bf4ac7ebc6b18f12c1c2d469dd9df756c910e85d11a88  Piklin-2.0.2-x86_64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.2** te deja editar la información de una foto o de un grupo entero, muestra las miniaturas en cuanto te desplazas, mantiene las fotos con su fecha real después de restaurar, y hace las copias de seguridad más tranquilas.

### Funciona en

| Sistema | Versiones | Computadoras |
|---|---|---|
| 🍎 macOS | 11 Big Sur o más nuevo | Apple Silicon (M1 y posteriores). Mac Intel: la versión para Intel viene en camino; quédate en la 2.0.1 hasta que llegue |
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

### Descargar

Elige la fila de tu computadora y descarga **un** archivo.

| Tu computadora | Archivo | Qué es |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) | [**Piklin-2.0.2.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/Piklin-2.0.2.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.2_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/piklin_2.0.2_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.2-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.2/Piklin-2.0.2-x86_64.AppImage) | Funciona sin instalar; también en Fedora |

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0.1:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**Si tienes Piklin 2.0.0:** instala esta versión a mano, una sola vez (descárgala aquí abajo). Si pulsas Instalar dentro de la 2.0.0 dirá que no pudo verificar la actualización, porque se reemplazó la clave que firma las actualizaciones. Desde la 2.0.1 las actualizaciones se instalan solas.

```bash
chmod +x Piklin-2.0.2-x86_64.AppImage
./Piklin-2.0.2-x86_64.AppImage
```

En un Mac, abre `Piklin-2.0.2.dmg` y arrastra Piklin a **Aplicaciones**, reemplazando el anterior.

```bash
sudo apt install ./piklin_2.0.2_amd64.deb
```

### Novedades

#### Información de las fotos

- **Editar información de una foto o de muchas.** Haz clic derecho en una foto, o en un grupo que hayas seleccionado, y elige **Editar información…**: título, descripción, palabras clave, fecha y hora, y un botón para elegir el lugar en el mapa. En un grupo, un campo vacío conserva lo que tiene cada foto; las palabras clave se pueden añadir a las que ya tiene cada foto o reemplazarlas; la fecha se puede poner igual a todas o mover el grupo conservando la separación. Los archivos de tus fotos no cambian: lo que escribes se guarda en Piklin y en tu copia.

#### Fechas

- **Se acabó el "Ayer" después de restaurar.** Una foto sin fecha adentro se fechaba por su archivo, que tras una restauración o una copia es el momento en que se escribió. Ahora Piklin lee la fecha del nombre del archivo donde esté (una portada de ráfaga de Pixel, una captura de pantalla, una imagen de WhatsApp), y para una foto guardada en una carpeta con fecha usa ese día.
- **Las fotos que ya tienes con una fecha equivocada se corrigen** al abrir Piklin. Una fecha leída de la foto o puesta a mano nunca se cambia.
- **Una restauración ya no puede reemplazar la hora de una foto** por el día en que se hizo: la hora de una foto que no la trae se guarda en la copia.

#### Miniaturas, mapa y menús

- **Las miniaturas llegan primero para lo que estás mirando.** Después de pasar cientos de fotos, las que dejabas en pantalla esperaban detrás de todas las que habías pasado.
- **Al elegir un lugar en el mapa:** la rueda ya no desliza el mapa al acercar mucho, y el selector tiene los mismos botones de zoom redondos que el mapa principal.
- **Un menú de clic derecho cabe en la ventana** donde hagas clic, sin tira de desplazamiento. Cerca del borde de abajo se abre hacia arriba.

#### Copias de seguridad al NAS

- **Una restauración o sincronización interrumpida ya no hace que la siguiente copia lo envíe todo otra vez.**
- **Cuando no hay nada nuevo, no se hace ninguna copia.**
- **Dos computadoras que comparten un NAS ya no hacen copia a la vez:** la segunda dice quién tiene el turno y espera.

#### La barra lateral y Linux

- **"Albums & Folders".** El título sobre tus álbumes y carpetas dice lo que contiene, y ya no se pliega. Las carpetas de adentro siguen abriéndose y cerrándose.
- **El AppImage y el .deb ahora encuentran su icono** en GNOME, KDE y X11: el lanzador nombra la clase de ventana que Piklin realmente anuncia.

#### Verifica tu descarga

SHA-256:

```
27d8c4a57d157bd44536c5948857f2f264085ed6e92e5a0d7eadfd2597e4e6e2  Piklin-2.0.2.dmg
6dd9b72ffac4d55e140eee66ee8cebb2e91731e066816d0c3cd60effe64dc9a3  piklin_2.0.2_amd64.deb
d47da5f270525746956bf4ac7ebc6b18f12c1c2d469dd9df756c910e85d11a88  Piklin-2.0.2-x86_64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
