# Trabajo para hacer: que Piklin en Windows sea igual que en Linux y Mac

Estado al 17-10-2026. Lo ya hecho está en `trabajohecho.md`. Aquí solo queda lo pendiente.
Todo es para la **2.0.6**. Orden = prioridad sugerida.

## 0. Pendiente inmediato
- [ ] **No tocar el README** respecto a los móviles ("no se pueden importar en Windows") hasta que el usuario confirme
      sus pruebas.
- [ ] Decidir qué hacer con `C:\piklin-build` (3,8 GB de pruebas mías: bibliotecas de prueba, instaladores, copias de
      seguridad del catálogo `library-backup-*`). Borrar lo que ya no haga falta cuando el usuario lo diga.

## 1. Diferencias con Linux y Mac

### 1.1 Teléfonos y cámaras: lo que falta
- [ ] Vídeos de verdad en un Android (la tableta de prueba no tiene; la vía de la vista previa está probada con un
      vídeo simulado) y un teléfono con `DCIM/Camera`.
- [ ] Cámara en modo PTP.
- [ ] Probar el flujo nuevo (lista primero, imágenes después) **dentro de la ventana de Piklin** con el iPhone y con la
      tableta: que las casillas se pinten según llegan las tandas y que "Import All New Items" funcione. Solo está probado
      por código y con pruebas simuladas.
- [ ] Desenchufar de verdad con un **iPhone**: probado 88 rondas seguidas sin fallo (iPhone enchufado todo el rato), pero
      **nadie lo desenchufó durante la prueba**; repetir con `C:/piklin-build/unplug_test.py` (la tableta Android ya está probada).
- [ ] Tarjetas y memorias USB reales (`drives.is_removable` solo se probó con discos fijos).

### 1.2 Actualización automática: lo que falta
- [ ] Publicar un release real con firma de la clave verdadera (`packaging/sign-release.sh` en la máquina que la tiene)
      y comprobar la descarga desde GitHub (se probó con una carpeta local).
- [ ] Ver en pantalla el diálogo de "listo para instalar" y el aviso "Piklin se actualizó a…" (la prueba fue por código).

### 1.3 Aviso de SmartScreen: se queda sin firma (decisión del usuario, 17-10-2026)
- El usuario no pagará un certificado ni una licencia para dar Piklin gratis; SignPath gratis no es posible (exige licencia OSI).
  El README ya explica cómo pasar el aviso. La tubería de firma queda hecha por si algún día cambia (`trabajohecho.md`).
- [ ] (Opcional, gratis) **Publicar en winget**: los archivos ya se generan y validan; falta lo que depende de un release real:
  con el `.msi` ya publicado, `packaging/windows/make-winget.sh dist/Piklin-<versión>-x64.msi`, copiar la carpeta resultante a un
  fork de github.com/microsoft/winget-pkgs (`manifests/v/VezzuStudio/Piklin/<versión>/`) y abrir el pull request (acción externa:
  solo con permiso del usuario). Después, mencionar `winget install VezzuStudio.Piklin` en el README.

### 1.4 Integración con el sistema (lo que `integrate.py` hace solo en Linux)
- "Abrir con Piklin" para fotos y vídeos (registro de Windows, `Applications\Piklin.exe`).
- Elegir Piklin como programa predeterminado para fotos (opcional, a petición de la persona).
- Identidad en la barra de tareas: `AppUserModelID` para que las ventanas se agrupen bajo un solo icono.
- Abrir la carpeta de un archivo en el Explorador y seleccionarlo (Mostrar en carpeta).
- Opción de instalación por usuario (sin permisos de administrador).

### 1.5 Idioma del sistema
- `system.preferred_languages()` solo lee el idioma en Mac. Comprobar que "Igual que el ordenador"
  elige bien el idioma en Windows (`GetUserPreferredUILanguages`).

### 1.6 Tamaño del instalador (243 MB)
- OpenCV de MSYS2 arrastra VTK, Qt6, Ogre y OpenBLAS (≈250 MB). Compilar un OpenCV mínimo
  (módulos que usa Piklin: `imgproc`, `imgcodecs`, `core`, `objdetect`/`dnn` para caras) sin esas dependencias.
  Objetivo: instalador por debajo de 150 MB.

## 2. Cosas sin probar a fondo en Windows
Probar a mano, con fotos y vídeos reales, y anotar lo que falle:

- [ ] Editor de fotos (Simple y Avanzado), copiar y pegar ajustes, exportar a JPEG/WebP/AVIF/PNG.
- [ ] Editor de vídeo: recortar, cortar, rotar, velocidad, exportar MP4/WebM/GIF; guardar un fotograma.
- [ ] Reproductor: sonido (miniaudio), velocidad, fotograma a fotograma, previsualización en la cuadrícula
      (la fluidez a 60 fps ya se midió; falta que el usuario la vea a ojo).
- [ ] Mapa (libshumate + red), búsqueda de lugares, poner lugar a un álbum.
- [ ] Personas (detección de caras) y búsqueda inteligente.
- [ ] Copias de seguridad: carpeta/disco, NAS/WebDAV (probado en parte), nube con rclone (Google Drive, OneDrive, etc.).
- [ ] Contraseñas guardadas en el Administrador de credenciales de Windows (la prueba básica pasa).
- [ ] Formatos: HEIC, RAW, JPEG XL (`cjxl`/`djxl` incluidos), Live Photos, vídeos MTS/MKV/AVI.
- [ ] Arrastrar fotos hacia otros programas (`dragout.py`) y desde el Explorador a un álbum.
- [ ] Fotos en un disco externo: desconectar y reconectar con el disco puesto y quitado.
- [ ] Rutas con acentos, espacios, OneDrive en Imágenes, rutas largas (>260), discos de red `\\servidor\recurso`.
      (Se vio que el catálogo guarda rutas con `\` y `/` mezcladas: no hay duplicados, pero conviene normalizarlas.)
- [ ] Pantallas con escala 125 %, 150 %, 200 % y varios monitores (la ventana de arranque ya usa la escala del sistema).
- [ ] Tema claro y oscuro del sistema, modo de alto contraste.
- [ ] Desinstalar y reinstalar sin perder la biblioteca.
- [ ] **Windows 10** (solo se ha probado Windows 11). Probar 22H2 en una máquina o VM limpia.
- [ ] Ordenador con poca memoria (4–8 GB) y sin tarjeta gráfica dedicada.

## 3. Rendimiento (memoria y CPU)
- Comparar con Linux y Mac en la misma biblioteca para saber si es peor que allí.
- Si hay que bajar el pico de 1,3–1,5 GB: reducir `system._CAPS` para Windows y decodificar HEIC a escala reducida
  (hoy `pi_heif` decodifica a tamaño completo antes de reducir).
- Medir también: reproducir vídeo 4K, exportar vídeo, y la memoria tras horas abierto (fugas).
- Primer arranque tras instalar (6 s): solo mejora con firma de código o menos archivos.

## 4. Pruebas automáticas
- `tests_full.py` no termina en Windows: usa `os.statvfs` (línea ≈817) y rutas de Linux
  (`/usr/share/backgrounds`). Hacerlo portable con `shutil.disk_usage` y una carpeta de fotos de prueba.
- Fallan dos comprobaciones que hay que mirar: "keyword is searchable" (dentro de la suite) y
  "visually lossless is still high quality" (con fotos de prueba reales, no con ruido sintético).
- Añadir una prueba que arranque `Piklin.exe` empaquetado y compruebe que abre la ventana.

## 5. Publicación
- [ ] Flujo de GitHub Actions que construya el `.msi` (MSYS2 + WiX 3.14) al crear una etiqueta `v*`
      y lo adjunte a la release.
- [ ] Publicar la suma SHA-256 del `.msi` en las notas de la release.
- [ ] Añadir "Windows" a `CHANGELOG.md` y a las notas de la release (`docs/release-*.md`).
- [ ] Actualizar la web (`docs/index.html`): botón de descarga para Windows.
- [ ] Revisar `NOTICE.md`: licencias de lo nuevo que se distribuye (GTK, libadwaita, Python, MSYS2/UCRT,
      OpenCV y sus dependencias, FFmpeg, libjxl, rclone para Windows) e incluir los textos LGPL en el instalador.
- [ ] Decidir el precio: recomendado gratis, igual que Linux y Mac (Ko-fi ya está en la app).
- [ ] **Hacer commit y push** de todo (nada está commiteado todavía; el usuario no quiere commit hasta que Windows
      funcione igual que los demás).

## 6. Cómo construir hoy (resumen)
```bash
# En una consola MSYS2 UCRT64, con WiX 3.14 en C:\piklin-build\wix
pacman -S --needed $(grep -v '^#' packaging/windows/msys2-packages.txt)
packaging/windows/build-windows.sh      # → dist/Piklin-<versión>-x64.msi (y .msi.build)
packaging/sign-release.sh dist/Piklin-<versión>-x64.msi   # en la máquina con la clave de firma
```
