#!/usr/bin/env python3
"""Phones and cameras on Windows (piklin/wpd.py), against a stand-in for
piklin-wpd.exe: a small program that prints what the real helper would, and can
behave like a phone that is unplugged halfway.

The real helper needs a phone plugged in. This checks everything around it -
which devices are offered, the records, previews, copying an original off,
importing into a library, never deleting from the phone, and what happens when the
phone goes away in the middle of a listing or a copy - on any system.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = Path(tempfile.mkdtemp())
os.environ["LOCALAPPDATA"] = str(TMP / "local")

from piklin import devices, wpd  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


FAKE = TMP / "fake_helper.py"
LOG = TMP / "helper.log"
FAKE.write_text(r'''
import json, os, sys, time
from pathlib import Path

mode = os.environ.get("FAKE_WPD_MODE", "")
log = Path(os.environ["FAKE_WPD_LOG"])
args = sys.argv[1:]
PHONE = "\\\\?\\usb#vid_04e8&pid_6860#r58n#{6ac27878-a6fa-4155-ba85-f98f491d4f33}"
STICK = "\\\\?\\usbstor#disk&ven_sandisk&prod_ultra#4c53#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}"
CARD = "SWD\\WPDBUSENUM\\_??_USBSTOR#DISK&VEN_GENERIC#0&0"
SCAN = [
    {"o": "o1", "n": "IMG_0001.JPG", "d": "Internal storage/DCIM/Camera", "s": 2048, "t": 1696161600},
    {"o": "o2", "n": "IMG_0002.HEIC", "d": "Internal storage/DCIM/Camera", "s": 1024, "t": 1696248000},
    {"o": "o3", "n": "notes.txt", "d": "Internal storage/Documents", "s": 10, "t": 1696248000},
    {"o": "o4", "n": ".hidden.jpg", "d": "Internal storage/DCIM/Camera", "s": 5, "t": 1696248000},
    {"o": "o5", "n": "clip.mp4", "d": "Internal storage/DCIM/Camera", "s": 4096, "t": 1696334400},
]
if os.environ.get("FAKE_WPD_VIDEO"):
    SCAN[4]["s"] = os.path.getsize(os.environ["FAKE_WPD_VIDEO"])
def out(o):
    print(json.dumps(o), flush=True)


def note(text):
    with open(log, "a") as f:
        f.write(text + "\n")


cmd = args[0]
if cmd == "list":
    for d in ({"id": PHONE, "name": "Galaxy S23", "maker": "Samsung", "desc": "SM-S911B"},
              {"id": STICK, "name": "MEMORIA USB", "maker": "", "desc": ""},
              {"id": CARD, "name": "SD", "maker": "", "desc": ""}):
        out(d)
elif cmd == "scan":
    for i, item in enumerate(SCAN):
        if mode == "scan-die" and i == 3:
            sys.exit(1)                       # unplugged
        if mode == "scan-hang" and i == 3:
            time.sleep(600)                   # the phone stopped answering
        line = dict(item)
        if Path(os.environ["FAKE_WPD_LOG"] + ".renumbered").exists():
            line["o"] += "-v2"
        if mode == "slow":
            time.sleep(0.6)
        out(line)
    out({"done": True})
elif cmd == "thumbs":
    from PIL import Image
    for row in sys.stdin.read().splitlines():
        cols = row.split("\t")
        obj, target = cols[0], cols[1]
        if mode == "android":                 # Android gives no small picture
            out({"o": obj, "ok": False})
            continue
        if mode == "locks-after-1" and obj != "o1":
            out({"o": obj, "ok": False, "e": "0x80070141"})       # the phone locked itself
            continue
        if mode == "slow-thumbs":
            time.sleep(0.5)
        Image.new("RGB", (160, 120), (200, 30, 30)).save(target, "JPEG")    # landscape
        if len(cols) > 2:
            # The whole photo: stored wide, and (for o2) marked as turned a quarter, as an iPhone's are.
            exif = Image.Exif()
            exif[274] = 6 if obj == "o2" else 1
            Image.new("RGB", (800, 600), (30, 30, 200)).save(cols[2], "JPEG", exif=exif)
        out({"o": obj, "ok": True})
elif cmd == "fetch":
    obj, dest = args[2], Path(args[3])
    size = next(x["s"] for x in SCAN if x["o"] == obj.replace("-v2", ""))
    note("fetch " + obj)
    if Path(os.environ["FAKE_WPD_LOG"] + ".renumbered").exists() and not obj.endswith("-v2"):
        sys.exit(1)                           # the phone numbered its objects again
    if mode == "fetch-die:" + obj:
        sys.exit(1)
    if mode == "fetch-hang":
        Path(str(dest) + ".part").write_bytes(b"x" * 100)       # some of it arrived, then nothing
        time.sleep(600)
    if mode == "android" and obj == "o1":
        from PIL import Image
        Image.new("RGB", (64, 48), (10, 200, 10)).save(dest, "JPEG")
        dest.write_bytes(dest.read_bytes().ljust(size, bytes(1)))   # a real photo, of the listed size
    elif mode == "android" and obj == "o5":
        dest.write_bytes(Path(os.environ["FAKE_WPD_VIDEO"]).read_bytes())
    else:
        dest.write_bytes((b"ORIGINAL-" + obj.encode()).ljust(size, b"."))
elif cmd == "delete":
    note("delete asked")                      # the real helper has no such command
''', encoding="utf-8")

wpd._tool = lambda: "piklin-wpd.exe"
wpd._command = lambda exe, args: [sys.executable, str(FAKE), *args]
os.environ["FAKE_WPD_LOG"] = str(LOG)


def logged():
    return LOG.read_text().splitlines() if LOG.exists() else []


def mode(value):
    if value:
        os.environ["FAKE_WPD_MODE"] = value
    else:
        os.environ.pop("FAKE_WPD_MODE", None)


print("\nWindows phones and cameras")
cache = wpd._cache_root()
(cache / "old-phone-folder").mkdir(parents=True)
(cache / "old-phone-folder" / "p.jpg").write_bytes(b"x")
os.utime(cache / "old-phone-folder", (time.time() - 3 * 86400,) * 2)
(cache / "recent-phone-folder").mkdir(parents=True)
found = wpd.devices()
check("what is left for a phone not connected for days is removed, the rest kept",
      not (cache / "old-phone-folder").exists() and (cache / "recent-phone-folder").exists())
check("only the phone is offered, not USB sticks or cards", [d["name"] for d in found] == ["Galaxy S23"])
check("it is a phone", found and found[0]["phone"] is True)

devs = [d for d in devices.list_devices() if d.uri.startswith("wpd://")]
check("it appears among the devices, with a folder of its own",
      len(devs) == 1 and devs[0].kind == "phone" and devs[0].path.is_dir(), str(devs[:1]))

seen = []
records = devices.scan_device(devs[0], on_batch=seen.extend)
names = sorted(r["filename"] for r in records)
check("photos and videos are listed; documents and hidden files are not",
      names == ["IMG_0001.JPG", "IMG_0002.HEIC", "clip.mp4"], str(names))
check("a batch reached the window as it was found", len(seen) == len(records))
r1 = next(r for r in records if r["filename"] == "IMG_0001.JPG")
check("each photo sits under the folder it has on the phone",
      Path(r1["path"]).parent == devs[0].path / "Internal storage" / "DCIM" / "Camera")
check("each has a preview on disk", all(Path(r["preview"]).is_file() for r in records))
from PIL import Image  # noqa: E402
r2 = next(r for r in records if r["filename"] == "IMG_0002.HEIC")
check("a photo taken upright has its picture made upright, and sharp",
      Image.open(r2["preview"]).size == (300, 400),
      str(Image.open(r2["preview"]).size))
check("a photo that is not turned has its picture made sharp too", Image.open(r1["preview"]).size == (400, 300))
check("the photos saved to make them are removed", not list(Path(r2["preview"]).parent.glob("*.original")))
check("the date, size and device are on the record",
      r1["taken_at"] == 1696161600 and r1["bytes"] == 2048 and r1["camera"].startswith("\\\\?\\usb#"))
check("nothing is copied off the phone while browsing", not any(Path(r["path"]).exists() for r in records))

check("opening one copies it off",
      devices.fetch(r1["path"]) and Path(r1["path"]).read_bytes().startswith(b"ORIGINAL-o1")
      and Path(r1["path"]).stat().st_size == 2048)
check("a second open does not ask the phone again", devices.fetch(r1["path"]) and logged().count("fetch o1") == 1)
check("a path from nowhere is not fetched", devices.fetch(str(devs[0].path / "nope.jpg")) is False)


class Lib:
    pass


lib = Lib()
lib.originals = TMP / "Originals"
placed = []
res = devices.import_photos(lib, [r for r in records if r["filename"] != "IMG_0001.JPG"],
                            on_placed=placed.append, workers=2)
check("importing copies the photos off the phone into the library",
      res["failed"] == 0 and len(res["copied"]) == 2
      and all(Path(p).read_bytes().startswith(b"ORIGINAL-") for p in res["copied"]), str(res["failed"]))
check("the copies taken off for the import are removed again",
      not any(Path(r["path"]).exists() for r in records if r["filename"] != "IMG_0001.JPG"))

removed, failed = devices.delete_from_device([r["path"] for r in records])
check("nothing is ever deleted from the phone, even when asked",
      (removed, failed) == (0, 0) and "delete asked" not in logged(), f"{removed} removed, {failed} failed")
check("the window is not offered the choice to delete from it", devs[0].can_delete is False)

print("\nThe phone goes away halfway")
mode("scan-die")
partial = devices.scan_device(devs[0])
check("unplugged during a listing: what was read is kept, nothing breaks",
      sorted(r["filename"] for r in partial) == ["IMG_0001.JPG", "IMG_0002.HEIC"],
      str([r["filename"] for r in partial]))

mode("scan-hang")
wpd.IDLE_SECONDS = 1.5
t = time.monotonic()
partial = devices.scan_device(devs[0])
took = time.monotonic() - t
check("a phone that stops answering during a listing is let go, keeping what it gave",
      took < 20 and sorted(r["filename"] for r in partial) == ["IMG_0001.JPG", "IMG_0002.HEIC"],
      f"{took:.1f}s, {[r['filename'] for r in partial]}")
wpd.IDLE_SECONDS = 45.0

mode("fetch-hang")
wpd.STALL_SECONDS = 1.5
r5 = next(r for r in records if r["filename"] == "clip.mp4")
t = time.monotonic()
ok = devices.fetch(r5["path"])
took = time.monotonic() - t
check("a copy that stops partway is let go and fails", ok is False and took < 20, f"{took:.1f}s")
check("no half-copied file is left where the photo would be",
      not Path(r5["path"]).exists() and not list(Path(r5["path"]).parent.glob(".downloading-*")))
wpd.STALL_SECONDS = 60.0

mode("fetch-die:o2")
lib.originals = TMP / "Originals2"
res = devices.import_photos(lib, [r for r in records if r["filename"] != "IMG_0001.JPG"], workers=2)
check("one photo failing does not stop the others, and is counted",
      res["failed"] == 1 and len(res["copied"]) == 1 and Path(res["copied"][0]).name == "clip.mp4",
      f"copied {len(res['copied'])}, failed {res['failed']}")
mode("")

print("\nPhotos show as they are read")
for r in records:
    for ext in ("", ".original"):
        Path(r["preview"] + ext).unlink(missing_ok=True)
mode("slow-thumbs")
listed, pictured = [], []
began = time.monotonic()
streamed = devices.scan_device(devs[0], on_batch=lambda b: listed.append((time.monotonic() - began, len(b))),
                               on_previews=lambda m: pictured.append((time.monotonic() - began, [r["preview"] for r in m])))
total = time.monotonic() - began
check("what is on the phone is handed over at once, before any picture",
      listed and pictured and listed[0][0] < pictured[0][0] and listed[0][0] < 1.0,
      f"list at {listed[0][0]:.1f}s, first pictures at {pictured[0][0]:.1f}s of {total:.1f}s" if listed and pictured else "none")
check("every photo is in that first list", sum(n for _t, n in listed) == 3)
check("the pictures are reported as they are made, each with its file already there",
      pictured and all(Path(p).is_file() for _t, ps in pictured for p in ps)
      and sum(len(ps) for _t, ps in pictured) == 3)
check("a photo taken upright is turned upright",
      Image.open(next(r["preview"] for r in streamed if r["filename"] == "IMG_0002.HEIC")).size == (300, 400))
mode("")

mode("locks-after-1")
for r in records:
    Path(r["preview"]).unlink(missing_ok=True)
locked = devices.scan_device(devs[0])
check("a phone that locks itself halfway still lists everything", len(locked) == 3, str(len(locked)))
check("and Piklin is told, so it can ask for the phone to be unlocked",
      wpd.take_problem(devs[0].uri.split("://", 1)[1]) == "locked"
      and wpd.take_problem(devs[0].uri.split("://", 1)[1]) is None)
mode("")

print("\nAn iPhone that numbers its objects again")
flag = Path(str(LOG) + ".renumbered")
for r in records:
    Path(r["path"]).unlink(missing_ok=True)
flag.write_text("x")
ok = devices.fetch(r5["path"])
check("a photo whose number changed is found again by name and copied",
      ok and Path(r5["path"]).read_bytes().startswith(b"ORIGINAL-o5") and "fetch o5-v2" in logged(), str(logged()[-2:]))
flag.unlink()
Path(r5["path"]).unlink(missing_ok=True)

print("\nAn Android phone, which gives no small pictures")
VIDEO = Path("C:/piklin-build/android_clip.mp4")
if VIDEO.is_file():
    os.environ["FAKE_WPD_VIDEO"] = str(VIDEO)
    for r in records:
        Path(r["preview"]).unlink(missing_ok=True)
    mode("android")
    android = devices.scan_device(devs[0])
    shots = {r["filename"]: Path(r["preview"]) for r in android}
    check("a photo gets its small picture made from the photo itself",
          shots["IMG_0001.JPG"].is_file() and max(Image.open(shots["IMG_0001.JPG"]).size) <= 400)
    check("a video gets its small picture made from a frame of it",
          shots["clip.mp4"].is_file() and max(Image.open(shots["clip.mp4"]).size) <= 400,
          str(Image.open(shots["clip.mp4"]).size) if shots["clip.mp4"].is_file() else "no picture")
    check("the copies made for that are removed, and nothing is left in the phone's folder",
          not any(Path(r["path"]).exists() for r in android if r["filename"] != "IMG_0001.JPG")
          and not list(devs[0].path.rglob(".making-*")))
    mode("")
else:
    print("  (no sample video: skipped)")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
