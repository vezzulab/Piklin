"""Video playback for the viewer.

Everything it needs ships inside Piklin, so a video plays with sound on any
system the package installs on:

* PyAV - FFmpeg built for Piklin - demuxes and decodes every common format.
  Frames are scaled in C to the size they are shown at, so a 4K clip does
  not push 4K buffers through Python.
* miniaudio plays the sound through whatever the desktop uses (PipeWire,
  PulseAudio or ALSA), found at run time rather than through a package.

Playback runs on the system clock; the sound is kept on it by dropping or
padding a few milliseconds when it drifts, so a picture never waits for
audio and lip sync holds. Speed changes go through FFmpeg's atempo filter,
which changes speed without changing pitch.

If PyAV cannot be loaded at all, the FFmpeg decoder inside OpenCV still
plays the picture - a video is never simply refused.
"""
from __future__ import annotations

import collections
import threading
import time

import gi
from ..i18n import _

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from ..video import format_duration, stream_info

RATES = (0.25, 0.5, 1.0, 1.5, 2.0)
# Frames are scaled to at most this before reaching the screen: sharp on a
# 4K monitor at full screen, light enough to paint at 60 fps.
MAX_FRAME = (2560, 1440)
SAMPLE_RATE = 48000
_SYNC_SLACK = 0.08          # seconds of audio drift tolerated before correcting


def _target_size(w: int, h: int, limit=None) -> tuple[int, int]:
    if w <= 0 or h <= 0:
        return 0, 0
    lw, lh = limit or MAX_FRAME
    scale = min(1.0, lw / w, lh / h)
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


def _av():
    try:
        import av
        return av
    except Exception:
        return None


def playback_engine() -> str:
    """"full" (sound, speed, every format) or "basic" (picture only)."""
    return "full" if _av() else "basic"


class _Clock:
    """Media time, running at ``rate`` while playing."""

    def __init__(self):
        self.base = 0.0
        self.t0 = None
        self.rate = 1.0

    def now(self) -> float:
        if self.t0 is None:
            return self.base
        return self.base + (time.monotonic() - self.t0) * self.rate

    def play(self):
        if self.t0 is None:
            self.t0 = time.monotonic()

    def pause(self):
        self.base, self.t0 = self.now(), None

    def set(self, t: float):
        running = self.t0 is not None
        self.base, self.t0 = t, (time.monotonic() if running else None)

    def set_rate(self, rate: float):
        self.base = self.now()
        if self.t0 is not None:
            self.t0 = time.monotonic()
        self.rate = rate


# -- PyAV engine -------------------------------------------------------------
class _AvEngine:
    def __init__(self, on_frame, on_event, sound=True, max_frame=None):
        # sound=False and a small max_frame make a silent, light preview
        # (a video skimmed under the pointer in the grid).
        self._want_sound = sound
        self._max_frame = max_frame
        self._on_frame = on_frame
        self._on_event = on_event
        self.av = _av()
        self.has_sound = False
        self.loop = False
        self.fps = 30.0
        self._rate = 1.0
        self._muted = False
        self._dur = 0.0
        self._clock = _Clock()
        self._cond = threading.Condition()
        self._cmds: collections.deque = collections.deque()
        self._stop = threading.Event()
        self._thread = None
        self._device = None
        self._playing = False
        # sound waiting for the device: s16 stereo at SAMPLE_RATE, after
        # the speed filter; its last sample belongs to media time _abuf_end
        self._alock = threading.Lock()
        self._abuf = bytearray()
        self._abuf_end = 0.0
        self._latency = 0.05
        # an edit being previewed (video_edit.VideoEdit) and the last frame
        # shown, so a changed edit can be redrawn while paused
        self._edit = None
        self._last_frame = None

    # -- public (UI thread) ------------------------------------------------
    def load(self, path, width, height, fps):
        self.unload()
        self._path = str(path)
        self._hint = (width, height)
        self.fps = fps or 30.0
        self._stop.clear()
        self._cmds.clear()
        self._clock = _Clock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="video-play")
        self._thread.start()

    def _send(self, *cmd):
        with self._cond:
            self._cmds.append(cmd)
            self._cond.notify_all()

    def play(self):
        self._send("play")

    def pause(self):
        self._send("pause")

    def seek(self, seconds: float, accurate: bool = True):
        self._send("seek", max(0.0, seconds), accurate)

    def set_rate(self, rate: float):
        self._send("rate", rate)

    def step(self, frames: int):
        self._send("step", frames)

    def set_muted(self, muted: bool):
        self._muted = muted

    def set_edit(self, edit):
        """Preview an edit: trimmed ends and cuts are skipped, and the
        picture is turned, mirrored and cropped as it will export."""
        self._send("edit", edit)

    def position(self) -> float:
        pos = self._clock.now()
        return min(pos, self._dur) if self._dur else pos

    def duration(self) -> float:
        return self._dur

    def unload(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_device()

    # -- sound device --------------------------------------------------------
    def _open_device(self):
        try:
            import miniaudio
        except Exception:
            return False
        try:
            buffer_ms = 50
            dev = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2,
                sample_rate=SAMPLE_RATE, buffersize_msec=buffer_ms)
            gen = self._audio_gen()
            next(gen)
            dev.start(gen)
        except Exception:
            return False
        self._device = dev
        self._latency = buffer_ms / 1000.0
        return True

    def _close_device(self):
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    def _audio_gen(self):
        required = yield b""
        while True:
            want = required * 4
            chunk = b""
            with self._alock:
                if self._playing and self._abuf:
                    rate = self._rate
                    start = self._abuf_end - (len(self._abuf) / 4 / SAMPLE_RATE) * rate
                    lag = self._clock.now() - (start - self._latency * rate)
                    if lag > _SYNC_SLACK:
                        # behind the picture: skip ahead to catch up
                        drop = min(len(self._abuf), int(lag / rate * SAMPLE_RATE) * 4)
                        del self._abuf[:drop]
                    if lag >= -_SYNC_SLACK and self._abuf:
                        chunk = bytes(self._abuf[:want])
                        del self._abuf[:want]
            if self._muted and chunk:
                chunk = bytes(len(chunk))
            if len(chunk) < want:
                chunk += bytes(want - len(chunk))
            required = yield chunk

    # -- decoding thread -----------------------------------------------------
    def _run(self):
        av = self.av
        try:
            self._c = av.open(self._path)
        except Exception as exc:
            self._on_event("error", str(exc))
            return
        c = self._c
        try:
            if not c.streams.video:
                self._on_event("error", _("There is no picture in this file."))
                return
            self._v = v = c.streams.video[0]
            v.thread_type = "AUTO"
            self._a = c.streams.audio[0] if c.streams.audio else None
            if c.duration:
                self._dur = c.duration / 1_000_000
            elif v.duration and v.time_base:
                self._dur = float(v.duration * v.time_base)
            if v.average_rate:
                self.fps = float(v.average_rate)
            w = v.codec_context.width or self._hint[0]
            h = v.codec_context.height or self._hint[1]
            self._rotation = self._stream_rotation(v)
            if self._rotation in (90, 270):
                tw, th = _target_size(h, w, self._max_frame)
                self._size = (th, tw)
            else:
                self._size = _target_size(w, h, self._max_frame)
            if self._a is not None and self._want_sound:
                self.has_sound = self._open_device()
            self._frames: collections.deque = collections.deque()
            self._shown = 0.0
            from av.video.reformatter import Interpolation
            self._reformatter = None
            self._interp = Interpolation.FAST_BILINEAR
            self._seek_to(0.0, show=True)
            self._on_event("ready", None)
            self._loop()
        finally:
            try:
                c.close()
            except Exception:
                pass

    def _stream_rotation(self, v) -> int:
        """Clockwise degrees the picture must turn to be upright.

        A phone filming upright stores the picture on its side with a note
        to turn it. Older FFmpeg gave that note as a "rotate" tag; current
        FFmpeg only puts it on the decoded frames (the display matrix), so
        reading just the tag played every upright phone video sideways."""
        try:
            tag = v.metadata.get("rotate")
            if tag is not None:
                return int(float(tag)) % 360
        except (AttributeError, ValueError):
            pass
        try:
            for frame in self._c.decode(v):
                # counter-clockwise degrees in the display matrix
                return int(round(-(getattr(frame, "rotation", 0) or 0))) % 360
        except Exception:
            pass
        return 0

    def _build_filter(self):
        a = self._a
        if a is None:
            self._graph = None
            return
        av = self.av
        g = av.filter.Graph()
        src = g.add_abuffer(template=a)
        chain = [src]
        rate = self._rate
        while rate < 0.5:                       # atempo takes 0.5..100 per stage
            chain.append(g.add("atempo", "0.5"))
            rate /= 0.5
        if abs(rate - 1.0) > 1e-6:
            chain.append(g.add("atempo", f"{rate:.4f}"))
        chain.append(g.add("aformat", f"sample_fmts=s16:sample_rates={SAMPLE_RATE}"
                                      f":channel_layouts=stereo"))
        chain.append(g.add("abuffersink"))
        for x, y in zip(chain, chain[1:]):
            x.link_to(y)
        g.configure()
        self._graph = g

    def _seek_to(self, target: float, show: bool, accurate: bool = True):
        c, v = self._c, self._v
        try:
            c.seek(int(target / v.time_base), stream=v, backward=True,
                   any_frame=False)
        except Exception:
            pass
        for s in (v, self._a):
            if s is not None:
                try:
                    s.codec_context.flush_buffers()
                except Exception:
                    pass
        streams = [s for s in (v, self._a) if s is not None]
        self._packets = c.demux(*streams)
        self._eof = False
        self._frames.clear()
        with self._alock:
            self._abuf.clear()
            self._abuf_end = target
        self._build_filter()
        self._discard = target - 0.5 / self.fps if accurate else 0.0
        self._clock.set(target)
        if show:
            frame = self._next_frame()
            if frame is not None:
                self._emit(frame)
                self._clock.set(max(target, frame.time or target) if accurate
                                else (frame.time or target))

    def _decode_one(self) -> bool:
        """Decode one packet into the frame queue and the sound buffer."""
        try:
            packet = next(self._packets)
        except StopIteration:
            self._eof = True
            return False
        except Exception:
            return True                         # a damaged packet: go on
        try:
            decoded = packet.decode()
        except Exception:
            return True
        for f in decoded:
            if packet.stream.type == "video":
                if f.time is not None and f.time < self._discard:
                    continue
                self._frames.append(f)
            elif self._graph is not None and self.has_sound:
                if f.time is not None and f.time + f.samples / (f.sample_rate or 1) < self._discard:
                    continue
                try:
                    self._graph.push(f)
                    while True:
                        out = self._graph.pull()
                        data = out.to_ndarray().tobytes()
                        with self._alock:
                            self._abuf.extend(data)
                            self._abuf_end = (f.time or 0.0) + f.samples / (f.sample_rate or SAMPLE_RATE)
                except (self.av.error.BlockingIOError, self.av.error.EOFError):
                    pass
                except Exception:
                    pass
        return True

    def _next_frame(self):
        while not self._frames and not self._eof and not self._stop.is_set():
            self._decode_one()
        return self._frames.popleft() if self._frames else None

    def _fill_audio(self, seconds: float):
        if not self.has_sound:
            return
        while (not self._eof and len(self._frames) < 90
               and len(self._abuf) < seconds * SAMPLE_RATE * 4):
            self._decode_one()

    def _emit(self, frame):
        tw, th = self._size
        try:
            # One converter kept for the whole clip, with fast bilinear
            # scaling: frame.reformat() builds a new converter for every
            # frame and uses a slow high-quality filter - 31 ms a frame on a
            # 720p clip against under 6 ms this way.
            if self._reformatter is None:
                from av.video.reformatter import VideoReformatter
                self._reformatter = VideoReformatter()
            img = self._reformatter.reformat(
                frame, width=tw or None, height=th or None, format="rgba",
                interpolation=self._interp)
            edit = self._edit
            turns = (self._rotation + (edit.rotate if edit else 0)) % 360
            flip = bool(edit and edit.flip)
            crop = edit.crop if edit else None
            if turns or flip or crop:
                import numpy as np
                arr = img.to_ndarray()
                if turns:
                    arr = np.rot90(arr, k={90: -1, 180: 2, 270: 1}[turns])
                if flip:
                    arr = arr[:, ::-1]
                if crop:
                    h, w = arr.shape[:2]
                    x, y, cw, ch = crop
                    arr = arr[int(y * h):max(int(y * h) + 2, int((y + ch) * h)),
                              int(x * w):max(int(x * w) + 2, int((x + cw) * w))]
                arr = np.ascontiguousarray(arr)
                h, w = arr.shape[:2]
                self._on_frame(w, h, arr.tobytes(), w * 4)
            else:
                plane = img.planes[0]
                self._on_frame(img.width, img.height, bytes(plane), plane.line_size)
            self._shown = frame.time or self._shown
            self._last_frame = frame
        except Exception:
            pass

    def _loop(self):
        pending = None
        while not self._stop.is_set():
            with self._cond:
                cmd = self._cmds.popleft() if self._cmds else None
                if cmd is None and not self._playing:
                    self._cond.wait(0.05)
                    continue
            if cmd is not None:
                pending = self._handle(cmd, pending)
                continue
            frame = pending or self._next_frame()
            pending = None
            if frame is not None and self._edit is not None and frame.time is not None:
                resume = self._edit.next_kept(frame.time)
                if resume is None:
                    frame = None                # past the edited end
                elif resume > frame.time + 0.5 / self.fps:
                    self._seek_to(resume, show=False)
                    continue
            if frame is None:
                if self.loop:
                    self._seek_to(self._first_kept(), show=False)
                    self._clock.play()
                    continue
                self._playing = False
                self._clock.pause()
                self._on_event("ended", None)
                continue
            self._fill_audio(0.35)
            due = frame.time or self._clock.now()
            interrupted = False
            while not self._stop.is_set():
                wait = (due - self._clock.now()) / max(self._rate, 0.01)
                if wait <= 0:
                    break
                with self._cond:
                    if self._cmds:
                        interrupted = True
                        break
                    self._cond.wait(min(wait, 0.02))
            if interrupted:
                pending = frame
                continue
            late = self._clock.now() - due
            if late > 0.25 and self._frames:
                continue                        # too late to be worth painting
            self._emit(frame)

    def _handle(self, cmd, pending):
        kind = cmd[0]
        if kind == "play":
            if self._eof and not self._frames and pending is None:
                self._seek_to(self._first_kept(), show=False)
            elif self._edit is not None:
                resume = self._edit.next_kept(self._clock.now())
                if resume is None:
                    self._seek_to(self._first_kept(), show=False)
                elif resume > self._clock.now() + 0.5 / self.fps:
                    self._seek_to(resume, show=False)
                    pending = None
            self._playing = True
            self._clock.play()
        elif kind == "pause":
            self._playing = False
            self._clock.pause()
        elif kind == "seek":
            _, target, accurate = cmd
            self._seek_to(target, show=True, accurate=accurate)
            return None
        elif kind == "rate":
            self._rate = cmd[1]
            self._clock.set_rate(cmd[1])
            now = self._clock.now()
            with self._alock:
                self._abuf.clear()
            self._seek_to(now, show=not self._playing)
            return None
        elif kind == "step":
            frames = cmd[1]
            self._playing = False
            self._clock.pause()
            if frames > 0:
                frame = pending or self._next_frame()
                for _ in range(frames - 1):
                    frame = self._next_frame() or frame
                if frame is not None:
                    self._emit(frame)
                    self._clock.set(frame.time or self._clock.now())
            else:
                self._seek_to(max(0.0, self._shown + frames / self.fps), show=True)
            return None
        elif kind == "edit":
            self._edit = cmd[1]
            if not self._playing and self._last_frame is not None:
                self._emit(self._last_frame)
        return pending

    def _first_kept(self) -> float:
        if self._edit is not None:
            pieces = self._edit.segments()
            if pieces:
                return pieces[0][0]
        return 0.0


# -- OpenCV engine (last resort) ------------------------------------------------
class _CvEngine:
    """Picture-only playback on the decoder inside OpenCV."""
    has_sound = False

    def __init__(self, on_frame, on_event):
        self._on_frame = on_frame
        self._on_event = on_event
        self.loop = False
        self.fps = 30.0
        self._rate = 1.0
        self._dur = 0.0
        self._pos = 0.0
        self._lock = threading.Lock()
        self._cmd = None
        self._playing = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._size = (0, 0)

    def load(self, path, width, height, fps):
        self.unload()
        info = stream_info(path) or {}
        self.fps = fps or info.get("fps") or 30.0
        self._dur = info.get("duration") or 0.0
        self._size = _target_size(width or info.get("width", 0),
                                  height or info.get("height", 0))
        self._pos = 0.0
        self._stop.clear()
        self._cmd = ("seek", 0.0)
        self._thread = threading.Thread(target=self._run, args=(str(path),),
                                        daemon=True, name="video-basic")
        self._thread.start()

    def _emit(self, frame):
        import cv2
        tw, th = self._size
        if tw and th and (frame.shape[1], frame.shape[0]) != (tw, th):
            frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        h, w = rgba.shape[:2]
        self._on_frame(w, h, rgba.tobytes(), w * 4)

    def _run(self, path):
        import cv2
        cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self._on_event("error", _("This video could not be opened."))
            return
        self._on_event("ready", None)
        next_due = time.monotonic()
        while not self._stop.is_set():
            with self._lock:
                cmd, self._cmd = self._cmd, None
            if cmd is not None:
                kind, value = cmd
                if kind == "seek":
                    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, value) * 1000.0)
                elif kind == "step" and value < 0:
                    target = max(0, int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1 + value)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = cap.read()
                if ok:
                    self._pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    self._emit(frame)
                next_due = time.monotonic()
                continue
            if not self._playing.wait(0.05):
                continue
            now = time.monotonic()
            if now < next_due:
                time.sleep(min(next_due - now, 0.05))
                continue
            ok, frame = cap.read()
            if not ok:
                if self.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self._playing.clear()
                self._on_event("ended", None)
                continue
            self._pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            self._emit(frame)
            next_due = max(next_due + 1.0 / (self.fps * self._rate),
                           time.monotonic() - 0.25)
        cap.release()

    def play(self):
        self._playing.set()

    def pause(self):
        self._playing.clear()

    def position(self) -> float:
        return self._pos

    def duration(self) -> float:
        return self._dur

    def seek(self, seconds: float, accurate: bool = True):
        with self._lock:
            self._cmd = ("seek", seconds)

    def set_rate(self, rate: float):
        self._rate = rate

    def step(self, frames: int):
        self.pause()
        with self._lock:
            self._cmd = ("step", frames)

    def set_muted(self, muted: bool):
        pass

    def unload(self):
        self._stop.set()
        self._playing.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


# -- the widget --------------------------------------------------------------
class VideoPlayer(Gtk.Overlay):
    """A video on the dark stage with floating controls.

    Click the picture to play or pause, double-click for full screen. The
    controls fade out while the video plays and come back when the pointer
    moves.
    """
    __gsignals__ = {
        "toggle-fullscreen": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ended": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("pika-player")
        self.path = None
        self._engine = None
        self._playing = False
        self._scrubbing = False
        self._tick = 0
        self._hide_timer = 0
        self._pending = None
        self._frame_idle = 0
        self._frame_lock = threading.Lock()
        self._duration_hint = 0.0
        self._edit = None
        self._chrome = True

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, hexpand=True, vexpand=True)
        self.set_child(self.picture)
        click = Gtk.GestureClick()
        click.connect("released", self._on_picture_click)
        self.picture.add_controller(click)

        self.big_play = Gtk.Button(icon_name="media-playback-start-symbolic",
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                   tooltip_text=_("Play (Space)"))
        self.big_play.add_css_class("pika-player-big")
        self.big_play.connect("clicked", lambda *_: self.toggle())
        self.add_overlay(self.big_play)

        self.note = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                              margin_top=16, visible=False, wrap=True)
        self.note.add_css_class("pika-player-note")
        self.add_overlay(self.note)

        self.controls = self._build_controls()
        self.revealer = Gtk.Revealer(
            child=self.controls, reveal_child=True,
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=220, valign=Gtk.Align.END,
            halign=Gtk.Align.FILL)
        self.add_overlay(self.revealer)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._show_controls())
        self.add_controller(motion)

    # -- controls ----------------------------------------------------------
    def _build_controls(self):
        bar = Gtk.Box(spacing=10, margin_start=24, margin_end=24,
                      margin_bottom=22)
        bar.add_css_class("pika-player-controls")

        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic",
                                   tooltip_text=_("Play (Space)"))
        self.play_btn.connect("clicked", lambda *_: self.toggle())
        bar.append(self.play_btn)

        self.time_label = Gtk.Label(label="0:00", width_chars=5, xalign=1)
        self.time_label.add_css_class("pika-player-time")
        bar.append(self.time_label)

        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                               hexpand=True, draw_value=False,
                               valign=Gtk.Align.CENTER)
        self.scale.set_range(0.0, 1.0)
        self.scale.add_css_class("pika-player-scrubber")
        self.scale.connect("change-value", self._on_scrub)
        release = Gtk.GestureClick()
        release.connect("released", self._on_scrub_end)
        release.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.scale.add_controller(release)
        bar.append(self.scale)

        self.dur_label = Gtk.Label(label="0:00", width_chars=5, xalign=0)
        self.dur_label.add_css_class("pika-player-time")
        bar.append(self.dur_label)

        group = Gio.SimpleActionGroup()
        rate = Gio.SimpleAction.new_stateful(
            "rate", GLib.VariantType.new("s"), GLib.Variant.new_string("1.0"))
        rate.connect("change-state", self._on_rate)
        group.add_action(rate)
        self.insert_action_group("player", group)
        menu = Gio.Menu()
        for r in RATES:
            item = Gio.MenuItem.new(f"{r:g}×", None)
            item.set_action_and_target_value("player.rate",
                                             GLib.Variant.new_string(str(r)))
            menu.append_item(item)
        self.rate_btn = Gtk.MenuButton(label="1×", menu_model=menu,
                                       tooltip_text=_("Playback Speed"),
                                       direction=Gtk.ArrowType.UP)
        self.rate_btn.add_css_class("pika-player-rate")
        bar.append(self.rate_btn)

        self.loop_btn = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic",
                                         tooltip_text=_("Loop"))
        self.loop_btn.connect("toggled", self._on_loop)
        bar.append(self.loop_btn)

        self.mute_btn = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic",
                                         tooltip_text=_("Mute (M)"))
        self.mute_btn.connect("toggled", self._on_mute)
        bar.append(self.mute_btn)

        full = Gtk.Button(icon_name="view-fullscreen-symbolic",
                          tooltip_text=_("Full Screen (F11)"))
        full.connect("clicked", lambda *_: self.emit("toggle-fullscreen"))
        bar.append(full)
        return bar

    # -- loading -----------------------------------------------------------
    def load(self, path, width=0, height=0, fps=0.0, duration=0.0, poster=None):
        self.unload()
        self.path = str(path)
        if poster is not None:
            self.picture.set_paintable(poster)
        engine_cls = _AvEngine if _av() else _CvEngine
        self._engine = engine_cls(self._frame_from_engine, self._event_from_engine)
        self._engine.loop = self.loop_btn.get_active()
        self.rate_btn.set_label("1×")
        self._duration_hint = duration or 0.0
        self._engine.load(path, width, height, fps)
        self._engine.set_muted(self.mute_btn.get_active())
        if self._edit is not None and hasattr(self._engine, "set_edit"):
            self._engine.set_edit(self._edit)
        self.note.set_visible(False)
        self._set_playing(False)
        self._update_time()
        if not self._tick:
            self._tick = GLib.timeout_add(100, self._on_tick)

    def unload(self):
        if self._engine is not None:
            self._engine.unload()
            self._engine = None
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        self._set_playing(False)
        self.path = None

    # -- frames and events from the engines (any thread) -------------------
    def _frame_from_engine(self, w, h, data, stride):
        # Only the newest frame matters: if the UI is behind, frames in
        # between are dropped rather than queued.
        with self._frame_lock:
            self._pending = (w, h, data, stride)
            if self._frame_idle:
                return
            self._frame_idle = GLib.idle_add(self._paint_frame)

    def _paint_frame(self):
        with self._frame_lock:
            frame, self._pending = self._pending, None
            self._frame_idle = 0
        if frame is None or self.path is None:
            return GLib.SOURCE_REMOVE
        w, h, data, stride = frame
        texture = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8,
                                        GLib.Bytes.new(data), stride)
        self.picture.set_paintable(texture)
        return GLib.SOURCE_REMOVE

    def _event_from_engine(self, kind, detail):
        GLib.idle_add(self._handle_event, kind, detail)

    def _handle_event(self, kind, detail):
        if kind == "ready":
            has_sound = bool(getattr(self._engine, "has_sound", False))
            self.mute_btn.set_sensitive(has_sound)
        elif kind == "ended":
            self._set_playing(False)
            self._show_controls()
            self.emit("ended")
        elif kind == "error":
            self._set_playing(False)
            self._flash_note((_("This video can't be played.") + " "
                              + (detail or "")).strip(), 0)
        return GLib.SOURCE_REMOVE

    # -- actions -----------------------------------------------------------
    def set_edit(self, edit):
        """Play ``edit`` (a video_edit.VideoEdit, or None for the original)."""
        self._edit = edit
        if self._engine is not None and hasattr(self._engine, "set_edit"):
            self._engine.set_edit(edit)

    def set_chrome(self, visible: bool):
        """Show or hide the controls and the big play button (hidden while
        a live photo plays)."""
        self._chrome = visible
        self.revealer.set_visible(visible)
        self.big_play.set_visible(visible and not self._playing)

    def seek(self, seconds: float):
        if self._engine is not None:
            self._engine.seek(max(0.0, seconds))

    def position(self) -> float:
        return self._engine.position() if self._engine is not None else 0.0

    def set_rate(self, rate: float):
        self.rate_btn.set_label(f"{rate:g}\u00d7")
        if self._engine is not None:
            self._engine.set_rate(rate)

    def is_playing(self) -> bool:
        return self._playing

    def toggle(self):
        if self._engine is None:
            return
        if self._playing:
            self._engine.pause()
            self._set_playing(False)
        else:
            self._engine.play()
            self._set_playing(True)

    def pause(self):
        if self._engine is not None and self._playing:
            self._engine.pause()
            self._set_playing(False)

    def step(self, frames: int):
        if self._engine is not None:
            self._engine.step(frames)
            self._set_playing(False)
            GLib.timeout_add(150, lambda: (self._update_time(), False)[1])

    def skip(self, seconds: float):
        if self._engine is not None:
            dur = self._engine.duration() or self._duration_hint
            pos = self._engine.position() + seconds
            self._engine.seek(max(0.0, min(pos, dur or pos)))

    def toggle_mute(self):
        if self.mute_btn.get_sensitive():
            self.mute_btn.set_active(not self.mute_btn.get_active())

    def _set_playing(self, playing: bool):
        self._playing = playing
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self.play_btn.set_icon_name(icon)
        self.play_btn.set_tooltip_text(_("Pause (Space)") if playing else _("Play (Space)"))
        self.big_play.set_visible(not playing and self._chrome)
        if playing:
            self._schedule_hide()
        else:
            self._show_controls(hide_later=False)

    def _on_picture_click(self, gesture, n_press, _x, _y):
        if n_press == 2:
            self.emit("toggle-fullscreen")
        elif n_press == 1:
            self.toggle()

    def _on_rate(self, action, value):
        action.set_state(value)
        rate = float(value.get_string())
        self.rate_btn.set_label(f"{rate:g}×")
        if self._engine is not None:
            self._engine.set_rate(rate)

    def _on_loop(self, btn):
        if self._engine is not None:
            self._engine.loop = btn.get_active()

    def _on_mute(self, btn):
        btn.set_icon_name("audio-volume-muted-symbolic" if btn.get_active()
                          else "audio-volume-high-symbolic")
        if self._engine is not None:
            self._engine.set_muted(btn.get_active())

    def _on_scrub(self, _scale, _scroll, value):
        if self._engine is None:
            return False
        self._scrubbing = True
        dur = self._engine.duration() or self._duration_hint
        if dur:
            self._engine.seek(value * dur, accurate=False)
            self.time_label.set_text(format_duration(value * dur))
        return False

    def _on_scrub_end(self, *_args):
        if self._engine is None or not self._scrubbing:
            return
        self._scrubbing = False
        dur = self._engine.duration() or self._duration_hint
        if dur:
            self._engine.seek(self.scale.get_value() * dur, accurate=True)

    # -- time and chrome ---------------------------------------------------
    def _on_tick(self):
        if self._engine is None:
            self._tick = 0
            return GLib.SOURCE_REMOVE
        if not self._scrubbing:
            self._update_time()
        return GLib.SOURCE_CONTINUE

    def _update_time(self):
        if self._engine is None:
            return
        pos = self._engine.position()
        dur = self._engine.duration() or self._duration_hint
        self.time_label.set_text(format_duration(pos))
        self.dur_label.set_text(format_duration(dur))
        if dur:
            self.scale.set_value(min(1.0, pos / dur))

    def _show_controls(self, hide_later=True):
        self.revealer.set_reveal_child(True)
        self.set_cursor(None)
        if hide_later and self._playing:
            self._schedule_hide()

    def _schedule_hide(self):
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)

        def hide():
            self._hide_timer = 0
            if self._playing and not self._scrubbing:
                self.revealer.set_reveal_child(False)
                self.set_cursor_from_name("none")
            return GLib.SOURCE_REMOVE
        self._hide_timer = GLib.timeout_add(2500, hide)

    def _flash_note(self, text, ms):
        self.note.set_text(text)
        self.note.set_visible(True)
        if ms:
            GLib.timeout_add(ms, lambda: (self.note.set_visible(False), False)[1])
