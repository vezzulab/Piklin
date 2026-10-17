"""Everything a photo or a video says about itself.

The library keeps what it needs to browse and search (date, camera, lens,
exposure, place). This reads the rest, straight from the file and only when
asked for: every EXIF tag with its name, the GPS block, IPTC and XMP (titles,
keywords, copyright written by other programs), the text chunks of a PNG, and
for a video its container, every stream and the tags it carries.

``read_all(path)`` returns ``[(group, [(name, value), ...]), ...]`` in the order
they should be shown; nothing is changed in the file.
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from .i18n import _

MAX_VALUE = 600          # a value longer than this is cut: the file may carry a megabyte of notes


def _text(v) -> str:
    """One value, written the way a person reads it."""
    try:
        from PIL.TiffImagePlugin import IFDRational
    except Exception:                                  # pragma: no cover
        IFDRational = ()
    if isinstance(v, IFDRational):
        try:
            f = float(v)
            return f"{f:.6g}" if abs(f) >= 1 or f == 0 else (f"1/{round(1 / f)}" if round(1 / f) >= 1 and abs(1 / f - round(1 / f)) < 0.02 else f"{f:.6g}")
        except Exception:
            return str(v)
    if isinstance(v, bytes):
        raw = v.rstrip(b"\x00")
        for enc in ("utf-8", "latin-1"):
            try:
                s = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            if s and all(c.isprintable() or c in "\n\t" for c in s):
                return s[:MAX_VALUE]
        return _("{n} bytes of data").format(n=f"{len(v):,}")
    if isinstance(v, (tuple, list)):
        return ", ".join(_text(x) for x in v)[:MAX_VALUE]
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_text(x)}" for k, x in v.items())[:MAX_VALUE]
    s = str(v).replace("\x00", "").strip()
    return s[:MAX_VALUE] + ("…" if len(s) > MAX_VALUE else "")


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:,} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def _when(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""



# -- values said in words ------------------------------------------------------
_ENUM = {
    "Orientation": {1: "Normal", 2: "Mirrored", 3: "Upside down", 4: "Mirrored, upside down", 5: "Mirrored, turned right",
                    6: "Turned right", 7: "Mirrored, turned left", 8: "Turned left"},
    "ExposureProgram": {0: "Not defined", 1: "Manual", 2: "Normal", 3: "Aperture priority", 4: "Shutter priority",
                        5: "Creative", 6: "Action", 7: "Portrait", 8: "Landscape"},
    "MeteringMode": {0: "Unknown", 1: "Average", 2: "Centre-weighted", 3: "Spot", 4: "Multi-spot", 5: "Pattern",
                     6: "Partial", 255: "Other"},
    "WhiteBalance": {0: "Auto", 1: "Manual"},
    "ExposureMode": {0: "Auto", 1: "Manual", 2: "Auto bracket"},
    "SceneCaptureType": {0: "Standard", 1: "Landscape", 2: "Portrait", 3: "Night"},
    "ColorSpace": {1: "sRGB", 2: "Adobe RGB", 65535: "Uncalibrated"},
    "ResolutionUnit": {1: "None", 2: "Inches", 3: "Centimetres"},
    "SensingMethod": {1: "Not defined", 2: "One-chip colour", 3: "Two-chip colour", 4: "Three-chip colour"},
    "LightSource": {0: "Unknown", 1: "Daylight", 2: "Fluorescent", 3: "Tungsten", 4: "Flash", 9: "Fine weather",
                    10: "Cloudy", 11: "Shade", 17: "Standard light A", 18: "Standard light B", 19: "Standard light C",
                    255: "Other"},
    "GainControl": {0: "None", 1: "Low gain up", 2: "High gain up", 3: "Low gain down", 4: "High gain down"},
    "Contrast": {0: "Normal", 1: "Soft", 2: "Hard"}, "Saturation": {0: "Normal", 1: "Low", 2: "High"},
    "Sharpness": {0: "Normal", 1: "Soft", 2: "Hard"},
    "CustomRendered": {0: "Normal", 1: "Custom"},
    "FileSource": {3: "Digital camera"}, "YCbCrPositioning": {1: "Centred", 2: "Co-sited"},
    "GPSAltitudeRef": {0: "Above sea level", 1: "Below sea level"},
}
_FLASH = {0x0: "Did not fire", 0x1: "Fired", 0x5: "Fired, no return light", 0x7: "Fired, return light detected",
          0x8: "Did not fire, compulsory flash", 0x9: "Fired, compulsory", 0xD: "Fired, compulsory, no return",
          0xF: "Fired, compulsory, return detected", 0x10: "Did not fire, compulsory off", 0x14: "Did not fire, off, no return",
          0x18: "Did not fire, auto", 0x19: "Fired, auto", 0x1D: "Fired, auto, no return", 0x1F: "Fired, auto, return detected",
          0x20: "No flash function", 0x30: "No flash function", 0x41: "Fired, red-eye reduction",
          0x45: "Fired, red-eye, no return", 0x47: "Fired, red-eye, return detected", 0x49: "Fired, compulsory, red-eye",
          0x59: "Fired, auto, red-eye", 0x5D: "Fired, auto, red-eye, no return", 0x5F: "Fired, auto, red-eye, return detected"}


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pretty(name: str, value, shown: str) -> str:
    """A tag's value with its unit or its meaning, where there is one."""
    n = _number(value)
    if name in _ENUM and n is not None and int(n) == n:
        return _ENUM[name].get(int(n), shown)
    if name == "Flash" and n is not None:
        return _FLASH.get(int(n), shown)
    if n is None:
        return shown
    if name == "ExposureTime" and n > 0:
        return f"1/{round(1 / n)} s" if n < 1 else f"{n:g} s"
    if name in ("FNumber", "MaxApertureValue") and name == "FNumber":
        return f"f/{n:.1f}".replace(".0", "") if n else shown
    if name in ("FocalLength", "FocalLengthIn35mmFilm"):
        return f"{n:g} mm"
    if name in ("ISOSpeedRatings", "PhotographicSensitivity", "ISOSpeed"):
        return f"ISO {int(n)}"
    if name == "GPSAltitude":
        return f"{n:g} m"
    if name == "ExposureBiasValue":
        return f"{n:+.1f} EV".replace("+0.0", "0.0")
    if name in ("XResolution", "YResolution"):
        return f"{n:g}"
    if name == "SubjectDistance":
        return f"{n:g} m"
    return shown


# -- photos -----------------------------------------------------------------
_SKIP = {0x8769, 0x8825, 0xA005}          # pointers to the blocks that are read apart
_IPTC = {(1, 90): "Coded character set", (2, 5): "Title", (2, 15): "Category", (2, 20): "Supplemental categories",
         (2, 25): "Keywords", (2, 40): "Special instructions", (2, 55): "Date created", (2, 60): "Time created",
         (2, 80): "Creator", (2, 85): "Creator's job title", (2, 90): "City", (2, 92): "Sublocation",
         (2, 95): "Province or state", (2, 100): "Country code", (2, 101): "Country", (2, 105): "Headline",
         (2, 110): "Credit", (2, 115): "Source", (2, 116): "Copyright", (2, 120): "Caption", (2, 122): "Caption writer"}


def _exif_groups(im) -> list[tuple[str, list[tuple[str, str]]]]:
    from PIL import ExifTags
    out = []
    try:
        exif = im.getexif()
    except Exception:
        return out
    if not exif:
        return out

    def rows(table, ifd, skip=()):
        res = []
        for tag, value in ifd.items():
            if tag in skip:
                continue
            name = ExifTags.TAGS.get(tag) if table == "tags" else ExifTags.GPSTAGS.get(tag)
            name = name or f"0x{tag:04X}"
            if tag == 0x927C:                                  # the maker's own notes
                value = _("{n} bytes of the maker's own notes").format(n=f"{len(value):,}") \
                    if isinstance(value, bytes) else value
            res.append((re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name), _pretty(name, value, _text(value))))
        return res
    main = rows("tags", exif, _SKIP)
    if main:
        out.append((_("Camera and file (EXIF)"), main))
    for pointer, title, table in ((0x8769, _("Exposure (EXIF)"), "tags"), (0xA005, _("Interoperability"), "tags")):
        try:
            sub = exif.get_ifd(pointer)
        except Exception:
            sub = {}
        if pointer == 0xA005:
            try:
                sub = exif.get_ifd(0x8769).get(0xA005) and exif.get_ifd(0xA005) or {}
            except Exception:
                sub = {}
        r = rows(table, sub, _SKIP)
        if r:
            out.append((title, r))
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        gps = {}
    if gps:
        r = rows("gps", gps)
        from .imageio import _gps
        try:
            lat, lon = _gps(exif)
            if lat is not None and lon is not None:
                r.insert(0, (_("Position"), f"{lat:.6f}, {lon:.6f}"))
        except Exception:
            pass
        out.append((_("GPS"), r))
    return out


def _iptc(im) -> list[tuple[str, str]]:
    try:
        from PIL import IptcImagePlugin
        info = IptcImagePlugin.getiptcinfo(im)
    except Exception:
        return []
    rows = []
    for key, value in (info or {}).items():
        if isinstance(value, list):
            value = [v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in value]
        rows.append((_IPTC.get(key, f"{key[0]}:{key[1]}"), _text(value)))
    return rows


def _xmp_packet(path: Path, im) -> bytes | None:
    pkt = None
    try:
        pkt = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
    except Exception:
        pass
    if isinstance(pkt, str):
        pkt = pkt.encode("utf-8", "replace")
    if pkt:
        return pkt
    try:
        with open(path, "rb") as f:
            head = f.read(2_000_000)
    except OSError:
        return None
    a = head.find(b"<x:xmpmeta")
    if a < 0:
        return None
    b = head.find(b"</x:xmpmeta>", a)
    return head[a:b + 12] if b > 0 else None


_ATTR = re.compile(r'\s([A-Za-z0-9]+:[A-Za-z0-9]+)="([^"]*)"')
_LIST = re.compile(r"<([A-Za-z0-9]+:[A-Za-z0-9]+)>\s*<rdf:(?:Bag|Seq|Alt)>(.*?)</rdf:(?:Bag|Seq|Alt)>\s*</\1>", re.S)
_ITEM = re.compile(r"<rdf:li[^>]*>(.*?)</rdf:li>", re.S)
_LEAF = re.compile(r"<([A-Za-z0-9]+:[A-Za-z0-9]+)>([^<]+)</\1>")


def _xmp(packet: bytes) -> list[tuple[str, str]]:
    """The properties of an XMP packet, read with patterns (never an XML parser:
    a file can come from anywhere)."""
    text = packet.decode("utf-8", "replace")
    seen, rows = set(), []

    def add(name, value):
        value = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
        if value and (name, value) not in seen and not name.startswith(("xmlns", "rdf:", "xml:")):
            seen.add((name, value))
            rows.append((name, _text(value)))
    for m in _LIST.finditer(text):
        add(m.group(1), ", ".join(re.sub(r"<[^>]+>", "", i).strip() for i in _ITEM.findall(m.group(2))))
    for m in _ATTR.finditer(text):
        add(m.group(1), m.group(2))
    for m in _LEAF.finditer(text):
        add(m.group(1), m.group(2))
    return rows


def _photo(path: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    from . import imageio
    groups = []
    try:
        st = path.stat()
    except OSError:
        return groups
    file_rows = [(_("Name"), path.name), (_("Folder"), str(path.parent)), (_("Size"), _size(st.st_size)),
                 (_("Modified"), _when(st.st_mtime))]
    birth = getattr(st, "st_birthtime", None)
    if birth:
        file_rows.append((_("Created"), _when(birth)))
    im = None
    try:
        ext = path.suffix.lower()
        if ext in getattr(imageio, "RAW_EXT", ()) and getattr(imageio, "_RAWPY", None):
            import rawpy
            with rawpy.imread(str(path)) as raw:
                s = raw.sizes
                raw_rows = [(_("Sensor size"), f"{s.raw_width} × {s.raw_height}"),
                            (_("Image size"), f"{s.width} × {s.height}"),
                            (_("Colours"), str(raw.num_colors)),
                            (_("Colour filter"), _text(raw.color_desc))]
                try:
                    raw_rows.append((_("Camera white balance"), _text([round(x, 3) for x in raw.camera_whitebalance])))
                except Exception:
                    pass
                groups.append((_("RAW"), raw_rows))
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        from PIL import Image
                        import io
                        im = Image.open(io.BytesIO(thumb.data))
                except Exception:
                    im = None
        if im is None:
            im = imageio._open(path)
    except Exception:
        im = None
    if im is not None:
        try:
            file_rows += [(_("Format"), str(im.format or path.suffix.lstrip(".").upper())),
                          (_("Dimensions"), f"{im.width} × {im.height}"), (_("Colour mode"), str(im.mode))]
            dpi = im.info.get("dpi")
            if dpi:
                file_rows.append((_("Resolution"), f"{_text(dpi)} dpi"))
            icc = im.info.get("icc_profile")
            if icc:
                try:
                    from PIL import ImageCms
                    import io
                    prof = ImageCms.getOpenProfile(io.BytesIO(icc))
                    file_rows.append((_("Colour profile"), ImageCms.getProfileDescription(prof).strip()))
                except Exception:
                    file_rows.append((_("Colour profile"), _("{n} bytes").format(n=f"{len(icc):,}")))
        except Exception:
            pass
    groups.insert(0, (_("File"), file_rows))
    if im is not None:
        groups += _exif_groups(im)
        iptc = _iptc(im)
        if iptc:
            groups.append((_("IPTC"), iptc))
        packet = _xmp_packet(path, im)
        if packet:
            x = _xmp(packet)
            if x:
                groups.append((_("XMP"), x))
        skip = {"exif", "icc_profile", "xmp", "dpi", "photoshop", "XML:com.adobe.xmp", "transparency", "adobe", "adobe_transform",
                "jfif", "jfif_version", "jfif_unit", "jfif_density", "progressive", "progression", "icc_profile"}
        text_rows = [(str(k), _text(v)) for k, v in im.info.items()
                     if k not in skip and isinstance(v, (str, bytes, int, float)) and not isinstance(v, bool)]
        if text_rows:
            groups.append((_("Text in the file"), text_rows))
        try:
            im.close()
        except Exception:
            pass
    return groups


# -- videos -------------------------------------------------------------------
def _duration(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _bitrate(b) -> str:
    return f"{b / 1000:,.0f} kb/s" if b else ""


def _video(path: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    groups = []
    try:
        st = path.stat()
    except OSError:
        return groups
    groups.append((_("File"), [(_("Name"), path.name), (_("Folder"), str(path.parent)), (_("Size"), _size(st.st_size)),
                               (_("Modified"), _when(st.st_mtime))]))
    try:
        import av
        c = av.open(str(path))
    except Exception:
        return groups
    try:
        fmt = [(_("Container"), c.format.long_name or c.format.name)]
        if c.duration:
            fmt.append((_("Length"), _duration(c.duration / 1_000_000)))
        if c.bit_rate:
            fmt.append((_("Overall bit rate"), _bitrate(c.bit_rate)))
        groups[0][1].extend(fmt)
        tags = [(k, _text(v)) for k, v in (c.metadata or {}).items()]
        if tags:
            groups.append((_("Tags in the file"), tags))
        n = {"video": 0, "audio": 0}
        for s in c.streams:
            kind = s.type
            if kind not in ("video", "audio", "subtitle", "data"):
                continue
            n[kind] = n.get(kind, 0) + 1
            title = {"video": _("Video"), "audio": _("Audio"), "subtitle": _("Subtitles"), "data": _("Data")}[kind]
            if n[kind] > 1:
                title += f" {n[kind]}"
            cc = s.codec_context
            rows = [(_("Codec"), (cc.codec.long_name if cc and cc.codec else "") or (cc.name if cc else ""))]
            try:
                if getattr(cc, "profile", None):
                    rows.append((_("Profile"), str(cc.profile)))
            except Exception:
                pass
            if kind == "video":
                rows.append((_("Dimensions"), f"{cc.width} × {cc.height}"))
                fps = s.average_rate or s.guessed_rate
                if fps:
                    rows.append((_("Frame rate"), f"{float(fps):.3f} fps"))
                if cc.pix_fmt:
                    rows.append((_("Pixel format"), str(cc.pix_fmt)))
                for label, attr, names in (
                        (_("Colour range"), "color_range", {1: _("Limited (TV)"), 2: _("Full (PC)")}),
                        (_("Colour space"), "colorspace", {0: "RGB", 1: "BT.709", 4: "FCC", 5: "BT.601 (PAL)", 6: "BT.601 (NTSC)",
                                                          7: "SMPTE 240M", 9: "BT.2020", 10: "BT.2020"}),
                        (_("Colour primaries"), "color_primaries", {1: "BT.709", 4: "BT.470M", 5: "BT.601 (PAL)", 6: "BT.601 (NTSC)",
                                                                   9: "BT.2020", 11: "DCI-P3", 12: "Display P3"}),
                        (_("Transfer"), "color_trc", {1: "BT.709", 6: "BT.601", 13: "sRGB", 16: "PQ (HDR10)", 18: "HLG"})):
                    v = getattr(cc, attr, None)
                    v = getattr(v, "value", v)
                    if isinstance(v, int) and v in names:
                        rows.append((label, names[v]))
                if s.frames:
                    rows.append((_("Frames"), f"{s.frames:,}"))
            elif kind == "audio":
                rows.append((_("Sample rate"), f"{cc.sample_rate:,} Hz"))
                rows.append((_("Channels"), f"{cc.channels} ({cc.layout.name})" if getattr(cc, "layout", None) else str(cc.channels)))
            br = cc.bit_rate if cc else 0
            if br:
                rows.append((_("Bit rate"), _bitrate(br)))
            if s.duration and s.time_base:
                rows.append((_("Length"), _duration(float(s.duration * s.time_base))))
            for k, v in (s.metadata or {}).items():
                rows.append((k, _text(v)))
            groups.append((title, rows))
    finally:
        try:
            c.close()
        except Exception:
            pass
    return groups


def read_all(path) -> list[tuple[str, list[tuple[str, str]]]]:
    """Every group of metadata the file carries, in the order to show them."""
    p = Path(path)
    from .video import VIDEO_EXT
    try:
        groups = _video(p) if p.suffix.lower() in VIDEO_EXT else _photo(p)
    except Exception:
        groups = []
    return [(g, [(n, v) for n, v in rows if v != ""]) for g, rows in groups if rows]


def as_text(groups) -> str:
    """The same, as text to copy."""
    out = []
    for group, rows in groups:
        out.append(f"[{group}]")
        out += [f"{n}: {v}" for n, v in rows]
        out.append("")
    return "\n".join(out).strip()
