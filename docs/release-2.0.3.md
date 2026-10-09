<details open>
<summary>

## English

</summary>

**Piklin 2.0.3** makes Piklin light on your computer when it opens a large library: it no longer takes the processor and memory all at once.

## Works on

| System | Versions | Computers |
|---|---|---|
| 🍎 macOS | Not in this release | The Mac version of 2.0.3 comes later; stay on 2.0.2 until it is here |
| 🐧 Ubuntu | 24.04 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Linux Mint | 22 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Debian | 13 or newer | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Fedora | 40 or newer, with the AppImage | 64-bit PC (Intel or AMD, x86-64) |
| 🐧 Based on the above | Pop!_OS, Zorin OS, elementary OS and others built on those versions | The same as the version they are built on |

## Download

Find your computer in the table and download **one** file.

| Your computer | File | What it is |
|---|---|---|
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.3_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.3/piklin_2.0.3_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.3-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.3/Piklin-2.0.3-x86_64.AppImage) | Runs without installing; works on Fedora too |

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0.1 or 2.0.2 on Linux:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

```bash
chmod +x Piklin-2.0.3-x86_64.AppImage
./Piklin-2.0.3-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.3_amd64.deb
```

## What's new

### Memory and processor

- **Opening a large library is gentler.** The first screen loads first, and the scan, the date fixes and the folder arrangement start a few seconds later, one after the other, instead of all at once.
- **About 300 MB less memory.** Each background task kept its own 64 MiB database cache; now only the window keeps that, and the others use 4 MiB.
- **Thumbnails and photo details are made in blocks** instead of queueing thousands of jobs at once, and the memory they used is given back between blocks.
- **Photos shot upright and tagged as turned are no longer read again every time Piklin opens.** They are checked once.

## Verify your download

SHA-256:

```
7738e360cf028cf3f2a1ebb68f907707cd6eb0181536bd5f09567131221011fc  piklin_2.0.3_amd64.deb
076730b05ad9cf30d7da5ee8f930f57c0dbb7f7c9d89c4af07c2101b22f22bc5  Piklin-2.0.3-x86_64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.3** hace que Piklin sea ligero en tu computadora al abrir una biblioteca grande: ya no toma el procesador y la memoria de golpe.

### Funciona en

| Sistema | Versiones | Computadoras |
|---|---|---|
| 🍎 macOS | No viene en esta versión | La versión para Mac de la 2.0.3 viene después; quédate en la 2.0.2 hasta que llegue |
| 🐧 Ubuntu | 24.04 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Linux Mint | 22 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Debian | 13 o más nuevo | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Fedora | 40 o más nuevo, con el AppImage | PC de 64 bits (Intel o AMD, x86-64) |
| 🐧 Basadas en las anteriores | Pop!_OS, Zorin OS, elementary OS y otras | Las mismas que la versión en la que se basan |

### Descargar

Elige la fila de tu computadora y descarga **un** archivo.

| Tu computadora | Archivo | Qué es |
|---|---|---|
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.3_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.3/piklin_2.0.3_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.3-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.3/Piklin-2.0.3-x86_64.AppImage) | Funciona sin instalar; también en Fedora |

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0.1 o 2.0.2 en Linux:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

```bash
chmod +x Piklin-2.0.3-x86_64.AppImage
./Piklin-2.0.3-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.3_amd64.deb
```

### Novedades

#### Memoria y procesador

- **Abrir una biblioteca grande es más suave.** Primero carga la primera pantalla, y el escaneo, la corrección de fechas y el ordenado de carpetas empiezan unos segundos después, uno tras otro, en vez de todos a la vez.
- **Unos 300 MB menos de memoria.** Cada tarea de fondo guardaba su propio caché de 64 MiB de la base de datos; ahora solo lo guarda la ventana y las demás usan 4 MiB.
- **Las miniaturas y los datos de las fotos se hacen por bloques** en vez de poner en cola miles de trabajos a la vez, y la memoria que usaron se devuelve entre bloques.
- **Las fotos tomadas de pie y marcadas como giradas ya no se vuelven a leer cada vez que abres Piklin.** Se revisan una sola vez.

#### Verifica tu descarga

SHA-256:

```
7738e360cf028cf3f2a1ebb68f907707cd6eb0181536bd5f09567131221011fc  piklin_2.0.3_amd64.deb
076730b05ad9cf30d7da5ee8f930f57c0dbb7f7c9d89c4af07c2101b22f22bc5  Piklin-2.0.3-x86_64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
