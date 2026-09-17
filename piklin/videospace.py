"""Room taken by videos: which ones can be made smaller, by how much, and
whether a smaller copy is really the same video.

Only videos inside the library's own Originals folder are ever considered.
A video in a watched folder outside the library belongs to that folder - other
programs may use it, and it has no copy in the backup to fall back on - so it
is never replaced.

A video is made smaller by capping its bits per second by the size of its
picture, at rates where the difference does not show at a normal viewing
distance. Videos already under the cap, or that would save little, are left
alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# bits per second for the video track, by the long side of the picture
CAPS = ((854, 1_500_000), (1280, 4_000_000), (1920, 8_000_000), (10_000, 20_000_000))
AUDIO_BPS = 160_000
# not worth the work or the small loss of detail below this
MIN_SAVING_BYTES = 20 * 1024 * 1024
MIN_SAVING_SHARE = 0.25


def cap_for(width: int, height: int) -> int:
    side = max(width or 0, height or 0)
    for limit, bps in CAPS:
        if side <= limit:
            return bps
    return CAPS[-1][1]


@dataclass
class Candidate:
    photo_id: int
    path: str
    bytes: int
    duration: float
    width: int
    height: int
    bit_rate: int               # the video bits per second to ask for
    expected: int               # bytes afterwards, about

    @property
    def saving(self) -> int:
        return max(0, self.bytes - self.expected)

    @property
    def work(self) -> float:
        """How much encoding it takes: pixels times seconds."""
        return max(1, self.width * self.height) * max(self.duration, 0.1)


def plan(catalog, originals: Path | str, min_saving: int = MIN_SAVING_BYTES,
         min_share: float = MIN_SAVING_SHARE) -> list[Candidate]:
    """The library's videos worth making smaller, the most space for the
    least work first."""
    root = Path(originals).resolve()
    out = []
    for r in catalog.q(
            "SELECT id, path, bytes, duration, width, height FROM photos "
            "WHERE duration > 0 AND trashed_at IS NULL AND paired_to IS NULL"):
        try:
            Path(r["path"]).resolve().relative_to(root)
        except ValueError:
            continue                      # outside the library: never touched
        dur = float(r["duration"] or 0)
        size = int(r["bytes"] or 0)
        if dur <= 0 or size <= 0:
            continue
        current = size * 8 / dur
        target = min(cap_for(r["width"], r["height"]), int(current * 0.8))
        expected = int((target + AUDIO_BPS) * dur / 8)
        saving = size - expected
        if saving < min_saving or saving < size * min_share:
            continue
        out.append(Candidate(r["id"], r["path"], size, dur, int(r["width"] or 0),
                             int(r["height"] or 0), target, expected))
    out.sort(key=lambda c: c.saving / c.work, reverse=True)
    return out


def _has_audio(path) -> bool:
    import av
    try:
        with av.open(str(path)) as c:
            return bool(c.streams.audio)
    except Exception:
        return False


def _frames(path, times, side=320):
    import av
    import numpy as np
    shots = []
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        for t in times:
            c.seek(int(t / v.time_base), stream=v, any_frame=False, backward=True)
            got = None
            for frame in c.decode(v):
                if frame.time is not None and frame.time >= t - 0.05:
                    got = frame
                    break
                got = frame
            if got is None:
                return None
            img = got.to_ndarray(format="gray")
            h, w = img.shape
            step = max(1, max(h, w) // side)
            shots.append(np.asarray(img[::step, ::step], dtype=np.float32) / 255.0)
    return shots


def verify(src, out, min_similarity: float = 0.80) -> bool:
    """Whether ``out`` is the same video as ``src``: as long, with sound when
    it had sound, and looking the same at its start, middle and end."""
    from .video import stream_info
    from .quality import ssim
    a, b = stream_info(src) or {}, stream_info(out) or {}
    da, db = float(a.get("duration") or 0), float(b.get("duration") or 0)
    if da <= 0 or db <= 0 or abs(da - db) > max(0.5, da * 0.01):
        return False
    if _has_audio(src) and not _has_audio(out):
        return False
    times = [da * 0.1, da * 0.5, max(0.0, da * 0.9 - 0.1)]
    fa, fb = _frames(src, times), _frames(out, times)
    if not fa or not fb or len(fa) != len(fb):
        return False
    import numpy as np
    for x, y in zip(fa, fb):
        if x.shape != y.shape:
            h, w = min(x.shape[0], y.shape[0]), min(x.shape[1], y.shape[1])
            x, y = x[:h, :w], y[:h, :w]
        if ssim(np.stack([x] * 3, -1), np.stack([y] * 3, -1)) < min_similarity:
            return False
    return True
