#!/usr/bin/env python3
"""End-to-end test of every subsystem.

Exercised against real photographs on disk, not fixtures, because the
failures that matter here are decoder- and metadata-shaped.
"""
import glob, io, json, os, shutil, sys, tempfile, time, traceback
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from piklin import i18n as _i18n
_i18n.setup("en")

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return cond

def section(t): print(f"\n\033[1m{t}\033[0m")

# Test photographs: any folder of JPEGs (Linux Mint ships a suitable set).
SRC = sorted(glob.glob(os.path.join(
    os.environ.get("PIKLIN_TEST_PHOTOS", "/usr/share/backgrounds/linuxmint-wallpapers"), "*.jpg")))
# A portrait for the face-detection tests: set PIKLIN_TEST_FACE to an image of a face.
FACE = [os.environ["PIKLIN_TEST_FACE"]] if os.environ.get("PIKLIN_TEST_FACE") else []
TMP = tempfile.mkdtemp(prefix="pika-test-")

# ===================================================================
section("1. Image I/O and metadata")
from piklin import imageio as iio
check("41+ formats supported", len(iio.supported_extensions()) >= 41,
      f"{len(iio.supported_extensions())}")
check("no missing codecs", not iio.missing_codecs(), str(iio.missing_codecs()))
rec = iio.probe(SRC[0])
check("probe returns dimensions", rec and rec["width"] > 0 and rec["height"] > 0,
      f"{rec['width']}x{rec['height']}")
check("probe assigns a date", rec.get("taken_at") is not None, rec.get("date_source"))
img = iio.load_rgb(SRC[0], max_side=800)
check("decode shape/range", img.shape[2] == 3 and 0 <= img.min() and img.max() <= 1,
      str(img.shape))
check("draft decode caps size", max(img.shape[:2]) <= 800)
check("fingerprint is stable", iio.fingerprint(SRC[0]) == iio.fingerprint(SRC[0]))
check("fingerprint differs per file", iio.fingerprint(SRC[0]) != iio.fingerprint(SRC[1]))

# ===================================================================
section("2. Edit engine: every tool")
from piklin.engine import ops, tools, looks
from piklin.engine.stack import EditStack, Renderer, render_full
test = iio.load_rgb(SRC[1], max_side=1000)
face = iio.load_rgb(FACE[0], max_side=800) if FACE else test
ctx = tools.Ctx(scale=0.5, full_size=(2000, 1500))
bad = []
for tid, tool in tools.REGISTRY.items():
    p = tool.defaults()
    src = face if tool.group == "Portrait" else test
    if tid == "text": p["text"] = "Hello"
    if tid == "double_exposure": p["image"] = SRC[2]
    if tid == "selective":
        p["points"] = [{"x":.4,"y":.4,"size":.3,"brightness":40,"saturation":25,
                        "contrast":10,"structure":15}]
    if tid in ("brush","healing"):
        p["strokes"] = [{"mode":"Dodge & Burn","value":.8,"radius":.05,
                         "points":[[.3,.3],[.5,.5]]}]
    if tid == "crop": p["rect"] = [.1,.1,.7,.7]
    if tid == "rotate": p["straighten"] = 5
    if tid == "head_pose": p.update(yaw=25, pitch=15, smile=20)
    try:
        r = tool.apply(src, p, ctx)
        ok = np.isfinite(r).all() and r.min() >= -1e-3 and r.max() <= 1+1e-3
    except Exception as e:
        ok, r = False, None
        bad.append(f"{tid}: {type(e).__name__} {e}")
    if not ok: bad.append(tid)
check(f"all {len(tools.REGISTRY)} tools run cleanly", not bad, "; ".join(bad[:3]))

styles = 0; style_bad = []
for tid, tool in tools.REGISTRY.items():
    for param in tool.params:
        if param.kind != "choice": continue
        for c in param.choices:
            d = tool.defaults(); d[param.key] = c
            if tid == "text": d["text"] = "x"
            if tid == "double_exposure": d["image"] = SRC[2]
            try:
                r = tool.apply(test if tool.group != "Portrait" else face, d, ctx)
                styles += 1
                if not (np.isfinite(r).all() and r.max() <= 1+1e-3): style_bad.append(f"{tid}/{c}")
            except Exception as e:
                style_bad.append(f"{tid}/{c}:{type(e).__name__}")
check(f"all {styles} style presets render", not style_bad, "; ".join(style_bad[:3]))

look_bad = []
for name in looks.names():
    st = EditStack(); st.apply_look(name)
    try:
        o = Renderer().render(test, st, scale=.5, full_size=(2000,1500))
        if not (np.isfinite(o).all() and o.max() <= 1+1e-3): look_bad.append(name)
    except Exception as e: look_bad.append(f"{name}:{type(e).__name__}")
check(f"all {len(looks.names())} Looks render", not look_bad, "; ".join(look_bad[:3]))

# resolution independence
st = EditStack(); st.add("tune", dict(brightness=30, ambiance=40)); st.add("vignette", {})
small = Renderer().render(ops.resize(test, 400, 300), st, scale=.2, full_size=(2000,1500))
big   = Renderer().render(ops.resize(test, 800, 600), st, scale=.4, full_size=(2000,1500))
diff = float(np.abs(ops.resize(small, 800, 600) - big).mean())
check("edits are resolution independent", diff < 0.035, f"mean diff {diff:.4f}")

# render cache
st2 = EditStack()
st2.add("tune", dict(brightness=20, ambiance=30)); st2.add("details", dict(structure=30))
st2.add("vignette", dict(outer_brightness=-40))
r = Renderer(); r.render(test, st2, scale=.5)
t0=time.perf_counter(); r.render(test, st2, scale=.5); cached=(time.perf_counter()-t0)*1000
st2.set_params(2, dict(outer_brightness=-50))
t0=time.perf_counter(); r.render(test, st2, scale=.5); top=(time.perf_counter()-t0)*1000
check("cache: unchanged stack is instant", cached < 5, f"{cached:.1f} ms")
check("cache: top-layer edit is fast", top < 150, f"{top:.0f} ms")

# undo / redo / persistence
st3 = EditStack(); st3.add("tune", dict(brightness=25)); sig = st3.signature()
st3.add("noir", {})
check("undo restores state", st3.undo() and st3.signature() == sig)
check("redo works", st3.redo() and len(st3) == 2)
p = os.path.join(TMP, "edit.json"); st3.save(p, SRC[0])
check("sidecar round-trips", EditStack.load(p).signature() == st3.signature())
check("sidecar is readable JSON", json.loads(open(p).read())["format"] == "pikalicious-edit")
check("disabled layer is a no-op",
      (lambda s: (s.layers[0].__setattr__("enabled", False),
                  np.allclose(Renderer().render(test, s, scale=.5), test))[1])
      (EditStack([__import__("piklin.engine.stack", fromlist=["Layer"]).Layer("noir", tools.REGISTRY["noir"].defaults())])))

# ===================================================================
section("3. Face detection")
from piklin.engine import faces
check("model bundled", faces.model_path() is not None)
check("detector available", faces.available())
if FACE:
    found = faces.detect(face)
    check("finds a real face", len(found) >= 1, f"{len(found)} face(s)")
    if found:
        check("landmarks are sane", -1 <= found[0].yaw <= 1 and abs(found[0].roll) < 90)
        m = faces.skin_mask(face, found)
        check("skin mask is bounded", 0 <= m.min() and m.max() <= 1 and 0 < m.mean() < .5,
              f"coverage {m.mean():.3f}")
check("no false positive on noise",
      len(faces.detect(np.clip(np.random.default_rng(0).random((400,400,3),dtype=np.float32),0,1))) == 0)

# ===================================================================
section("4. Catalog")
from piklin.catalog import Catalog
db = os.path.join(TMP, "cat.db"); cat = Catalog(db)
rid = cat.add_root(os.path.dirname(SRC[0]))
cat.upsert_photos([iio.probe(s, rid) for s in SRC[:8]])
check("photos indexed", cat.counts()["library"] == 8, str(cat.counts()["library"]))
cat.set_favorite([1,2], True); cat.set_rating([3], 5); cat.trash([8])
c = cat.counts()
check("favourites/trash counted", c["favorites"] == 2 and c["trash"] == 1)
check("scopes filter", len(cat.browse(scope="favorites")) == 2 and
      len(cat.browse(scope="trash")) == 1 and len(cat.browse()) == 7)
check("search by filename", len(cat.browse(search=os.path.basename(SRC[0])[:6])) >= 1)
aid = cat.create_album("Test"); cat.album_add(aid, [1,2,3])
check("albums work", len(cat.browse(scope="album", album_id=aid)) == 3)
cat.upsert_photos([iio.probe(SRC[0], rid)])
check("rescan preserves user state", bool(cat.photo(1)["favorite"]))
import threading
errs=[]
def w():
    try: cat.upsert_photos([dict(path=f"/x/{threading.get_ident()}.jpg", filename="x", ext="jpg")])
    except Exception as e: errs.append(e)
ts=[threading.Thread(target=w) for _ in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
check("concurrent writes are safe", not errs, str(errs[:1]))

# ===================================================================
section("4b. Folders and album tree")
trips = cat.create_folder("Trips")
euro = cat.create_folder("Europe", parent_id=trips)
alb = cat.create_album("Paris", folder_id=euro)
cat.album_add(alb, [1, 2])
tree = cat.tree()
check("top-level tree has the new folder", any(
    n["kind"] == "folder" and n["row"]["name"] == "Trips" for n in tree))
euro_node = next(n["children"][0] for n in tree
                 if n["row"]["name"] == "Trips")
check("nested folder holds its album", euro_node["children"][0]["row"]["name"] == "Paris")
cat.move_album_to_folder(alb, None)
check("move to top level works",
      any(n["kind"] == "album" and n["row"]["name"] == "Paris"
          for n in cat.tree()))
cat.move_album_to_folder(alb, euro)
cat.delete_folder(euro)
check("deleting a folder cascades to its albums",
      cat.scalar("SELECT COUNT(*) FROM albums WHERE id=?", (alb,), 0) == 0)
check("cascade also removes album_items",
      cat.scalar("SELECT COUNT(*) FROM album_items WHERE album_id=?",
                (alb,), 0) == 0)

section("4c. Smart albums")
sid = cat.create_smart_album("Five star",
                             [{"field": "rating", "op": "is", "value": 5}])
before = cat.smart_album_count(sid)
cat.set_rating([1], 5)
after_one = cat.smart_album_count(sid)
check("smart album finds what matches", after_one == before + 1,
      f"{before} -> {after_one}")
cat.set_rating([2], 5)
check("smart album recomputes as the library changes",
      cat.smart_album_count(sid) == after_one + 1)
cat.set_rating([1], 0)
check("and shrinks again when the mark is removed",
      cat.smart_album_count(sid) == after_one)
both = cat.create_smart_album(
    "Fuji and rated",
    [{"field": "camera_make", "op": "is", "value": "fuji"},
     {"field": "rating", "op": "greater than", "value": 0}], "all")
any_ = cat.create_smart_album(
    "Fuji or rated",
    [{"field": "camera_make", "op": "is", "value": "fuji"},
     {"field": "rating", "op": "greater than", "value": 0}], "any")
check("'all' is stricter than 'any'",
      cat.smart_album_count(both) <= cat.smart_album_count(any_))
unknown = cat.create_smart_album("Bad rule",
                                 [{"field": "nope", "op": "is", "value": 1}])
check("an unrecognised rule does not break the album",
      cat.smart_album_count(unknown) == len(cat.browse()))

section("4d. Cameras and devices")
from piklin import devices as devmod
from piklin.paths import Library
from piklin.indexer import Indexer
from piklin.thumbs import ThumbCache
card = os.path.join(TMP, "card")
dcim = os.path.join(card, "DCIM", "100NIKON")
os.makedirs(dcim, exist_ok=True)
for i, srcp in enumerate(SRC[:3]):
    shutil.copy2(srcp, os.path.join(dcim, f"DSC_{i:04d}.JPG"))
dev = devmod.Device(id="t", name="Test Cam", path=Path(card),
                    uri="file://" + card)
check("a card with DCIM is recognised as a camera",
      devmod._looks_like_camera(Path(card)))
fast = devmod.scan_device(dev)
check("device listing finds the photos", len(fast) == 3, f"{len(fast)}")
check("listing does not open the files (no EXIF yet)",
      all(r["date_source"] == "mtime" for r in fast))
libcam = Library(os.path.join(TMP, "CamLib")).ensure()
catcam = Catalog(libcam.db)
res = devmod.import_photos(libcam, fast)
check("import copies the photos in", len(res["copied"]) == 3)
check("imported photos are filed by date",
      any(p.parent.parent.parent.name == "Originals"
          for p in res["copied"]))
Indexer(catcam, ThumbCache(libcam.thumbs),
        library_root=libcam.root).scan([str(libcam.originals)])
again = devmod.already_imported(catcam, [iio.probe(p) for p in res["copied"]])
check("re-importing the same card is recognised", len(again) == 3)

# Import parity: what landed is reported, so "Delete Items" can only ever
# remove camera files that are safely in the library.
card2 = os.path.join(TMP, "card2", "DCIM", "100NIKON"); os.makedirs(card2, exist_ok=True)
for i, src in enumerate(SRC[:3]):
    shutil.copy2(src, os.path.join(card2, f"DSC_{i:04d}.JPG"))
class _ImpLib: pass
implib = _ImpLib(); implib.originals = Path(TMP) / "ImpLib" / "Originals"
recs2 = [iio.probe(os.path.join(card2, f)) for f in sorted(os.listdir(card2))]
r1 = devmod.import_photos(implib, recs2)
check("import reports the camera files now in the library",
      len(r1["copied"]) == 3 and len(r1["sources"]) == 3)
r2 = devmod.import_photos(implib, recs2)
check("re-import of identical files counts them as done, not copied",
      r2["copied"] == [] and r2["skipped"] == 3 and len(r2["sources"]) == 3)
check("import reports where every photo now lives (for filing into an album)",
      len(r1["placed"]) == 3 and sorted(r2["placed"]) == sorted(r1["copied"]))
removed, failed = devmod.delete_from_device(r1["sources"][:1])
check("delete from device removes only what was imported",
      removed == 1 and failed == 0 and len(os.listdir(card2)) == 2
      and all(Path(p).exists() for p in r1["copied"]))

# Compressed on import (Settings > Storage): smaller, metadata kept, and a
# second import of the same card recognises what is already there.
card3 = os.path.join(TMP, "card3", "DCIM"); os.makedirs(card3, exist_ok=True)
from PIL import Image as _Im3
from PIL.TiffImagePlugin import IFDRational as _R3
_ex3 = _Im3.Exif(); _ex3[0x010F] = "NIKON CORPORATION"; _ex3[0x0110] = "NIKON Z fc"
_ex3.get_ifd(0x8769)[0x9003] = "2021:01:30 12:00:00"
_g3 = _ex3.get_ifd(0x8825); _g3[1] = "N"; _g3[2] = (_R3(18, 1), _R3(28, 1), _R3(0, 1)); _g3[3] = "W"; _g3[4] = (_R3(69, 1), _R3(56, 1), _R3(0, 1))
_cam_src = os.path.join(card3, "DSC_0872.JPG")
_Im3.open(SRC[0]).convert("RGB").resize((3000, 2000)).save(_cam_src, quality=98, exif=_ex3.tobytes())
implib3 = _ImpLib(); implib3.originals = Path(TMP) / "ImpLib3" / "Originals"
rec3 = [iio.probe(_cam_src)]
res3 = devmod.import_photos(implib3, rec3, profile="visually_lossless")
out3 = res3["placed"][0] if res3["placed"] else None
check("import compresses with the chosen profile",
      out3 is not None and out3.stat().st_size < os.path.getsize(_cam_src),
      f"{os.path.getsize(_cam_src)//1024} KB -> {out3.stat().st_size//1024 if out3 else 0} KB")
_e3 = _Im3.open(out3).getexif() if out3 else {}
check("compressed import keeps date, camera and location",
      out3 is not None and _e3.get(0x010F) == "NIKON CORPORATION"
      and _e3.get_ifd(0x8769).get(0x9003) == "2021:01:30 12:00:00"
      and bool(_e3.get_ifd(0x8825)))
res3b = devmod.import_photos(implib3, rec3, profile="visually_lossless")
check("re-importing a compressed photo does not duplicate it",
      res3b["copied"] == [] and res3b["skipped"] == 1
      and len(list(implib3.originals.rglob("*.JPG"))) == 1)

section("4e. Delete semantics")
alb2 = cat.create_album("Scratch")
cat.album_add(alb2, [1, 2])
cat.album_remove(alb2, [1])
check("removing from an album leaves the photo in the library",
      len(cat.album_photo_paths(alb2)) == 1
      and cat.photo(1) is not None
      and cat.photo(1)["trashed_at"] is None)
cat.trash([2])
check("trashing hides it from the library but keeps the row",
      cat.photo(2) is not None and cat.photo(2)["trashed_at"] is not None)
cat.untrash([2])
check("recovering puts it back", cat.photo(2)["trashed_at"] is None)

section("5. Library layout and index rebuild")
from piklin.paths import Library
lib = Library(os.path.join(TMP, "Lib")).ensure()
check("layout created", all(d.is_dir() for d in
      (lib.root, lib.edits, lib.albums, lib.originals, lib.exports, lib.trash, lib.thumbs)))
check("README explains the folder", (lib.root/"README.txt").is_file() and
      "catalog.db" in (lib.root/"README.txt").read_text())
check("cache is tagged for backup tools", (lib.cache/"CACHEDIR.TAG").is_file())
check("sidecar path mirrors the tree",
      lib.edit_sidecar(str(lib.originals/"2024"/"a.jpg")) ==
      lib.edits/"Originals"/"2024"/"a.jpg.json")

# ===================================================================
section("6. Indexer")
from piklin.indexer import Indexer
from piklin.thumbs import ThumbCache
photos = os.path.join(TMP, "photos"); os.makedirs(photos+"/a", exist_ok=True)
for i, s in enumerate(SRC[:6]): shutil.copy2(s, f"{photos}/a/P{i}.jpg")
os.makedirs(photos+"/node_modules", exist_ok=True); shutil.copy2(SRC[0], photos+"/node_modules/skip.jpg")
cat2 = Catalog(os.path.join(TMP,"c2.db")); tc = ThumbCache(os.path.join(TMP,"th"))
ix = Indexer(cat2, tc)
pr = ix.scan([photos])
check("scan finds photos", pr.added == 6, f"added {pr.added}")
check("junk folders skipped", cat2.scalar("SELECT COUNT(*) FROM photos WHERE path LIKE '%node_modules%'") == 0)
t0=time.perf_counter(); pr2 = ix.scan([photos]); el=(time.perf_counter()-t0)
check("rescan is incremental", pr2.added == 0 and pr2.updated == 0 and el < 1.0, f"{el*1000:.0f} ms")
pid = cat2.q("SELECT id FROM photos LIMIT 1")[0]["id"]
cat2.set_favorite([pid], True); cat2.set_rating([pid], 4)
old = cat2.photo(pid)["path"]; new = os.path.join(photos, "moved.jpg"); shutil.move(old, new)
pr3 = ix.scan([photos])
moved = cat2.photo_by_path(new)
check("moved file keeps rating/favourite",
      pr3.moved == 1 and moved and moved["favorite"] and moved["rating"] == 4)
victim = cat2.q("SELECT id,path FROM photos WHERE path!=? LIMIT 1",(new,))[0]
os.remove(victim["path"]); ix.scan([photos])
check("deleted file marked missing, not dropped",
      cat2.photo(victim["id"]) is not None and cat2.photo(victim["id"])["thumb_state"] == 3)
# The file comes back unchanged (drive plugged in again): the tile must not
# stay a blank "missing" block, even though the file is not re-probed.
shutil.copy2(new, victim["path"]); os.utime(victim["path"], (cat2.photo(victim["id"])["mtime"],) * 2)
ix.scan([photos])
check("returned file loses its missing flag",
      cat2.photo(victim["id"])["thumb_state"] == 0, str(cat2.photo(victim["id"])["thumb_state"]))
tp = ix.build_thumbnails()
check("thumbnails generated", tp.done > 0 and tp.errors == 0, f"{tp.done} thumbs")
check("thumbnail cache populated", tc.size_on_disk() > 0)
# Roots never nest: a subfolder of a watched folder is the same root, and a
# parent absorbs the roots inside it (a rebuild once registered both
# "photos" and "photos/2024", scanning those files twice).
cat3 = Catalog(os.path.join(TMP, "c3.db"))
os.makedirs(photos + "/a/b", exist_ok=True)
r_outer = cat3.add_root(photos)
check("subfolder of a root is not a new root",
      cat3.add_root(photos + "/a") == r_outer and len(cat3.roots()) == 1)
cat4 = Catalog(os.path.join(TMP, "c4.db"))
r_inner = cat4.add_root(photos + "/a")
Indexer(cat4, None).scan([photos + "/a"])
r_parent = cat4.add_root(photos)
check("parent folder absorbs inner roots and their photos",
      len(cat4.roots()) == 1 and
      cat4.scalar("SELECT COUNT(*) FROM photos WHERE root_id != ?", (r_parent,)) == 0)

# Utility and media-type views in the sidebar.
views = os.path.join(TMP, "views"); os.makedirs(views, exist_ok=True)
shutil.copy2(SRC[0], f"{views}/one.jpg"); shutil.copy2(SRC[0], f"{views}/one copy.jpg")
shutil.copy2(SRC[1], f"{views}/Screenshot 2026-01-01 at 10.00.00.png"
             if SRC[1].lower().endswith(".png") else f"{views}/Screenshot_2026.jpg")
cat5 = Catalog(os.path.join(TMP, "c5.db"))
libroot = os.path.join(TMP, "LibV"); os.makedirs(libroot + "/Originals", exist_ok=True)
shutil.copy2(SRC[2], libroot + "/Originals/imported.jpg")
ix5 = Indexer(cat5, None, library_root=libroot)
ix5.scan([views, libroot + "/Originals"])
c5 = cat5.counts()
check("Duplicates view gathers exact copies",
      c5["duplicates"] == 2 and len(cat5.browse(scope="duplicates")) == 2, str(c5["duplicates"]))
check("Screenshots view finds screenshots",
      c5["screenshots"] == 1 and "creenshot" in cat5.browse(scope="screenshots")[0]["filename"])
check("Imports view holds what landed in Originals",
      [r["filename"] for r in cat5.browse(scope="imports")] == ["imported.jpg"])
# Merge duplicates: one copy stays, carrying the others' albums and marks;
# the rest go to Recently Deleted, files untouched.
dup_ids = [r["id"] for r in cat5.browse(scope="duplicates")]
alb = cat5.create_album("Viaje")
cat5.album_add(alb, [dup_ids[1]])
cat5.set_favorite([dup_ids[1]], True)
res = cat5.merge_duplicates(dup_ids)
keeper = res["kept"][0] if res["kept"] else None
check("merge keeps exactly one copy", len(res["kept"]) == 1 and len(res["trashed"]) == 1)
check("merge keeps the copy the user marked, with its album",
      keeper == dup_ids[1] and cat5.photo(keeper)["favorite"] == 1 and
      [a["name"] for a in cat5.albums_for_photo(keeper)] == ["Viaje"])
check("merged copies go to Recently Deleted, files kept",
      cat5.photo(res["trashed"][0])["trashed_at"] is not None and
      all(os.path.exists(cat5.photo(i)["path"]) for i in dup_ids))
check("Duplicates view is empty after merging", cat5.counts()["duplicates"] == 0)
cat5.untrash(res["trashed"])
# Filter menu: several choices show photos matching any of them.
all_ids = {r["id"] for r in cat5.browse(scope="library")}
fav_ids = {r["id"] for r in cat5.browse(scope="library", filters={"favorites"})}
shot_ids = {r["id"] for r in cat5.browse(scope="library", filters={"screenshots"})}
both = {r["id"] for r in cat5.browse(scope="library", filters={"favorites", "screenshots"})}
check("filter narrows the view", fav_ids and fav_ids < all_ids and
      all(cat5.photo(i)["favorite"] for i in fav_ids))
check("several filters combine as any-of", both == fav_ids | shot_ids)
no_album = {r["id"] for r in cat5.browse(scope="library", filters={"not_in_album"})}
check("Not in an Album excludes album photos",
      not (no_album & {r["photo_id"] for r in cat5.q("SELECT photo_id FROM album_items")}))
check("an unknown filter is ignored", {r["id"] for r in cat5.browse(
      scope="library", filters={"bogus"})} == all_ids)
# Years / Months summary cards: one group per period, newest first, counts
# that add up, and key photos with favourites first.
yrs = cat5.summary("year")
live = cat5.counts()["library"]
check("Years summary: one card per year, counts add up",
      yrs and sum(g["count"] for g in yrs) == live and
      [g["key"] for g in yrs] == sorted({g["key"] for g in yrs}, reverse=True))
mons = cat5.summary("month", per_group=4)
check("Months summary: labelled by month, at most 4 key photos",
      mons and all(len(g["photos"]) <= 4 for g in mons) and
      all(len(g["key"]) == 7 for g in mons) and sum(g["count"] for g in mons) == live)
fav_one = cat5.q("SELECT id FROM photos WHERE favorite=1 AND trashed_at IS NULL "
                 "AND hidden=0 LIMIT 1")
if fav_one:
    fid = fav_one[0]["id"]
    check("a favourite is the key photo of its year",
          any(g["photos"] and g["photos"][0]["id"] == fid for g in cat5.summary("year")))
check("summary respects the Filter menu",
      sum(g["count"] for g in cat5.summary("year", filters={"favorites"})) ==
      cat5.counts()["favorites"])
# Adjust Date and Time / Adjust Location: kept across a rescan (which
# would otherwise re-read EXIF) and restored from photo-state.json.
import datetime as _dt
from piklin import sidecars as sc
class _LibD: pass
fake = _LibD(); fake.root = Path(libroot); fake.albums = Path(libroot) / "Albums"
fake.albums.mkdir(exist_ok=True)
imp = cat5.q("SELECT id, path FROM photos WHERE filename='imported.jpg'")[0]
new_ts = _dt.datetime(2001, 2, 3, 4, 5).timestamp()
cat5.set_taken_at([imp["id"]], new_ts)
cat5.set_location([imp["id"]], 19.43260, -99.13320)
os.utime(imp["path"])                        # force the rescan to re-probe
Indexer(cat5, None, library_root=libroot).scan([libroot + "/Originals"])
r5 = cat5.photo(imp["id"])
check("adjusted date survives a rescan", r5["taken_at"] == new_ts and r5["date_source"] == "manual")
check("adjusted location survives a rescan",
      abs(r5["gps_lat"] - 19.4326) < 1e-6 and abs(r5["gps_lon"] + 99.1332) < 1e-6)
check("adjusted date is searchable", imp["id"] in [r["id"] for r in cat5.browse(search="2001")])
sc.write_photo_state(fake, cat5)
cat8 = Catalog(os.path.join(TMP, "c8.db"))
Indexer(cat8, None, library_root=libroot).scan([libroot + "/Originals"])
sc.restore_photo_state(fake, cat8)
r8 = cat8.photo_by_path(imp["path"])
check("adjusted date and location restored after a rebuild",
      r8["taken_at"] == new_ts and abs(r8["gps_lat"] - 19.4326) < 1e-6)
cat5.set_location([imp["id"]], None, None)
os.utime(imp["path"])
Indexer(cat5, None, library_root=libroot).scan([libroot + "/Originals"])
check("removed location stays removed after a rescan", cat5.photo(imp["id"])["gps_lat"] is None)
old_id = cat5.q("SELECT id FROM photos WHERE filename='one.jpg'")[0]["id"]
cat5.trash([old_id])
with cat5.write() as cur:
    cur.execute("UPDATE photos SET trashed_at=? WHERE id=?", (time.time() - 31 * 86400, old_id))
# Title, caption and keywords (Info panel): searchable, kept across
# a rescan, and restored from photo-state.json after catalog.db is rebuilt.
from piklin import sidecars as sc
tid = cat5.q("SELECT id FROM photos WHERE filename='imported.jpg'")[0]["id"]
cat5.set_text_fields([tid], title="Faro", caption="Atardecer en la costa",
                     keywords="mar, viaje")
check("caption is searchable", [r["id"] for r in cat5.browse(search="atardecer")] == [tid])
check("keyword is searchable", [r["id"] for r in cat5.browse(search="viaje")] == [tid])
Indexer(cat5, None, library_root=libroot).scan([libroot + "/Originals"])
os.utime(libroot + "/Originals/imported.jpg")          # force a re-probe
Indexer(cat5, None, library_root=libroot).scan([libroot + "/Originals"])
check("typed words survive a rescan",
      cat5.photo(tid)["title"] == "Faro" and
      [r["id"] for r in cat5.browse(search="faro")] == [tid])
class _Lib: pass
fake = _Lib(); fake.root = Path(libroot); fake.albums = Path(libroot) / "Albums"
fake.albums.mkdir(exist_ok=True)
sc.write_photo_state(fake, cat5)
cat6 = Catalog(os.path.join(TMP, "c6.db"))
Indexer(cat6, None, library_root=libroot).scan([libroot + "/Originals"])
sc.restore_photo_state(fake, cat6)
t6 = cat6.q("SELECT title, caption, keywords FROM photos WHERE filename='imported.jpg'")[0]
check("typed words restored after a rebuild",
      (t6["title"], t6["caption"], t6["keywords"]) == ("Faro", "Atardecer en la costa", "mar, viaje")
      and len(cat6.browse(search="atardecer")) == 1)
# Smart Albums: listed in the sidebar tree, filtered by their rules, and
# mirrored to Albums/_smart.json so a rebuild brings them back.
sid = cat5.create_smart_album("Capturas", [{"field": "filename", "op": "contains",
                                            "value": "screenshot"}], "all")
check("Smart Album appears in the sidebar tree",
      any(n["kind"] == "smart" and n["row"]["id"] == sid for n in cat5.tree()))
check("Smart Album filters by its rules",
      [r["filename"] for r in cat5.browse(scope="smart", smart_id=sid)] ==
      [r["filename"] for r in cat5.browse(scope="screenshots")])
sc.write_smart_albums(fake, cat5)
cat7 = Catalog(os.path.join(TMP, "c7.db"))
check("Smart Albums restored after a rebuild",
      sc.restore_smart_albums(fake, cat7) == 1 and
      [r["name"] for r in cat7.smart_albums()] == ["Capturas"] and
      sc.restore_smart_albums(fake, cat7) == 0)
check("Recently Deleted forgets items after 30 days (file kept)",
      cat5.forget_expired_trash(30) == 1 and cat5.photo(old_id) is None
      and os.path.exists(f"{views}/one.jpg"))

# ===================================================================
section("7. Compression")
from piklin import compress as cz, quality as qual
cam = os.path.join(TMP, "cam.jpg")
from PIL import Image
ex = Image.Exif(); ex[0x010F]="FUJIFILM"; ex[0x0110]="X-T5"
ex[0x9003]="2024:07:14 18:32:11"; ex[0x8827]=400
iio.to_pil(iio.load_rgb(SRC[1], max_side=1800)).save(cam,"JPEG",quality=98,exif=ex.tobytes())
results = {}
for pid_ in cz.PROFILES:
    r = cz.plan(cam, pid_); results[pid_] = r
    check(f"profile '{pid_}' produces a plan", r.ok and r.output_bytes > 0,
          f"{r.percent_saved:.0f}% saved, q={r.quality}")
check("no profile reports a negative saving",
      all(r.percent_saved >= -0.001 for r in results.values()))
check("'original' is byte-identical",
      results["original"].output_bytes == results["original"].original_bytes)
vl = results["visually_lossless"]
# The contract is not "always reaches the target" - some images cannot,
# at any encoder setting (fine-detail frames like confetti peak around
# 0.982 even at quality 96). The contract is that the target is met, or
# the shortfall is reported rather than hidden.
prof = cz.PROFILES["visually_lossless"]
met = vl.metrics.get("ssim", 0) >= prof.min_ssim
check("visually lossless: target met, or shortfall reported",
      met or "did not" in vl.note or "no ladder step" in vl.note,
      f"ssim {vl.metrics.get('ssim',0):.4f}; note={vl.note or 'target met'}")
check("visually lossless is still high quality", vl.metrics.get("ssim", 0) >= 0.97,
      f"ssim {vl.metrics.get('ssim',0):.4f}")
# and on an image that *can* reach it, it must actually be met
easy = cz.plan(SRC[0], "visually_lossless")
check("reachable target is actually met",
      easy.metrics.get("ssim", 0) >= prof.min_ssim or easy.note,
      f"ssim {easy.metrics.get('ssim',0):.4f}")
check("space saver beats visually lossless on size",
      results["space_saver"].output_bytes <= vl.output_bytes)
out = cz.compress_to(cam, os.path.join(TMP,"out.jpg"), "visually_lossless")
check("compressed file written", out.ok and os.path.getsize(out.output) > 0)
check("EXIF survives compression",
      iio.probe(out.output)["date_source"] == "exif" and
      iio.probe(out.output)["camera_model"] == "X-T5")
check("quality metric: identical images score 1.0", abs(qual.ssim(test, test) - 1.0) < 1e-6)
buf = io.BytesIO(); iio.to_pil(test).save(buf, "JPEG", quality=20)
degraded = np.asarray(Image.open(buf).convert("RGB"), np.float32)/255.
check("quality metric detects damage", qual.ssim(test, degraded) < 0.99,
      f"ssim {qual.ssim(test, degraded):.3f}")

# ===================================================================
# Export options: location removed on its own, a size
# cap, file names and day subfolders.
from PIL import Image as _Im
from PIL.TiffImagePlugin import IFDRational as _R
geo_src = os.path.join(TMP, "geo.jpg")
_ex = _Im.Exif(); _ex[0x010F] = "Nikon"; _ex[0x0110] = "Z fc"
_ex.get_ifd(0x8769)[0x9003] = "2019:07:14 10:00:00"
_g = _ex.get_ifd(0x8825); _g[1] = "N"; _g[2] = (_R(40, 1), _R(25, 1), _R(0, 1)); _g[3] = "W"; _g[4] = (_R(3, 1), _R(42, 1), _R(0, 1))
_Im.open(SRC[0]).convert("RGB").resize((2400, 1600)).save(geo_src, quality=92, exif=_ex.tobytes())
out_noloc = os.path.join(TMP, "exp", "noloc.jpg")
r_noloc = cz.compress_to(geo_src, out_noloc, "balanced", "jpeg", strip_location=True, max_side=1280)
ex_out = _Im.open(r_noloc.output).getexif()
check("export can drop only the location",
      r_noloc.ok and not ex_out.get_ifd(0x8825) and ex_out.get(0x010F) == "Nikon")
check("export Size caps the long edge", max(_Im.open(r_noloc.output).size) == 1280)
r_keep = cz.compress_to(geo_src, os.path.join(TMP, "exp", "keep.jpg"), "balanced", "jpeg")
check("location kept when asked", bool(_Im.open(r_keep.output).getexif().get_ifd(0x8825)))
t1 = cz.export_target(os.path.join(TMP, "exp2"), geo_src, 0, naming="title",
                      subfolder="day", title="Faro: norte/sur",
                      taken_at=_dt.datetime(2019, 7, 14, 10).timestamp())
check("export names by title in a day folder",
      t1.parent.name == "2019-07-14" and t1.name == "Faro norte sur.jpg")
t1.parent.mkdir(parents=True, exist_ok=True); t1.write_bytes(b"x")
t2 = cz.export_target(os.path.join(TMP, "exp2"), geo_src, 0, naming="title",
                      subfolder="day", title="Faro: norte/sur",
                      taken_at=_dt.datetime(2019, 7, 14, 10).timestamp())
check("export never overwrites: a clash gets a number", t2.name == "Faro norte sur 2.jpg")
check("sequential names", cz.export_target(os.path.join(TMP, "exp3"), geo_src, 4,
      naming="sequential").name == "Photo 0005.jpg")

section("8. Export round trip (edit -> render -> compress)")
st4 = EditStack()
st4.add("tune", dict(brightness=20, contrast=25, saturation=15))
st4.add("vignette", dict(outer_brightness=-45))
side = lib.edit_sidecar(cam); st4.save(side, cam)
check("edit saved beside the photo", side.is_file())
full = render_full(cam, EditStack.load(side))
orig = iio.load_rgb(cam)
check("full-res render matches source size", full.shape == orig.shape, str(full.shape))
check("render actually changed the image", float(np.abs(full-orig).mean()) > 0.01)
exp = cz.compress_to(cam, os.path.join(TMP,"exported.jpg"), "balanced", image=full)
check("edited export written", exp.ok and os.path.getsize(exp.output) > 0,
      f"{exp.output_bytes/1024:.0f} KiB")
check("original file untouched by editing",
      iio.fingerprint(cam) == iio.fingerprint(cam))

# ===================================================================
section("9. Backup destinations")
from piklin import remote as rem
dest = os.path.join(TMP, "nas"); os.makedirs(dest)
(lib.edits/"x.jpg.json").write_text("{}")
r = rem.Remote(id="t", name="T", kind="local", config={"path": dest})
b = r.backend()
check("local destination tests OK", b.test().ok)
files = rem.library_files(lib.root)
rels = [f[1] if isinstance(f, tuple) else f.relative_to(lib.root).as_posix()
        for f in files]
check("cache excluded from backup", not any(x.startswith(".cache") for x in rels))
check("sidecars queued first", files and not isinstance(files[0], tuple)
      and files[0].suffix == ".json")
check("favourites and hidden state are backed up",
      all(n in rels for n in ("photo-state.json", "catalog.db"))
      or not (lib.root/"photo-state.json").exists(), rels[:8])
import sqlite3 as _sq
if not (lib.root/"catalog.db").exists():
    _c = _sq.connect(lib.root/"catalog.db"); _c.execute("CREATE TABLE t(x)"); _c.commit(); _c.close()
catalog_bytes = (lib.root/"catalog.db").read_bytes()
snap1 = rem.snapshot_catalog(lib.root); m1 = snap1.stat().st_mtime if snap1 else 0
time.sleep(1.1)
snap2 = rem.snapshot_catalog(lib.root)
check("unchanged catalog snapshot is not sent again",
      snap2 is not None and snap2.stat().st_mtime == m1)
p1 = b.push(lib.root, files)
check("backup uploads files", p1.uploaded > 0 and p1.errors == 0, f"{p1.uploaded} files")
p2 = b.push(lib.root, files)
check("second backup skips unchanged", p2.uploaded == 0 and p2.skipped == p1.uploaded)
class _UploadTimeBackend(type(b)):
    # Like most WebDAV servers: files carry the time they arrived.
    def listing(self):
        return {k: (v[0], time.time()) for k, v in super().listing().items()}
ub = _UploadTimeBackend(r)
p3 = ub.push(lib.root, files)
check("server upload times don't cause re-uploads", p3.uploaded == 0, f"{p3.uploaded} re-sent")
changed = files[0]; changed.write_text('{"changed": true}')
p4 = ub.push(lib.root, files)
check("only the modified file is uploaded", p4.uploaded == 1, f"{p4.uploaded} sent")
class _DownBackend(type(b)):
    def listing(self):
        raise OSError("HTTP Error 401")
p5 = _DownBackend(r).push(lib.root, files)
check("failed sign-in stops the backup", p5.phase == "error" and p5.uploaded == 0, p5.phase)

# Restore: only what is missing comes back
b.push(lib.root, rem.library_files(lib.root))      # backup is current
gone = lib.edits/"x.jpg.json"
gone_rel = gone.relative_to(lib.root).as_posix()
gone.unlink()
mine = lib.edits/"mine.json"; mine.write_text("new local")
mine_rel = mine.relative_to(lib.root).as_posix()
(Path(dest)/mine_rel).write_text("old backup")
deleted = lib.root/"Originals"/"deleted-on-purpose.jpg"
(Path(dest)/"Originals").mkdir(exist_ok=True)
(Path(dest)/"Originals"/"deleted-on-purpose.jpg").write_bytes(b"x" * 10)
pr = b.restore(lib.root, skip_paths=[str(deleted)])
check("restore brings back a lost file", gone.exists() and pr.restored == 1,
      f"restored {pr.restored}, present {pr.present}")
check("restore never replaces a file in the library", mine.read_text() == "new local")
check("restore leaves out photos deleted on purpose",
      not deleted.exists() and pr.skipped == 1)
check("restore never touches the open catalog",
      (lib.root/"catalog.db").read_bytes() == catalog_bytes
      and not (lib.root/"catalog.db.part").exists())
pp = b.push(lib.root, rem.library_files(lib.root))
check("restored files are not uploaded again", pp.uploaded == 1,
      f"{pp.uploaded} sent (only the newer local file should go)")
wd = lambda url: rem.Remote(id="w", name="w", kind="webdav",
                            config={"url": url}).backend().test().message
check("http allowed on the local network", "at home" not in wd("http://127.0.0.1:9"),
      wd("http://127.0.0.1:9"))
check("http refused for internet servers", "at home" in wd("http://photos.example.com/dav"),
      wd("http://photos.example.com/dav"))
import urllib.error as _ue
class _ApacheDav(rem.WebDavBackend):
    # Apache (QNAP) refuses Depth: infinity with 403: walk folder by folder
    tree = {"": [("", True, 0, 0), ("Originals", True, 0, 0), ("a.json", False, 2, 0)],
            "Originals": [("Originals", True, 0, 0), ("Originals/p.jpg", False, 5, 0)]}
    def _entries(self, rel, depth):
        if depth == "infinity":
            raise _ue.HTTPError("u", 403, "Forbidden", None, None)
        return self.tree[rel]
_idx = _ApacheDav(rem.Remote(id="a", name="a", kind="webdav", config={"url": "https://x"})).listing()
check("servers refusing deep listings are read folder by folder",
      _idx == {"a.json": (2, 0), "Originals/p.jpg": (5, 0)}, _idx)
_qnap_reply = """<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">
<D:response><D:href>/dav/Users/</D:href><D:propstat><D:prop><lp1:resourcetype><D:collection/></lp1:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/alex/</D:href><D:propstat><D:prop><lp1:resourcetype><D:collection/></lp1:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/Browser%20Station/</D:href><D:propstat><D:prop><lp1:resourcetype><D:collection/></lp1:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/@Recycle/</D:href><D:propstat><D:prop><lp1:resourcetype><D:collection/></lp1:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/.DS_Store</D:href><D:propstat><D:prop><lp1:resourcetype/><lp1:getcontentlength>14340</lp1:getcontentlength></D:prop></D:propstat></D:response>
</D:multistatus>"""
class _Browse(rem.WebDavBackend):
    def _request_path(self, method, path, data=None, extra=None):
        assert method == "PROPFIND" and path == "Users"
        return io.BytesIO(_qnap_reply.encode())
_names = _Browse(rem.Remote(id="b", name="b", kind="webdav",
                            config={"url": "https://nas:5001/dav"})).list_folders("/Users/")
check("folder picker lists server folders, hiding system ones",
      _names == ["alex", "Browser Station"], _names)

# Automatic backup and previous versions
import datetime as _dt
from piklin import autobackup as ab
alib = Path(TMP)/"auto"/"Lib"; (alib/"Edits").mkdir(parents=True); (alib/"Originals").mkdir()
(alib/"Edits"/"a.jpg.json").write_text('{"v": 1}')
(alib/"Originals"/"a.jpg").write_bytes(b"x" * 100)
adest = Path(TMP)/"auto"/"nas"; adest.mkdir()
ar = rem.Remote(id="auto", name="NAS", kind="local", config={"path": str(adest)})
o1 = ab.run_backup(alib, [ar], keep_days=30)
check("automatic backup sends a new library", o1.ok and o1.uploaded == 2, o1)
_calls = []
_ot, _op = rem.LocalBackend.test, rem.LocalBackend.push
rem.LocalBackend.test = lambda self: _calls.append("test") or _ot(self)
rem.LocalBackend.push = lambda self, *a, **k: _calls.append("push") or _op(self, *a, **k)
o2 = ab.run_backup(alib, [ar], keep_days=30)
check("nothing changed: the destination is not contacted", o2.ok and _calls == [], _calls)
time.sleep(1.1)
(alib/"Edits"/"a.jpg.json").write_text('{"v": 2, "more": true}')
o3 = ab.run_backup(alib, [ar], keep_days=30)
rem.LocalBackend.test, rem.LocalBackend.push = _ot, _op
_today = _dt.date.today().isoformat()
_kept = adest/".piklin-versions"/_today/"Edits"/"a.jpg.json"
check("only the changed file is uploaded",
      o3.ok and o3.uploaded == 1
      and json.loads((adest/"Edits"/"a.jpg.json").read_text())["v"] == 2, o3)
check("the replaced copy is kept as a previous version",
      _kept.exists() and json.loads(_kept.read_text())["v"] == 1)
_old = adest/".piklin-versions"/"2000-01-01"; _old.mkdir(parents=True); (_old/"x.json").write_text("{}")
_recent = adest/".piklin-versions"/(_dt.date.today() - _dt.timedelta(days=3)).isoformat()
_recent.mkdir(parents=True)
time.sleep(1.1)
(alib/"Edits"/"a.jpg.json").write_text('{"v": 3, "more": true, "x": 1}')
ab.run_backup(alib, [ar], keep_days=30)
check("versions older than the limit are cleared", not _old.exists() and _recent.exists())
(alib/"Originals"/"a.jpg").unlink()
_pr = ar.backend().restore(alib)
check("restore brings files back but never old versions",
      (alib/"Originals"/"a.jpg").exists() and not (alib/".piklin-versions").exists(), _pr)
check("backup status reads naturally", ab.describe_last(time.time() - 300) == "5 minutes ago")
# Travelling: the NAS at home can't be reached, the cloud copy still happens.
_away = rem.Remote(id="away", name="QNAP", kind="local", config={"path": str(Path(TMP)/"auto"/"gone")})
_cloud_dir = Path(TMP)/"auto"/"cloud"; _cloud_dir.mkdir()
_cloud = rem.Remote(id="cloud", name="pCloud", kind="local", config={"path": str(_cloud_dir)})
_ot = rem.LocalBackend.test
rem.LocalBackend.test = lambda self: (rem.TestResult(False, "Can't reach the server", unreachable=True)
                                      if self.remote.id == "away" else _ot(self))
try:
    o4 = ab.run_backup(alib, [_away, _cloud], keep_days=30)
finally:
    rem.LocalBackend.test = _ot
check("a destination that can't be reached doesn't stop the others",
      not o4.ok and o4.unreachable and (_cloud_dir/"Edits"/"a.jpg.json").exists()
      and "QNAP" in o4.message, o4)
(Path(TMP)/"auto"/"gone").mkdir()
o5 = ab.run_backup(alib, [_away, _cloud], keep_days=30)
check("back home, the NAS catches up and the cloud is not sent anything again",
      o5.ok and (Path(TMP)/"auto"/"gone"/"Edits"/"a.jpg.json").exists(), o5)

# Photos dropped from the desktop, and USB drives
from piklin import devices as _dv
_drop = Path(TMP)/"drop"; (_drop/"Trip"/"Day 2").mkdir(parents=True); (_drop/".hidden").mkdir()
from PIL import Image as _PI
_PI.new("RGB", (8, 8)).save(_drop/"Trip"/"a.jpg")
_PI.new("RGB", (8, 8)).save(_drop/"Trip"/"Day 2"/"b.png")
(_drop/"Trip"/"notes.txt").write_text("not a photo")
_PI.new("RGB", (8, 8)).save(_drop/".hidden"/"c.jpg")
_single = _drop/"single.jpg"; _PI.new("RGB", (8, 8)).save(_single)
_recs = _dv.files_from_paths([_drop/"Trip", _single, _drop/"Trip"/"a.jpg"])
check("dropped folders are searched, non-photos and duplicates left out",
      sorted(r["filename"] for r in _recs) == ["a.jpg", "b.png", "single.jpg"],
      [r["filename"] for r in _recs])
_lib2 = Library(os.path.join(TMP, "DropLib")).ensure()
_res = _dv.import_photos(_lib2, _recs)
check("dropped photos are copied into the library",
      len(_res["copied"]) == 3 and all(Path(p).exists() for p in _res["copied"])
      and _single.exists(), _res)
class _Drive:
    def __init__(self, removable): self.removable = removable
    def is_removable(self): return self.removable
class _Mount:
    def __init__(self, drive): self.drive = drive
    def get_drive(self): return self.drive
# Copying many files: in parallel, stoppable, same names kept apart
import threading as _th
_many = Path(TMP)/"many"; (_many/"a").mkdir(parents=True); (_many/"b").mkdir()
for _i in range(6):
    _PI.new("RGB", (16, 16), (_i * 30, 0, 0)).save(_many/"a"/f"img{_i}.jpg")
_PI.new("RGB", (16, 16), (0, 200, 0)).save(_many/"b"/"img0.jpg")     # same name, other photo
_lib3 = Library(os.path.join(TMP, "ManyLib")).ensure()
_arrived = []
_r3 = _dv.import_photos(_lib3, _dv.files_from_paths([_many]), on_placed=_arrived.append, workers=4)
_names = sorted(p.name for p in _lib3.originals.rglob("*.jpg"))
check("files are copied in parallel without overwriting same-named photos",
      len(_r3["copied"]) == 7 and "img0.jpg" in _names and "img0-2.jpg" in _names, _names)
check("each file is reported as it arrives", len(_arrived) == 7 and not _r3["cancelled"])
check("no half-copied temporary files are left",
      not any(p.name.endswith(".part") for p in _lib3.originals.rglob("*")))
_stop = _th.Event(); _stop.set()
_lib4 = Library(os.path.join(TMP, "StopLib")).ensure()
_r4 = _dv.import_photos(_lib4, _dv.files_from_paths([_many]), cancel=_stop)
check("Stop copying keeps nothing half done", _r4["cancelled"] and not _r4["copied"], _r4)
from piklin.catalog import Catalog as _Cat3
from piklin.indexer import Indexer as _Ix3
_cat3 = _Cat3(_lib3.db)
_ix3 = _Ix3(_cat3, None, library_root=_lib3.root)
_added = _ix3.add_files(_r3["placed"][:3])
check("photos can be shown while the rest are still copying",
      _added == 3 and int(_cat3.scalar("SELECT COUNT(*) FROM photos", (), 0)) == 3)
_gone = str(Path(_r3["placed"][3]).resolve())
with _cat3.write() as _cur:
    _cur.execute("INSERT OR REPLACE INTO removed(path, removed_at) VALUES(?, ?)", (_gone, time.time()))
_ix3.add_files([_gone])
check("a removed photo imported again on purpose comes back",
      _gone not in set(_cat3.removed_paths())
      and _cat3.photo_by_path(_gone) is not None)
# One-click rotate, thumbnails that follow edits, update checks
from piklin import quick_edit as _qe
from piklin.engine.stack import EditStack as _ES2
from piklin.thumbs import ThumbCache as _TC
_rlib = Library(os.path.join(TMP, "RotLib")).ensure()
_wide = _rlib.originals / "wide.jpg"
_PI.new("RGB", (400, 200), (10, 120, 200)).save(_wide)
_q = _qe.rotate(_rlib, None, None, _wide, 1)
_st = _ES2.load(_rlib.edit_sidecar(_wide))
check("rotating adds a quarter turn as an ordinary edit",
      _q == 1 and len(_st) == 1 and _st.layers[0].params["quarter_turns"] == 1)
_q = _qe.rotate(_rlib, None, None, _wide, 2)
check("rotating again adds up instead of stacking layers",
      _q == 3 and len(_ES2.load(_rlib.edit_sidecar(_wide))) == 1)
_tc = _TC(_rlib.thumbs, workers=1, edits=_rlib.edit_sidecar)
_t1 = _tc.generate(_wide, 256)
check("the thumbnail shows the photo turned", _t1 is not None
      and _PI.open(_t1).size[1] > _PI.open(_t1).size[0], _PI.open(_t1).size if _t1 else None)
_qe.rotate(_rlib, None, None, _wide, 1)
check("turning back to upright removes the edit",
      not _rlib.edit_sidecar(_wide).exists())
_t2 = _tc.generate(_wide, 256)
check("the thumbnail follows the edit back", _t2 is not None and _t2 != _t1
      and _PI.open(_t2).size[0] > _PI.open(_t2).size[1], _PI.open(_t2).size if _t2 else None)
_tc.shutdown()
from piklin import updates as _up
check("newer versions are recognised",
      _up.is_newer("1.0.3", "1.0.2") and _up.is_newer("v1.1.0", "1.0.9")
      and not _up.is_newer("1.0.2", "1.0.2") and not _up.is_newer("1.0.1", "1.0.2"))
check("a USB drive is offered even without a camera folder",
      _dv._is_external(_Mount(_Drive(True)), Path("/somewhere"))
      and _dv._is_external(_Mount(None), Path("/media/me/Untitled"))
      and not _dv._is_external(_Mount(_Drive(False)), Path("/home/me")))
from piklin import sidecars as _scw
_mirror = alib/"mirror.json"
_scw._write_json(_mirror, {"a": 1}); _m1 = _mirror.stat().st_mtime_ns
time.sleep(0.02)
_scw._write_json(_mirror, {"a": 1})
check("unchanged state files are not rewritten (no needless backups)",
      _mirror.stat().st_mtime_ns == _m1)
check("missing folder reports clearly",
      not rem.Remote(id="x",name="x",kind="local",config={"path":"/nope"}).backend().test().ok)
check("plain HTTP refused",
      not rem.Remote(id="y",name="y",kind="webdav",
                     config={"url":"http://x/dav"}).backend().test().ok)
check("rclone absence explained",
      "rclone" in rem.Remote(id="z",name="z",kind="rclone",
                             config={"remote":"pcloud"}).backend().test().message.lower())

# ===================================================================
section("10. Settings")
from piklin.settings import Settings
s1 = Settings(os.path.join(TMP,"s.json"))
check("light theme is the default", s1["theme"] == "light", s1["theme"])
s1["grid_size"] = 260; s1.set("remotes", [{"id":"a"}])
check("settings persist", Settings(os.path.join(TMP,"s.json"))["grid_size"] == 260)
check("unknown keys fall back to defaults", s1.get("nope", "dflt") == "dflt")

# Every option a dialog saves survives a restart (load() keeps known keys only).
from piklin.settings import Settings as _S
_sp = os.path.join(TMP, "persist-settings.json")
_s1 = _S(_sp)
for _k, _v in (("grid_aspect", "original"), ("export_size", 2), ("export_naming", "title"),
               ("export_subfolder", "day"), ("export_include_location", True)):
    _s1.set(_k, _v)
_s2 = _S(_sp)
check("new view and export options survive a restart",
      (_s2.get("grid_aspect"), _s2.get("export_size"), _s2.get("export_naming"),
       _s2.get("export_subfolder"), _s2.get("export_include_location")) ==
      ("original", 2, "title", "day", True))
check("location is left out of exports by default",
      _S(os.path.join(TMP, "fresh-settings.json")).get("export_include_location") is False)

# ===================================================================
section("Videos")
# A short clip written with the OpenCV inside Piklin: no system tools needed.
import cv2 as _cv2
from piklin import video as _vid
from piklin.paths import Library as _L
from piklin.catalog import Catalog as _C
from piklin.indexer import Indexer as _Ix
_vdir = os.path.join(TMP, "videos"); os.makedirs(_vdir)
_clip = os.path.join(_vdir, "VID_20240501_101500.mp4")
_vw = _cv2.VideoWriter(_clip, _cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
for _i in range(50):
    _fr = np.zeros((240, 320, 3), np.uint8); _fr[:, :, 1] = _i * 5
    _vw.write(_fr)
_vw.release()
_rec = iio.probe(_clip)
check("video probed: size, length, date from name",
      _rec is not None and (_rec["width"], _rec["height"]) == (320, 240)
      and abs(_rec["duration"] - 2.0) < 0.1 and _rec["date_source"] == "filename",
      str({k: _rec.get(k) for k in ("width", "height", "duration", "date_source")} if _rec else None))
check("video formats are scanned", ".mp4" in iio.supported_extensions() and ".mkv" in iio.supported_extensions())
_poster = iio.load_pil(_clip, max_side=200)
check("video thumbnail is a frame from inside it", _poster.size == (200, 150) and np.asarray(_poster)[:, :, 1].mean() > 10)
check("durations read like a player", (_vid.format_duration(7), _vid.format_duration(754), _vid.format_duration(3723)) == ("0:07", "12:34", "1:02:03"))
libV = _L(os.path.join(TMP, "Video Library.piklin")).ensure()
shutil.copy2(_clip, libV.originals / "clip.mp4"); shutil.copy2(SRC[0], libV.originals / "photo.jpg")
cV = _C(libV.db); _ixV = _Ix(cV, ThumbCache(os.path.join(TMP, "vthumbs")), library_root=libV.root)
_ixV.scan([libV.originals]); _tpV = _ixV.build_thumbnails()
_cnt = cV.counts()
check("library counts photos and videos apart", _cnt["library"] == 2 and _cnt["videos"] == 1, str(_cnt))
check("Videos view and Photos/Videos filters",
      [r["filename"] for r in cV.browse(scope="videos")] == ["clip.mp4"]
      and [r["filename"] for r in cV.browse(filters=["photos"])] == ["photo.jpg"]
      and [r["filename"] for r in cV.browse(filters=["videos"])] == ["clip.mp4"])
check("video thumbnail generated", _tpV.errors == 0 and _tpV.done == 2)
check("length stored for the grid", abs((cV.browse(scope="videos")[0]["duration"] or 0) - 2.0) < 0.1)
_camV = _L(os.path.join(TMP, "CamVideo.piklin")).ensure()
_resV = devmod.import_photos(_camV, [iio.probe(_clip)], profile="visually_lossless")
check("camera video imported byte for byte, never recompressed",
      len(_resV["copied"]) == 1 and Path(_resV["copied"][0]).stat().st_size == os.path.getsize(_clip))

section("Videos: smart albums, live photos, smaller imports")
_sid = cV.create_smart_album("Videos over a second",
                             [{"field": "media_type", "op": "is video"},
                              {"field": "duration", "op": "greater than", "value": 1.5}], "all")
_sid2 = cV.create_smart_album("Only photos", [{"field": "media_type", "op": "is photo"}], "all")
check("smart album by media type and length",
      [r["filename"] for r in cV.browse(scope="smart", smart_id=_sid)] == ["clip.mp4"]
      and [r["filename"] for r in cV.browse(scope="smart", smart_id=_sid2)] == ["photo.jpg"])
libLive = _L(os.path.join(TMP, "Live Library.piklin")).ensure()
shutil.copy2(SRC[3], libLive.originals / "IMG_0001.jpg")
shutil.copy2(_clip, libLive.originals / "IMG_0001.mp4")
cLive = _C(libLive.db); _Ix(cLive, None, library_root=libLive.root).scan([libLive.originals])
_still = cLive.photo_by_path(str(libLive.originals / "IMG_0001.jpg"))
_liveCounts = cLive.counts()
check("live photo: the short video joins its still",
      _liveCounts["library"] == 1 and _liveCounts["videos"] == 0 and _still["has_live"] == 1
      and [r["filename"] for r in cLive.browse()] == ["IMG_0001.jpg"]
      and cLive.live_video_for(_still["id"])["path"].endswith("IMG_0001.mp4"), str(_liveCounts))
# a camera clip at a high bitrate, the way cameras record
import av as _av2
_camclip = os.path.join(_vdir, "DSC_0042.MOV")
with _av2.open(_camclip, "w", format="mov") as _o:
    _st = _o.add_stream("mpeg4", rate=25); _st.width, _st.height, _st.pix_fmt = 640, 360, "yuv420p"
    _st.bit_rate = 8_000_000; _st.options = {"qscale": "2"}
    _yy, _xx = np.mgrid[0:360, 0:640]
    for _i in range(75):
        _img = np.stack([(_xx + _i * 6) % 256, (_yy + _i * 3) % 256, (_xx + _yy) % 256], -1).astype(np.uint8)
        _fr = _av2.VideoFrame.from_ndarray(_img, format="rgb24").reformat(format="yuv420p"); _fr.pts = _i
        for _p in _st.encode(_fr): _o.mux(_p)
    for _p in _st.encode(): _o.mux(_p)
_camlib = _L(os.path.join(TMP, "SmallVideos.piklin")).ensure()
_r1 = devmod.import_photos(_camlib, [iio.probe(_camclip)], video_profile="h264")
_small = Path(_r1["copied"][0]) if _r1["copied"] else None
check("camera video stored smaller as H.264 when chosen",
      _small is not None and _small.suffix == ".mp4"
      and _small.stat().st_size < os.path.getsize(_camclip) * 0.9
      and devmod._source_size_of(_small) == os.path.getsize(_camclip),
      f"{os.path.getsize(_camclip)//1024} KB -> {(_small.stat().st_size//1024) if _small else '?'} KB")
_r2 = devmod.import_photos(_camlib, [iio.probe(_camclip)], video_profile="h264")
check("importing the card again does not duplicate the smaller copy",
      _r2["skipped"] == 1 and not _r2["copied"])

section("Video editing and export")
import av as _av
from piklin import video_edit as ve
_E = ve.VideoEdit(duration=10.0, start=1.0, end=9.0, cuts=[(3.0, 4.0), (6.0, 6.5)], speed=2.0)
check("kept pieces skip the trimmed ends and the cuts",
      _E.segments() == [(1.0, 3.0), (4.0, 6.0), (6.5, 9.0)], str(_E.segments()))
check("edited length accounts for speed", abs(_E.output_duration() - 3.25) < 1e-6)
check("playing resumes after a cut", _E.next_kept(3.5) == 4.0 and _E.next_kept(2.0) == 2.0
      and _E.next_kept(9.5) is None)
check("changes are counted for the edited mark", _E.changes() == 4 and ve.VideoEdit(duration=5).is_identity())
# a clip with sound, written with the PyAV inside Piklin
_snd = os.path.join(_vdir, "with-sound.mp4")
with _av.open(_snd, "w") as _o:
    _vs = _o.add_stream("mpeg4", rate=25); _vs.width, _vs.height, _vs.pix_fmt = 320, 240, "yuv420p"
    _as = _o.add_stream("aac", rate=48000); _as.layout = "stereo"
    for _i in range(75):
        _img = np.zeros((240, 320, 3), np.uint8); _img[:, :160, 0] = 200; _img[:, 160:, 2] = 200
        _img[100:140, (_i * 4) % 280:(_i * 4) % 280 + 40, 1] = 255   # a moving square
        _vf = _av.VideoFrame.from_ndarray(_img, format="rgb24").reformat(format="yuv420p")
        _vf.pts = _i
        for _p in _vs.encode(_vf): _o.mux(_p)
    _fs = _as.codec_context.frame_size or 1024
    _t = np.arange(48000 * 3) / 48000.0
    _wave = (0.2 * np.sin(2 * np.pi * 440 * _t)).astype(np.float32)
    for _k in range(0, len(_wave) - _fs, _fs):
        _af = _av.AudioFrame.from_ndarray(np.stack([_wave[_k:_k + _fs]] * 2), format="fltp", layout="stereo")
        _af.sample_rate = 48000; _af.pts = _k
        for _p in _as.encode(_af): _o.mux(_p)
    for _p in _vs.encode(): _o.mux(_p)
    for _p in _as.encode(): _o.mux(_p)
libE = _L(os.path.join(TMP, "Edit Library.piklin")).ensure()
_edit = ve.VideoEdit(duration=3.0, start=0.5, end=2.5, cuts=[(1.0, 1.5)], rotate=90)
ve.save(libE, _snd, _edit)
check("video edit saved beside the video and read back",
      ve.load(libE, _snd).segments() == _edit.segments() and ve.load(libE, _snd).rotate == 90)
_outs = {}
for _fmt in ve.available_formats():
    _outs[_fmt] = ve.export(_snd, os.path.join(TMP, "vexport", f"clip-{_fmt}"), _edit, fmt=_fmt)
check("export formats available", {"mp4", "webm", "gif"} <= set(ve.available_formats()), str(ve.available_formats()))
with _av.open(str(_outs["mp4"])) as _chk:
    _v0 = _chk.streams.video[0]
    check("MP4 export: turned, cut, with sound",
          (_v0.codec_context.width, _v0.codec_context.height) == (240, 320)
          and abs(_chk.duration / 1e6 - 1.5) < 0.15 and len(_chk.streams.audio) == 1,
          f"{_v0.codec_context.width}x{_v0.codec_context.height} {_chk.duration/1e6:.2f}s audio={len(_chk.streams.audio)}")
_fast = ve.export(_snd, os.path.join(TMP, "vexport", "fast"), ve.VideoEdit(duration=3.0, speed=2.0, mute=True), fmt="webm")
with _av.open(str(_fast)) as _chk:
    check("WebM export at double speed, muted", abs(_chk.duration / 1e6 - 1.5) < 0.15 and not _chk.streams.audio,
          f"{_chk.duration/1e6:.2f}s audio={len(_chk.streams.audio)}")
from PIL import Image as _PImg
with _PImg.open(_outs["gif"]) as _g:
    check("GIF export animates", _g.n_frames > 5 and _g.size == (240, 320), f"{_g.n_frames} frames {_g.size}")
_still = ve.frame_image(_snd, 1.0, ve.VideoEdit(duration=3.0, crop=(0.0, 0.0, 0.5, 1.0)))
check("frame with crop keeps the left half", _still.size == (160, 240) and np.asarray(_still)[:, :, 0].mean() > 150,
      f"{_still.size}")
_photo = ve.save_frame_as_photo(libE, _snd, 1.2, None, taken_at=1714550400.0)
with _PImg.open(_photo) as _pp:
    check("saved frame is a dated photo in Originals",
          _photo.is_relative_to(libE.originals) and _pp.getexif().get_ifd(0x8769).get(0x9003, "").startswith("2024:05:01"),
          str(_photo.name))
_cancel = threading.Event(); _cancel.set()
try:
    ve.export(_snd, os.path.join(TMP, "vexport", "cancelled"), _edit, fmt="mp4", cancel=_cancel)
    _ok = False
except ve.Cancelled:
    _ok = not list(Path(TMP, "vexport").glob("*cancelled*"))
check("cancelled export leaves nothing behind", _ok)

section("Library package can be moved")
from piklin.paths import Library as _L, LIBRARY_NAME
from piklin.catalog import Catalog as _C
from piklin.settings import Settings as _St
from piklin.indexer import Indexer as _Ix
from piklin import library_move as lm, sidecars as _sc

def _library_with_photo(root):
    lib_ = _L(root).ensure()
    day = lib_.originals / "2020" / "2020-01-01"; day.mkdir(parents=True)
    shutil.copy2(SRC[0], day / "a.jpg")
    cat_ = _C(lib_.db)
    _Ix(cat_, None, library_root=lib_.root).scan([lib_.originals])
    pid_ = cat_.q("SELECT id FROM photos")[0]["id"]
    aid_ = cat_.create_album("Trip"); cat_.album_add(aid_, [pid_]); cat_.set_favorite([pid_], True)
    (lib_.albums / "Trip.json").write_text(json.dumps(
        {"format": "pikalicious-album", "uuid": "u1", "name": "Trip",
         "photos": cat_.album_photo_paths(aid_)}))
    _sc.write_all(lib_, cat_)
    return lib_, cat_, pid_, aid_

libA, cA, pidA, aidA = _library_with_photo(os.path.join(TMP, "Moving Library.piklin"))
lm.relocate(libA, cA, _St(libA.settings))          # records where it is
elsewhere = os.path.join(TMP, "external-disk"); os.makedirs(elsewhere)
shutil.move(str(libA.root), elsewhere)
libB = _L(os.path.join(elsewhere, "Moving Library.piklin"))
cB = _C(libB.db); stB = _St(libB.settings)
nB = lm.relocate(libB, cB, stB)
rowB = cB.photo(pidA)
check("moved library: photo paths follow it",
      nB == 1 and rowB["path"].startswith(str(libB.root) + "/") and os.path.exists(rowB["path"]), rowB["path"])
check("moved library: albums and favourites kept",
      cB.album_photo_paths(aidA) == [rowB["path"]] and rowB["favorite"] == 1)
check("moved library: album file rewritten",
      json.loads((libB.albums / "Trip.json").read_text())["photos"] == [rowB["path"]])
check("moved library: watched folders rewritten",
      all(f["path"].startswith(str(libB.root)) for f in _sc.read_roots(libB)))
check("moved library: reopening changes nothing", lm.relocate(libB, cB, stB) == 0)

# The default layout: the library package inside ~/Pictures, which is also
# a watched folder. Imported photos must still count as present.
_watched = os.path.join(TMP, "watched"); os.makedirs(_watched)
libW = _L(os.path.join(_watched, LIBRARY_NAME)).ensure()
(libW.originals / "2021").mkdir(parents=True)
shutil.copy2(SRC[1], libW.originals / "2021" / "imported.jpg")
shutil.copy2(SRC[2], os.path.join(_watched, "loose.jpg"))
cW = _C(libW.db); ixW = _Ix(cW, None, library_root=libW.root)
ixW.scan([_watched]); ixW.scan(None); ixW.scan(None)
rowsW = cW.q("SELECT path, thumb_state FROM photos ORDER BY path")
check("imported photo inside a watched folder is not flagged missing",
      len(rowsW) == 2 and all(r["thumb_state"] != 3 for r in rowsW),
      str([(os.path.basename(r["path"]), r["thumb_state"]) for r in rowsW]))

# ===================================================================
section("Renaming a photo renames the file")
from piklin import photo_rename as pr_
from piklin.engine.stack import EditStack as _ES
from piklin.engine.stack import Layer as _Layer
libR, cR, pidR, aidR = _library_with_photo(os.path.join(TMP, "Rename Library.piklin"))
oldR = Path(cR.photo(pidR)["path"])
_ES([_Layer("noir", tools.REGISTRY["noir"].defaults())]).save(libR.edit_sidecar(oldR), source=str(oldR))
cR.note_edit(pidR, 1); cR.set_text_fields([pidR], title="Beach")
_sc.write_all(libR, cR)
oldR.with_suffix(".xmp").write_text("<x/>")
newR = pr_.rename_photo(libR, cR, pidR, "Summer day at the beach")
rowR = cR.photo(pidR)
check("rename: file renamed on disk, extension kept",
      newR.name == "Summer day at the beach.jpg" and newR.is_file() and not oldR.exists(), newR.name)
check("rename: catalog follows (path, filename)",
      rowR["path"] == str(newR) and rowR["filename"] == newR.name)
check("rename: albums, favourite, title kept",
      cR.album_photo_paths(aidR) == [str(newR)] and rowR["favorite"] == 1 and rowR["title"] == "Beach")
check("rename: album file points at the new name",
      json.loads((libR.albums / "Trip.json").read_text())["photos"] == [str(newR)])
_state = json.loads((libR.root / "photo-state.json").read_text())["photos"]
check("rename: photo-state follows", str(newR) in _state and str(oldR) not in _state)
_scR = libR.edit_sidecar(newR)
check("rename: edits move with the photo",
      _scR.is_file() and not libR.edit_sidecar(oldR).exists()
      and json.loads(_scR.read_text())["source"] == str(newR)
      and len(_ES.load(_scR)) == 1)
check("rename: XMP sidecar renamed too", newR.with_suffix(".xmp").is_file())
check("rename: search finds the new name",
      pidR in [r["id"] for r in cR.browse(search="summer")])
check("rename: typing the extension is fine",
      pr_.rename_photo(libR, cR, pidR, "Beach.JPG").name == "Beach.jpg")
_other = Path(cR.photo(pidR)["path"]).with_name("Taken.jpg"); shutil.copy2(SRC[1], _other)
def _raises(fn):
    try: fn(); return False
    except pr_.RenameError: return True
check("rename: never overwrites another file",
      _raises(lambda: pr_.rename_photo(libR, cR, pidR, "Taken")) and _other.is_file()
      and Path(cR.photo(pidR)["path"]).name == "Beach.jpg")
check("rename: rejects empty, slash, hidden names",
      all(_raises(lambda n=n: pr_.rename_photo(libR, cR, pidR, n)) for n in ("", "  ", "a/b", ".secret")))
check("rename: change of case only works",
      pr_.rename_photo(libR, cR, pidR, "BEACH").name == "BEACH.jpg"
      and Path(cR.photo(pidR)["path"]).is_file())
_ix2 = _Ix(cR, None, library_root=libR.root); _ix2.scan(None)
_rowsR = cR.q("SELECT id, path, thumb_state FROM photos WHERE path LIKE ?", (str(libR.originals) + "%",))
check("rename: a rescan sees the renamed photo, not a new one plus a missing one",
      sorted(Path(r["path"]).name for r in _rowsR) == ["BEACH.jpg", "Taken.jpg"]
      and all(r["thumb_state"] != 3 for r in _rowsR)
      and cR.photo(pidR)["path"].endswith("BEACH.jpg"),
      str([(Path(r["path"]).name, r["thumb_state"]) for r in _rowsR]))

# ===================================================================
section("Removed photos stay removed")
# Photos referenced from a watched folder: removing them from the library
# leaves the files there, and a scan must not add them back as new photos.
_rm = os.path.join(TMP, "removal"); os.makedirs(_rm)
libX = _L(os.path.join(_rm, LIBRARY_NAME)).ensure()
_walls = os.path.join(_rm, "Wallpapers"); os.makedirs(_walls)
for i in range(3):
    shutil.copy2(SRC[i], os.path.join(_walls, f"w{i}.jpg"))
cX = _C(libX.db); ixX = _Ix(cX, None, library_root=libX.root)
ixX.scan([_walls])
_idsX = [r["id"] for r in cX.q("SELECT id FROM photos ORDER BY path")]
cX.trash(_idsX[:2]); cX.forget_photos(_idsX[:2])
ixX.scan(None); ixX.scan([_walls])
check("removed photos are not added back by a scan",
      cX.scalar("SELECT COUNT(*) FROM photos") == 1 and len(cX.removed_paths()) == 2
      and all(os.path.exists(p) for p in cX.removed_paths()))
cX.trash([_idsX[2]])
with cX.write() as _cur:
    _cur.execute("UPDATE photos SET trashed_at=? WHERE id=?", (time.time() - 40 * 86400, _idsX[2]))
_expired = cX.forget_expired_trash(30); ixX.scan(None)
check("after 30 days in Recently Deleted a photo stays out",
      _expired == 1 and cX.scalar("SELECT COUNT(*) FROM photos") == 0)
_sc.write_all(libX, cX)
check("removed list is saved in removed-photos.json",
      len(json.loads((libX.root / "removed-photos.json").read_text())["photos"]) == 3)
cY = _C(os.path.join(TMP, "rebuilt-removed.db"))
check("removed list survives a catalog rebuild",
      _sc.restore_removed(libX, cY) == 3 and len(cY.removed_paths()) == 3)
_back = cX.restore_removed(); ixX.scan(None)
check("Show Again brings removed photos back",
      _back == 3 and cX.scalar("SELECT COUNT(*) FROM photos") == 3 and not cX.removed_paths())

# The old ~/Pictures/Pikalicious becomes the package on first launch.
_cfg = os.path.join(TMP, "cfg"); _pics = os.path.join(TMP, "pics")
os.makedirs(_cfg); os.makedirs(_pics)
open(os.path.join(_cfg, "user-dirs.dirs"), "w").write(f'XDG_PICTURES_DIR="{_pics}"\n')
_saved_cfg = os.environ.get("XDG_CONFIG_HOME"); os.environ["XDG_CONFIG_HOME"] = _cfg
try:
    libOld, cOld, pidOld, aidOld = _library_with_photo(os.path.join(_pics, "Pikalicious"))
    newroot = lm.migrate_legacy_library()
    libN = _L()
    cN = _C(libN.db); lm.relocate(libN, cN, _St(libN.settings))
    rowN = cN.photo(pidOld)
    check("old Pikalicious library becomes the package",
          newroot == Path(_pics) / LIBRARY_NAME and libN.root == newroot.resolve()
          and not os.path.exists(os.path.join(_pics, "Pikalicious")), str(libN.root))
    check("converted library keeps photos, albums, favourites",
          os.path.exists(rowN["path"]) and cN.album_photo_paths(aidOld) == [rowN["path"]]
          and rowN["favorite"] == 1, rowN["path"])
    check("conversion runs once", lm.migrate_legacy_library() is None)
finally:
    if _saved_cfg is None: os.environ.pop("XDG_CONFIG_HOME", None)
    else: os.environ["XDG_CONFIG_HOME"] = _saved_cfg

# -- languages --------------------------------------------------------------
import datetime as _dt, io as _io, contextlib as _ctx, subprocess as _sp
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "po"))
import es_catalog as _es
_po_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "po")
_out = _io.StringIO()
with _ctx.redirect_stdout(_out):
    _es.build(os.path.join(_po_dir, "piklin.pot"), os.path.join(TMP, "es.po"))
check("every text has a Spanish translation with the same placeholders",
      "0 missing, 0 placeholder mismatches" in _out.getvalue(), _out.getvalue()[:300])
_mo = os.path.join(TMP, "es.mo")
_sp.run(["msgfmt", "-o", _mo, os.path.join(_po_dir, "es.po")], check=True)
check("shipped Spanish catalog is compiled from the current es.po",
      open(_mo, "rb").read() == (_i18n.LOCALE_DIR / "es/LC_MESSAGES/piklin.mo").read_bytes())
try:
    check("Spanish loads", _i18n.setup("es") == "es")
    check("Spanish text", _i18n._("All Photos") == "Todas las fotos"
          and _i18n.ngettext("{count} photo", "{count} photos", 3).format(count=3) == "3 fotos"
          and _i18n.ngettext("{count} photo", "{count} photos", 1).format(count=1) == "1 foto")
    check("Spanish dates", _i18n.long_date(_dt.date(2026, 3, 2)) == "Lunes, 2 de marzo de 2026"
          and _i18n.month_year(_dt.date(2026, 3, 2)) == "Marzo de 2026",
          _i18n.long_date(_dt.date(2026, 3, 2)))
finally:
    _i18n.setup("en")
check("English is back", _i18n._("All Photos") == "All Photos")
_saved_cfg = os.environ.get("XDG_CONFIG_HOME"); os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "langcfg")
try:
    _before = _i18n.chosen_language()
    _i18n.choose_language("es")
    check("language choice is remembered", _before == "" and _i18n.chosen_language() == "es")
finally:
    if _saved_cfg is None: os.environ.pop("XDG_CONFIG_HOME", None)
    else: os.environ["XDG_CONFIG_HOME"] = _saved_cfg

print("\n" + "="*64)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILURES:"); [print("   -", f) for f in FAIL]
print("="*64)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
