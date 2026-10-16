<details open>
<summary>

## English

</summary>

**Piklin 2.0.6** brings People with no cloud (Piklin finds and names the faces in your photos on your own computer), a much smarter search box, and photos that can stay on an external drive.

> **Update every computer that shares a backup,** so they share the list of people too.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.6.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.6_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/piklin_2.0.6_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.6-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.6_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/piklin_2.0.6_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.6-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6-aarch64.AppImage) | Runs without installing; works on Fedora too |

**Not sure which one?** On a Mac, the first row works for every Mac, new or old. On Linux, a normal PC needs the Intel or AMD file; ARM is for machines like a Raspberry Pi 5 or Linux on Apple Silicon.

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0 on a Mac or on Linux:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**On a Mac:**
1. Open `Piklin-2.0.6.dmg` and drag Piklin into **Applications**.
2. Open Piklin. macOS says it cannot check it for malware. That is expected: Piklin is free and not sold through Apple. Click **Done** (not *Move to Trash*).
3. Open **System Settings › Privacy & Security**, scroll to **Security**, click **Open Anyway** beside Piklin, enter your password and click **Open**.
4. When macOS asks whether Piklin may use your Pictures or Downloads folder, click **Allow**.

It asks only once. On macOS 11 to 14 you can also hold **Control**, click Piklin and choose **Open**.

**On Linux:**

```bash
chmod +x Piklin-2.0.6-x86_64.AppImage
./Piklin-2.0.6-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.6_amd64.deb
```

## What's new

### People, with no cloud

- **Piklin finds the faces in your photos on your own computer** and groups them by person. Nothing leaves your computer, and it looks at photos only, never videos. You turn it on from **People**, and you can turn it off: everything it made is deleted and your photos are not touched.
- **Name a person and it keeps learning.** "Is this the same person?" asks only when it is not sure, and what you answer teaches it: faces you confirm, faces you reject and how strict to be.
- **Faces lying on their side or upside down are found too,** on their own.
- **Use as Picture** opens a round frame: move it, make it larger or smaller and turn the photo, so the picture of a person is the face you want. **Find More People** looks through the library again for faces not placed yet.
- **One list of people on all your computers:** each computer writes its own file in the backup and reads the others, and the faces are matched by the photo's content, so nothing is looked at twice.
- **Fast:** a person with thousands of photos opens at once and scrolls without flicker.

### Search

- **The search box understands what you type**, in Spanish or English: people (`Ana y Luis`), places named from the photos' coordinates with no network, dates written out (`15/07/2019`, `15 de julio de 2019`, `4 julio`), `antes de 2012`, `desde marzo 2023`, `entre 2015 y 2017`, kinds (`videos`, `capturas`, `vertical`, `raw`), cameras, sizes, stars. `-Ana` leaves a person out. What it understood shows as chips you can take away.
- **Albums and folders:** type the name of a folder and see it with its albums as cards, as clicking it does ("viaje disney" shows Epcot, Magic Kingdom and the rest); type the name of an album and go straight to its photos. When a name is both an album and a folder, the suggestions list each apart and you choose.
- **Suggestions as you type:** people, places, albums, folders, months, years and cameras of your own library.
- **Searching for a person is immediate,** and a search typed inside a person looks through the whole library.

### Photos on a drive

- **Keep your photos on an external drive without copying them.** Choose **A Drive With Your Photos** in the first steps, or **Add a Drive…** in Settings. Piklin never copies or moves them: the list, previews, albums, edits and people stay on the computer, so with the drive unplugged you can still browse, search and use the map. The footer says which drive is missing, and when it comes back Piklin looks for new photos on its own.

### Viewer

- **Zoom out, zoom in and fit-to-window buttons,** and the `+`, `-` and `0` keys. The photo stays centered while you zoom.
- **Back from a photo returns to that photo** in the list, not to the top.

## Verify your download

SHA-256:

```
16a071f6adc478b2c87a4d05454e207bd1402dba477ca8f9dfdd9bfbcc7ef4e5  Piklin-2.0.6.dmg
68ddc04b533b2d3d370b0cc0acec8b3331d33be3d260a59f07390ffd199f7b81  piklin_2.0.6_amd64.deb
995776b036177f25393b8c37ed407c3b36ef65e866a6c1dc84122cdd69750e9c  Piklin-2.0.6-x86_64.AppImage
03991d7df09485823fc675550bbf6c3e67862eaeb8f69018938582d6d9503e2d  piklin_2.0.6_arm64.deb
41f62de18afab61c6a3145305bdca4c3470c2d0fba2794efd9fd89adf5e64d04  Piklin-2.0.6-aarch64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.6** trae People sin nube (Piklin encuentra y nombra las caras de tus fotos en tu propia computadora), un buscador mucho más inteligente y fotos que pueden quedarse en un disco externo.

> **Actualiza todas las computadoras que comparten una copia,** para que compartan también la lista de personas.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.6.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.6_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/piklin_2.0.6_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.6-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.6_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/piklin_2.0.6_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.6-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.6/Piklin-2.0.6-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

**¿No sabes cuál?** En una Mac, la primera fila sirve para todas, nuevas o viejas. En Linux, una PC normal necesita el archivo Intel o AMD; ARM es para equipos como una Raspberry Pi 5 o Linux en Apple Silicon.

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0 en Mac o en Linux:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**En Mac:**
1. Abre `Piklin-2.0.6.dmg` y arrastra Piklin a **Aplicaciones**.
2. Abre Piklin. macOS dice que no puede comprobar si tiene malware. Es normal: Piklin es gratis y no se vende por Apple. Haz clic en **Aceptar** (no en *Mover a la papelera*).
3. Abre **Ajustes del Sistema › Privacidad y seguridad**, baja hasta **Seguridad**, haz clic en **Abrir de todos modos** junto a Piklin, escribe tu contraseña y haz clic en **Abrir**.
4. Cuando macOS pregunte si Piklin puede usar tu carpeta de Imágenes o Descargas, haz clic en **Permitir**.

Solo lo pregunta una vez. En macOS 11 a 14 también puedes mantener **Control**, hacer clic en Piklin y elegir **Abrir**.

**En Linux:**

```bash
chmod +x Piklin-2.0.6-x86_64.AppImage
./Piklin-2.0.6-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.6_amd64.deb
```

### Novedades

#### People, sin nube

- **Piklin encuentra las caras de tus fotos en tu propia computadora** y las agrupa por persona. Nada sale de tu computadora, y mira solo fotos, nunca videos. Lo activas desde **People**, y puedes apagarlo: todo lo que hizo se borra y tus fotos no se tocan.
- **Nombra a una persona y sigue aprendiendo.** "¿Es la misma persona?" pregunta solo cuando no está seguro, y lo que respondes le enseña: las caras que confirmas, las que rechazas y qué tan estricto ser.
- **También encuentra las caras acostadas o de cabeza,** sin que hagas nada.
- **Usar como imagen** abre un marco redondo: muévelo, hazlo más grande o más pequeño y gira la foto, para que la imagen de una persona sea la cara que quieres. **Buscar más personas** revisa de nuevo la biblioteca en busca de caras que aún no tienen lugar.
- **Una sola lista de personas en todas tus computadoras:** cada una escribe su archivo en la copia y lee los de las demás, y las caras se reconocen por el contenido de la foto, así que nada se revisa dos veces.
- **Rápido:** una persona con miles de fotos se abre al instante y se recorre sin parpadeos.

#### Búsqueda

- **El buscador entiende lo que escribes**, en español o en inglés: personas (`Ana y Luis`), lugares nombrados con las coordenadas de las fotos y sin red, fechas escritas (`15/07/2019`, `15 de julio de 2019`, `4 julio`), `antes de 2012`, `desde marzo 2023`, `entre 2015 y 2017`, tipos (`videos`, `capturas`, `vertical`, `raw`), cámaras, tamaños, estrellas. `-Ana` deja fuera a una persona. Lo que entendió se muestra en etiquetas que puedes quitar.
- **Álbumes y carpetas:** escribe el nombre de una carpeta y la ves con sus álbumes como tarjetas, igual que al hacer clic en ella ("viaje disney" muestra Epcot, Magic Kingdom y los demás); escribe el nombre de un álbum y entras directo a sus fotos. Cuando un nombre es álbum y carpeta a la vez, las sugerencias muestran cada uno por separado y tú eliges.
- **Sugerencias mientras escribes:** personas, lugares, álbumes, carpetas, meses, años y cámaras de tu propia biblioteca.
- **Buscar a una persona es inmediato,** y una búsqueda escrita dentro de una persona mira toda la biblioteca.

#### Fotos en un disco

- **Deja tus fotos en un disco externo sin copiarlas.** Elige **Un disco con tus fotos** en los primeros pasos, o **Agregar un disco…** en Ajustes. Piklin nunca las copia ni las mueve: la lista, las vistas previas, los álbumes, los cambios y las personas se quedan en la computadora, así que con el disco desconectado sigues pudiendo navegar, buscar y usar el mapa. El pie dice qué disco falta, y cuando vuelve Piklin busca fotos nuevas solo.

#### Visor

- **Botones de alejar, acercar y ajustar a la ventana,** y las teclas `+`, `-` y `0`. La foto se queda centrada mientras haces zoom.
- **Al volver de una foto regresas a esa foto** en la lista, no al principio.

#### Verifica tu descarga

SHA-256:

```
16a071f6adc478b2c87a4d05454e207bd1402dba477ca8f9dfdd9bfbcc7ef4e5  Piklin-2.0.6.dmg
68ddc04b533b2d3d370b0cc0acec8b3331d33be3d260a59f07390ffd199f7b81  piklin_2.0.6_amd64.deb
995776b036177f25393b8c37ed407c3b36ef65e866a6c1dc84122cdd69750e9c  Piklin-2.0.6-x86_64.AppImage
03991d7df09485823fc675550bbf6c3e67862eaeb8f69018938582d6d9503e2d  piklin_2.0.6_arm64.deb
41f62de18afab61c6a3145305bdca4c3470c2d0fba2794efd9fd89adf5e64d04  Piklin-2.0.6-aarch64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
