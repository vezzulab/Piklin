<details open>
<summary>

## English

</summary>

**Piklin 2.0.5** brings a dark mode, a simpler photo editor, and a backup that finally keeps several computers in step: they read what the others did before they decide, and no longer redo or undo each other's work.

> **Update every computer that shares a backup.** A computer still on an older version can put a large video back over a smaller one in the backup; Piklin 2.0.5 notices and repairs it, but two versions together will keep fighting over it.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) and Intel | [**Piklin-2.0.5.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5.dmg) | Disk image: open it and drag Piklin to Applications |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**piklin_2.0.5_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/piklin_2.0.5_amd64.deb) | Installer for Ubuntu, Mint, Debian |
| 🐧 **Linux PC** — Intel or AMD (x86-64) | [**Piklin-2.0.5-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5-x86_64.AppImage) | Runs without installing; works on Fedora too |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**piklin_2.0.5_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/piklin_2.0.5_arm64.deb) | Installer for Ubuntu, Debian |
| 🐧 **Linux ARM** — 64-bit ARM (arm64) | [**Piklin-2.0.5-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5-aarch64.AppImage) | Runs without installing; works on Fedora too |

**Not sure which one?** On a Mac, the first row works for every Mac, new or old. On Linux, a normal PC needs the Intel or AMD file; ARM is for machines like a Raspberry Pi 5 or Linux on Apple Silicon.

The `.sha256`, `.sha256.sig` and `.build` files are for Piklin's automatic updates. You don't need to download them.

## Install or update

**If you already have Piklin 2.0 on a Mac or on Linux:** Piklin offers the update itself. Your library, albums, edits and backups stay as they are.

**On a Mac:**
1. Open `Piklin-2.0.5.dmg` and drag Piklin into **Applications**.
2. Open Piklin. macOS says it cannot check it for malware. That is expected: Piklin is free and not sold through Apple. Click **Done** (not *Move to Trash*).
3. Open **System Settings › Privacy & Security**, scroll to **Security**, click **Open Anyway** beside Piklin, enter your password and click **Open**.
4. When macOS asks whether Piklin may use your Pictures or Downloads folder, click **Allow**.

It asks only once. On macOS 11 to 14 you can also hold **Control**, click Piklin and choose **Open**.

**On Linux:**

```bash
chmod +x Piklin-2.0.5-x86_64.AppImage
./Piklin-2.0.5-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.5_amd64.deb
```

## What's new

### Dark mode

- **Piklin follows your computer:** dark when your Mac or Linux is dark, light when it is not. You can still choose **Light** or **Dark** in Preferences. The photographs stay the brightest thing on the screen, the photo viewer and the video controls stay dark in both looks, and the map stays light.
- It changes at once, without closing Piklin.

### Photo editor

- **Simple and Advanced.** Simple shows a one-click **Improve** and six quick fixes in plain words (light and colour, crop, rotate, sharper, warmer or cooler, black and white). Advanced is every tool. Piklin remembers your choice.
- **Copy Adjustments and Paste Adjustments.** Copy the edits of one photo (light and colour by default, or looks, crop, portrait, text, local retouching) and paste them on one photo or a whole selection. Pasting happens in the background and one **Undo** puts every photo back.
- **New icons**, drawn for photo editing.

### Several computers, one backup

- **Nothing is decided before Piklin has read the backup.** Making videos smaller used to begin twenty seconds after opening, before it knew what another computer had done.
- **One computer at a time makes videos smaller,** however many share the backup, and each keeps its own record of what it made. A video already made smaller is never made smaller again.
- **A computer that lost its smaller video from the backup sends it again,** and a large original that another computer made smaller is no longer sent over it. Piklin looks at its own smaller videos in the backup folder by folder, not only at the top of it.
- **The check that a smaller video is the same video** rejected good ones: phone videos stored sideways, screen recordings and any video with a key frame at the moment compared. All of those now pass, and different videos still do not.
- **On Linux, videos Piklin could not read** (AV1) are read now, and their thumbnails show.
- **A file with an "&" or ";" in its name** was sent again at every backup. It is found under its own name.
- **The backup to a NAS is faster:** Piklin keeps the connection open and sends four files at once.
- Checked with a simulation of many computers at once against one backup, with half of them joining late, with a backup that had already been put back, and with a computer that cannot check videos: every video is made smaller once, and in the end every computer and the backup have the smaller video.

### Sidebar and Preferences

- **No numbers in the sidebar.** Recently Deleted shows a **full red bin** when it holds something, and an empty one when it does not.
- **The Pause and Resume button for making videos smaller is gone:** it looks after itself.

### On a Mac

- **A click that moved a pixel was lost** and had to be made two or three times. It is fixed in the GTK that Piklin carries.

## Verify your download

SHA-256:

```
224044aca127efa9e779a682d4cc55cf4c4b773f36562ee09e0db9ae8c305705  Piklin-2.0.5.dmg
31b8d4b9bbf8ee6be30d0faf116dba63edf3172ab32adf9418ea32cf8bcfc68a  piklin_2.0.5_amd64.deb
a738518ea5868e059627a330689cef2f2c51b1c93fb2ab267c15308e5abdd51b  Piklin-2.0.5-x86_64.AppImage
4598450cbfbcfd28da9faecaa324bef22707a35e18cd65319e418586f53c3376  piklin_2.0.5_arm64.deb
de9c67e05cede96b19ff2f7705e23c7386a773e3fcda90432fe3817a348ab87f  Piklin-2.0.5-aarch64.AppImage
```

The `.sha256` and `.sha256.sig` files are what Piklin's updater checks: each checksum, signed with Vezzu Studio's release key.

Official downloads come only from this repository and [vezzu.studio](https://vezzu.studio).

Piklin is free. If it helps you, you can [buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>

<details>
<summary>

## Español

</summary>

**Piklin 2.0.5** trae un modo oscuro, un editor de fotos más sencillo y una copia de seguridad que por fin mantiene en orden a varias computadoras: leen lo que hicieron las demás antes de decidir, y ya no repiten ni deshacen el trabajo de otra.

> **Actualiza todas las computadoras que comparten una copia.** Una computadora con una versión anterior puede poner un video grande encima de uno pequeño en la copia; Piklin 2.0.5 lo nota y lo repara, pero dos versiones juntas seguirán peleando por ello.

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
| 🍎 **Mac** — Apple Silicon (M1, M2, M3, M4…) e Intel | [**Piklin-2.0.5.dmg**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5.dmg) | Imagen de disco: ábrela y arrastra Piklin a Aplicaciones |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**piklin_2.0.5_amd64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/piklin_2.0.5_amd64.deb) | Instalador para Ubuntu, Mint, Debian |
| 🐧 **PC con Linux** — Intel o AMD (x86-64) | [**Piklin-2.0.5-x86_64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5-x86_64.AppImage) | Funciona sin instalar; también en Fedora |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**piklin_2.0.5_arm64.deb**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/piklin_2.0.5_arm64.deb) | Instalador para Ubuntu, Debian |
| 🐧 **Linux ARM** — ARM de 64 bits (arm64) | [**Piklin-2.0.5-aarch64.AppImage**](https://github.com/vezzulab/Piklin/releases/download/v2.0.5/Piklin-2.0.5-aarch64.AppImage) | Funciona sin instalar; también en Fedora |

**¿No sabes cuál?** En una Mac, la primera fila sirve para todas, nuevas o viejas. En Linux, una PC normal necesita el archivo Intel o AMD; ARM es para equipos como una Raspberry Pi 5 o Linux en Apple Silicon.

Los archivos `.sha256`, `.sha256.sig` y `.build` son para las actualizaciones automáticas. No necesitas descargarlos.

### Instalar o actualizar

**Si ya tienes Piklin 2.0 en Mac o en Linux:** Piklin te ofrece la actualización. Tu biblioteca, álbumes, cambios y copias se quedan como están.

**En Mac:**
1. Abre `Piklin-2.0.5.dmg` y arrastra Piklin a **Aplicaciones**.
2. Abre Piklin. macOS dice que no puede comprobar si tiene malware. Es normal: Piklin es gratis y no se vende por Apple. Haz clic en **Aceptar** (no en *Mover a la papelera*).
3. Abre **Ajustes del Sistema › Privacidad y seguridad**, baja hasta **Seguridad**, haz clic en **Abrir de todos modos** junto a Piklin, escribe tu contraseña y haz clic en **Abrir**.
4. Cuando macOS pregunte si Piklin puede usar tu carpeta de Imágenes o Descargas, haz clic en **Permitir**.

Solo lo pregunta una vez. En macOS 11 a 14 también puedes mantener **Control**, hacer clic en Piklin y elegir **Abrir**.

**En Linux:**

```bash
chmod +x Piklin-2.0.5-x86_64.AppImage
./Piklin-2.0.5-x86_64.AppImage
```

```bash
sudo apt install ./piklin_2.0.5_amd64.deb
```

### Novedades

#### Modo oscuro

- **Piklin sigue a tu computadora:** oscuro cuando tu Mac o tu Linux lo está, claro cuando no. Puedes seguir eligiendo **Claro** u **Oscuro** en Preferencias. Las fotografías siguen siendo lo más brillante de la pantalla, el visor de fotos y los controles de video se quedan oscuros en los dos modos, y el mapa se queda claro.
- Cambia al instante, sin cerrar Piklin.

#### Editor de fotos

- **Sencillo y Avanzado.** Sencillo muestra un botón **Mejorar** de un clic y seis arreglos rápidos con palabras normales (luz y color, recortar, girar, más nítida, más cálida o más fría, blanco y negro). Avanzado es cada herramienta. Piklin recuerda tu elección.
- **Copiar ajustes y Pegar ajustes.** Copia los cambios de una foto (por defecto luz y color, o estilos, recorte, retrato, texto, retoque local) y pégalos en una foto o en toda una selección. El pegado ocurre en segundo plano y un solo **Deshacer** devuelve cada foto a como estaba.
- **Iconos nuevos**, dibujados para editar fotos.

#### Varias computadoras, una copia de seguridad

- **Nada se decide antes de que Piklin haya leído la copia de seguridad.** Hacer los videos más pequeños empezaba veinte segundos después de abrir, antes de saber qué había hecho otra computadora.
- **Una computadora a la vez hace los videos más pequeños,** sean cuantas sean las que comparten la copia, y cada una guarda su propio registro de lo que hizo. Un video que ya se hizo más pequeño no se vuelve a hacer más pequeño.
- **Una computadora a la que se le perdió su video más pequeño de la copia lo vuelve a enviar,** y un original grande que otra computadora ya redujo ya no se envía encima. Piklin mira sus videos más pequeños en la copia carpeta por carpeta, no solo en la parte de arriba.
- **La comprobación de que un video más pequeño es el mismo video** rechazaba videos buenos: los de teléfono guardados de lado, las grabaciones de pantalla y cualquier video con un fotograma clave justo en el momento comparado. Ahora todos pasan, y los videos distintos siguen sin pasar.
- **En Linux, los videos que Piklin no podía leer** (AV1) ahora se leen, y se ven sus miniaturas.
- **Un archivo con "&" o ";" en el nombre** se enviaba de nuevo en cada copia. Ahora se encuentra con su nombre.
- **La copia a un NAS es más rápida:** Piklin mantiene la conexión abierta y envía cuatro archivos a la vez.
- Probado con una simulación de muchas computadoras a la vez contra una sola copia, con la mitad llegando tarde, con una copia a la que ya le habían puesto los originales encima y con una computadora que no puede comprobar videos: cada video se hace más pequeño una sola vez, y al final todas las computadoras y la copia tienen el video más pequeño.

#### Barra lateral y Preferencias

- **Sin números en la barra lateral.** Eliminados recientemente muestra un **cesto rojo y lleno** cuando tiene algo, y vacío cuando no.
- **El botón de Pausar y Reanudar de hacer los videos más pequeños ya no está:** se cuida solo.

#### En Mac

- **Un clic en el que el puntero se movía un píxel se perdía** y había que hacerlo dos o tres veces. Está arreglado en el GTK que lleva Piklin.

#### Verifica tu descarga

SHA-256:

```
224044aca127efa9e779a682d4cc55cf4c4b773f36562ee09e0db9ae8c305705  Piklin-2.0.5.dmg
31b8d4b9bbf8ee6be30d0faf116dba63edf3172ab32adf9418ea32cf8bcfc68a  piklin_2.0.5_amd64.deb
a738518ea5868e059627a330689cef2f2c51b1c93fb2ab267c15308e5abdd51b  Piklin-2.0.5-x86_64.AppImage
4598450cbfbcfd28da9faecaa324bef22707a35e18cd65319e418586f53c3376  piklin_2.0.5_arm64.deb
de9c67e05cede96b19ff2f7705e23c7386a773e3fcda90432fe3817a348ab87f  Piklin-2.0.5-aarch64.AppImage
```

Los archivos `.sha256` y `.sha256.sig` son lo que comprueba el actualizador de Piklin: cada checksum, firmado con la clave de publicación de Vezzu Studio.

Las descargas oficiales vienen solo de este repositorio y de [vezzu.studio](https://vezzu.studio).

Piklin es gratis. Si te sirve, puedes [invitarnos un café en Ko-fi](https://ko-fi.com/vezzustudio) ☕

</details>
