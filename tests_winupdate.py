#!/usr/bin/env python3
"""Updating Piklin on Windows (piklin/winupdate.py): what is checked before an
update is installed. Runs on Windows only, against the .msi in dist/.

A test key stands in for Vezzu Studio's: what matters is that an installer is
refused unless the key that Piklin carries signed it, and that it is Piklin, at
the version it says, and not older than what is installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.platform != "win32":
    print("Windows only")
    sys.exit(0)

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp())
os.environ["LOCALAPPDATA"] = str(TMP / "local")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


from piklin import updates, winupdate  # noqa: E402
from piklin.updates import UpdateError  # noqa: E402

MSI = next(iter(sorted((HERE / "dist").glob("Piklin-*-x64.msi"))), None)
if MSI is None:
    print("build the installer first (packaging/windows/build-windows.sh)")
    sys.exit(1)
VERSION = MSI.name.split("-")[1]

OPENSSL = next((c for c in ("C:/msys64/ucrt64/bin/openssl.exe", "openssl") if shutil.which(c) or Path(c).exists()), "openssl")


def sh(*args):
    subprocess.run([OPENSSL, *args], check=True, capture_output=True)


def make_key(name):
    priv = TMP / f"{name}.pem"
    pub = TMP / f"{name}.pub.pem"
    sh("genpkey", "-algorithm", "ed25519", "-out", str(priv))
    sh("pkey", "-in", str(priv), "-pubout", "-out", str(pub))
    return priv, pub


def release(folder, key, msi_path=MSI, build="abc123def456-20260101000000"):
    """A release folder as GitHub would serve it: the .msi, its checksum, the
    signature of the checksum, and the .build file."""
    import hashlib
    folder.mkdir(parents=True, exist_ok=True)
    msi, manifest, sig = winupdate.msi_names(VERSION)
    shutil.copyfile(msi_path, folder / msi)
    digest = hashlib.sha256((folder / msi).read_bytes()).hexdigest()
    (folder / manifest).write_bytes(f"{digest}  {msi}\n".encode())      # LF, as sha256sum writes it
    sh("pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(folder / manifest), "-out", str(folder / sig))
    (folder / (msi + ".build")).write_text(build + "\n")
    return folder


def fake_install(pub):
    """A Piklin folder holding the public key, as the installed program does."""
    base = TMP / "Piklin"
    (base / "runtime" / "bin").mkdir(parents=True, exist_ok=True)
    (base / "runtime" / "bin" / "Piklin.exe").write_bytes(b"")
    shutil.copyfile(pub, base / "release-key.pem")
    os.environ["PIKLIN_APPDIR"] = str(base)
    return base


def serve(folder):
    """updates._download, answered from a folder instead of GitHub."""
    def download(url, dest):
        src = folder / url.rsplit("/", 1)[1]
        if not src.is_file():
            raise UpdateError("download", f"missing {src.name}")
        shutil.copyfile(src, dest)
    updates._download = download


def staged(version=VERSION):
    try:
        winupdate.stage(version)
        return None
    except UpdateError as exc:
        if os.environ.get("DEBUG_STAGE"): print("   ->", exc.kind, exc)
        return exc.kind


print("\nUpdating on Windows")
key, pub = make_key("release")
other_key, other_pub = make_key("someone-else")
fake_install(pub)
winupdate.installed_version = lambda: "2.0.0"            # something older than the .msi
updates.installed_build = lambda: "000000000000-20250101000000"

props = winupdate.msi_properties(MSI)
check("the installer says what it is", props.get("ProductName") == "Piklin"
      and props.get("UpgradeCode", "").upper() == winupdate.UPGRADE_CODE
      and props.get("ProductVersion") == VERSION, str(props))
try:
    winupdate.check_package(MSI, VERSION)
    ok = True
except UpdateError:
    ok = False
check("it is accepted as Piklin at its own version", ok)
try:
    winupdate.check_package(MSI, "9.9.9")
    wrong = False
except UpdateError as exc:
    wrong = exc.kind == "verification"
check("it is refused as any other version", wrong)

good = release(TMP / "good", key)
serve(good)
check("an installer signed with Piklin's key is accepted", staged() is None)
staged_dir = winupdate._find_staged()
check("it waits in a folder of its own, not installed",
      staged_dir is not None and (staged_dir / "staged.json").is_file()
      and (staged_dir / winupdate.msi_names(VERSION)[0]).is_file())

forged = release(TMP / "forged", other_key)
serve(forged)
check("an installer signed with another key is refused", staged() == "verification")
check("what was left of it is cleaned away", not (winupdate.updates_dir() / VERSION).exists())

tampered = release(TMP / "tampered", key)
with open(tampered / winupdate.msi_names(VERSION)[0], "r+b") as f:
    f.seek(1000)
    f.write(b"\x00\x01\x02\x03")
serve(tampered)
check("an installer changed after it was signed is refused", staged() == "verification")

nosig = release(TMP / "nosig", key)
(nosig / winupdate.msi_names(VERSION)[2]).unlink()
serve(nosig)
check("an installer with no signature is refused", staged() in ("verification", "download"))

serve(good)
winupdate.installed_version = lambda: VERSION
updates.installed_build = lambda: "abc123def456-20260101000000"
check("the same build that is installed is not an update", staged() == "not-newer")
updates.installed_build = lambda: "abc123def456-20250101000000"
check("the same version built later is an update", staged() is None)
updates.installed_build = lambda: "abc123def456-20270101000000"
check("the same version built earlier than the one installed is not", staged() == "not-newer")
winupdate.installed_version = lambda: "99.0.0"
check("an older version is never installed over a newer one", staged() == "not-newer")
winupdate.installed_version = lambda: "2.0.0"
updates.installed_build = lambda: ""

check("a version that is not a number is refused", staged("2.0.x;calc") == "install")
os.environ.pop("PIKLIN_APPDIR")
check("running from the source tree, it does not update itself", not updates.can_install_itself())
fake_install(pub)
check("installed, it can update itself", updates.can_install_itself())
check("the file to ask for is the one the release uses",
      updates.build_asset("2.0.7") == "Piklin-2.0.7-x64.msi.build")
check("Piklin reopens through its own launcher",
      Path(updates.launcher()).parts[-3:] == ("runtime", "bin", "Piklin.exe"), updates.launcher())

# what the script says afterwards
rf = winupdate.result_file()
rf.parent.mkdir(parents=True, exist_ok=True)
rf.write_text('{"version": "2.0.7", "ok": true, "code": 0, "time": 1}', encoding="ascii")
got = winupdate.take_result()
check("how it went is read once, then gone", got and got["ok"] is True and winupdate.take_result() is None)

# the script itself must at least parse
ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                     "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
                     f"'{HERE / 'piklin' / 'winupdate.ps1'}',[ref]$null,[ref]$e);$e.Count"],
                    capture_output=True, text=True)
check("the installation script parses", ps.stdout.strip() == "0", ps.stdout + ps.stderr)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
