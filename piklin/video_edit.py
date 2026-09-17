"""Non-destructive video editing: what was changed, and how to render it.

An edit is a small description saved beside the video in Edits/, the same
place a photo's adjustments go. The video file itself is never written.

What an edit can say:

* trim the start and the end
* cut any number of pieces out of the middle
* rotate in quarter turns and mirror
* crop the picture
* change the speed (sound keeps its pitch)
* mute
* choose the poster frame

Rendering happens only on export - or when a frame is saved as a photo -
through the FFmpeg inside Piklin, with its filter graphs doing the picture
work in C.
"""
from __future__ import annotations

import fractions
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from .i18n import _, N_

FORMAT = "piklin-video-edit"
VERSION = 1
_MIN_PIECE = 1.0 / 60.0         # seconds; shorter kept pieces are dropped
GIF_MAX_SECONDS = 30.0


@dataclass
class VideoEdit:
    duration: float = 0.0                       # length of the source
    start: float = 0.0
    end: float | None = None                    # None: to the end
    cuts: list[tuple[float, float]] = field(default_factory=list)
    rotate: int = 0                             # clockwise quarter turns, degrees
    flip: bool = False                          # mirrored left to right
    crop: tuple[float, float, float, float] | None = None   # x, y, w, h (0..1)
    speed: float = 1.0
    mute: bool = False
    poster: float | None = None

    # -- time ----------------------------------------------------------------
    @property
    def stop(self) -> float:
        end = self.end if self.end is not None else self.duration
        return max(self.start, min(end, self.duration) if self.duration else end)

    def segments(self) -> list[tuple[float, float]]:
        """The pieces kept, in source time, in order."""
        pieces = [(self.start, self.stop)]
        for a, b in sorted((min(c), max(c)) for c in self.cuts):
            nxt = []
            for s, e in pieces:
                if b <= s or a >= e:
                    nxt.append((s, e))
                    continue
                if a > s:
                    nxt.append((s, a))
                if b < e:
                    nxt.append((b, e))
            pieces = nxt
        return [(s, e) for s, e in pieces if e - s >= _MIN_PIECE]

    def output_duration(self) -> float:
        return sum(e - s for s, e in self.segments()) / (self.speed or 1.0)

    def next_kept(self, t: float) -> float | None:
        """``t`` itself when it is kept, else where playing resumes
        (None past the end)."""
        for s, e in self.segments():
            if t < s:
                return s
            if t < e:
                return t
        return None

    # -- bookkeeping ---------------------------------------------------------
    def changes(self) -> int:
        n = 0
        if self.start > 0.0005 or (self.end is not None and self.duration
                                   and self.end < self.duration - 0.0005):
            n += 1
        n += len(self.cuts)
        n += bool(self.rotate % 360) + bool(self.flip) + bool(self.crop)
        n += abs(self.speed - 1.0) > 1e-6
        n += bool(self.mute)
        return n

    def is_identity(self) -> bool:
        return self.changes() == 0

    def to_dict(self, source: str | None = None) -> dict:
        return {
            "format": FORMAT, "version": VERSION, "source": source,
            "duration": self.duration, "start": self.start, "end": self.end,
            "cuts": [list(c) for c in self.cuts], "rotate": self.rotate % 360,
            "flip": self.flip, "crop": list(self.crop) if self.crop else None,
            "speed": self.speed, "mute": self.mute, "poster": self.poster,
            # read by --rebuild-index to mark the video edited again
            "changes": self.changes(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VideoEdit":
        crop = d.get("crop")
        return cls(
            duration=float(d.get("duration") or 0.0),
            start=float(d.get("start") or 0.0),
            end=(float(d["end"]) if d.get("end") is not None else None),
            cuts=[(float(a), float(b)) for a, b in (d.get("cuts") or [])],
            rotate=int(d.get("rotate") or 0) % 360,
            flip=bool(d.get("flip")),
            crop=tuple(float(v) for v in crop) if crop else None,
            speed=float(d.get("speed") or 1.0),
            mute=bool(d.get("mute")),
            poster=(float(d["poster"]) if d.get("poster") is not None else None),
        )


# -- sidecar -------------------------------------------------------------------
def load(library, path, duration: float = 0.0) -> VideoEdit:
    sidecar = library.edit_sidecar(path)
    try:
        data = json.loads(sidecar.read_text())
        if data.get("format") == FORMAT:
            edit = VideoEdit.from_dict(data)
            if duration and not edit.duration:
                edit.duration = duration
            return edit
    except (OSError, ValueError, TypeError):
        pass
    return VideoEdit(duration=duration)


def save(library, path, edit: VideoEdit, catalog=None, photo_id=None) -> None:
    sidecar = library.edit_sidecar(path)
    if edit.is_identity() and edit.poster is None:
        sidecar.unlink(missing_ok=True)
    else:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp = sidecar.with_name(sidecar.name + ".tmp")
        tmp.write_text(json.dumps(edit.to_dict(str(path)), indent=2))
        tmp.replace(sidecar)
    if catalog is not None and photo_id is not None:
        catalog.note_edit(photo_id, edit.changes())


# -- picture geometry ----------------------------------------------------------
def _even(n: float) -> int:
    return max(2, int(round(n)) // 2 * 2)


def geometry(width: int, height: int, source_rotation: int, edit: VideoEdit,
             max_side: int | None = None) -> dict:
    """Turns, crop rectangle and output size for a source picture."""
    turns = (source_rotation + edit.rotate) % 360
    disp_w, disp_h = (height, width) if turns in (90, 270) else (width, height)
    if edit.crop:
        x, y, w, h = edit.crop
        cw, ch = _even(max(2, w * disp_w)), _even(max(2, h * disp_h))
        cx = min(max(0, int(x * disp_w)), disp_w - cw)
        cy = min(max(0, int(y * disp_h)), disp_h - ch)
    else:
        cx = cy = 0
        cw, ch = _even(disp_w), _even(disp_h)
    scale = 1.0
    if max_side and max(cw, ch) > max_side:
        scale = max_side / max(cw, ch)
    return {"turns": turns, "crop": (cx, cy, cw, ch),
            "out": (_even(cw * scale), _even(ch * scale))}


def _stored_as_shown(stream) -> tuple[int, int]:
    """The stream's picture size as it is meant to be seen: its stored size
    with non-square pixels made square (see video.display_size)."""
    from .video import _av_pixel_aspect, display_size
    cc = stream.codec_context
    return display_size(cc.width, cc.height, _av_pixel_aspect(stream))


def _video_graph(av, stream, geo, pix_fmt):
    g = av.filter.Graph()
    chain = [g.add_buffer(template=stream)]
    shown = _stored_as_shown(stream)
    if shown != (stream.codec_context.width, stream.codec_context.height):
        # square pixels first, so turning, cropping and scaling keep the shape
        chain += [g.add("scale", f"{shown[0]}:{shown[1]}:flags=bicubic"), g.add("setsar", "1")]
    turns = geo["turns"]
    if turns == 90:
        chain.append(g.add("transpose", "clock"))
    elif turns == 180:
        chain += [g.add("hflip"), g.add("vflip")]
    elif turns == 270:
        chain.append(g.add("transpose", "cclock"))
    return g, chain


def _finish_graph(av, g, chain, geo, edit, pix_fmt):
    if edit.flip:
        chain.append(g.add("hflip"))
    cx, cy, cw, ch = geo["crop"]
    chain.append(g.add("crop", f"{cw}:{ch}:{cx}:{cy}"))
    ow, oh = geo["out"]
    if (ow, oh) != (cw, ch):
        chain.append(g.add("scale", f"{ow}:{oh}:flags=bicubic"))
    chain.append(g.add("format", pix_fmt))
    chain.append(g.add("buffersink"))
    for a, b in zip(chain, chain[1:]):
        a.link_to(b)
    g.configure()
    return g


def _pull_all(av, graph) -> list:
    out = []
    while True:
        try:
            out.append(graph.pull())
        except (av.error.BlockingIOError, av.error.EOFError):
            return out


def _source_rotation(path) -> int:
    from .video import stream_info
    return int((stream_info(path) or {}).get("rotation") or 0)


def _threads() -> int:
    """Threads a video decoder or encoder may use: what the computer can
    spare, not every core (see system.work_budget)."""
    from . import system
    return system.work_budget("video")


# -- single frames ---------------------------------------------------------------
def frame_image(path, t: float, edit: VideoEdit | None = None,
                max_side: int | None = None):
    """The frame at ``t`` seconds as an RGB PIL image, with the edit's
    turns, mirror and crop applied."""
    import av
    edit = edit or VideoEdit()
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        v.codec_context.thread_count = _threads()
        geo = geometry(*_stored_as_shown(v), _source_rotation(path), edit, max_side)
        c.seek(int(max(0.0, t) / v.time_base), stream=v, backward=True)
        fps = float(v.average_rate or 30)
        chosen = None
        for frame in c.decode(v):
            if frame.time is None:
                continue
            chosen = frame
            if frame.time >= t - 0.5 / fps:
                break
        if chosen is None:
            raise OSError(f"no frame at {t:.2f}s in {path}")
        g, chain = _video_graph(av, v, geo, "rgb24")
        g = _finish_graph(av, g, chain, geo, edit, "rgb24")
        g.push(chosen)
        frames = _pull_all(av, g)
        return frames[0].to_image()


def save_frame_as_photo(library, path, t: float, edit: VideoEdit | None,
                        taken_at: float | None) -> Path:
    """Save the frame at ``t`` as a JPEG photo in the library's Originals,
    dated the moment it was filmed."""
    from PIL import Image
    img = frame_image(path, t, edit)
    when = (taken_at or os.path.getmtime(path)) + t
    day = datetime.fromtimestamp(when)
    folder = library.originals / day.strftime("%Y") / day.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    minutes, seconds = divmod(t, 60)
    base = f"{Path(path).stem} {int(minutes)}.{seconds:05.2f}"
    target = folder / f"{base}.jpg"
    n = 2
    while target.exists():
        target = folder / f"{base} {n}.jpg"
        n += 1
    exif = Image.Exif()
    stamp = day.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = stamp
    exif.get_ifd(0x8769)[0x9003] = stamp
    img.save(target, "JPEG", quality=95, exif=exif)
    return target


# -- export ----------------------------------------------------------------------
EXPORT_FORMATS = {
    "mp4": {"label": N_("MP4 (H.264)"), "suffix": ".mp4", "container": "mp4",
            "video": ("libopenh264", "h264", "mpeg4"), "audio": ("aac",)},
    "webm": {"label": N_("WebM (VP9)"), "suffix": ".webm", "container": "webm",
             "video": ("libvpx-vp9", "libvpx"), "audio": ("libopus",)},
    "gif": {"label": N_("Animated GIF"), "suffix": ".gif"},
}


def encoder_for(kind: str, fmt: str) -> str | None:
    import av
    for name in EXPORT_FORMATS[fmt].get(kind, ()):
        try:
            codec = av.codec.Codec(name, "w")
        except Exception:
            continue
        if codec.is_encoder:
            return name
    return None


def available_formats() -> list[str]:
    return [f for f in EXPORT_FORMATS if f == "gif" or encoder_for("video", f)]


def export(path, dst, edit: VideoEdit, fmt: str = "mp4",
           max_side: int | None = None, keep_location: bool = False,
           strip_metadata: bool = False, meta: dict | None = None,
           on_progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None,
           bit_rate: int | None = None) -> Path:
    """Render ``path`` with ``edit`` into ``dst``. Returns the file written.

    The output is written under a temporary name and only renamed into
    place once complete, so a cancelled or failed export leaves nothing
    half-written behind. ``bit_rate`` caps the video's bits per second (see
    videospace.py); the usual rate for its size is used when it is lower.
    """
    import av
    segs = edit.segments()
    if not segs:
        raise ValueError(_("Nothing is left to export: the whole video is cut out."))
    dst = Path(dst).with_suffix(EXPORT_FORMATS[fmt]["suffix"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.part")
    try:
        if fmt == "gif":
            _export_gif(av, path, tmp, edit, segs, max_side or 480, on_progress, cancel)
        else:
            _export_movie(av, path, tmp, edit, segs, fmt, max_side, keep_location,
                          strip_metadata, meta or {}, on_progress, cancel, bit_rate)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


class Cancelled(Exception):
    pass


def _export_movie(av, path, tmp, edit, segs, fmt, max_side, keep_location,
                  strip_metadata, meta, on_progress, cancel, bit_rate=None):
    spec = EXPORT_FORMATS[fmt]
    vcodec = encoder_for("video", fmt)
    if vcodec is None:
        raise RuntimeError(_("{format} can't be created on this computer.").format(
            format=_(spec["label"])))
    total = edit.output_duration() or 1.0
    inp = av.open(str(path))
    try:
        vin = inp.streams.video[0]
        vin.thread_type = "AUTO"
        vin.codec_context.thread_count = _threads()
        ain = inp.streams.audio[0] if inp.streams.audio and not edit.mute else None
        geo = geometry(*_stored_as_shown(vin), _source_rotation(path), edit, max_side)
        ow, oh = geo["out"]
        fps = min(float(vin.average_rate or 30), 60.0)
        rate = fractions.Fraction(fps).limit_denominator(1001)

        container_options = {"movflags": "+faststart"} if fmt == "mp4" else {}
        out = av.open(str(tmp), "w", format=spec["container"],
                      container_options=container_options)
        vout = out.add_stream(vcodec, rate=rate)
        vout.width, vout.height, vout.pix_fmt = ow, oh, "yuv420p"
        vout.codec_context.thread_count = _threads()
        # about 0.12 bit per pixel per frame: 1080p30 near 7.5 Mbit/s
        vout.bit_rate = int(ow * oh * fps * 0.12)
        if bit_rate:
            vout.bit_rate = min(vout.bit_rate, int(bit_rate))
        if vcodec.startswith("libvpx"):
            vout.options = {"crf": "31", "b:v": "0", "row-mt": "1",
                            "deadline": "good", "cpu-used": "4"}
        vtb = fractions.Fraction(1, 1) / rate

        aout = fifo = None
        if ain is not None:
            acodec = encoder_for("audio", fmt)
            if acodec:
                aout = out.add_stream(acodec, rate=48000)
                aout.layout = "stereo"
                aout.bit_rate = 160_000 if fmt == "mp4" else 128_000
                fifo = av.AudioFifo()

        if not strip_metadata:
            taken = meta.get("taken_at")
            if taken:
                out.metadata["creation_time"] = datetime.utcfromtimestamp(
                    taken + edit.start).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            if keep_location and meta.get("location"):
                lat, lon = meta["location"]
                out.metadata["location"] = f"{lat:+.4f}{lon:+.4f}/"
            if meta.get("title"):
                out.metadata["title"] = meta["title"]

        vgraph = None
        last_vpts = -1
        apts = 0
        offset = 0.0
        speed = edit.speed or 1.0

        def mux_audio(flush=False):
            nonlocal apts
            size = aout.codec_context.frame_size or 1024
            while fifo.samples >= size or (flush and fifo.samples):
                chunk = fifo.read(min(size, fifo.samples) if flush else size)
                chunk.pts = apts
                chunk.time_base = fractions.Fraction(1, 48000)
                apts += chunk.samples
                for pkt in aout.encode(chunk):
                    out.mux(pkt)

        for s, e in segs:
            inp.seek(int(max(0.0, s) / vin.time_base), stream=vin, backward=True)
            for st in (vin, ain):
                if st is not None:
                    try:
                        st.codec_context.flush_buffers()
                    except Exception:
                        pass
            if vgraph is None:
                vg, chain = _video_graph(av, vin, geo, "yuv420p")
                vgraph = _finish_graph(av, vg, chain, geo, edit, "yuv420p")
            agraph = None
            if aout is not None:
                agraph = av.filter.Graph()
                nodes = [agraph.add_abuffer(template=ain)]
                r = speed
                while r < 0.5:
                    nodes.append(agraph.add("atempo", "0.5"))
                    r /= 0.5
                while r > 2.0:
                    nodes.append(agraph.add("atempo", "2.0"))
                    r /= 2.0
                if abs(r - 1.0) > 1e-6:
                    nodes.append(agraph.add("atempo", f"{r:.5f}"))
                nodes.append(agraph.add(
                    "aformat", "sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"))
                nodes.append(agraph.add("abuffersink"))
                for a, b in zip(nodes, nodes[1:]):
                    a.link_to(b)
                agraph.configure()

            video_done, audio_done = False, agraph is None
            streams = [st for st in (vin, ain if aout is not None else None) if st is not None]
            for packet in inp.demux(*streams):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    frames = packet.decode()
                except Exception:
                    continue
                for frame in frames:
                    t = frame.time
                    if t is None:
                        continue
                    if packet.stream.type == "video":
                        if t < s - 1e-3:
                            continue
                        if t >= e:
                            video_done = True
                            continue
                        out_t = offset + (t - s) / speed
                        pts = int(round(out_t * fps))
                        if pts <= last_vpts:
                            continue
                        last_vpts = pts
                        vgraph.push(frame)
                        for f in _pull_all(av, vgraph):
                            f.pts = pts
                            f.time_base = vtb
                            for pkt in vout.encode(f):
                                out.mux(pkt)
                        if on_progress:
                            on_progress(min(1.0, out_t / total))
                    else:
                        length = frame.samples / (frame.sample_rate or 48000)
                        if t + length <= s:
                            continue
                        if t >= e:
                            audio_done = True
                            continue
                        frame.pts = None
                        agraph.push(frame)
                        for af in _pull_all(av, agraph):
                            fifo.write(af)
                        mux_audio()
                if video_done and audio_done:
                    break
            offset += (e - s) / speed
            if agraph is not None:
                agraph.push(None)
                for af in _pull_all(av, agraph):
                    fifo.write(af)
                # keep sound and picture together across the joins
                want = int(round(offset * 48000)) - apts
                if fifo.samples > want > 0:
                    keep = fifo.read(want)
                    fifo = av.AudioFifo()
                    fifo.write(keep)
                elif fifo.samples < want:
                    import numpy as np
                    gap = want - fifo.samples
                    silence = av.AudioFrame.from_ndarray(
                        np.zeros((2, gap), dtype=np.float32), format="fltp", layout="stereo")
                    silence.sample_rate = 48000
                    fifo.write(silence)
                mux_audio()

        for pkt in vout.encode():
            out.mux(pkt)
        if aout is not None:
            mux_audio(flush=True)
            for pkt in aout.encode():
                out.mux(pkt)
        out.close()
        if on_progress:
            on_progress(1.0)
    finally:
        inp.close()


def _export_gif(av, path, tmp, edit, segs, max_side, on_progress, cancel):
    from PIL import Image
    total = edit.output_duration()
    if total > GIF_MAX_SECONDS:
        raise ValueError(_("A GIF can be at most {limit} seconds long; this one would "
                           "be {length}.").format(limit=f"{GIF_MAX_SECONDS:.0f}",
                                                  length=f"{total:.0f}"))
    gif_fps = 12.0
    images = []
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        v.codec_context.thread_count = _threads()
        geo = geometry(*_stored_as_shown(v), _source_rotation(path), edit, max_side)
        g, chain = _video_graph(av, v, geo, "rgb24")
        graph = _finish_graph(av, g, chain, geo, edit, "rgb24")
        offset = 0.0
        next_tick = 0.0
        speed = edit.speed or 1.0
        for s, e in segs:
            c.seek(int(max(0.0, s) / v.time_base), stream=v, backward=True)
            for frame in c.decode(v):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                t = frame.time
                if t is None or t < s - 1e-3:
                    continue
                if t >= e:
                    break
                out_t = offset + (t - s) / speed
                if out_t + 1e-6 < next_tick:
                    continue
                next_tick += 1.0 / gif_fps
                graph.push(frame)
                for f in _pull_all(av, graph):
                    images.append(f.to_image().convert(
                        "P", palette=Image.ADAPTIVE, colors=255))
                if on_progress:
                    on_progress(min(1.0, out_t / (total or 1.0)))
            offset += (e - s) / speed
    if not images:
        raise ValueError(_("No frames to put in the GIF."))
    images[0].save(tmp, "GIF", save_all=True, append_images=images[1:],
                   duration=int(1000 / gif_fps), loop=0, optimize=True, disposal=2)
    if on_progress:
        on_progress(1.0)
