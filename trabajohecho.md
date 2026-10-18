# Trabajo hecho: Piklin 2.0.6 en Windows

Todo lo de aquí está hecho (marcado con [x]) y probado en Windows 11 (17-10-2026), salvo donde se dice otra cosa.
Lo que falta está en `trabajoparahacer.md`. Los detalles técnicos y las trampas quedaron en las notas locales del port a Windows.

## Instalador y programa
- [x] **Instalador `.msi`** para Windows 10 y 11 de 64 bits (`packaging/windows/`): GTK 4, libadwaita, libshumate, Python,
  OpenCV, FFmpeg (PyAV), rclone y JPEG XL incluidos; no hace falta instalar nada más. Accesos en Inicio y Escritorio.
  Se construye con `packaging/windows/build-windows.sh` (MSYS2 UCRT64 + WiX 3.14).
- [x] **Lanzador propio `Piklin.exe`**: la barra de tareas muestra el nombre y el icono de Piklin; acepta las mismas
  opciones que Python; pasa `-P` para no cargar por error una carpeta `piklin` ajena.
- [x] **Ventana de arranque** con el banner de 2.0.6 del README de GitHub, esquinas redondeadas suaves (también en
  Windows 10), visible desde los 0,04 s hasta que aparece Piklin.
- [x] **Un solo Piklin a la vez**: un segundo lo cede al primero (`gdbus.exe` incluido).
- [x] **Fuente Inter** y interfaz correctas; discos externos, memorias USB y recursos de red reconocidos; copias de
  seguridad a carpeta o disco (fallaban por `os.statvfs`); registros y caché en `%LOCALAPPDATA%\Piklin`.
- [x] README con la sección de Windows (instalación, SmartScreen, requisitos) y la forma de construirlo.

## Rendimiento
- [x] Medido: quieto 450–600 MB de RAM y 1–3 % de CPU; pico de 1,3–1,5 GB solo al generar miniaturas de fotos nuevas.
- [x] **Arranque**: de 1,7 s a 0,8–1,1 s (los 1 051 `.pyc` quedaban "obsoletos" por las fechas del instalador; ahora son
  siempre válidos). El primer arranque tras instalar tarda más (Windows Defender revisa los archivos nuevos): la
  ventana de bienvenida evita que parezca colgado.
- [x] **Vídeo a 60 fps sin tirones**: el 15 % de los fotogramas llegaba tarde por el reloj de 15,6 ms de Windows;
  `system.raise_timer_resolution()` lo deja en 1 ms (0 fotogramas tardíos).

## Teléfonos, tabletas y cámaras (Windows Portable Devices)
- [x] `piklin-wpd.exe` + `piklin/wpd.py`, con la misma interfaz que Image Capture de Mac, conectado en `devices.py`.
- [x] **iPhone real**: aparece en la barra lateral, lista 832 archivos, vistas previas de fotos y vídeos (con la
  rotación corregida), abre y copia con el tamaño exacto, importa en paralelo, arrastrar a un álbum funciona
  (probado por el usuario).
- [x] **Android real (tableta Samsung SM-T820)**: lista, vistas previas (hechas copiando la foto y borrando la copia
  temporal, porque Android no da miniaturas) e importación de 40 fotos sin fallos.
- [x] **Regla del usuario: Piklin nunca borra nada del teléfono.** No existe la casilla ni el comando.
- [x] Aviso cuando un teléfono está bloqueado o sin permiso ("Desbloquéalo, permite el acceso a sus archivos…"),
  traducido al español.
- [x] Los vídeos sin duración conocida muestran solo el icono, sin "0:00" (también en Linux y Mac).
- [x] **Desenchufar a mitad no cuelga Piklin**: si el teléfono deja de responder 45 s durante un listado o 60 s durante una
  copia, se corta, se conserva lo ya leído y no queda ningún archivo a medio copiar; si una foto falla en una
  importación, el resto sigue y se cuenta el fallo.
- [x] **Desenchufar el cable de verdad (tableta Android, 17-10-2026)**: Piklin detecta que se fue, espera, detecta que vuelve y
  retoma solo; una copia cortada a mitad cuenta como fallida las fotos que no llegaron y guarda las demás; ninguna
  operación se cuelga (rondas de 0,3–0,5 s). Antes, 182 rondas seguidas sin un fallo.
- [x] **Teléfono y cámara como en Apple Fotos (17-10-2026): primero la lista, después las imágenes, y el usuario importa si
  quiere.** El listado (nombres, tamaños, fechas) se entrega entero y enseguida; las vistas previas se hacen después en
  tandas cortas de 40, cada una en su propia sesión, y cada tanda avisa a la ventana, que repinta esas casillas
  (`PhotoGrid.refresh_device_pictures`; el texto del aviso dice "Getting pictures from…"). Un listado largo con
  miniaturas en la misma sesión hacía que el iPhone dejara de dar contenido.
  **Medido con el iPhone real (671 fotos/vídeos): la lista completa a los 0,1 s; 665–669 vistas previas en 2–65 s
  (la primera vez; después salen de la caché); abrir 13 originales sin fallo; importar 30/30, todas legibles, vídeos con su
  duración.** Las vistas previas se comprueban (reducidas a ≤640 px si llegan enteras, borradas si llegan cortadas).
- [x] **Vistas previas nítidas y bien giradas (iPhone, 17-10-2026)**: la miniatura del iPhone mide 160 px y no sigue la
  rotación de la foto. Ahora, de cada foto (HEIC/JPEG de hasta 20 MB) que ya se descarga entera en la tanda, Piklin hace una
  vista previa de **400 px, con la orientación correcta**, y borra la foto. Medido: 366 de 671 a 400 px (todas las fotos);
  los vídeos conservan la miniatura del iPhone. Las vistas previas pequeñas de visitas anteriores se renuevan solas. Ninguna
  de las 671 se queda sin imagen (antes faltaban 2). La parte de girar por la nota `irot` se sustituyó: ya no hace falta.
- [x] **Duración de los vídeos del iPhone antes de copiarlos: no es posible** (WPD no la da y el `moov` de un `.MOV` está al
  final del archivo): se muestra el icono sin "0:00" y la duración sale al importar. Decisión tomada, no es un pendiente.
- [x] **iPhone recién enchufado**: tarda en cargar su biblioteca (la lista crece 73→839 durante el primer minuto) y **renumera
  los objetos**; los identificadores de un listado viejo dejan de valer y las copias fallan. Ahora, si una copia falla, Piklin
  vuelve a listar, actualiza los identificadores por nombre y reintenta una vez (`wpd._renumber`).
- [x] **iPhone que se bloquea solo (a los ~30 s) detectado**: el ayudante informa del código `0x80070141` ("dispositivo
  inalcanzable"), Piklin lo recoge (`wpd.take_problem`) y avisa en español. Probado con el iPhone real bloqueado.
- [x] **Android, vídeos**: la vista previa de un vídeo se hace con un fotograma del propio vídeo copiado a una carpeta
  temporal (400 px); probado con un vídeo real y un ayudante que imita a Android (la tableta real no tiene vídeos).
- [x] **Limpieza de la caché de dispositivos**: lo que quedó de teléfonos que llevan más de un día sin conectarse (vistas
  previas y fotos copiadas para abrirlas) se borra al arrancar; no se toca lo reciente.
- [x] 35 comprobaciones automáticas en `tests_wpd.py`, con un ayudante falso que puede imitar un teléfono desenchufado.

## Actualización automática (antes 1.2)
- [x] El Piklin abierto descarga el `.msi`, comprueba la firma ed25519 con la clave de Vezzu Studio y que es Piklin a la
  versión correcta, pide permiso de administrador al pulsar "Instalar", se cierra, instala y se reabre solo.
- [x] Un Piklin con permisos **vuelve a verificar la firma sobre una copia en una carpeta protegida** antes de instalar.
- [x] Nunca instala una versión más antigua; reconoce "la misma versión con una compilación posterior".
- [x] Probada de principio a fin en esta máquina; 20 comprobaciones automáticas en `tests_winupdate.py` (firma ajena,
  archivo manipulado, sin firma, versión vieja, versión no numérica…).
- [x] `packaging/sign-release.sh` firma también `.msi`; `build_asset()` y `latest_release()` piden el archivo de Windows.

## Firma de código (tubería lista, 17-10-2026)
- [x] **`build-windows.sh` firma `Piklin.exe`, `piklin-wpd.exe` y al final el `.msi`** (con sello de tiempo) cuando se le dan las
  variables: `PIKLIN_SIGN_PFX` y `PIKLIN_SIGN_PASSWORD` (certificado en archivo, con `osslsigncode`) o `PIKLIN_SIGNTOOL`,
  `PIKLIN_SIGN_DLIB` y `PIKLIN_SIGN_METADATA` (Azure Artifact Signing, con `signtool`); `PIKLIN_SIGN_TIMESTAMP` cambia el servidor.
  Sin variables no firma nada. Probado de punta a punta con un certificado autofirmado de prueba: los tres archivos salen firmados y
  con sello de tiempo (estado "UnknownError" = no confiable, lo esperado) y el `Piklin.exe` firmado arranca.
  Orden de publicación: construir (firma) y **después** `packaging/sign-release.sh` (la suma firmada cubre el `.msi` ya firmado).
- [x] Investigado qué certificado conviene: SignPath gratis no es posible por la licencia; Azure cuesta unos 10 $/mes.
- [x] **winget preparado**: `packaging/windows/make-winget.sh <msi>` genera los tres archivos de descripción (versión, instalador con
  su SHA-256 y código de producto leídos del propio `.msi`, y textos) en `dist/winget/manifests/…` y los valida con `winget validate`
  ("Manifest validation succeeded" con el `.msi` actual). Con winget no sale SmartScreen. Falta publicar un release y enviarlo.
- [x] **Decisión del usuario (17-10-2026): Piklin se distribuye sin firma** (no pagará por un certificado). Queda la tubería de firma
  por si cambia de idea, y el README explica cómo pasar el aviso de SmartScreen.

## Biblioteca y carpeta de Fotos
- [x] Por defecto, Piklin empieza **solo con lo que hay en su propia carpeta**; la carpeta de Fotos entera, una unidad u
  otra carpeta son opciones que elige la persona ("Only the Piklin Folder", traducido).
- [x] **Biblioteca real del usuario limpiada (17-10-2026):** dejó de vigilar `C:/Users/luieq/Pictures` (entera) y
  `C:/piklin-build` (mis fotos de prueba, que se habían colado). Ahora vigila solo su propia carpeta
  (`Piklin Library.piklin/Originals`). Comprobado: 18 292 archivos en disco = 18 292 fotos en el catálogo, sin
  duplicados. Copia de seguridad previa del catálogo en `C:/piklin-build/library-backup-20261017-185928`.
  Script: `C:/piklin-build/cleanup_roots.py` (convierte la raíz sin borrar filas; no toca archivos).
- [x] Borrada `C:/piklin-build/cold1` (copia de pruebas de 870 MB).

## Calidad y documentación
- [x] 157 comprobaciones de `tests_full.py` pasan en Windows (la suite se detiene en un test de Linux; ver pendientes).
- [x] Traducciones al español de todos los textos nuevos.
- [x] Notas del proyecto para cualquier sesión futura (reglas: todo para la 2.0.6, sin preguntas; no commit ni push sin pedirlo).
- [x] Protección del código: la licencia ya prohíbe copiar y derivar; opciones legales anotadas (aviso DMCA, marca, firma).
