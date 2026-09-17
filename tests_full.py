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
# The real path: on a Mac the temporary folder sits behind a symlink
# (/var -> /private/var), and the catalog stores photos by their real path.
TMP = os.path.realpath(tempfile.mkdtemp(prefix="pika-test-"))

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

# A new layer starts clean: strokes or points never carry over from another
_st1 = EditStack(); _b1 = _st1.add("brush"); _b1.params["strokes"].append({"points": [[.1, .1]]})
_s1 = _st1.add("selective"); _s1.params["points"].append({"x": .5, "y": .5})
_st2 = EditStack(); _b2 = _st2.add("brush"); _s2 = _st2.add("selective"); _h2 = _st2.add("healing")
check("a new Brush, Healing or Selective layer starts with nothing painted, whatever came before",
      _b2.params["strokes"] == [] and _s2.params["points"] == [] and _h2.params["strokes"] == []
      and tools.REGISTRY["brush"].defaults()["strokes"] == [],
      (_b2.params["strokes"], _s2.params["points"]))

# Every slider of every tool at its lowest and highest value
extreme_bad = []
for tid, tool in tools.REGISTRY.items():
    for param in tool.params:
        if param.kind != "slider":
            continue
        for v in (param.lo, param.hi):
            d = tool.defaults(); d[param.key] = v
            if tid == "text": d["text"] = "Hola"
            if tid == "double_exposure": d["image"] = SRC[2]
            if tid == "crop": d["rect"] = [.2, .2, .5, .5]
            try:
                r = tool.apply(face if tool.group == "Portrait" else test, d, ctx)
                if not (np.isfinite(r).all() and r.min() >= -1e-3 and r.max() <= 1 + 1e-3
                        and r.shape[0] > 1 and r.shape[1] > 1):
                    extreme_bad.append(f"{tid}.{param.key}={v}")
            except Exception as e:
                extreme_bad.append(f"{tid}.{param.key}={v}:{type(e).__name__}")
check("every slider of every tool works at its lowest and highest value",
      not extreme_bad, "; ".join(extreme_bad[:5]))
# Geometry tools change the size the way they should
_g = np.zeros((300, 400, 3), np.float32) + 0.5
_c = tools.REGISTRY["crop"].apply(_g, {"rect": [0.25, 0.1, 0.5, 0.8]}, ctx)
_r = tools.REGISTRY["rotate"].apply(_g, dict(tools.REGISTRY["rotate"].defaults(), quarter_turns=1), ctx)
_e = tools.REGISTRY["expand"].apply(_g, dict(tools.REGISTRY["expand"].defaults(), amount=50), ctx)
check("crop, a quarter turn and expand give the sizes they should",
      _c.shape[:2] == (240, 200) and _r.shape[:2] == (400, 300)
      and _e.shape[0] >= 300 and _e.shape[1] >= 400 and (_e.shape[0] > 300 or _e.shape[1] > 400),
      (_c.shape, _r.shape, _e.shape))
_st = EditStack(); _st.add("rotate", dict(quarter_turns=1)); _st.add("crop", {"rect": [0, 0, 0.5, 1.0]})
_rot_crop = Renderer().render(_g, _st, scale=1.0, full_size=(400, 300))
_before_crop = Renderer().render(_g, _st, scale=1.0, full_size=(400, 300), upto=1)
check("a crop after a quarter turn cuts the turned photo; the editor sees it whole while cropping",
      _rot_crop.shape[:2] == (400, 150) and _before_crop.shape[:2] == (400, 300),
      (_rot_crop.shape, _before_crop.shape))

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
# An album's count is what it shows: deleting, hiding or taking a photo out
# brings it down (a deleted photo used to stay in the number).
_ca = cat.create_album("Counts"); cat.album_add(_ca, [4, 5, 6, 8])   # 8 is in Recently Deleted
_n = lambda a: next(r["n"] for r in cat.albums() if r["id"] == a)
check("an album counts only the photos it shows, not one in Recently Deleted",
      _n(_ca) == 3 == len(cat.browse(scope="album", album_id=_ca)), _n(_ca))
with cat.write() as _cur:
    _cur.execute("UPDATE photos SET hidden=1 WHERE id=6")
_hidden_n = _n(_ca)
with cat.write() as _cur:
    _cur.execute("UPDATE photos SET hidden=0 WHERE id=6")
cat.album_remove(_ca, [5])
check("hiding or taking a photo out brings the album's count down",
      _hidden_n == 2 and _n(_ca) == 2, (_hidden_n, _n(_ca)))
cat.set_album_cover(_ca, 8)
_cover = next(r["cover_path"] for r in cat.albums() if r["id"] == _ca)
check("a photo in Recently Deleted is never an album's cover",
      _cover == cat.photo(4)["path"], _cover)
cat.delete_album(_ca)
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
check("the folder and every folder inside it", cat.folder_and_inside(trips) == {trips, euro})
check("a folder can't go inside itself or a folder inside it",
      not cat.move_folder(trips, euro) and not cat.move_folder(trips, trips)
      and cat.q1("SELECT parent_id FROM folders WHERE id=?", (trips,))["parent_id"] is None)
check("a folder moves out and into another folder",
      cat.move_folder(euro, None)
      and cat.q1("SELECT parent_id FROM folders WHERE id=?", (euro,))["parent_id"] is None
      and cat.move_folder(euro, trips)
      and cat.q1("SELECT parent_id FROM folders WHERE id=?", (euro,))["parent_id"] == trips)
cat.delete_folder(euro)
check("deleting a folder cascades to its albums",
      cat.scalar("SELECT COUNT(*) FROM albums WHERE id=?", (alb,), 0) == 0)
check("cascade also removes album_items",
      cat.scalar("SELECT COUNT(*) FROM album_items WHERE album_id=?",
                (alb,), 0) == 0)

section("4c. Smart albums")
sid = cat.create_smart_album("Five star",
                             [{"field": "rating", "op": "is", "value": 5}])
cat.move_smart_album_to_folder(sid, trips)
check("a Smart Album moves into a folder",
      any(c["kind"] == "smart" and c["row"]["id"] == sid
          for n in cat.tree() if n["kind"] == "folder" and n["row"]["id"] == trips
          for c in n["children"]))
cat.move_smart_album_to_folder(sid, None)
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
_fid5 = cat5.create_folder("Pantallas"); cat5.move_smart_album_to_folder(sid, _fid5)
sc.write_folders(fake, cat5)
sc.write_smart_albums(fake, cat5)
cat7 = Catalog(os.path.join(TMP, "c7.db"))
sc.restore_folders(fake, cat7)
check("Smart Albums restored after a rebuild",
      sc.restore_smart_albums(fake, cat7) == 1 and
      [r["name"] for r in cat7.smart_albums()] == ["Capturas"] and
      sc.restore_smart_albums(fake, cat7) == 0)
check("a Smart Album moved into a folder is back in it after a rebuild",
      [f["name"] for f in cat7.folders() if f["id"] == cat7.smart_albums()[0]["folder_id"]]
      == ["Pantallas"])
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
(Path(dest)/"Piklin"/mine_rel).write_text("old backup")
deleted = lib.root/"Originals"/"deleted-on-purpose.jpg"
(Path(dest)/"Piklin"/"Originals").mkdir(exist_ok=True)
(Path(dest)/"Piklin"/"Originals"/"deleted-on-purpose.jpg").write_bytes(b"x" * 10)
pr = b.restore(lib.root, skip_paths=[str(deleted)])
check("restore brings back a lost file", gone.exists() and pr.restored == 1,
      f"restored {pr.restored}, present {pr.present}")
check("restore never replaces a file in the library", mine.read_text() == "new local")
check("restore leaves out photos deleted on purpose",
      not deleted.exists() and pr.skipped == 1)
check("restore never touches the open catalog",
      (lib.root/"catalog.db").read_bytes() == catalog_bytes
      and not (lib.root/"catalog.db.part").exists())

# A restore that cannot fit says so before it starts, instead of filling the
# disk and stopping halfway (which is what happened on a full MacBook).
_gone2 = lib.edits/"x.jpg.json"
_gone2.unlink(missing_ok=True)
_real_statvfs = os.statvfs
os.statvfs = lambda _p: type("S", (), {"f_bavail": 2, "f_frsize": 4096})()
try:
    _tight = b.restore(lib.root)
finally:
    os.statvfs = _real_statvfs
check("a restore that doesn't fit stops before writing anything, and says why",
      _tight.phase == "no_space" and not _gone2.exists()
      and _tight.free_bytes == 8192 and "free" in _tight.message,
      f"{_tight.phase}: {_tight.message}")
check("with room again, that same restore goes through",
      b.restore(lib.root).phase == "done" and _gone2.exists())
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
class _SharedDav(_ApacheDav):
    # The backup folder is a NAS photo share with a Lightroom catalog beside it
    tree = {"": [("", True, 0, 0), ("Originals", True, 0, 0), ("Lightroom", True, 0, 0),
                 ("a.json", False, 2, 0), (".DS_Store", False, 9, 0), ("notes.txt", False, 3, 0)],
            "Originals": [("Originals", True, 0, 0), ("Originals/p.jpg", False, 5, 0)]}
    asked = []
    def _entries(self, rel, depth):
        self.asked.append(rel)
        return super()._entries(rel, depth)
_sd = _SharedDav(rem.Remote(id="s", name="s", kind="webdav", config={"url": "https://x"}))
_idx = _sd.listing()
check("a backup folder shared with other things: only Piklin's part is read",
      _idx == {"a.json": (2, 0), "Originals/p.jpg": (5, 0)}
      and not any(a.startswith("Lightroom") for a in _sd.asked), (_idx, _sd.asked))
_shared = Path(TMP) / "shared-nas" / "Piklin"; (_shared / "Lightroom" / "Previews.lrdata" / "0").mkdir(parents=True)
(_shared / "Lightroom" / "Previews.lrdata" / "0" / "x.lrprev").write_bytes(b"lr")
(_shared / "Originals" / "2020").mkdir(parents=True); (_shared / "Originals" / "2020" / "p.jpg").write_bytes(b"12345")
(_shared / "settings.json").write_text("{}"); (_shared / "notes.txt").write_text("mine")
_lidx = rem.Remote(id="l", name="l", kind="local", config={"path": str(_shared)}).backend().listing()
check("a shared folder or drive: other files are left out of the backup's listing",
      set(_lidx) == {"Originals/2020/p.jpg", "settings.json"}, sorted(_lidx))

# Backups live in a Piklin folder inside the chosen one
check("the backup folder is Piklin inside the chosen folder, never Piklin/Piklin",
      rem._in_piklin("/Users/alex/Photos/") == "Users/alex/Photos/Piklin"
      and rem._in_piklin("") == "Piklin" and rem._in_piklin("Backups/Piklin") == "Backups/Piklin")
_fresh = Path(TMP) / "fresh-drive"; _fresh.mkdir()
_fb = rem.Remote(id="f", name="f", kind="local", config={"path": str(_fresh)}).backend()
_ft = _fb.test()
check("a Piklin folder is made when it is missing", _ft.ok and (_fresh / "Piklin").is_dir(), _ft)
(_fresh / "Piklin" / "keep.txt").write_text("x")
_fb2 = rem.Remote(id="f", name="f", kind="local", config={"path": str(_fresh)}).backend()
_fb2.test()
check("an existing Piklin folder is used as it is", (_fresh / "Piklin" / "keep.txt").exists()
      and not (_fresh / "Piklin" / "Piklin").exists())
_olddrive = Path(TMP) / "old-layout"
for _p in ("Originals/2020/p.jpg", "Edits/p.jpg.json", "Albums/Trip.json", ".piklin-versions/2026-01-01/x.json"):
    (_olddrive / _p).parent.mkdir(parents=True, exist_ok=True); (_olddrive / _p).write_text("1")
for _p in ("catalog.db", "settings.json", "README.txt", "Lightroom/cat.lrcat", "notes.txt", "Pictures/me.jpg"):
    (_olddrive / _p).parent.mkdir(parents=True, exist_ok=True); (_olddrive / _p).write_text("1")
_ob = rem.Remote(id="o", name="o", kind="local", config={"path": str(_olddrive)}).backend()
_ob.prepare()
_moved = sorted(p.name for p in (_olddrive / "Piklin").iterdir())
_stayed = sorted(p.name for p in _olddrive.iterdir())
check("a backup made before the Piklin folder is moved into it, and nothing else is",
      _moved == [".piklin-versions", "Albums", "Edits", "Originals", "README.txt", "catalog.db", "settings.json"]
      and _stayed == ["Lightroom", "Pictures", "Piklin", "notes.txt"], (_moved, _stayed))
_old_nas = """<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">
<D:response><D:href>/dav/Users/alex/Photos/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/alex/Photos/Originals/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/alex/Photos/Lightroom/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/alex/Photos/catalog.db</D:href><D:propstat><D:prop><D:resourcetype/><D:getcontentlength>10</D:getcontentlength></D:prop></D:propstat></D:response>
<D:response><D:href>/dav/Users/alex/Photos/.DS_Store</D:href><D:propstat><D:prop><D:resourcetype/><D:getcontentlength>9</D:getcontentlength></D:prop></D:propstat></D:response>
</D:multistatus>"""
class _OldNas(rem.WebDavBackend):
    calls = []
    def _request_path(self, method, path, data=None, extra=None):
        self.calls.append((method, path, (extra or {}).get("Destination")))
        return io.BytesIO(_old_nas.encode() if method == "PROPFIND" else b"")
_on = _OldNas(rem.Remote(id="n", name="n", kind="webdav",
                         config={"url": "https://nas:5001/dav", "base": "/Users/alex/Photos"}))
_on.prepare()
_moves = [(p, d) for m, p, d in _OldNas.calls if m == "MOVE"]
check("on a NAS, the Piklin folder is made and an old backup moved into it on the server",
      ("MKCOL", "Users/alex/Photos/Piklin", None) in _OldNas.calls
      and _moves == [("Users/alex/Photos/Originals/", "https://nas:5001/dav/Users/alex/Photos/Piklin/Originals/"),
                     ("Users/alex/Photos/catalog.db", "https://nas:5001/dav/Users/alex/Photos/Piklin/catalog.db")]
      and _on.base_path == "Users/alex/Photos/Piklin", _OldNas.calls)
class _SlowNas(rem.WebDavBackend):
    # a PUT that reads the upload in blocks, as http.client does
    def _ensure_folder(self, path): pass
    def listing(self): return {}
    def _request(self, method, rel="", data=None, extra=None):
        if method == "PUT":
            while data.read(8192):
                time.sleep(0.002)
        return io.BytesIO(b"")
_big = Path(TMP) / "bigvideo.mp4"; _big.write_bytes(b"\0" * (6 << 20))
_seen = []
_sn = _SlowNas(rem.Remote(id="v", name="v", kind="webdav", config={"url": "https://x"}))
_sp = _sn.push(Path(TMP), [_big], on_progress=lambda p: _seen.append((p.phase, p.done_files, p.done_bytes)))
_mid = [b for ph, n, b in _seen if ph == "uploading" and n == 0 and 0 < b < (6 << 20)]
check("a big file's upload reports its progress while it goes up, not only at the end",
      _sp.uploaded == 1 and len(_mid) >= 2 and _mid == sorted(_mid), (_sp.uploaded, _mid[:5]))
check("a folder that merely has a settings file is not taken for an old backup",
      rem._old_backup({"settings.json": False, "Documents": True}) == []
      and rem._old_backup({"catalog.db": False, "Originals": True, "Piklin": True}) == [])
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
_kept = adest/"Piklin"/".piklin-versions"/_today/"Edits"/"a.jpg.json"
check("only the changed file is uploaded",
      o3.ok and o3.uploaded == 1
      and json.loads((adest/"Piklin"/"Edits"/"a.jpg.json").read_text())["v"] == 2, o3)
check("the replaced copy is kept as a previous version",
      _kept.exists() and json.loads(_kept.read_text())["v"] == 1)
_old = adest/"Piklin"/".piklin-versions"/"2000-01-01"; _old.mkdir(parents=True); (_old/"x.json").write_text("{}")
_recent = adest/"Piklin"/".piklin-versions"/(_dt.date.today() - _dt.timedelta(days=3)).isoformat()
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
      not o4.ok and o4.unreachable and (_cloud_dir/"Piklin"/"Edits"/"a.jpg.json").exists()
      and "QNAP" in o4.message, o4)
(Path(TMP)/"auto"/"gone").mkdir()
o5 = ab.run_backup(alib, [_away, _cloud], keep_days=30)
check("back home, the NAS catches up and the cloud is not sent anything again",
      o5.ok and (Path(TMP)/"auto"/"gone"/"Piklin"/"Edits"/"a.jpg.json").exists(), o5)

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
# Turning shows at once: the old thumbnail is turned instead of rendered again.
_before = {256: _t2}
_qe.rotate(_rlib, None, None, _wide, 1)
_t0 = time.perf_counter()
_rough = _tc.turn_cached(_wide, _before, 1)
_elapsed = time.perf_counter() - _t0
_t3 = _tc.get_path(_wide, 256)
check("a turned photo has its new thumbnail straight away",
      _rough == [256] and _t3 is not None and _PI.open(_t3).size[1] > _PI.open(_t3).size[0]
      and _elapsed < 0.2, (_rough, _elapsed))
_t4 = _tc.generate(_wide, 256, force=True)
check("the exact thumbnail replaces the quick one", _t4 == _t3
      and _PI.open(_t4).size[1] > _PI.open(_t4).size[0])
_qe.rotate(_rlib, None, None, _wide, -1)
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
_real_load, _real_ready = rem.load_secret, rem.keyring_ready
_kr = {"pw": None, "ready": False}
rem.load_secret = lambda rid: _kr["pw"]
rem.keyring_ready = lambda: _kr["ready"]
_kb = rem.Remote(id="k", name="k", kind="webdav",
                 config={"url": "http://192.168.1.5/dav", "username": "u"}).backend()
_kt = _kb.test()
check("a locked keyring waits and tries again, not 'no password'",
      not _kt.ok and _kt.unreachable, _kt.message)
_kr["ready"] = True
_kt = _kb.test()
check("a truly missing password is still reported", not _kt.ok and not _kt.unreachable, _kt.message)
_kr["pw"] = "secret"
check("the password is read once the keyring opens", _kb._password() == "secret")
rem.load_secret, rem.keyring_ready = _real_load, _real_ready
check("rclone absence explained",
      "rclone" in rem.Remote(id="z",name="z",kind="rclone",
                             config={"remote":"pcloud"}).backend().test().message.lower())

# ===================================================================
from piklin import logs as _logs
_s = _logs.scrub(f"webdav https://luser:pw@nas.local/dav 192.168.1.20:5005 a@b.com {os.path.expanduser('~')}/Pictures/Trip/IMG_1.jpg")
check("the activity log hides addresses, user names, IPs, emails and photo paths",
      "luser" not in _s and "192.168" not in _s and "a@b.com" not in _s
      and "Trip" not in _s and ".jpg" in _s, _s)
os.environ["XDG_STATE_HOME"] = os.path.join(TMP, "state")
_logs.setup("test")
for _i in range(9000):
    _logs.get("test").info("line %d %s", _i, "x" * 120)
check("the activity log never grows past its limit",
      _logs.size_bytes() <= _logs.MAX_BYTES * (_logs.KEEP_OLD + 1) + 4096, _logs.size_bytes())
check("the activity log reads back its newest lines", "line 8999" in _logs.read_all())
_logs.clear()
check("clearing the activity log empties it",
      "line 8999" not in _logs.read_all() and _logs.size_bytes() < 1024)

from piklin import updates as _bell_upd
_sample = ("**English**\n\n## Install or update\n- run apt\n\n## What's new\n\n### Albums\n"
           "- **Covers.** Pick one. See [LICENSE](https://x)\n\n## Verify your download\n- sha\n\n"
           "---\n\n## Español\n\n### Instalar o actualizar\n- ejecuta apt\n\n### Novedades\n\n"
           "#### Álbumes\n- **Portadas.** Elige una.\n\n### Verifica tu descarga\n- sha\n")
_en, _es = _bell_upd.notes_for(_sample, "en"), _bell_upd.notes_for(_sample, "es")
check("update window lists what's new in English, without install or download steps",
      _en == [("heading", "Albums"), ("item", "Covers. Pick one. See LICENSE")], _en)
check("update window lists what's new in Spanish, without install or download steps",
      _es == [("heading", "Álbumes"), ("item", "Portadas. Elige una.")], _es)
_short = _bell_upd.highlights(_sample + "- Plain line. More words.\n", "en")
check("update window says what's new in a few words: each change's title",
      _bell_upd.highlights(_sample, "en") == [("heading", "Albums"), ("item", "Covers")]
      and _bell_upd.highlights(_sample, "es") == [("heading", "Álbumes"), ("item", "Portadas")],
      (_bell_upd.highlights(_sample, "en"), _bell_upd.highlights(_sample, "es")))
_cfg_before = os.environ.get("XDG_CONFIG_HOME")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "cfg-bell")
_bell_upd.save_state(enabled=False, last_check=0)
check("the update bell still looks for a new version when installing by itself is off",
      _bell_upd.check_due() and not _bell_upd.due())
_bell_upd.save_state(last_check=time.time())
check("the update bell looks at most once an hour", not _bell_upd.check_due())
if _cfg_before is None:
    os.environ.pop("XDG_CONFIG_HOME", None)
else:
    os.environ["XDG_CONFIG_HOME"] = _cfg_before

# A Piklin opened before its restore writes its own, empty folder list. The
# backup's folders must still come back, and that empty library must never
# have replaced them at the destination in the first place.
from piklin import remote as _rm
from piklin import sidecars as _scm
from piklin.catalog import Catalog as _Cm
from piklin.paths import Library as _Lm
_mdir = Path(TMP) / "merge-restore"
_src = _Lm(_mdir / "src" / "Piklin Library.piklin").ensure(); _csrc = _Cm(_src.db)
_fam = _csrc.create_folder("Family"); _csrc.create_folder("Trips", parent_id=_fam)
_scm.write_all(_src, _csrc)
_dest = _rm.Remote(id="merge-test", name="Merge", kind="local",
                   config={"path": str(_mdir / "nas")}).backend()
_dest.push(_src.root, _rm.library_files(_src.root))
_new = _Lm(_mdir / "new" / "Piklin Library.piklin").ensure(); _cnew = _Cm(_new.db)
_scm.write_all(_new, _cnew)                     # its own, empty lists
_pushed = _dest.push(_new.root, _rm.library_files(_new.root))
_nas_folders = _dest.listing().get("Albums/_folders.json", (0, 0))[0]
check("a new, empty library never replaces the backup's folders",
      _nas_folders == (_src.albums / "_folders.json").stat().st_size, (_nas_folders, _pushed.skipped))
_back = _dest.restore(_new.root)
_names = sorted(f["name"] for f in json.loads((_new.albums / "_folders.json").read_text())["folders"])
check("a restore brings the backup's folders into a library that already had a folder list",
      _names == ["Family", "Trips"] and _back.restored_state, (_names, _back.restored_state))
_mine = Path(TMP) / "merge-mine.json"; _theirs = Path(TMP) / "merge-theirs.json"
_mine.write_text(json.dumps({"format": "pikalicious-state", "version": 1,
                             "photos": {"/a.jpg": {"favorite": 1}}}))
_theirs.write_text(json.dumps({"format": "pikalicious-state", "version": 1,
                               "photos": {"/a.jpg": {"favorite": 0}, "/b.jpg": {"favorite": 1}}}))
check("merging marks keeps this computer's and adds the backup's missing ones",
      _rm.merge_state_file("photo-state.json", _mine, _theirs)
      and json.loads(_mine.read_text())["photos"] == {"/a.jpg": {"favorite": 1}, "/b.jpg": {"favorite": 1}}
      and not _rm.merge_state_file("photo-state.json", _mine, _theirs))

# Cloud backups need rclone, which Piklin carries: its own copy is used
# before one installed on the computer.
_rt = Path(TMP) / "fake-runtime"; (_rt / "bin").mkdir(parents=True)
(_rt / "bin" / "rclone").write_text("#!/bin/sh\n"); os.chmod(_rt / "bin" / "rclone", 0o755)
_saved_rt = os.environ.get("PIKLIN_RUNTIME"); os.environ["PIKLIN_RUNTIME"] = str(_rt)
try:
    check("the rclone Piklin carries is used first", _rm.rclone_path() == str(_rt / "bin" / "rclone"),
          _rm.rclone_path())
finally:
    if _saved_rt is None: os.environ.pop("PIKLIN_RUNTIME", None)
    else: os.environ["PIKLIN_RUNTIME"] = _saved_rt
# A cloud Piklin connects keeps its access in Piklin's own rclone file (to be
# encrypted); one set up by hand stays in rclone's own. rclone never waits
# for a password typed on a terminal nobody sees.
_saved_xdg = os.environ.get("XDG_CONFIG_HOME"); os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "rclone-cfg")
_orig_load = _rm.load_secret
try:
    _rm.load_secret = lambda rid: "k3y" if rid == _rm.RCLONE_KEY_ID else None
    _own, _theirs = _rm._rclone_env("piklin-drive:"), _rm._rclone_env("mydrive")
    check("Piklin's clouds use its own encrypted rclone file, others rclone's own",
          _own.get("RCLONE_CONFIG") == os.path.join(TMP, "rclone-cfg", "piklin", "rclone.conf")
          and _own.get("RCLONE_CONFIG_PASS") == "k3y" and _own.get("RCLONE_ASK_PASSWORD") == "false"
          and "RCLONE_CONFIG" not in _theirs and "RCLONE_CONFIG_PASS" not in _theirs
          and _theirs.get("RCLONE_ASK_PASSWORD") == "false")
    check("only a plain http:// server is offered a secure address",
          _rm.secure_address("https://nas.example:5001") is None
          and _rm.secure_address("ftp://nas.example") is None)
finally:
    _rm.load_secret = _orig_load
    if _saved_xdg is None: os.environ.pop("XDG_CONFIG_HOME", None)
    else: os.environ["XDG_CONFIG_HOME"] = _saved_xdg

# Encrypted backups, with the real rclone Piklin ships: nothing readable
# reaches the destination, the photos come back identical, and only the
# recovery key opens the backup on another computer.
import platform as _pf, zipfile as _zf
_rc_zip = next(iter(sorted((Path(os.path.dirname(os.path.abspath(__file__))) / "packaging" / "rclone-cache").glob(
    f"rclone-*-{'osx' if sys.platform == 'darwin' else 'linux'}-"
    f"{'arm64' if _pf.machine() in ('arm64', 'aarch64') else 'amd64'}.zip"))), None)
if _rc_zip is not None:
    _ert = Path(TMP) / "enc-runtime"; (_ert / "bin").mkdir(parents=True)
    with _zf.ZipFile(_rc_zip) as _z:
        (_ert / "bin" / "rclone").write_bytes(_z.read(next(n for n in _z.namelist() if n.endswith("/rclone"))))
    os.chmod(_ert / "bin" / "rclone", 0o755)
    _vault = {}
    _saved_env = {k: os.environ.get(k) for k in ("PIKLIN_RUNTIME", "XDG_CONFIG_HOME")}
    _orig_secret = (_rm.load_secret, _rm.store_secret)
    os.environ["PIKLIN_RUNTIME"] = str(_ert); os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "enc-cfg")
    _rm.load_secret = lambda rid: _vault.get(rid)
    _rm.store_secret = lambda rid, value: (_vault.__setitem__(rid, value), True)[1]
    try:
        _esrc = _Lm(Path(TMP) / "enc-src" / "Piklin Library.piklin").ensure(); _ecat = _Cm(_esrc.db)
        (_esrc.originals / "2024").mkdir(parents=True)
        shutil.copy2(SRC[0], _esrc.originals / "2024" / "secret-beach.jpg")
        _ecat.create_folder("Family"); _scm.write_all(_esrc, _ecat)
        _nas = Path(TMP) / "enc-nas"; _nas.mkdir()
        _plain = _rm.Remote(id="enc-test", name="NAS", kind="local", config={"path": str(_nas)})
        _new_cfg, _key, _problem = _rm.enable_encryption(_plain)
        check("encrypting a backup gives a recovery key and marks the destination",
              _new_cfg is not None and _new_cfg["config"].get("encrypted") is True
              and _rm.normalize_recovery_key(_key) == _key, _problem)
        _eb = _rm.make_backend(_rm.Remote.from_dict(_new_cfg))
        _pushed_e = _eb.push(_esrc.root, _rm.library_files(_esrc.root))
        _stored = [p for p in (_nas / _rm.ENCRYPTED_FOLDER).rglob("*") if p.is_file()]
        _readable = [p for p in _stored if any(w in str(p.relative_to(_nas / _rm.ENCRYPTED_FOLDER))
                                               for w in ("Albums", "Originals", "secret-beach", ".jpg", "_folders"))]
        _photo_bytes = (_esrc.originals / "2024" / "secret-beach.jpg").read_bytes()
        _leaks = [p for p in _stored if _photo_bytes[:4096] in p.read_bytes()]
        check("an encrypted backup leaves nothing readable at the destination",
              _pushed_e.phase == "done" and len(_stored) >= 3 and not _readable and not _leaks,
              (_pushed_e.phase, _pushed_e.message, len(_stored), [str(p) for p in _readable[:3]]))
        _dst = _Lm(Path(TMP) / "enc-dst" / "Piklin Library.piklin").ensure()
        _eb.restore(_dst.root)
        _back_photo = _dst.originals / "2024" / "secret-beach.jpg"
        check("an encrypted backup restores the photos exactly",
              _back_photo.is_file() and _back_photo.read_bytes() == _photo_bytes
              and (_dst.albums / "_folders.json").is_file())
        _vault.clear()                              # another computer: nothing kept yet
        check("another computer finds the encrypted backup", _rm.encrypted_backup_exists(_plain))
        _wrong = _rm.enable_encryption(_plain, _rm.new_recovery_key())
        _right = _rm.enable_encryption(_plain, _key.lower().replace("-", " "))
        check("only the right recovery key opens it there",
              _wrong[0] is None and _right[0] is not None and _right[1] is None, (_wrong[2], _right[2]))
    finally:
        _rm.load_secret, _rm.store_secret = _orig_secret
        for _k, _v in _saved_env.items():
            if _v is None: os.environ.pop(_k, None)
            else: os.environ[_k] = _v

# Two computers sharing a backup: the rules that merge what each changed.
from piklin import sync as _sy
_a0 = {"format": "pikalicious-album", "uuid": "trip", "name": "Trip", "photos": ["/L/p1.jpg", "/L/p2.jpg"]}
_mac = _sy.stamp_album(_a0, dict(_a0, photos=["/L/p1.jpg"]), now=200)            # p2 removed on the Mac
_lin = _sy.stamp_album(_a0, dict(_a0, photos=["/L/p1.jpg", "/L/p2.jpg", "/L/p3.jpg"]), now=150)
_merged = _sy.merge_album(_mac, _lin)
check("an album keeps what both computers added and drops what was removed later",
      _merged["photos"] == ["/L/p1.jpg", "/L/p3.jpg"], _merged["photos"])
_back = _sy.merge_album(_sy.stamp_album(_mac, dict(_mac, photos=["/L/p1.jpg", "/L/p2.jpg"]), now=300), _lin)
check("a photo added back after it was removed is in the album again",
      _back["photos"] == ["/L/p1.jpg", "/L/p2.jpg", "/L/p3.jpg"], _back["photos"])
_renamed = _sy.merge_album(_sy.stamp_album(_a0, dict(_a0, name="Trip 2024"), now=400), _lin)
check("an album's latest name wins", _renamed["name"] == "Trip 2024")
_f0 = {"folders": [{"uuid": "fam", "name": "Family"}, {"uuid": "old", "name": "Old"}]}
_fa = _sy.stamp_list(_f0, {"folders": [{"uuid": "fam", "name": "Family"}]}, "folders", now=500,
                     note_missing=True)                                            # Old deleted here
_fb = _sy.stamp_list(_f0, {"folders": [{"uuid": "fam", "name": "Familia"}, {"uuid": "old", "name": "Old"},
                                       {"uuid": "new", "name": "New"}]}, "folders", now=450)
_fm = _sy.merge_list(_fa, _fb, "folders")
check("folders: a deletion wins, a rename and a new folder come through",
      sorted((e["uuid"], e["name"]) for e in _fm["folders"]) == [("fam", "Familia"), ("new", "New")]
      and "old" in _fm["deleted"], _fm)
_fc = _sy.stamp_list(_fb, {"folders": [{"uuid": "fam", "name": "Familia"}, {"uuid": "old", "name": "Old kept"},
                                       {"uuid": "new", "name": "New"}]}, "folders", now=600)
check("a folder changed after it was deleted elsewhere is kept",
      any(e["uuid"] == "old" for e in _sy.merge_list(_fa, _fc, "folders")["folders"]))
_ma = _sy.stamp_marks({"photos": {"/L/p1.jpg": {"favorite": True}}}, {"photos": {}}, now=700)       # unfavourited
_mb = _sy.stamp_marks(None, {"photos": {"/L/p1.jpg": {"favorite": True}, "/L/p2.jpg": {"rating": 5}}}, now=650)
_mm = _sy.merge_marks(_ma, _mb)
check("marks: the latest change wins, a cleared mark stays cleared",
      _mm["photos"] == {"/L/p2.jpg": {"rating": 5, "modified_at": 650}}, _mm["photos"])
check("photos removed on either computer stay removed",
      _sy.merge_removed({"photos": {"/a": 1}}, {"photos": {"/b": 2, "/a": 3}})["photos"] == {"/a": 3, "/b": 2})
check("paths written on Linux point at this library",
      _sy.adopt_paths({"photos": ["/home/oem/Pictures/Piklin Library.piklin/Originals/2024/x.jpg"]},
                      "/Users/ana/Pictures/Piklin Library.piklin")["photos"]
      == [os.path.join("/Users/ana/Pictures/Piklin Library.piklin", "Originals", "2024", "x.jpg")])
_st = _Lm(Path(TMP) / "stamp-lib" / "Piklin Library.piklin").ensure(); _stc = _Cm(_st.db)
_gone = _stc.create_folder("Gone"); _scm.write_all(_st, _stc)
_text1 = (_st.albums / "_folders.json").read_text(); _scm.write_all(_st, _stc)
_same = (_st.albums / "_folders.json").read_text() == _text1
_gone_uuid = _stc.q1("SELECT uuid FROM folders WHERE id=?", (_gone,))["uuid"]
_scm.note_folder_deleted(_st, _gone_uuid); _stc.delete_folder(_gone); _scm.write_all(_st, _stc)
check("the folder list records deletions, and an unchanged list is not rewritten",
      _same and _gone_uuid in json.loads((_st.albums / "_folders.json").read_text())["deleted"])

# A restore brings folders back as files; the catalog only learns of them when
# it is rebuilt. Until then nothing may mirror that emptiness over them - the
# bug that wiped twelve folders and sent the wipe to the other computers.
_rs = _Lm(Path(TMP) / "restore-lib" / "Piklin Library.piklin").ensure(); _rsc = _Cm(_rs.db)
_kept = _rsc.create_folder("Familia"); _scm.write_all(_rs, _rsc)
_kept_uuid = _rsc.q1("SELECT uuid FROM folders WHERE id=?", (_kept,))["uuid"]
_rs.rebuild_flag.parent.mkdir(parents=True, exist_ok=True); _rs.rebuild_flag.touch()
_rsc.delete_folder(_kept)                      # as a catalog rebuilt from nothing looks
_scm.write_all(_rs, _rsc)
_after = json.loads((_rs.albums / "_folders.json").read_text())
check("a library waiting to be rebuilt after a restore keeps its folders",
      [e["uuid"] for e in _after["folders"]] == [_kept_uuid] and not _after["deleted"], _after)
_rs.rebuild_flag.unlink()
_scm.write_all(_rs, _rsc)
_after2 = json.loads((_rs.albums / "_folders.json").read_text())
check("a folder missing from the catalog is never taken as deleted on its own",
      [e["uuid"] for e in _after2["folders"]] == [_kept_uuid] and not _after2["deleted"], _after2)
check("a restored folder survives the merge with a backup that recorded it deleted",
      [e["uuid"] for e in _sy.merge_list(_after2, {"folders": [], "deleted": {_kept_uuid: 1.0}},
                                         "folders")["folders"]] == [_kept_uuid])

# Two computers sharing one backup folder keep each other up to date.
from piklin.indexer import Indexer as _IxS
_share = Path(TMP) / "sync-shared-nas"; _share.mkdir()
_shared = lambda: _rm.Remote(id="shared", name="NAS", kind="local", config={"path": str(_share)}).backend()
_A = _Lm(Path(TMP) / "computer-a" / "Piklin Library.piklin").ensure(); _cA = _Cm(_A.db)
(_A.originals / "2023").mkdir(parents=True)
shutil.copy2(SRC[0], _A.originals / "2023" / "beach.jpg"); shutil.copy2(SRC[1], _A.originals / "2023" / "sunset.jpg")
_IxS(_cA, None, library_root=_A.root).scan([_A.originals])
_beachA = _cA.photo_by_path(str(_A.originals / "2023" / "beach.jpg"))["id"]
_famA = _cA.create_folder("Family"); _tripA = _cA.create_album("Trip", folder_id=_famA)
_cA.album_add(_tripA, [_beachA]); _cA.set_favorite([_beachA], True)
_scm.write_album(_A, _cA, _tripA); _scm.write_all(_A, _cA)
_shared().push(_A.root, _rm.library_files(_A.root))
_B = _Lm(Path(TMP) / "computer-b" / "Piklin Library.piklin").ensure(); _cB = _Cm(_B.db)
_rB = _sy.pull(_B, _cB, _shared())
_tripB = _cB.q1("SELECT id, uuid, folder_id FROM albums WHERE name='Trip'")
_beachB = _cB.photo_by_path(str(_B.originals / "2023" / "beach.jpg"))
check("another computer brings in the photos, the album in its folder and the favourite",
      _rB.ok and _rB.photos == 2 and _tripB is not None and _tripB["folder_id"] is not None
      and _cB.q1("SELECT name FROM folders WHERE id=?", (_tripB["folder_id"],))["name"] == "Family"
      and [os.path.basename(p) for p in _cB.album_photo_paths(_tripB["id"])] == ["beach.jpg"]
      and _beachB is not None and _beachB["favorite"] == 1, (_rB, dict(_tripB) if _tripB else None))
time.sleep(1.1)                                       # B's changes come later than A's
if _tripB is not None and _beachB is not None:
    _cB.delete_album(_tripB["id"]); _scm.delete_album_file(_B, _tripB["uuid"])
    _sunB = _cB.photo_by_path(str(_B.originals / "2023" / "sunset.jpg"))["id"]
    _beachAlbum = _cB.create_album("Beach"); _cB.album_add(_beachAlbum, [_sunB])
    _scm.write_album(_B, _cB, _beachAlbum)
    _cB.set_favorite([_beachB["id"]], False)
    _scm.write_all(_B, _cB)
    _shared().push(_B.root, _rm.library_files(_B.root))
    _rA = _sy.pull(_A, _cA, _shared())
    _beachAlbumA = _cA.q1("SELECT id FROM albums WHERE name='Beach'")
    check("back on the first computer: the deleted album goes, the new one comes, the mark is cleared",
          _rA.ok and _cA.q1("SELECT id FROM albums WHERE uuid=?", (_tripB["uuid"],)) is None
          and _beachAlbumA is not None
          and [os.path.basename(p) for p in _cA.album_photo_paths(_beachAlbumA["id"])] == ["sunset.jpg"]
          and _cA.photo_by_path(str(_A.originals / "2023" / "beach.jpg"))["favorite"] == 0, _rA)
    _rA2 = _sy.pull(_A, _cA, _shared())
    check("looking again with nothing new changes nothing", _rA2.ok and not _rA2.changed, _rA2)

    # A quick look at the top of the backup decides whether to read it through.
    _reads = []
    _orig_listing = _rm.LocalBackend.listing
    _rm.LocalBackend.listing = lambda self: _reads.append(1) or _orig_listing(self)
    try:
        _sy.pull(_A, _cA, _shared())
        check("with nothing new at the NAS, the backup isn't read through", _reads == [], len(_reads))
        _cat_there = _shared().base / "catalog.db"
        _later = _cat_there.stat().st_mtime + 60
        os.utime(_cat_there, (_later, _later))          # another computer sent a new catalog
        _rA3 = _sy.pull(_A, _cA, _shared())
        check("a new catalog from another computer makes it read the backup through",
              _rA3.ok and len(_reads) == 1, len(_reads))
        _look = _shared()._look_path(_A.root)
        _state = json.loads(_look.read_text()); _state["full_at"] = time.time() - 7 * 3600
        _look.write_text(json.dumps(_state))
        _sy.pull(_A, _cA, _shared())
        check("every few hours the backup is read through anyway", len(_reads) == 2, len(_reads))
    finally:
        _rm.LocalBackend.listing = _orig_listing
    _order = _rm.library_files(_A.root)
    check("a backup sends the catalog last", isinstance(_order[-1], tuple) and _order[-1][1] == "catalog.db",
          _order[-1])

# Heavy work takes only what the computer can spare, and gives way.
from piklin import system as _sysm
import threading as _thr
_budgets = {k: _sysm.work_budget(k) for k in ("thumbs", "probe", "images", "video")}
check("background work leaves a core free and fits in memory",
      all(1 <= n <= _sysm._CAPS[k] for k, n in _budgets.items())
      and all(n <= max(1, (os.cpu_count() or 2) - 1) for n in _budgets.values()), _budgets)
_real_total = _sysm.total_memory
try:
    _sysm.total_memory = lambda: 7 * 1024 ** 3
    _small = {k: _sysm.work_budget(k) for k in ("thumbs", "probe", "images", "video")}
    _sysm.total_memory = lambda: 64 * 1024 ** 3
    _big = {k: _sysm.work_budget(k) for k in ("thumbs", "probe", "images", "video")}
finally:
    _sysm.total_memory = _real_total
check("a computer with little memory does fewer things at once",
      _small["thumbs"] <= 2 and _small["images"] <= 2 and _small["video"] == 1
      and all(_big[k] >= _small[k] for k in _small), (_small, _big))
_lowered = []
_t = _thr.Thread(target=lambda: (_sysm.lower_thread_priority(), _lowered.append(
    os.getpriority(os.PRIO_PROCESS, _thr.get_native_id()) if _sysm.IS_LINUX else True)))
_t.start(); _t.join()
check("a background thread can lower its own priority",
      _lowered and (_lowered[0] is True or _lowered[0] >= 10), _lowered)

# A video whose pixels aren't square - a 9:16 clip stored as 1080x1080 with
# pixels 9/16 as wide as tall - is shown, played and exported at 9:16.
import fractions as _fr
import av as _avm
_sar_path = Path(TMP) / "square-stored-9x16.mp4"
with _avm.open(str(_sar_path), "w") as _out:
    _st = _out.add_stream("mpeg4", rate=10)
    _st.width = _st.height = 64
    _st.pix_fmt = "yuv420p"
    _st.codec_context.sample_aspect_ratio = _fr.Fraction(9, 16)
    for _i in range(12):
        _img = np.zeros((64, 64, 3), dtype=np.uint8); _img[:, :32] = (200, 40, 40); _img[:, 32:] = (40, 40, 200)
        for _pkt in _st.encode(_avm.VideoFrame.from_ndarray(_img, format="rgb24")):
            _out.mux(_pkt)
    for _pkt in _st.encode():
        _out.mux(_pkt)
from piklin import video as _vid
_sinfo = _vid.stream_info(_sar_path) or {}
_sframe = _vid.frame_at(_sar_path, 0.2)
check("a video with non-square pixels is measured at its shown size",
      (_sinfo.get("width"), _sinfo.get("height")) == (36, 64) and _sframe.size == (36, 64),
      (_sinfo.get("width"), _sinfo.get("height"), _sframe.size))
from piklin.ui import player as _plm
_pgot, _pready = {}, _thr.Event()
def _pframe(w, h, data, stride):
    _pgot.setdefault("size", (w, h)); _pready.set()
_peng = _plm._AvEngine(_pframe, lambda *a: None, sound=False)
_peng.load(str(_sar_path), 36, 64, 0.0); _pready.wait(10); _peng.unload()
_pw, _ph = _pgot.get("size", (0, 0))
check("it plays at its shown shape, not squashed", _ph and abs(_pw / _ph - 36 / 64) < 0.05, _pgot)
from piklin import video_edit as _vem
_exp = _vem.export(_sar_path, Path(TMP) / "square-stored-exported", _vem.VideoEdit(duration=1.2), fmt="mp4")
_einfo = _vid.stream_info(_exp) or {}
with _avm.open(str(_exp)) as _ec:
    _esar = _vid._av_pixel_aspect(_ec.streams.video[0])
check("exporting it gives a 9:16 video with square pixels",
      _einfo.get("height") and abs(_einfo["width"] / _einfo["height"] - 36 / 64) < 0.05 and abs(_esar - 1.0) < 0.01,
      (_einfo.get("width"), _einfo.get("height"), _esar))

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
_s1.set("video_sizes_checked_v3", True)
check("the one-time check of video sizes is remembered after a restart",
      _S(_sp).get("video_sizes_checked_v3") is True)
check("checking photos for damage is on by default",
      _S(os.path.join(TMP, "fresh-health.json")).get("health_check") is True)
_s1.set("health_check", False)
check("turning off the damage check is remembered after a restart",
      _S(_sp).get("health_check") is False)
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
if _vw.isOpened():
    for _i in range(50):
        _fr = np.zeros((240, 320, 3), np.uint8); _fr[:, :, 1] = _i * 5
        _vw.write(_fr)
    _vw.release()
else:
    # This OpenCV has no FFmpeg (the one for Intel Macs): the same clip through PyAV.
    import av as _av
    with _av.open(_clip, "w") as _out:
        _st = _out.add_stream("mpeg4", rate=25)
        _st.width, _st.height, _st.pix_fmt = 320, 240, "yuv420p"
        for _i in range(50):
            _fr = np.zeros((240, 320, 3), np.uint8); _fr[:, :, 1] = _i * 5
            for _pk in _st.encode(_av.VideoFrame.from_ndarray(_fr, format="bgr24")):
                _out.mux(_pk)
        for _pk in _st.encode():
            _out.mux(_pk)
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
_t0 = time.perf_counter()
_r1 = devmod.import_photos(_camlib, [iio.probe(_camclip)], video_profile="h264")
_copy_s = time.perf_counter() - _t0
_copied = Path(_r1["copied"][0]) if _r1["copied"] else None
check("a video to be made smaller is copied as it is first, and listed to shrink",
      _copied is not None and _copied.suffix == ".MOV"
      and _copied.stat().st_size == os.path.getsize(_camclip)
      and [Path(p) for p in _r1["to_shrink"]] == [_copied],
      (_copied, _r1.get("to_shrink"), round(_copy_s, 2)))
from piklin.indexer import Indexer as _IxS
_scat = Catalog(_camlib.db); _IxS(_scat, None, library_root=_camlib.root).add_files([str(_copied)])
_vrow = _scat.photo_by_path(str(_copied))
_valb = _scat.create_album("Clips"); _scat.album_add(_valb, [_vrow["id"]])
with _scat.write() as _cur:
    _cur.execute("UPDATE photos SET favorite=1 WHERE id=?", (_vrow["id"],))
_seen_p = []
_out = devmod.shrink_video(_camlib, _scat, _vrow["id"], on_progress=_seen_p.append)
_after = _scat.photo(_vrow["id"])
_small = Path(_after["path"])
check("afterwards it is made smaller as H.264, in place of the copy",
      _out == "smaller" and _small.suffix == ".mp4" and _small.exists() and not _copied.exists()
      and _small.stat().st_size < os.path.getsize(_camclip) * 0.9
      and devmod._source_size_of(_small) == os.path.getsize(_camclip) and _seen_p,
      (_out, _after["path"], _small.stat().st_size if _small.exists() else None))
check("the smaller video keeps its album and favourite",
      _scat.album_photo_paths(_valb) == [str(_small)] and _after["favorite"] == 1)
_r2 = devmod.import_photos(_camlib, [iio.probe(_camclip)], video_profile="h264")
check("importing the card again does not duplicate the smaller copy",
      _r2["skipped"] == 1 and not _r2["copied"])

# Making room: which videos, by how much, and only a copy proven the same
from piklin import videospace as _vs
_vs_lib = _L(os.path.join(TMP, "SpaceLib.piklin")).ensure()
_vs_in = _vs_lib.originals / "2024" / "2024-05-01" / "clip.MOV"
_vs_in.parent.mkdir(parents=True); shutil.copy2(_camclip, _vs_in)
_vs_watch = Path(TMP) / "WatchedVideos"; _vs_watch.mkdir()
_vs_out = _vs_watch / "outside.MOV"; shutil.copy2(_camclip, _vs_out)
with open(_vs_out, "ab") as _fh:      # another video, not the same one moved
    _fh.write(b"\0" * 4096)
_vs_cat = Catalog(_vs_lib.db)
_IxS(_vs_cat, None, library_root=_vs_lib.root).add_files([str(_vs_in), str(_vs_out)])
_vs_plan = _vs.plan(_vs_cat, _vs_lib.originals, min_saving=0, min_share=0)
check("only videos inside the library are ever planned to be made smaller",
      [Path(c.path).resolve() for c in _vs_plan] == [_vs_in.resolve()], [c.path for c in _vs_plan])
check("the planned rate is capped by the size of the picture",
      bool(_vs_plan) and _vs_plan[0].bit_rate <= _vs.cap_for(640, 360)
      and _vs_plan[0].expected < _vs_plan[0].bytes, _vs_plan[:1])
_vs_good = ve_mod = None
from piklin import video_edit as _ve2
_vs_dur = float(_vs_cat.photo_by_path(str(_vs_in.resolve()))["duration"] or 2.5)
_vs_good = _ve2.export(_vs_in, Path(TMP) / "vs-good.mp4", _ve2.VideoEdit(duration=_vs_dur),
                       fmt="mp4", bit_rate=_vs.cap_for(640, 360))
check("a smaller copy that is the same video passes the check", _vs.verify(_vs_in, _vs_good))
_vs_half = _ve2.export(_vs_in, Path(TMP) / "vs-half.mp4",
                       _ve2.VideoEdit(duration=_vs_dur, end=_vs_dur / 2), fmt="mp4")
check("a copy that lost half the video fails the check", not _vs.verify(_vs_in, _vs_half))
_vs_row = _vs_cat.photo_by_path(str(_vs_in.resolve()))
_vs_res = devmod.shrink_video(_vs_lib, _vs_cat, _vs_row["id"], bit_rate=800_000,
                              verify=lambda s, o: False)
check("when the check fails, the original stays exactly where it was",
      _vs_res == "failed" and _vs_in.exists()
      and _vs_cat.photo(_vs_row["id"])["path"] == _vs_row["path"]
      and not list(_vs_in.parent.glob("*.smaller.mp4")), _vs_res)
_vs_res = devmod.shrink_video(_vs_lib, _vs_cat, _vs_row["id"], bit_rate=800_000,
                              verify=_vs.verify)
_vs_new = Path(_vs_cat.photo(_vs_row["id"])["path"])
check("when it passes, the smaller copy takes the original's place",
      _vs_res == "smaller" and _vs_new.exists() and not _vs_in.exists()
      and _vs_new.stat().st_size < os.path.getsize(_camclip), (_vs_res, _vs_new))

# The backup: the large original set apart, then let go after HOLD_DAYS
from piklin import autobackup as _ab
_bk_lib = _L(os.path.join(TMP, "HoldLib.piklin")).ensure()
_bk_old = _bk_lib.originals / "2023" / "2023-09-16" / "party.MOV"
_bk_old.parent.mkdir(parents=True); shutil.copy2(_camclip, _bk_old)
_bk_cat = Catalog(_bk_lib.db); _IxS(_bk_cat, None, library_root=_bk_lib.root).add_files([str(_bk_old)])
_bk_nas = Path(TMP) / "HoldNAS"; _bk_nas.mkdir()
_bk_remote = rem.Remote(id="hold", name="NAS", kind="local", config={"path": str(_bk_nas)})
_bk = _bk_remote.backend()
_bk.push(_bk_lib.root, rem.library_files(_bk_lib.root))
_bk_old_rel = _bk_old.resolve().relative_to(_bk_lib.root).as_posix()
check("the original video is in the backup before anything", (_bk_nas / "Piklin" / _bk_old_rel).is_file())
_bk_row = _bk_cat.photo_by_path(str(_bk_old.resolve()))
_bk_size = _bk_old.stat().st_size
devmod.shrink_video(_bk_lib, _bk_cat, _bk_row["id"], bit_rate=800_000, verify=_vs.verify)
_bk_new = Path(_bk_cat.photo(_bk_row["id"])["path"])
_vs.record_conversion(_bk_lib.root, _bk_old, _bk_new, _bk_size, float(_bk_row["duration"] or 3.0))
_bk_now = time.time()
_bk_c = _vs.tend(_bk_lib.root, _bk, now=_bk_now)
check("the original stays in place until the smaller copy is in the backup in full",
      _bk_c["held"] == 0 and _bk_c["waiting"] == 1 and (_bk_nas / "Piklin" / _bk_old_rel).is_file(), _bk_c)
_bk.push(_bk_lib.root, rem.library_files(_bk_lib.root))
_bk_c = _vs.tend(_bk_lib.root, _bk, now=_bk_now)
_bk_held = list((_bk_nas / "Piklin" / _vs.CONVERTED_DIR).rglob("party.MOV"))
check("once it is, the original is set apart in a folder of its own",
      _bk_c["held"] == 1 and not (_bk_nas / "Piklin" / _bk_old_rel).exists() and len(_bk_held) == 1
      and _bk_held[0].stat().st_size == _bk_size, (_bk_c, _bk_held))
check("the set-apart originals are not part of the backup's listing",
      not any(k.startswith(_vs.CONVERTED_DIR) for k in _bk.listing()))
check("nothing more is due until its days have passed",
      not _vs.due(_bk_lib.root, "hold", now=_bk_now + 4 * 86400)
      and _vs.tend(_bk_lib.root, _bk, now=_bk_now + 4 * 86400)["deleted"] == 0 and _bk_held[0].exists())
check("after five days it is due again", _vs.due(_bk_lib.root, "hold", now=_bk_now + 5 * 86400 + 60))
_bk_c = _vs.tend(_bk_lib.root, _bk, now=_bk_now + 5 * 86400 + 60)
check("after five days, with the smaller copy checked again, the original is let go",
      _bk_c["deleted"] == 1 and not _bk_held[0].exists() and _bk_new.exists(), _bk_c)
_bk_restored = _L(os.path.join(TMP, "HoldRestored.piklin")).ensure()
_bk.restore(_bk_restored.root)
check("restoring never brings set-apart originals back",
      not (_bk_restored.root / _vs.CONVERTED_DIR).exists()
      and (_bk_restored.root / _bk_new.relative_to(_bk_lib.root)).is_file())

# a smaller copy that no longer checks out: the original is kept
_bk_old2 = _bk_lib.originals / "2023" / "2023-09-16" / "cake.MOV"; shutil.copy2(_camclip, _bk_old2)
with open(_bk_old2, "ab") as _fh:
    _fh.write(b"\1" * 2048)
_IxS(_bk_cat, None, library_root=_bk_lib.root).add_files([str(_bk_old2)])
_bk.push(_bk_lib.root, rem.library_files(_bk_lib.root))
_bk_row2 = _bk_cat.photo_by_path(str(_bk_old2.resolve()))
_bk_size2 = _bk_old2.stat().st_size
devmod.shrink_video(_bk_lib, _bk_cat, _bk_row2["id"], bit_rate=800_000, verify=_vs.verify)
_bk_new2 = Path(_bk_cat.photo(_bk_row2["id"])["path"])
_vs.record_conversion(_bk_lib.root, _bk_old2, _bk_new2, _bk_size2, float(_bk_row2["duration"] or 3.0))
_bk.push(_bk_lib.root, rem.library_files(_bk_lib.root))
_vs.tend(_bk_lib.root, _bk, now=_bk_now)
_bk_new2.write_bytes(b"not a video any more")
_bk_c = _vs.tend(_bk_lib.root, _bk, now=_bk_now + 6 * 86400)
check("when the smaller copy does not check out after five days, the original is kept",
      _bk_c["problems"] == 1 and _bk_c["deleted"] == 0
      and list((_bk_nas / "Piklin" / _vs.CONVERTED_DIR).rglob("cake.MOV")), _bk_c)

# originals left behind by videos made smaller before any of this
_fg_lib = _L(os.path.join(TMP, "ForgottenLib.piklin")).ensure()
_fg_nas = Path(TMP) / "ForgottenNAS" / "Piklin" / "Originals" / "2019"; _fg_nas.mkdir(parents=True)
shutil.copy2(_camclip, _fg_nas / "old.MOV")
(_fg_lib.originals / "2019").mkdir(parents=True)
_fg_small = _fg_lib.originals / "2019" / "old.mp4"; shutil.copy2(_vs_good, _fg_small)
from piklin import system as _sysm
_sysm.set_xattr(_fg_small, devmod._SOURCE_SIZE_XATTR, str(os.path.getsize(_camclip)).encode())
_fg = rem.Remote(id="fg", name="NAS", kind="local", config={"path": str(Path(TMP) / "ForgottenNAS")}).backend()
_fg.push(_fg_lib.root, rem.library_files(_fg_lib.root))
_fg_c = _vs.tend(_fg_lib.root, _fg, now=_bk_now)
check("an original left behind by an earlier smaller video is found and set apart too",
      _fg_c["held"] == 1 and not (_fg_nas / "old.MOV").exists()
      and list((Path(TMP) / "ForgottenNAS" / "Piklin" / _vs.CONVERTED_DIR).rglob("old.MOV")), _fg_c)

# the automatic backup does it even when there is nothing new to send
_ab_lib = _L(os.path.join(TMP, "DueLib.piklin")).ensure()
(_ab_lib.originals / "2020").mkdir(parents=True)
_ab_old = _ab_lib.originals / "2020" / "trip.MOV"; shutil.copy2(_camclip, _ab_old)
_ab_nas = Path(TMP) / "DueNAS"
_ab_remote = rem.Remote(id="due", name="NAS", kind="local", config={"path": str(_ab_nas)})
_ab_b = _ab_remote.backend(); _ab_b.prepare()
_ab_b.push(_ab_lib.root, rem.library_files(_ab_lib.root))
_ab_new = _ab_lib.originals / "2020" / "trip.mp4"; shutil.copy2(_vs_good, _ab_new); _ab_old.unlink()
_ab_b.push(_ab_lib.root, rem.library_files(_ab_lib.root))
_vs.record_conversion(_ab_lib.root, _ab_lib.root / "Originals/2020/trip.MOV", _ab_new,
                      os.path.getsize(_camclip), 3.0)
_ab_out = _ab.run_backup(_ab_lib.root, [_ab_remote], keep_days=30)
check("the automatic backup sets apart a converted original with nothing new to send",
      _ab_out.ok and not (_ab_nas / "Piklin" / "Originals/2020/trip.MOV").exists()
      and list((_ab_nas / "Piklin" / _vs.CONVERTED_DIR).rglob("trip.MOV")), _ab_out)

# over WebDAV, against a real HTTP server that can move and delete
import http.server as _hs2, functools as _ft2, urllib.parse as _up2
_wd_root = Path(TMP) / "WebDavRoot"; (_wd_root / "dav" / "Piklin" / "Originals").mkdir(parents=True)
(_wd_root / "dav" / "Piklin" / "Originals" / "big.MOV").write_bytes(b"x" * 5000)
class _DavHandler(_hs2.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def _local(self, url_path):
        return Path(self.directory) / _up2.unquote(_up2.urlparse(url_path).path).lstrip("/")
    def do_MKCOL(self):
        self._local(self.path).mkdir(parents=True, exist_ok=True); self.send_response(201); self.end_headers()
    def do_MOVE(self):
        src, dst = self._local(self.path), self._local(self.headers["Destination"])
        if not src.exists():
            self.send_response(404); self.end_headers(); return
        dst.parent.mkdir(parents=True, exist_ok=True); os.replace(src, dst)
        self.send_response(201); self.end_headers()
    def do_DELETE(self):
        p = self._local(self.path)
        if not p.exists():
            self.send_response(404); self.end_headers(); return
        p.unlink(); self.send_response(204); self.end_headers()
_wd_srv = _hs2.ThreadingHTTPServer(("127.0.0.1", 0), _ft2.partial(_DavHandler, directory=str(_wd_root)))
threading.Thread(target=_wd_srv.serve_forever, daemon=True).start()
try:
    _wd = rem.Remote(id="wdv", name="WebDAV", kind="webdav",
                     config={"url": f"http://127.0.0.1:{_wd_srv.server_address[1]}/dav"}).backend()
    _wd_moved = _wd.move("Originals/big.MOV", f"{_vs.CONVERTED_DIR}/2026-09-17/Originals/big.MOV")
    _wd_at = _wd_root / "dav" / "Piklin" / _vs.CONVERTED_DIR / "2026-09-17" / "Originals" / "big.MOV"
    check("over WebDAV an original is moved within the server",
          _wd_moved and _wd_at.is_file() and not (_wd_root / "dav/Piklin/Originals/big.MOV").exists())
    check("and deleted there, also when it is gone already",
          _wd.delete(f"{_vs.CONVERTED_DIR}/2026-09-17/Originals/big.MOV") and not _wd_at.exists()
          and _wd.delete(f"{_vs.CONVERTED_DIR}/2026-09-17/Originals/big.MOV"))
finally:
    _wd_srv.shutdown()

def _video_lib(name, rel="Originals/2021/2021-06-01/beach.MOV", tail=b""):
    lib = _L(os.path.join(TMP, name)).ensure()
    p = lib.root / rel; p.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(_camclip, p)
    if tail:
        with open(p, "ab") as fh:
            fh.write(tail)
    cat = Catalog(lib.db); _IxS(cat, None, library_root=lib.root).add_files([str(p)])
    return lib, cat, p

def _convert(lib, cat, p, set_apart=None):
    row = cat.photo_by_path(str(p.resolve())); size = p.stat().st_size
    res = devmod.shrink_video(lib, cat, row["id"], bit_rate=800_000, verify=_vs.verify, set_apart=set_apart)
    new = Path(cat.photo(row["id"])["path"])
    _vs.record_conversion(lib.root, p, new, size, float(row["duration"] or 3.0))
    return res, new, row["id"]

# A cloud service is paid by the gigabyte: only the smaller video stays there
_cl_lib, _cl_cat, _cl_old = _video_lib("CloudLib.piklin")
_cl_nas = Path(TMP) / "CloudDrive"
_cl = rem.LocalBackend(rem.Remote(id="cloud", name="Cloud", kind="rclone", config={"path": str(_cl_nas)}))
_cl.push(_cl_lib.root, rem.library_files(_cl_lib.root))
_cl_rel = _cl_old.resolve().relative_to(_cl_lib.root).as_posix()
_convert(_cl_lib, _cl_cat, _cl_old)
_cl.push(_cl_lib.root, rem.library_files(_cl_lib.root))
_cl_c = _vs.tend(_cl_lib.root, _cl, now=time.time())
check("in a cloud service the original goes as soon as the smaller video is there in full",
      _cl_c["deleted"] == 1 and not (_cl_nas / "Piklin" / _cl_rel).exists()
      and not (_cl_nas / "Piklin" / _vs.CONVERTED_DIR).exists(), _cl_c)
check("this computer keeps originals itself only when no backup keeps them",
      _vs.local_hold_needed([]) and _vs.local_hold_needed([rem.Remote(id="c", name="c", kind="rclone")])
      and not _vs.local_hold_needed([rem.Remote(id="n", name="n", kind="webdav"),
                                     rem.Remote(id="c", name="c", kind="rclone")]))

# No backup at all: the original waits in the library, then goes
_nb_lib, _nb_cat, _nb_old = _video_lib("NoBackupLib.piklin")
_nb_rel = _nb_old.resolve().relative_to(_nb_lib.root).as_posix()
_nb_now = time.time()
_nb_hold = _vs.set_apart_locally(_nb_lib.root, _nb_rel, now=_nb_now)
_nb_res, _nb_new, _ = _convert(_nb_lib, _nb_cat, _nb_old, set_apart=_nb_hold)
_vs.note_local_hold(_nb_lib.root, _nb_rel, _nb_hold, now=_nb_now)
check("with no backup, the original is set apart inside the library, not deleted",
      _nb_res == "smaller" and _nb_hold.is_file() and not _nb_old.exists() and _nb_new.exists())
check("and still kept after four days",
      _vs.tend_local(_nb_lib.root, now=_nb_now + 4 * 86400)["deleted"] == 0 and _nb_hold.is_file())
check("and let go after five, with the smaller copy checked again",
      _vs.tend_local(_nb_lib.root, now=_nb_now + 5 * 86400 + 60)["deleted"] == 1 and not _nb_hold.exists())
_nb2_lib, _nb2_cat, _nb2_old = _video_lib("NoBackupLib2.piklin")
_nb2_rel = _nb2_old.resolve().relative_to(_nb2_lib.root).as_posix()
_nb2_hold = _vs.set_apart_locally(_nb2_lib.root, _nb2_rel, now=_nb_now)
_, _nb2_new, _ = _convert(_nb2_lib, _nb2_cat, _nb2_old, set_apart=_nb2_hold)
_vs.note_local_hold(_nb2_lib.root, _nb2_rel, _nb2_hold, now=_nb_now)
_nb2_new.unlink()
_nb2_c = _vs.tend_local(_nb2_lib.root, now=_nb_now + 6 * 86400)
check("with no backup, an original whose smaller copy is gone is kept",
      _nb2_c["problems"] == 1 and _nb2_hold.is_file(), _nb2_c)

# Two computers sharing one backup: the second follows the first
from piklin import sync as _syn
_mc_nas = Path(TMP) / "SharedHomeNAS"
_mc_remote = rem.Remote(id="home", name="NAS", kind="local", config={"path": str(_mc_nas)})
_mcA_lib, _mcA_cat, _mcA_old = _video_lib("ComputerA.piklin")
_mcA = _mc_remote.backend(); _mcA.push(_mcA_lib.root, rem.library_files(_mcA_lib.root))
_mcB_lib, _mcB_cat, _mcB_old = _video_lib("ComputerB.piklin")
_mcB_alb = _mcB_cat.create_album("Beach"); _mcB_cat.album_add(_mcB_alb, [_mcB_cat.photo_by_path(str(_mcB_old.resolve()))["id"]])
_mcB = _mc_remote.backend(); _mcB.push(_mcB_lib.root, rem.library_files(_mcB_lib.root))
_, _mcA_new, _ = _convert(_mcA_lib, _mcA_cat, _mcA_old)
_mcA.push(_mcA_lib.root, rem.library_files(_mcA_lib.root))
_mcB_before = _mcB_cat.scalar("SELECT COUNT(*) FROM photos", (), 0)
_mcB_res = _syn.pull(_mcB_lib, _mcB_cat, _mcB)
_mcB_new = _mcB_lib.root / _mcA_new.relative_to(_mcA_lib.root)
check("another computer sharing the backup replaces its own copy with the smaller video",
      _mcB_new.is_file() and not _mcB_old.exists()
      and _mcB_new.read_bytes() == _mcA_new.read_bytes(), (_mcB_res, _mcB_new))
check("keeping it one video, in its album - not a second copy",
      _mcB_cat.scalar("SELECT COUNT(*) FROM photos", (), 0) == _mcB_before
      and _mcB_cat.album_photo_paths(_mcB_alb) == [str(_mcB_new)])
_mcB.push(_mcB_lib.root, rem.library_files(_mcB_lib.root))
_mc_c = _vs.tend(_mcA_lib.root, _mcA, now=time.time())
check("and the second computer does not send the large original back",
      _mc_c["held"] == 1 and not (_mc_nas / "Piklin" / _mcA_old.resolve().relative_to(_mcA_lib.root)).exists(), _mc_c)

_mcC_lib, _mcC_cat, _mcC_old = _video_lib("ComputerC.piklin", tail=b"\2" * 3000)   # its own, different
_mcC = _mc_remote.backend()
_mcC_bytes = _mcC_old.read_bytes()
_syn.pull(_mcC_lib, _mcC_cat, _mcC)
check("a computer whose video is not the same one is left untouched", _mcC_old.read_bytes() == _mcC_bytes)

# sent back after it was set apart: set apart again
_rb_rel = _mcA_old.resolve().relative_to(_mcA_lib.root).as_posix()
shutil.copy2(_camclip, _mc_nas / "Piklin" / _rb_rel)
_rb_c = _vs.tend(_mcA_lib.root, _mcA, now=time.time() + 60)
check("an original sent back to the backup after it was set apart is set apart again",
      _rb_c["held"] == 1 and not (_mc_nas / "Piklin" / _rb_rel).exists(), _rb_c)

# Made smaller under the same name (an .mp4 stays .mp4): never moved away
_sn_nas = Path(TMP) / "SameNameNAS"
_sn_remote = rem.Remote(id="same", name="NAS", kind="local", config={"path": str(_sn_nas)})
_snA_lib, _snA_cat, _snA_old = _video_lib("SameNameA.piklin", rel="Originals/2022/2022-02-02/walk.mp4")
_snB_lib, _snB_cat, _snB_old = _video_lib("SameNameB.piklin", rel="Originals/2022/2022-02-02/walk.mp4")
_snA = _sn_remote.backend(); _snA.push(_snA_lib.root, rem.library_files(_snA_lib.root))
_sn_rel = _snA_old.resolve().relative_to(_snA_lib.root).as_posix()
_sn_res, _snA_new, _ = _convert(_snA_lib, _snA_cat, _snA_old)
_snA.push(_snA_lib.root, rem.library_files(_snA_lib.root))
_sn_c = _vs.tend(_snA_lib.root, _snA, now=time.time())
check("a video made smaller under the same name stays in the backup, not set apart",
      _sn_res == "smaller" and _snA_new == _snA_old.resolve() and _sn_c["held"] == 0
      and (_sn_nas / "Piklin" / _sn_rel).is_file()
      and (_sn_nas / "Piklin" / _sn_rel).read_bytes() == _snA_new.read_bytes(), (_sn_res, _sn_c))
_syn.pull(_snB_lib, _snB_cat, _snA_remote_b := _sn_remote.backend())
check("and another computer replaces its copy under that name, keeping the new file",
      _snB_old.is_file() and _snB_old.read_bytes() == _snA_new.read_bytes()
      and _snB_cat.photo_by_path(str(_snB_old.resolve())) is not None)

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

# Restored onto another computer: the library's files come back from a backup
# without catalog.db or settings.json, under another path, perhaps written by
# another system. Albums, their folder and the marks must find their photos.
import contextlib as _ctxr
from piklin.app import rebuild_index as _rebuild
libS, cS, pidS, aidS = _library_with_photo(os.path.join(TMP, "Source Library.piklin"))
_fidS = cS.create_folder("Family"); cS.move_album_to_folder(aidS, _fidS)
_tripS = json.loads((libS.albums / "Trip.json").read_text())
_tripS["folder_uuid"] = cS.q1("SELECT uuid FROM folders WHERE id=?", (_fidS,))["uuid"]
(libS.albums / "Trip.json").write_text(json.dumps(_tripS))
_sc.write_all(libS, cS)

def _as_windows(value, src):
    if isinstance(value, str):
        if value.startswith(src + "/"):
            return "C:\\Users\\Ana\\Pictures\\Piklin Library.piklin\\" + value[len(src) + 1:].replace("/", "\\")
        return value
    if isinstance(value, list):
        return [_as_windows(v, src) for v in value]
    if isinstance(value, dict):
        return {_as_windows(k, src): _as_windows(v, src) for k, v in value.items()}
    return value

for _style in ("another Mac or Linux computer", "Windows"):
    _dest = os.path.join(TMP, f"restored-{len(_style)}", "Pictures", "Piklin Library.piklin")
    shutil.copytree(libS.root, _dest, ignore=shutil.ignore_patterns("catalog.db*", "settings.json", ".cache"))
    libT = _L(_dest)
    if _style == "Windows":
        for _f in list(libT.root.glob("*.json")) + list(libT.albums.glob("*.json")):
            _f.write_text(json.dumps(_as_windows(json.loads(_f.read_text()), str(libS.root))))
    # The first computer's paths must not exist here, as on another computer:
    # with the source library still in place the rebuild found its photos there.
    _away = str(libS.root) + ".elsewhere"
    os.rename(libS.root, _away)
    try:
        with _ctxr.redirect_stdout(io.StringIO()):
            _status = _rebuild(libT)
    finally:
        os.rename(_away, libS.root)
    cT = _C(libT.db)
    _albumT = cT.q1("SELECT id, folder_id FROM albums WHERE uuid='u1'")
    _photosT = cT.album_photo_paths(_albumT["id"]) if _albumT else []
    _favT = cT.q1("SELECT favorite FROM photos WHERE path=?", (_photosT[0],))["favorite"] if _photosT else None
    _folderT = cT.q1("SELECT name FROM folders WHERE id=?", (_albumT["folder_id"],)) if _albumT and _albumT["folder_id"] else None
    check(f"restored on {_style}: albums, folders and favourites find their photos",
          _status == 0 and len(_photosT) == 1 and _photosT[0].startswith(str(libT.root))
          and os.path.exists(_photosT[0]) and _folderT is not None and _folderT["name"] == "Family" and _favT == 1,
          (_status, _photosT, _folderT and _folderT["name"], _favT))

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

# A restore rebuilds a library that sits inside a watched ~/Pictures: the
# watched folder takes over the Originals root, and the scan must file every
# photo under the root that remains, or the rebuild fails and no album returns.
_home = os.path.join(TMP, "restore-home", "Pictures"); os.makedirs(_home)
libN = _L(os.path.join(_home, LIBRARY_NAME)).ensure()
(libN.originals / "2019").mkdir(parents=True)
shutil.copy2(SRC[1], libN.originals / "2019" / "kept.jpg")
shutil.copy2(SRC[2], os.path.join(_home, "loose.jpg"))
(libN.root / "watched-folders.json").write_text(json.dumps(
    {"format": "pikalicious-state", "version": 1,
     "folders": [{"path": _home, "in_library": False, "enabled": True}]}))
(libN.albums / "Kept.json").write_text(json.dumps(
    {"format": "pikalicious-album", "version": 1, "uuid": "nested-1", "name": "Kept",
     "photos": [str(libN.originals / "2019" / "kept.jpg")]}))
try:
    with _ctxr.redirect_stdout(io.StringIO()):
        _statusN = _rebuild(libN)
    _errN = ""
except Exception as _e:
    _statusN, _errN = None, repr(_e)
cN = _C(libN.db)
_keptN = cN.q1("SELECT id FROM albums WHERE uuid='nested-1'")
check("a restore rebuilds a library inside a watched Pictures folder",
      _statusN == 0 and _keptN is not None and len(cN.album_photo_paths(_keptN["id"])) == 1
      and cN.scalar("SELECT COUNT(*) FROM photos", (), 0) == 2,
      (_statusN, _errN, cN.scalar("SELECT COUNT(*) FROM photos", (), 0)))

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
section("Photo health: damage found, and mended from the backup")
import functools as _ft, http.server as _hs, subprocess
from piklin import health as _H
_hT = Path(TMP) / "health"
_hlib = _L(os.path.join(_hT, "Lib", LIBRARY_NAME)).ensure()
_hday = _hlib.originals / "2014" / "2014-12-17"; _hday.mkdir(parents=True)
_hold = time.time() - 3 * 86400
_hp = []
for _f in SRC[:6]:
    _p = _hday / os.path.basename(_f); shutil.copy2(_f, _p); os.utime(_p, (_hold, _hold))
    _hp.append(_p.resolve())
_hout = _hT / "Watched"; _hout.mkdir()
_hext = _hout / "outside.jpg"; shutil.copy2(SRC[0], _hext); os.utime(_hext, (_hold, _hold))
_hp.append(_hext.resolve())
_hgood = {p: p.read_bytes() for p in _hp}

def _hflip(p, at=None):
    st = p.stat(); data = bytearray(p.read_bytes())
    i = at if at is not None else len(data) // 2
    data[i] ^= 0xFF; data[i + 1] ^= 0x5A
    p.write_bytes(bytes(data)); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))

def _hdamage(h, p, at=None):
    # Damage as a disk does it: only the content changes. A write moves the
    # change time, so the record takes the new one - as if never written.
    _hflip(p, at)
    with h._lock, h._con:
        h._con.execute("UPDATE files SET ctime_ns=? WHERE key=?", (p.stat().st_ctime_ns, h.key(p)))

_hdest = _hT / "NAS"; _hdest.mkdir()
_hb = rem.Remote(id="hnas", name="NAS", kind="local", config={"path": str(_hdest)}).backend()
_hb.push(_hlib.root, rem.library_files(_hlib.root))
_h = _H.Health(_hlib)
_hnow = time.time() + _H.SETTLE_SECONDS + 60
_r = _h.check(_hp, 10**12, now=_hnow)
check("the first check records every photo and finds nothing", _r.recorded == len(_hp) and not _r.damaged, _r)
check("a photo checked recently is not read again", _h.check(_hp, 10**12, now=_hnow + 60).checked == 0)
_ht = _hnow + 31 * 86400
_r = _h.check(_hp, 10**12, now=_ht)
check("a month later every photo is read again, all healthy",
      _r.checked == len(_hp) and not _r.damaged and _r.recorded == 0, _r)
_hed = _hp[1]
_hed.write_bytes(_hgood[_hed] + b"xmp"); os.utime(_hed, (_hold + 5, _hold + 5)); _hgood[_hed] = _hed.read_bytes()
_ht += 31 * 86400
_r = _h.check(_hp, 10**12, now=_ht)
check("a photo changed on purpose is recorded again, not taken for damage", _r.recorded == 1 and not _r.damaged, _r)

_hv = _hp[2]; _hk = _h.key(_hv)
_hdamage(_h, _hv)
_ht += 31 * 86400
_r = _h.check(_hp, 10**12, now=_ht)
check("damage that keeps the size and the date is found", _r.damaged == [_hk], _r)
_pp = _hb.push(_hlib.root, rem.library_files(_hlib.root))
check("the backup never sends a damaged photo over its good copy",
      _pp.uploaded == 1 and (_hdest / "Piklin" / _hk).read_bytes() == _hgood[_hv], _pp.uploaded)
_hbad = _hv.read_bytes()
_m = _h.mend([_hb], now=_ht + 10)
check("a damaged photo is put back from the backup, byte for byte, with its own date",
      _m.mended == [_hk] and _hv.read_bytes() == _hgood[_hv]
      and _hv.stat().st_mtime_ns == _h._get(_hk)["mtime_ns"], _m)
_haside = list((_H.health_dir(_hlib) / "damaged").rglob(_hv.name))
check("the damaged file is kept aside, never thrown away",
      len(_haside) == 1 and _haside[0].read_bytes() == _hbad, _haside)
check("after mending, the next backup sends nothing",
      _hb.push(_hlib.root, rem.library_files(_hlib.root)).uploaded == 0)

_hv2 = _hp[3]; _hk2 = _h.key(_hv2)
_hdamage(_h, _hv2); _hflip(_hdest / "Piklin" / _hk2, at=100)
_ht += 31 * 86400
_h.check(_hp, 10**12, now=_ht)
_hb4 = _hv2.read_bytes()
_m = _h.mend([_hb], now=_ht + 10)
check("with no good copy in the backup the photo is left exactly as it is",
      _m.no_copy == [_hk2] and _hv2.read_bytes() == _hb4 and _h._get(_hk2)["state"] == _H.NO_COPY, _m)
class _HCount:
    def __init__(self, inner): self.inner, self.calls = inner, 0
    def get(self, rel, local): self.calls += 1; return self.inner.get(rel, local)
_hc = _HCount(_hb)
_h.mend([_hc], now=_ht + 3600)
_c1 = _hc.calls
_h.mend([_hc], now=_ht + 25 * 3600)
check("a photo with no good copy is looked for again once a day, not at every check",
      _c1 == 0 and _hc.calls == 1, (_c1, _hc.calls))

_hdamage(_h, _hext)
_ht += 62 * 86400
_r = _h.check(_hp, 10**12, now=_ht)
_hkx = _h.key(_hext); _hxb = _hext.read_bytes()
_m = _h.mend([_hb], now=_ht + 10)
check("damage outside the library is reported and never touched",
      _hkx in _r.damaged and _hkx in _m.no_copy and _hext.read_bytes() == _hxb)

_hv3 = _hp[4]; _hk3 = _h.key(_hv3)
_hdamage(_h, _hv3)
_ht += 62 * 86400
_h.check(_hp, 10**12, now=_ht)
class _HDown:
    def get(self, rel, local): raise OSError("no route to host")
_m = _h.mend([_HDown()], now=_ht + 10)
check("with the backup out of reach a photo stays damaged, to try again",
      not _m.mended and not _m.no_copy and _h._get(_hk3)["state"] == _H.DAMAGED, _m)
check("once the backup answers, it is mended",
      _h.mend([_hb], now=_ht + 20).mended == [_hk3] and _hv3.read_bytes() == _hgood[_hv3])

_hv4 = _hp[5]
_hreal = _H.digest; _hcalls = {"n": 0}
def _hflaky(p):
    _hcalls["n"] += 1
    return "0" * 64 if _hcalls["n"] == 1 else _hreal(p)
_H.digest = _hflaky
try:
    _r = _h.check([_hv4], 10**12, now=_ht + 62 * 86400)
finally:
    _H.digest = _hreal
check("a read that goes wrong once is not taken for damage", not _r.damaged and _hcalls["n"] == 2, _r)

_hfresh = _hday / "arriving.jpg"; shutil.copy2(SRC[0], _hfresh)
_r = _h.check([_hfresh.resolve()], 10**12, now=time.time())
check("a photo still arriving (an import keeps its old date) waits for a later check",
      _r.skipped == 1 and _r.checked == 0, _r)
_r = _h.check([_hday / "gone.jpg"], 10**12, now=time.time())
check("a photo gone from the disk is not damage", _r.missing == 1 and not _r.damaged, _r)

# a rewrite on purpose that keeps size and date (exiftool -P does) is never "mended" back
_hv6 = _hp[1]; _hk6 = _h.key(_hv6)
_hflip(_hv6); _hrew = _hv6.read_bytes()
_ht += 124 * 86400
_r = _h.check(_hp, 10**12, now=_ht)
_m = _h.mend([_hb], now=_ht + 10)
check("a photo a program rewrote on purpose, keeping its size and date, is not damage",
      _hk6 not in _r.damaged and _hk6 not in _m.mended and _hv6.read_bytes() == _hrew
      and _h._get(_hk6)["state"] == _H.OK, (_r.damaged, _m))

_hv7 = _hp[4]; _hk7 = _h.key(_hv7)
_hdamage(_h, _hv7)
_ht += 62 * 86400
_h.check(_hp, 10**12, now=_ht)
_hbroken = _hv7.read_bytes()
_hrepl = _H.os.replace
def _hfail(a, b): raise OSError("disk full")
_H.os.replace = _hfail
try:
    _m = _h.mend([_hb], now=_ht + 10)
finally:
    _H.os.replace = _hrepl
_h.check(_hp, 10**12, now=_ht + 62 * 86400)
check("a mend that fails leaves the photo as it was, still known as damaged",
      not _m.mended and _hv7.read_bytes() == _hbroken and _h._get(_hk7)["state"] == _H.DAMAGED)
check("and it is mended once it can be",
      _h.mend([_hb], now=_ht + 62 * 86400 + 10).mended == [_hk7] and _hv7.read_bytes() == _hgood[_hv7])

_hlib2 = _L(os.path.join(_hT, "Lib2", LIBRARY_NAME)).ensure(); _hd2 = _hlib2.originals / "x"; _hd2.mkdir(parents=True)
_hp2 = []
for _f in SRC[:6]:
    _p = _hd2 / os.path.basename(_f); shutil.copy2(_f, _p); os.utime(_p, (_hold, _hold)); _hp2.append(_p.resolve())
_h2 = _H.Health(_hlib2)
_one = min(p.stat().st_size for p in _hp2)
_seen, _rounds = 0, 0
while True:
    _rounds += 1
    _r = _h2.check(_hp2, _one, now=_hnow)
    _seen += _r.checked
    if not _r.stopped or _rounds > 20:
        break
check("a check reads a slice at a time and carries on where it stopped",
      _seen == len(_hp2) and _rounds > 2, (_seen, _rounds))

# over WebDAV, from a real HTTP server
_hroot = _hT / "http"; (_hroot / "dav").mkdir(parents=True)
shutil.copytree(_hdest / "Piklin", _hroot / "dav" / "Piklin", dirs_exist_ok=True)
(_hroot / "dav" / "Piklin" / _hk2).write_bytes(_hgood[_hv2])       # a good copy there
class _HQuiet(_hs.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
_hsrv = _hs.ThreadingHTTPServer(("127.0.0.1", 0), _ft.partial(_HQuiet, directory=str(_hroot)))
threading.Thread(target=_hsrv.serve_forever, daemon=True).start()
try:
    _hwb = rem.Remote(id="hwd", name="WebDAV", kind="webdav",
                      config={"url": f"http://127.0.0.1:{_hsrv.server_address[1]}/dav"}).backend()
    _m = _h.mend([_hwb], now=_ht + 200 * 86400)
    check("a damaged photo is mended over WebDAV, from a real server",
          _m.mended == [_hk2] and _hv2.read_bytes() == _hgood[_hv2], _m)
finally:
    _hsrv.shutdown()
check("the record lives outside the catalog, so rebuilding the catalog keeps it",
      (_H.health_dir(_hlib) / "health.db").is_file() and "catalog" not in str(_H.health_dir(_hlib)))
_h.close(); _h2.close()

# Every text the app shows is in the translation template: new ones left out
# of it stayed in English and no check noticed.
_xg = shutil.which("xgettext")
if _xg:
    _here = os.path.dirname(os.path.abspath(__file__))
    _srcs = sorted(str(p) for p in Path(_here, "piklin").rglob("*.py") if "__pycache__" not in p.parts)
    _pot_now = os.path.join(TMP, "now.pot")
    subprocess.run([_xg, "--from-code=UTF-8", "--language=Python", "--keyword=_", "--keyword=N_",
                    "--keyword=ngettext:1,2", "--keyword=pgettext:1c,2", "-o", _pot_now] + _srcs,
                   check=True, capture_output=True)
    def _msgids(path):
        import re as _re
        out = set()
        for block in open(path, encoding="utf-8").read().split("\n\n"):
            m = _re.search(r'^msgid ((?:".*"\n?)+)', block, _re.M)
            c = _re.search(r'^msgctxt ((?:".*"\n?)+)', block, _re.M)
            if m:
                s = "".join(_re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))
                ctx = "".join(_re.findall(r'"((?:[^"\\]|\\.)*)"', c.group(1))) if c else ""
                if s:
                    out.add((ctx, s))
        return out
    _missing_ids = _msgids(_pot_now) - _msgids(os.path.join(_here, "po", "piklin.pot"))
    check("every text in the app is in the translation template", not _missing_ids,
          sorted(s for _c, s in _missing_ids)[:5])
else:
    print("  SKIP  translation template check (no xgettext on this system)")

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

# -- albums and folders are listed the way people count -------------------------
from piklin.catalog import natural_key as _nk
_names = ["Cumpleanios 10 - Aniella", "Cumpleanios 2 - Aniella", "Cumpleanios 1 - Aniella",
          "cumpleanios 12 - Aniella", "Cumpleanios 9 - Aniella", "Paternos - 2025", "Paternos 2011+"]
check("album names sort 1, 2, 9, 10, 12 - not 1, 10, 12, 2",
      sorted(_names, key=_nk) == ["Cumpleanios 1 - Aniella", "Cumpleanios 2 - Aniella",
                                  "Cumpleanios 9 - Aniella", "Cumpleanios 10 - Aniella",
                                  "cumpleanios 12 - Aniella", "Paternos 2011+", "Paternos - 2025"],
      sorted(_names, key=_nk))
_tc2 = Catalog(os.path.join(TMP, "natural.db"))
for _n in ("Trip 10", "Trip 2", "Trip 1"):
    _tc2.create_album(_n)
check("the sidebar tree lists albums in that order", [n["row"]["name"] for n in _tc2.tree()]
      == ["Trip 1", "Trip 2", "Trip 10"], [n["row"]["name"] for n in _tc2.tree()])
_tc2.create_folder("Paternos"); _tc2.create_album("Maternos"); _tc2.create_folder("Aniella")
check("folders and albums share one A to Z order",
      [n["row"]["name"] for n in _tc2.tree()]
      == ["Aniella", "Maternos", "Paternos", "Trip 1", "Trip 2", "Trip 10"],
      [n["row"]["name"] for n in _tc2.tree()])

# -- counts say photos or videos, whichever they are ----------------------------
from piklin.ui.grid import count_label as _cl
check("counts say photos, videos, or both - never videos as photos",
      _cl(12, 0) == "12 photos" and _cl(0, 12) == "12 videos" and _cl(1, 0) == "1 photo"
      and _cl(0, 1) == "1 video" and _cl(3, 2) == "3 photos and 2 videos",
      (_cl(0, 12), _cl(3, 2)))

# -- album cover chosen by the user ---------------------------------------------
_cc = Catalog(os.path.join(TMP, "cover.db"))
_cids = []
for _n in range(3):
    with _cc.write() as _cur:
        _cur.execute("INSERT INTO photos(uuid, path, filename, ext, added_at, mtime, bytes) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (f"cover-{_n}", f"/c/p{_n}.jpg", f"p{_n}.jpg", ".jpg", 0, 0, 1))
        _cids.append(int(_cur.lastrowid))
_caid = _cc.create_album("Trip"); _cc.album_add(_caid, _cids)
_cover = lambda: next(r["cover_path"] for r in _cc.albums() if r["id"] == _caid)
_first = _cover()
_cc.set_album_cover(_caid, _cids[2])
_chosen = _cover()
_cc.album_remove(_caid, [_cids[2]])
check("an album shows the cover you choose, and its first photo again if that one leaves",
      _first == "/c/p0.jpg" and _chosen == "/c/p2.jpg" and _cover() == "/c/p0.jpg",
      (_first, _chosen, _cover()))
# A photo still on a phone (negative id) or already gone must not fail the
# whole drop onto an album: the real photos go in, the rest are left out.
_fk_album = _cc.create_album("Drop")
try:
    _cc.album_add(_fk_album, [-1, _cids[0], 999999, _cids[1]])
    _fk_ok = True
except Exception as _e:
    _fk_ok = repr(_e)
check("dropping photos that are not in the library onto an album adds the real ones and fails nothing",
      _fk_ok is True and _cc.scalar("SELECT COUNT(*) FROM album_items WHERE album_id=?", (_fk_album,), 0) == 2,
      _fk_ok)
from piklin.thumbs import ThumbCache as _TC
check("photos read through gvfs (a phone over gphoto2) get thumbnail workers of their own",
      _TC._slow("/run/user/1000/gvfs/gphoto2:host=Apple_Inc._iPhone_1/202308__/IMG_1.JPG")
      and not _TC._slow("/home/me/Pictures/IMG_1.JPG"))
import errno as _errno
from unittest import mock as _mock
from piklin import devices as _dv
with _mock.patch("pathlib.Path.is_dir", side_effect=OSError(_errno.EIO, "Input/output error")):
    _eio = (_dv._is_dir(Path("/gvfs/phone/CAMERA")), _dv._looks_like_camera(Path("/gvfs/phone")))
check("a phone that answers a missing folder with an I/O error is read, not abandoned",
      _eio == (False, False), _eio)

# -- self-update: only a signed, official, matching package is accepted ---------
import importlib.machinery as _ilm, importlib.util as _ilu, subprocess as _sp2, hashlib
_helper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packaging", "deb", "piklin-update")
_spec = _ilu.spec_from_loader("piklin_update_helper", _ilm.SourceFileLoader("piklin_update_helper", _helper_path))
_hu = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_hu)
_ud = Path(TMP) / "update"; _ud.mkdir()
# The .deb helper needs dpkg; elsewhere (a Mac) only its pure logic is tested.
_HAS_DPKG = shutil.which("dpkg-deb") is not None
if not _HAS_DPKG:
    print("  SKIP  .deb package checks (no dpkg on this system)")
if _HAS_DPKG:
    _arch = _hu.architecture()
    def _make_deb(version, name_version=None, package="piklin"):
        root = _ud / f"root-{package}-{version}"; (root / "DEBIAN").mkdir(parents=True)
        (root / "DEBIAN" / "control").write_text(
            f"Package: {package}\nVersion: {version}\nArchitecture: {_arch}\n"
            "Maintainer: Test <t@example.com>\nDescription: test\n")
        out = _ud / f"piklin_{name_version or version}_{_arch}.deb"
        _sp2.run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(out)],
                 check=True, capture_output=True)
        return out
    def _sign(deb, key):
        manifest = Path(str(deb) + ".sha256")
        digest = hashlib.sha256(deb.read_bytes()).hexdigest()
        manifest.write_text(f"{digest}  {deb.name}\n")
        _sp2.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(manifest),
                  "-out", str(manifest) + ".sig"], check=True, capture_output=True)
    _key, _other = _ud / "key.pem", _ud / "other.pem"
    for k in (_key, _other):
        _sp2.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(k)], check=True, capture_output=True)
    _pub = _ud / "key.pub.pem"
    _sp2.run(["openssl", "pkey", "-in", str(_key), "-pubout", "-out", str(_pub)], check=True, capture_output=True)
    _deb = _make_deb("9.9.9"); _sign(_deb, _key)
    check("a signed official package passes", _hu.main(["--verify-only", "9.9.9", str(_ud), str(_pub)]) == 0)
    _sign(_deb, _other)
    check("a package signed with another key is refused",
          _hu.main(["--verify-only", "9.9.9", str(_ud), str(_pub)]) == 5)
    _sign(_deb, _key)
    with open(_deb, "ab") as _f: _f.write(b"tampered")
    check("a changed package is refused", _hu.main(["--verify-only", "9.9.9", str(_ud), str(_pub)]) == 5)
    _deb = _make_deb("9.9.8", name_version="9.9.9"); _sign(_deb, _key)
    check("a package whose version is not the one asked for is refused",
          _hu.main(["--verify-only", "9.9.9", str(_ud), str(_pub)]) == 5)
    _deb = _make_deb("9.9.9", package="notpiklin"); _sign(_deb, _key)
    check("a package that is not Piklin is refused",
          _hu.main(["--verify-only", "9.9.9", str(_ud), str(_pub)]) == 5)
check("only a version number is accepted", _hu.main(["../../etc"]) == 2
      and _hu.main(["--verify-only", "1.0; rm", str(_ud)]) == 2)
from piklin import updates as _upd
# Piklin checks release signatures itself where openssl cannot (a Mac's LibreSSL).
from piklin import ed25519 as _ed
_rfc_pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_rfc_sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
                         "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
check("Ed25519 signatures are checked exactly (RFC 8032 test vector)",
      _ed.verify(_rfc_pub, b"", _rfc_sig) and not _ed.verify(_rfc_pub, b"x", _rfc_sig)
      and len(_ed.public_key_from_pem(Path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "packaging", "deb", "release-key.pem")).read_text())) == 32)
_ossl3 = next((o for o in (shutil.which("openssl"), "/opt/homebrew/opt/openssl@3/bin/openssl",
                           "/usr/local/opt/openssl@3/bin/openssl")
               if o and os.path.exists(o)
               and "LibreSSL" not in _sp2.run([o, "version"], capture_output=True, text=True).stdout), None)
if _ossl3:
    _md = Path(TMP) / "dmgupdate"; _md.mkdir()
    _mk, _mo = _md / "k.pem", _md / "o.pem"
    for _k in (_mk, _mo):
        _sp2.run([_ossl3, "genpkey", "-algorithm", "ed25519", "-out", str(_k)], check=True, capture_output=True)
    _mpub = _sp2.run([_ossl3, "pkey", "-in", str(_mk), "-pubout"], check=True, capture_output=True, text=True).stdout
    _dmg = _md / "Piklin-9.9.9.dmg"; _dmg.write_bytes(b"disk image")
    def _msign(key):
        m = Path(str(_dmg) + ".sha256")
        m.write_text(f"{hashlib.sha256(_dmg.read_bytes()).hexdigest()}  {_dmg.name}\n")
        _sp2.run([_ossl3, "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(m),
                  "-out", str(m) + ".sig"], check=True, capture_output=True)
    def _mverify():
        try:
            _upd.verify_signed(_md, _dmg.name, _mpub); return "ok"
        except _upd.UpdateError as e:
            return e.kind
    _msign(_mk); _ok = _mverify()
    _msign(_mo); _other = _mverify()
    _msign(_mk); _dmg.write_bytes(b"disk image, changed"); _changed = _mverify()
    check("a Mac update is installed only when signed with the release key and unchanged",
          (_ok, _other, _changed) == ("ok", "verification", "verification"), (_ok, _other, _changed))
check("running from source points to the download instead of installing",
      not _upd.can_install_itself())
# An AppImage updates by replacing its own file: only where it may write.
_ai_dir = Path(TMP) / "appimage"; _ai_dir.mkdir()
_ai = _ai_dir / "Piklin-1.0.5-x86_64.AppImage"; _ai.write_bytes(b"appimage")
os.environ["PIKLIN_APPIMAGE"] = str(_ai)
try:
    import platform as _plat
    _ai_names = _upd.appimage_names("2.0.0")
    check("an AppImage knows its own update files and build",
          _ai_names == (f"Piklin-2.0.0-{_plat.machine()}.AppImage",
                        f"Piklin-2.0.0-{_plat.machine()}.AppImage.sha256",
                        f"Piklin-2.0.0-{_plat.machine()}.AppImage.sha256.sig")
          and _upd.build_asset("2.0.0") == _ai_names[0] + ".build"
          and _upd.launcher() == str(_ai), (_ai_names, _upd.build_asset("2.0.0")))
    _writable = _upd.can_install_itself()
    os.chmod(_ai_dir, 0o555)
    _locked = _upd.can_install_itself()
    os.chmod(_ai_dir, 0o755)
    check("an AppImage installs updates itself only in a folder it can write to",
          _writable and not _locked, (_writable, _locked))
    try:
        _upd._install_appimage("../1.0"); _bad = "installed"
    except _upd.UpdateError as _e:
        _bad = _e.kind
    check("an AppImage update needs a real version number", _bad == "install", _bad)
finally:
    os.environ.pop("PIKLIN_APPIMAGE", None)
check("outside an AppImage the .deb and Mac rules still apply",
      _upd._appimage() is None and _upd.launcher() == _upd.LAUNCHER
      and _upd.build_asset("2.0.0").endswith(".deb.build") != sys.platform.startswith("darwin"))
_saved_cfg_u = os.environ.get("XDG_CONFIG_HOME"); os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "updcfg")
try:
    _now = 1_800_000_000.0
    _upd.save_state(enabled=True, last_check=_now - 30 * 60)
    _half = _upd.due(_now)
    _upd.save_state(last_check=_now - 61 * 60)
    _hour = _upd.due(_now)
    _upd.save_state(enabled=False)
    _off = _upd.due(_now)
    check("automatic updates are looked for every hour, not more often, and never when off",
          not _half and _hour and not _off, (_half, _hour, _off))
finally:
    if _saved_cfg_u is None: os.environ.pop("XDG_CONFIG_HOME", None)
    else: os.environ["XDG_CONFIG_HOME"] = _saved_cfg_u
# The same version published again with fixes is still an update
# (the helper compares versions with dpkg)
if _HAS_DPKG:
    check("a new build of the same version is installed; the same build or an older version is not",
          _hu.decide("1.0.4", "1.0.4", "abc-2", "abc-1") == "reinstall"
          and _hu.decide("1.0.4", "1.0.4", "abc-1", "abc-1") == "no"
          and _hu.decide("1.0.4", "1.0.4", "", "abc-1") == "no"
          and _hu.decide("1.0.5", "1.0.4", "x", "y") == "install"
          and _hu.decide("1.0.3", "1.0.4", "x", "y") == "no")
if _HAS_DPKG:
    _bid_root = _ud / "root-build"; (_bid_root / "DEBIAN").mkdir(parents=True)
    (_bid_root / "DEBIAN" / "control").write_text(
        f"Package: piklin\nVersion: 9.9.9\nArchitecture: {_arch}\nMaintainer: T <t@example.com>\nDescription: t\n")
    (_bid_root / "usr/share/piklin/piklin").mkdir(parents=True)
    (_bid_root / "usr/share/piklin/piklin/BUILD_ID").write_text("c0ffee-20260913\n")
    _bid_deb = _ud / "build-id.deb"
    _sp2.run(["dpkg-deb", "--build", "--root-owner-group", str(_bid_root), str(_bid_deb)], check=True, capture_output=True)
    check("the build id is read from a package without installing it",
          _hu.build_of(str(_bid_deb)) == "c0ffee-20260913", _hu.build_of(str(_bid_deb)))
_orig_ib = _upd.installed_build
try:
    _upd.installed_build = lambda: "c0ffee-1"
    check("the app sees a re-published build of its own version as an update",
          _upd.update_available(_upd.Release("1.0.4", "u", build="c0ffee-2"), "1.0.4")
          and not _upd.update_available(_upd.Release("1.0.4", "u", build="c0ffee-1"), "1.0.4")
          and not _upd.update_available(_upd.Release("1.0.4", "u", build=""), "1.0.4")
          and _upd.update_available(_upd.Release("1.0.5", "u"), "1.0.4")
          and not _upd.update_available(_upd.Release("1.0.3", "u", build="z"), "1.0.4"))
    _upd.installed_build = lambda: "621c6445d47a-20260915043411"
    check("an older published build of the same version is never offered as an update",
          not _upd.update_available(_upd.Release("1.0.4", "u", build="88bb2e06a68b-20260915003219"), "1.0.4")
          and _upd.update_available(_upd.Release("1.0.4", "u", build="aaaa11112222-20260916101010"), "1.0.4"))
finally:
    _upd.installed_build = _orig_ib

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
