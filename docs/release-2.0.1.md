<details>
<summary>

## English

</summary>

**Piklin 2.0.1** fixes the map that showed up white on Linux, and brings your folders and albums back first when a new computer receives your library.

> **Install this one by hand, once.** We had to replace the key that signs Piklin's updates, and installed copies only know the old one. So pressing **Install** inside Piklin 2.0.0 will say it could not verify the update. Download Piklin 2.0.1 below instead: your library, albums, edits and backups stay as they are, and from 2.0.1 on updates install themselves again.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🍎 macOS | 11 Big Sur or newer | Apple Silicon (M1 and later) and Intel Macs |
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) and 64-bit ARM (arm64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

## Download

Find your computer in the table and download **one** file.

| Your computer | File | What it is |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.1.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.1_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/piklin_2.0.1_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.1-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.1_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/piklin_2.0.1_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.1-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1-aarch64.AppImage) | Runs without installing; works on Fedora too |

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**On a Mac, and on Linux with the .deb, or the AppImage on Ubuntu, Mint and Debian:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**If you use the 2.0.0 AppImage on Fedora, Arch, openSUSE or another system that is not Ubuntu or Debian:** download the new AppImage below once and replace the old one. Piklin 2.0.0 there could not make secure connections, so it cannot look for updates; that is part of what this release fixes, and from 2.0.1 on it updates itself.

```bash
chmod +x Piklin-2.0.1-x86_64.AppImage
./Piklin-2.0.1-x86_64.AppImage
```

On a Mac, open `Piklin-2.0.1.dmg` and drag Piklin into **Applications**, replacing the old one.

```bash
sudo apt install ./piklin_2.0.1_amd64.deb     # Intel / AMD
sudo apt install ./piklin_2.0.1_arm64.deb     # ARM
```

## What's new

### Install this one by hand

- **Piklin 2.0.1 has to be installed by hand, once: download it at github.com/vezzulab/Piklin/releases/latest.** Pressing Install inside Piklin will say it could not verify the update, because we replaced our signing key and your copy only knows the old one. From 2.0.1 on, updates install themselves again. Your library, albums, edits and backups stay as they are.

### The map

- **No more white map on Linux.** The AppImage could not make secure connections for the map's tiles, and its Python did not know where a computer other than Ubuntu keeps its certificates, so nothing from the internet loaded: not the map, not place search, not updates. Both are fixed.
- **If the map cannot load, Piklin says so** instead of leaving it blank.
- **Gentler zoom.** The buttons move half a step, and the wheel and trackpad ease in and keep the place under the pointer, without running far ahead, so you do not lose where you are.
- **Zooming out goes as far as it should.** The clean map was stopping a level early and showed only a quarter of the world. The first view of your photos and the grouping of pins were off by the same level and are fixed too.

### Restoring and syncing from a backup

- **Folders and albums first.** As soon as a restore or a sync starts, your folders and albums appear, laid out as they are in the backup, instead of after everything has come back. On a new computer the sidebar used to stay empty for as long as the photos took to arrive.
- **Then photos, then videos.** Everything comes in three steps, and the photos are shown as soon as they are in, while the videos, which take the longest, are still coming. The progress at the foot of the window says which is arriving.
- **A sync cut short picks up where it was.** The folders and albums it had already brought are shown the next time Piklin opens.
- **A backup now sends photos before videos,** in the same order.

### Folders

- **Choose a cover for a folder.** A folder used to show the cover of its first album. Right-click a photo inside an album and choose **Make Folder Cover** to pick the picture for the folder the album is in. The choice is kept in your backup and reaches your other computers.

<!-- After building and signing, add a "Verify your download" section here with the SHA-256 lines, as in release-2.0.0.md. -->

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.1** arregla el mapa que salía en blanco en Linux, y trae primero tus carpetas y álbumes cuando una computadora nueva recibe tu biblioteca.

> **Instala esta versión a mano, una sola vez.** Tuvimos que reemplazar la clave que firma las actualizaciones de Piklin, y las copias ya instaladas solo conocen la anterior. Por eso, si pulsas **Instalar** dentro de Piklin 2.0.0, dirá que no pudo verificar la actualización. Descarga Piklin 2.0.1 aquí abajo: tu biblioteca, álbumes, cambios y copias se quedan como están, y desde la 2.0.1 las actualizaciones vuelven a instalarse solas.

### Funciona en

| Sistema | Versiones | Computadoras |
|---|---|---|
| 🍎 macOS | 11 Big Sur o más nuevo | Apple Silicon (M1 y posteriores) y Mac Intel |
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) y ARM de 64 bits (arm64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

### Descargar

Elige la fila de tu computadora y descarga **un** archivo.

| Tu computadora | Archivo | Qué es |
|---|---|---|
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.1.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.1_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/piklin_2.0.1_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.1-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.1_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/piklin_2.0.1_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.1-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.1/Piklin-2.0.1-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**En un Mac, en Linux con el .deb, o con el AppImage en Ubuntu, Mint y Debian:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**Si usas el AppImage 2.0.0 en Fedora, Arch, openSUSE u otro sistema que no sea Ubuntu ni Debian:** descarga una vez el AppImage nuevo y reemplaza el viejo. Piklin 2.0.0 ahí no podía hacer conexiones seguras, así que no puede buscar actualizaciones; es parte de lo que arregla esta versión, y desde la 2.0.1 se actualiza solo.

```bash
chmod +x Piklin-2.0.1-x86_64.AppImage
./Piklin-2.0.1-x86_64.AppImage
```

En un Mac, abre `Piklin-2.0.1.dmg` y arrastra Piklin a **Aplicaciones**, reemplazando el anterior.

```bash
sudo apt install ./piklin_2.0.1_amd64.deb     # Intel / AMD
sudo apt install ./piklin_2.0.1_arm64.deb     # ARM
```

### Novedades

#### Instala esta versión a mano

- **Piklin 2.0.1 hay que instalarlo a mano, una sola vez: descárgalo en github.com/vezzulab/Piklin/releases/latest.** Si pulsas Instalar dentro de Piklin dirá que no pudo verificar la actualización, porque reemplazamos nuestra clave de firma y tu copia solo conoce la anterior. Desde la 2.0.1 las actualizaciones vuelven a instalarse solas. Tu biblioteca, álbumes, cambios y copias se quedan como están.

#### El mapa

- **Se acabó el mapa en blanco en Linux.** El AppImage no podía hacer conexiones seguras para las teselas del mapa, y su Python no sabía dónde guarda los certificados una computadora que no es Ubuntu, así que no cargaba nada de internet: ni el mapa, ni la búsqueda de lugares, ni las actualizaciones. Ambas cosas están arregladas.
- **Si el mapa no puede cargar, Piklin lo dice** en vez de dejarlo en blanco.
- **Zoom más suave.** Los botones se mueven medio paso, y la rueda y el trackpad se acercan con suavidad y mantienen el lugar bajo el puntero, sin adelantarse demasiado, para que no pierdas dónde estás.
- **Al alejar, el mapa llega hasta donde debe.** El mapa limpio se detenía un nivel antes y enseñaba solo una cuarta parte del mundo. La primera vista de tus fotos y la agrupación de los pines tenían el mismo desfase y también están arregladas.

#### Restaurar y sincronizar desde una copia

- **Primero carpetas y álbumes.** En cuanto empieza una restauración o una sincronización, aparecen tus carpetas y álbumes, ordenados como están en la copia, en vez de después de que todo haya llegado. En una computadora nueva la barra lateral se quedaba vacía todo el tiempo que tardaban las fotos en llegar.
- **Luego las fotos, luego los videos.** Todo llega en tres pasos, y las fotos se muestran en cuanto están, mientras los videos, que tardan más, siguen llegando. El progreso al pie de la ventana dice qué está llegando.
- **Una sincronización interrumpida continúa donde iba.** Las carpetas y álbumes que ya había traído se muestran la próxima vez que abras Piklin.
- **Una copia ahora envía las fotos antes que los videos,** en el mismo orden.

#### Carpetas

- **Elige la portada de una carpeta.** Una carpeta mostraba la portada de su primer álbum. Haz clic derecho en una foto dentro de un álbum y elige **Usar como portada de la carpeta** para escoger la imagen de la carpeta en que está el álbum. La elección se guarda en tu copia y llega a tus otras computadoras.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
