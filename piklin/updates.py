"""Is there a newer Piklin, and installing it.

Piklin asks GitHub for its latest release - at most once an hour, a few
seconds after it opens, and only while updates are on in Preferences. The
request carries nothing about the person or their photos: it is the same
public page anyone can open in a browser.

An installed .deb updates itself: /usr/lib/piklin/piklin-update, run through
pkexec, downloads the new version, installs it only if it is signed with
Vezzu Studio's release key, and Piklin then reopens. Piklin.app does the same
on a Mac with the disk image (see the macOS part below), and the AppImage
replaces its own file. Anywhere none can (running from source, an old
package) Piklin points to the download.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import system

REPO = "vezzulab/Piklin"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DAY = 24 * 60 * 60
HELPER = "/usr/lib/piklin/piklin-update"
INSTALLED_CODE = "/usr/share/piklin/"
LAUNCHER = "/usr/bin/piklin"


class UpdateError(Exception):
    """Installing failed. ``kind``: cancelled, not-newer, download,
    verification or install."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def can_install_itself() -> bool:
    """True for Piklin installed from its .deb, carrying the update helper,
    and for Piklin.app or the AppImage in a folder it is allowed to replace
    itself in.

    Not on Windows: there Piklin is installed from its .msi, which is what
    replaces it, and a program that overwrites its own installation behind
    Windows Installer's back leaves it with a record of something that is
    no longer there. Piklin still says when a new version exists.
    """
    if system.IS_WINDOWS:
        return False
    image = _appimage()
    if image is not None:
        return (image.is_file() and os.access(image, os.W_OK)
                and os.access(image.parent, os.W_OK))
    if system.IS_MAC:
        app = _mac_app()
        return (app is not None and os.access(app, os.W_OK)
                and os.access(app.parent, os.W_OK))
    here = str(Path(__file__).resolve())
    return (here.startswith(INSTALLED_CODE) and os.access(HELPER, os.X_OK)
            and shutil.which("pkexec") is not None)


def install(version: str) -> None:
    """Download, check and install ``version``. Blocks for a while: call it
    off the UI thread. Raises UpdateError when it did not install."""
    if _appimage() is not None:
        return _install_appimage(version)
    if system.IS_MAC:
        return _install_mac(version)
    kinds = {3: "not-newer", 4: "download", 5: "verification", 6: "install",
             126: "cancelled", 127: "cancelled"}
    try:
        proc = subprocess.run(["pkexec", HELPER, version], capture_output=True,
                              text=True, timeout=45 * 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("install", str(exc))
    if proc.returncode != 0:
        raise UpdateError(kinds.get(proc.returncode, "install"),
                          (proc.stderr or proc.stdout).strip())


@dataclass
class Release:
    version: str
    url: str
    notes: str = ""
    build: str = ""         # the build id published with the package, if any


def installed_build() -> str:
    """The build id of this Piklin ("" when running from source)."""
    try:
        return (Path(__file__).resolve().parent / "BUILD_ID").read_text().strip()
    except OSError:
        return ""


def _arch() -> str:
    import platform
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


def update_available(release: "Release", current: str) -> bool:
    """A newer version, or the same version published again as a new build
    (a fix released under the same number)."""
    if is_newer(release.version, current):
        return True
    if is_newer(current, release.version):
        return False
    mine = installed_build()
    if not (release.build and mine and release.build != mine):
        return False
    # A build id ends in the time it was made (commit-YYYYMMDDhhmmss). Only
    # a later build of the same version is an update: a copy built after the
    # published one - a fix being tried before it is released - must not be
    # replaced by the older, published build.
    made = lambda build: build.rsplit("-", 1)[-1] if "-" in build else ""
    theirs, ours = made(release.build), made(mine)
    if theirs.isdigit() and ours.isdigit() and len(theirs) == len(ours):
        return theirs > ours
    return True


def parse_version(text: str) -> tuple[int, ...]:
    """ "v1.0.3" -> (1, 0, 3); anything unreadable sorts as oldest."""
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def latest_release(current: str, timeout: float = 10.0) -> Release:
    """The newest published release. Raises OSError when GitHub can't be
    reached."""
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Piklin/{current}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    tag = data.get("tag_name") or data.get("name") or ""
    version = tag.lstrip("vV")
    build = ""
    wanted = build_asset(version)
    for asset in data.get("assets") or []:
        if asset.get("name") == wanted and asset.get("browser_download_url"):
            try:
                breq = urllib.request.Request(asset["browser_download_url"],
                                              headers={"User-Agent": f"Piklin/{current}"})
                with urllib.request.urlopen(breq, timeout=timeout) as bresp:
                    build = bresp.read(200).decode("utf-8", "replace").strip()
            except OSError:
                build = ""
            break
    return Release(version=version, url=data.get("html_url") or RELEASES_PAGE,
                   notes=data.get("body") or "", build=build)


def build_asset(version: str) -> str:
    """The release file naming the build of the package this Piklin came in."""
    if _appimage() is not None:
        return appimage_names(version)[0] + ".build"
    if system.IS_MAC:
        return f"Piklin-{version}.dmg.build"
    return f"piklin_{version}_{_arch()}.deb.build"


def launcher() -> str:
    """What starts the installed Piklin: /usr/bin/piklin, the AppImage file,
    or on a Mac the app's own executable (the new one, once an update has
    replaced it)."""
    image = _appimage()
    if image is not None:
        return str(image)
    app = _mac_app() if system.IS_MAC else None
    return str(app / "Contents" / "MacOS" / "Piklin") if app else LAUNCHER


# -- AppImage -------------------------------------------------------------------
# The AppImage replaces its own file, under the same rules as the .deb helper:
# only a version number comes in, only Piklin's own GitHub releases are read,
# and nothing replaces it unless its checksum is signed with Vezzu Studio's
# release key.
def _appimage() -> Path | None:
    """The AppImage file, when this Piklin runs from one."""
    path = os.environ.get("PIKLIN_APPIMAGE")
    return Path(path) if path else None


def appimage_names(version: str) -> tuple[str, str, str]:
    import platform
    image = f"Piklin-{version}-{platform.machine()}.AppImage"
    return image, image + ".sha256", image + ".sha256.sig"


def _install_appimage(version: str) -> None:
    from .app import VERSION
    if not _VERSION_RE.match(version):
        raise UpdateError("install", "not a version number")
    image = _appimage()
    if image is None or not can_install_itself():
        raise UpdateError("install", "the AppImage cannot replace itself where it is")
    try:
        key_pem = (Path(os.environ.get("PIKLIN_APPDIR", "")) / "usr" / "lib" / "piklin"
                   / "release-key.pem").read_text()
    except OSError as exc:
        raise UpdateError("verification", f"no release key in the AppImage: {exc}")
    base = f"https://github.com/{REPO}/releases/download/v{version}/"
    names = appimage_names(version)
    # Downloaded beside the AppImage, so the last step is a rename on one disk.
    work = Path(tempfile.mkdtemp(prefix=".piklin-update-", dir=image.parent))
    try:
        for name in (*names, names[0] + ".build"):
            _download(base + name, work / name)
        new = verify_signed(work, names[0], key_pem)
        new_build = (work / (names[0] + ".build")).read_text(errors="replace").strip()
        if is_newer(VERSION, version) or (
                not is_newer(version, VERSION)
                and (not new_build or new_build == installed_build())):
            raise UpdateError("not-newer", f"Piklin {version} is already installed")
        os.chmod(new, 0o755)
        # The running AppImage keeps reading the old file, which stays on disk
        # until it closes; the next start is the new one.
        try:
            os.replace(new, image)
        except OSError as exc:
            raise UpdateError("install", str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- macOS ----------------------------------------------------------------------
# Piklin.app replaces itself under the same rules as the .deb helper: only a
# version number comes in, only Piklin's own GitHub releases are read, and
# nothing is installed unless its checksum is signed with Vezzu Studio's
# release key and the disk image really holds Piklin at that version.
MAC_BUNDLE_ID = "com.envy.Piklin"
MAX_BYTES = 1024 * 1024 * 1024
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
PREVIOUS_APP = ".Piklin-previous.app"


def _mac_app() -> Path | None:
    """Piklin.app, when this Piklin runs from it."""
    contents = os.environ.get("PIKLIN_BUNDLE_CONTENTS")
    return Path(contents).parent if contents else None


def dmg_names(version: str) -> tuple[str, str, str]:
    dmg = f"Piklin-{version}.dmg"
    return dmg, dmg + ".sha256", dmg + ".sha256.sig"


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Piklin-updater"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
            total = 0
            while chunk := resp.read(1 << 20):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise UpdateError("download", "download is too large")
                out.write(chunk)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("download", f"could not download {url}: {exc}")


def verify_signed(folder: Path, name: str, key_pem: str) -> Path:
    """``folder/name``, once its checksum file is signed with the release key
    and matches it. Raises UpdateError("verification") otherwise."""
    from . import ed25519
    target = folder / name
    manifest = folder / (name + ".sha256")
    signature = folder / (name + ".sha256.sig")
    for path in (target, manifest, signature):
        if not path.is_file():
            raise UpdateError("verification", f"missing {path.name}")
    try:
        key = ed25519.public_key_from_pem(key_pem)
    except ValueError as exc:
        raise UpdateError("verification", str(exc))
    text = manifest.read_bytes()
    if not ed25519.verify(key, text, signature.read_bytes()):
        raise UpdateError("verification",
                          "the signature does not match Vezzu Studio's release key")
    match = re.fullmatch(rb"([0-9a-f]{64})  (\S+)\n?", text)
    if not match or match.group(2).decode("utf-8", "replace") != name:
        raise UpdateError("verification", "the checksum file is not for this package")
    digest = hashlib.sha256()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().encode() != match.group(1):
        raise UpdateError("verification", "the package does not match its signed checksum")
    return target


def _install_mac(version: str) -> None:
    from .app import VERSION
    if not _VERSION_RE.match(version):
        raise UpdateError("install", "not a version number")
    app = _mac_app()
    if app is None or not can_install_itself():
        raise UpdateError("install", "Piklin.app cannot replace itself where it is")
    try:
        key_pem = (app / "Contents" / "Resources" / "release-key.pem").read_text()
    except OSError as exc:
        raise UpdateError("verification", f"no release key in the app: {exc}")
    work = Path(tempfile.mkdtemp(prefix="piklin-update-"))
    mount = work / "volume"
    attached = False
    try:
        base = f"https://github.com/{REPO}/releases/download/v{version}/"
        for name in dmg_names(version):
            _download(base + name, work / name)
        dmg = verify_signed(work, dmg_names(version)[0], key_pem)

        mount.mkdir()
        out = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                              "-mountpoint", str(mount), str(dmg)],
                             capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            raise UpdateError("install", (out.stderr or out.stdout).strip()[-2000:])
        attached = True
        new = mount / "Piklin.app"
        try:
            with open(new / "Contents" / "Info.plist", "rb") as f:
                info = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException) as exc:
            raise UpdateError("verification", f"the disk image holds no Piklin: {exc}")
        if (info.get("CFBundleIdentifier") != MAC_BUNDLE_ID
                or info.get("CFBundleShortVersionString") != version):
            raise UpdateError("verification", f"the disk image is not Piklin {version}")
        # Newer, or the same version published again as a different build.
        new_build = str(info.get("CFBundleVersion") or "")
        if is_newer(VERSION, version) or (
                not is_newer(version, VERSION)
                and (not new_build or new_build == installed_build())):
            raise UpdateError("not-newer", f"Piklin {version} is already installed")

        # Copied in beside the running app, then swapped with it. The old one
        # stays until the new one starts (boot.py removes it): this process
        # still reads from it until Piklin reopens.
        staged = app.with_name(".Piklin-update.app")
        previous = app.with_name(PREVIOUS_APP)
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        out = subprocess.run(["ditto", str(new), str(staged)],
                             capture_output=True, text=True, timeout=1800)
        if out.returncode != 0:
            shutil.rmtree(staged, ignore_errors=True)
            raise UpdateError("install", (out.stderr or out.stdout).strip()[-2000:])
        os.rename(app, previous)
        try:
            os.rename(staged, app)
        except OSError as exc:
            os.rename(previous, app)
            shutil.rmtree(staged, ignore_errors=True)
            raise UpdateError("install", str(exc))
    finally:
        if attached:
            subprocess.run(["hdiutil", "detach", "-force", str(mount)],
                           capture_output=True, timeout=120)
        shutil.rmtree(work, ignore_errors=True)


# -- remembered per person, not per library -----------------------------------
def _state_file() -> Path:
    from .paths import config_dir
    base = config_dir()
    return base / "piklin" / "updates.json"


def load_state() -> dict:
    try:
        data = json.loads(_state_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**values) -> None:
    state = load_state()
    state.update(values)
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(path)
    except OSError:
        pass


def enabled() -> bool:
    return bool(load_state().get("enabled", True))


CHECK_EVERY = 60 * 60     # an hour: a fix reaches people the same day it is out


def highlights(notes: str, language: str) -> list[tuple[str, str]]:
    """What's new, in a few words each: the bold title of every change
    ("Instant rotate") rather than its whole explanation."""
    return notes_for(notes, language, short=True)


def notes_for(notes: str, language: str, short: bool = False) -> list[tuple[str, str]]:
    """What a release changes, in the reader's language, as (kind, text)
    lines: kind is "heading" or "item". The published notes carry English
    first and Spanish after a "## Español" heading; install steps and
    download checks are left out, they mean nothing inside Piklin."""
    text = notes or ""
    marker = re.search(r"^##\s+Español\s*$", text, re.M)
    if marker:
        text = text[marker.end():] if language.startswith("es") else text[:marker.start()]
    out: list[tuple[str, str]] = []
    keep_level = 0                  # the level of the What's New heading, while inside it
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            level = len(line) - len(line.lstrip("#"))
            if title.lower() in ("what's new", "novedades"):
                keep_level = level
            elif keep_level and level <= keep_level:
                keep_level = 0      # the next section, such as how to verify a download
            elif keep_level:
                out.append(("heading", title))
            continue
        keep = bool(keep_level)
        if keep and line.startswith(("- ", "* ")):
            item = line[2:]
            if short:
                bold = re.match(r"\*\*(.+?)\*\*", item)
                if bold:
                    item = bold.group(1).strip().rstrip(".").rstrip(",")
                else:
                    item = re.split(r"(?<=[.!?])\s", item, maxsplit=1)[0].rstrip(".")
            item = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", item)   # links: their words
            item = item.replace("**", "").replace("`", "")
            out.append(("item", item))
    return out


def check_due(now: float | None = None) -> bool:
    """An hour since the last look. Unlike due(), true even when installing
    by itself is off: the update bell still says a new version is out."""
    state = load_state()
    return (now or time.time()) - float(state.get("last_check") or 0) >= CHECK_EVERY


def due(now: float | None = None) -> bool:
    """Checking is allowed and the last check was more than an hour ago."""
    state = load_state()
    if not state.get("enabled", True):
        return False
    return (now or time.time()) - float(state.get("last_check") or 0) >= CHECK_EVERY
