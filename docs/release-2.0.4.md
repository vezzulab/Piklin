<details open>
<summary>

## English

</summary>

**Piklin 2.0.4** lets you choose how your videos are kept: exactly as recorded, or compressed to take far less room. It also fixes cutting videos that have sound.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.4.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.4_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/piklin_2.0.4_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.4-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.4_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/piklin_2.0.4_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.4-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4-aarch64.AppImage) | Runs without installing; works on Fedora too |

**Not sure which one?** On a Mac, the first row works for every Mac, new or old. On Linux, a normal PC needs the Intel or AMD file; ARM is for machines like a Raspberry Pi 5 or Linux on Apple Silicon.

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0 on a Mac or on Linux:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**On a Mac:**
1. Open `Piklin-2.0.4.dmg` and drag Piklin into **Applications**.
2. Open Piklin. macOS says it cannot check it for malware. That is expected: Piklin is free and not sold through Apple. Click **Done** (not *Move to Trash*).
3. Open **System Settings › Privacy & Security**, scroll to **Security**, click **Open Anyway** beside Piklin, enter your password and click **Open**.
4. When macOS asks whether Piklin may use your Pictures or Downloads folder, click **Allow**.

It asks only once. On macOS 11 to 14 you can also hold **Control**, click Piklin and choose **Open**.

**On Linux:**

```bash
chmod +x Piklin-2.0.4-x86_64.AppImage
./Piklin-2.0.4-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.4_amd64.deb
```

## What's new

### Videos: Original or Compressed

- **Preferences › Storage has a new choice for the videos in your library.** **Original** keeps every video exactly as your camera recorded it. **Compressed** makes them smaller in the background: nothing wider than 1080p, and a phone's 4K video can take a few percent of its size. Original is what you have until you choose otherwise, and Piklin asks before it starts compressing.
- **Compressed works on its own.** It looks through your library, and again as new videos come in, and only while the computer is plugged in and not busy.
- **A smaller video replaces the original only after Piklin checks it is the same video:** its length, its sound and its picture. Albums, favourites and edits stay with it.
- **Your backup and your other computers follow.** The big original stays in the backup for the days you keep old versions, then goes; every other computer fetches the smaller video, checks it against its own and replaces it.
- **Making a video smaller is faster:** the AV1 encoder runs at a quicker setting for Compressed.

### Fixes

- **A computer takes the smaller video another computer already made,** from the backup, even when the backup has had no news since, and no longer makes that video smaller a second time.
- **Cutting a video that has sound could fail,** and with it making that video smaller. It happened for many cut points on videos with 44.1 kHz sound, which is what many phones record. A video can now be cut anywhere.

## Verify your download

SHA-256:

```
650a5d6ca5e7cc2eaac328155f757bb92514301c4cef7447867f2180147289ae  Piklin-2.0.4.dmg
4758608601f01c386b912b36286acf5ee24cd3abea3792166b43e807767c8c0a  piklin_2.0.4_amd64.deb
9f2cf9aa2c6e141a9b70f200c008fa1cce938b2f8ec138980ec17f1304ff00db  Piklin-2.0.4-x86_64.AppImage
5d73b826154af3394603f2fe0ef750cafd7fe31de8afab8a390e76391d74154c  piklin_2.0.4_arm64.deb
cc156dc8ee581f0df22148e65b8dc71bc060adcfb65405edd0e0186ee5793b96  Piklin-2.0.4-aarch64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.4** te deja elegir cómo se guardan tus videos: tal como se grabaron, o comprimidos para ocupar mucho menos espacio. También arregla el corte de videos que tienen sonido.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.4.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.4_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/piklin_2.0.4_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.4-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.4_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/piklin_2.0.4_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.4-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.4/Piklin-2.0.4-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

**¿No sabes cuál?** En una Mac, la primera fila sirve para todas, nuevas o viejas. En Linux, una PC normal necesita el archivo Intel o AMD; ARM es para equipos como una Raspberry Pi 5 o Linux en Apple Silicon.

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0 en Mac o en Linux:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**En Mac:**
1. Abre `Piklin-2.0.4.dmg` y arrastra Piklin a **Aplicaciones**.
2. Abre Piklin. macOS dice que no puede comprobar si tiene malware. Es normal: Piklin es gratis y no se vende por Apple. Haz clic en **Aceptar** (no en *Mover a la papelera*).
3. Abre **Ajustes del Sistema › Privacidad y seguridad**, baja hasta **Seguridad**, haz clic en **Abrir de todos modos** junto a Piklin, escribe tu contraseña y haz clic en **Abrir**.
4. Cuando macOS pregunte si Piklin puede usar tu carpeta de Imágenes o Descargas, haz clic en **Permitir**.

Solo lo pregunta una vez. En macOS 11 a 14 también puedes mantener **Control**, hacer clic en Piklin y elegir **Abrir**.

**En Linux:**

```bash
chmod +x Piklin-2.0.4-x86_64.AppImage
./Piklin-2.0.4-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.4_amd64.deb
```

### Novedades

#### Videos: Original o Comprimido

- **Preferencias › Almacenamiento tiene una opción nueva para los videos de tu biblioteca.** **Original** deja cada video exactamente como lo grabó tu cámara. **Comprimido** los hace más pequeños en segundo plano: nada más ancho que 1080p, y un video 4K de teléfono puede quedar en unos pocos por ciento de su tamaño. Original es lo que tienes hasta que elijas otra cosa, y Piklin te pregunta antes de empezar a comprimir.
- **Comprimido funciona solo.** Revisa tu biblioteca, y otra vez cuando llegan videos nuevos, y solo mientras la computadora está enchufada y sin hacer otra cosa.
- **Un video más pequeño reemplaza al original solo después de que Piklin comprueba que es el mismo video:** su duración, su sonido y su imagen. Los álbumes, los favoritos y las ediciones se quedan con él.
- **Tu copia de seguridad y tus otras computadoras lo siguen.** El original grande se queda en la copia durante los días que guardes versiones viejas y luego se va; cada otra computadora baja el video más pequeño, lo compara con el suyo y lo reemplaza.
- **Hacer un video más pequeño es más rápido:** el codificador AV1 trabaja con un ajuste más veloz en Comprimido.

#### Arreglos

- **Una computadora toma el video más pequeño que otra ya hizo,** desde la copia de seguridad, aunque la copia no tenga novedades, y ya no vuelve a hacerlo más pequeño.
- **Cortar un video que tiene sonido podía fallar,** y con eso también hacerlo más pequeño. Pasaba con muchos puntos de corte en videos con sonido de 44,1 kHz, que es lo que graban muchos teléfonos. Ahora un video se puede cortar en cualquier punto.

#### Verifica tu descarga

SHA-256:

```
650a5d6ca5e7cc2eaac328155f757bb92514301c4cef7447867f2180147289ae  Piklin-2.0.4.dmg
4758608601f01c386b912b36286acf5ee24cd3abea3792166b43e807767c8c0a  piklin_2.0.4_amd64.deb
9f2cf9aa2c6e141a9b70f200c008fa1cce938b2f8ec138980ec17f1304ff00db  Piklin-2.0.4-x86_64.AppImage
5d73b826154af3394603f2fe0ef750cafd7fe31de8afab8a390e76391d74154c  piklin_2.0.4_arm64.deb
cc156dc8ee581f0df22148e65b8dc71bc060adcfb65405edd0e0186ee5793b96  Piklin-2.0.4-aarch64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
