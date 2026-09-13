"""Face detection, for the portrait tools.

Uses YuNet, a small (230 KB) CNN detector bundled with the app.  It
returns a box plus five landmarks - both eyes, the nose tip and the two
mouth corners - which is enough to build a face mask, find the eyes, and
estimate a coarse head orientation.

Everything runs locally.  No image or derived embedding leaves the
machine, and nothing is uploaded to identify anyone.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ops

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
_lock = threading.Lock()
_detector = None
_tried = False


def model_path() -> Path | None:
    """Find the bundled model, in a source tree or inside an AppImage."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "data" / "models" / MODEL_NAME,
        here.parent.parent / "data" / "models" / MODEL_NAME,
        Path("/usr/share/piklin/models") / MODEL_NAME,
    ]
    import os
    if os.environ.get("APPDIR"):
        candidates.insert(0, Path(os.environ["APPDIR"]) /
                          "usr/share/piklin/models" / MODEL_NAME)
    for c in candidates:
        if c.is_file():
            return c
    return None


@dataclass
class Face:
    x: float            # all geometry normalised 0..1
    y: float
    w: float
    h: float
    score: float
    right_eye: tuple[float, float]
    left_eye: tuple[float, float]
    nose: tuple[float, float]
    mouth_right: tuple[float, float]
    mouth_left: tuple[float, float]

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def yaw(self) -> float:
        """Rough left/right head turn, -1..1.

        Derived from where the nose sits between the eyes: centred means
        facing forward, pushed toward one eye means turned the other way.
        """
        ex = (self.right_eye[0] + self.left_eye[0]) / 2
        span = abs(self.left_eye[0] - self.right_eye[0]) or 1e-3
        return float(np.clip((self.nose[0] - ex) / span * 2.0, -1, 1))

    @property
    def roll(self) -> float:
        """Head tilt in degrees, from the line between the eyes."""
        dx = self.left_eye[0] - self.right_eye[0]
        dy = self.left_eye[1] - self.right_eye[1]
        return float(np.degrees(np.arctan2(dy, dx)))


def available() -> bool:
    return _get_detector() is not None


def _get_detector():
    global _detector, _tried
    with _lock:
        if _tried:
            return _detector
        _tried = True
        if not ops.HAVE_CV:
            return None
        mp = model_path()
        if mp is None:
            return None
        try:
            import cv2
            _detector = cv2.FaceDetectorYN.create(
                str(mp), "", (320, 320), 0.72, 0.3, 5000)
        except Exception:
            _detector = None
        return _detector


def detect(img: np.ndarray, max_side: int = 640) -> list[Face]:
    """Find faces in a float 0..1 RGB image.

    Detection runs on a downscaled copy - a 640px pass finds every face
    large enough for the portrait tools to act on, and costs a fraction
    of a full-resolution pass.
    """
    det = _get_detector()
    if det is None:
        return []
    h, w = img.shape[:2]
    small = ops.fit_within(img, max_side)
    sh, sw = small.shape[:2]
    u8 = (np.clip(small, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    try:
        import cv2
        bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
        det.setInputSize((sw, sh))
        count, raw = det.detect(bgr)
    except Exception:
        return []
    if raw is None or len(raw) == 0:
        return []

    faces = []
    for row in raw:
        bx, by, bw, bh = (float(v) for v in row[:4])
        pts = [(float(row[4 + i * 2]) / sw, float(row[5 + i * 2]) / sh)
               for i in range(5)]
        faces.append(Face(
            x=bx / sw, y=by / sh, w=bw / sw, h=bh / sh, score=float(row[14]),
            right_eye=pts[0], left_eye=pts[1], nose=pts[2],
            mouth_right=pts[3], mouth_left=pts[4]))
    faces.sort(key=lambda f: f.w * f.h, reverse=True)
    return faces


def face_mask(shape: tuple[int, int], faces: list[Face],
              grow: float = 1.25, feather: float = 0.55) -> np.ndarray:
    """Soft elliptical mask covering the detected faces."""
    h, w = shape
    mask = np.zeros((h, w), ops.F32)
    for f in faces:
        cx, cy = f.center
        # faces are taller than wide, and the detector box cuts the chin
        rx = f.w * grow * 0.5
        ry = f.h * grow * 0.62
        cy = cy + f.h * 0.06
        m = ops.radial_mask((h, w), cx, cy, max(ry, 1e-3), feather=feather,
                            aspect=max(rx / max(ry, 1e-3), 0.05))
        mask = np.maximum(mask, m)
    return mask


def skin_mask(img: np.ndarray, faces: list[Face]) -> np.ndarray:
    """Restrict a face mask to skin-coloured pixels.

    Smoothing the whole face box softens eyes, nostrils and hair.
    Sampling the actual skin colour from the cheeks and keeping pixels
    that match it is what makes the smoothing land only on skin.
    """
    h, w = img.shape[:2]
    base = face_mask((h, w), faces, grow=1.15, feather=0.5)
    if not faces or base.max() <= 0:
        return base
    samples = []
    for f in faces:
        cx, cy = f.center
        for ox, oy in ((-0.26, 0.12), (0.26, 0.12), (0.0, 0.28)):
            px = int(np.clip(cx + f.w * ox, 0, 1) * (w - 1))
            py = int(np.clip(cy + f.h * oy, 0, 1) * (h - 1))
            samples.append(img[py, px])
    if not samples:
        return base
    skin = np.mean(samples, axis=0).astype(ops.F32)
    dist = np.sqrt(np.sum((img - skin) ** 2, axis=2))
    keep = np.clip(1.0 - dist / ops.F32(0.30), 0, 1)
    keep = keep * keep * (3.0 - 2.0 * keep)
    m = base * keep
    return np.clip(ops.guided_filter(img, m, max(2, int(min(h, w) * 0.008)),
                                     0.002), 0, 1)


def eye_mask(shape: tuple[int, int], faces: list[Face]) -> np.ndarray:
    h, w = shape
    mask = np.zeros((h, w), ops.F32)
    for f in faces:
        r = max(f.w * 0.14, 0.008)
        for eye in (f.right_eye, f.left_eye):
            mask = np.maximum(mask, ops.radial_mask(
                (h, w), eye[0], eye[1], r, feather=0.9))
    return mask
