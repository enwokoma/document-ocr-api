"""Lightweight flash/glare estimation used by image-quality responses."""

import cv2
import numpy as np


def flash_glance_hint(bgr: np.ndarray) -> dict | None:
    """Return a quick brightness summary for an uploaded BGR image.

    This is intentionally cheap: it downsizes large images, counts very bright
    pixels, and reports whether the image probably has visible flash glare.
    """
    if bgr is None or bgr.size == 0:
        return None
    h, w = bgr.shape[:2]
    m = max(h, w)
    if m > 256:
        s = 256 / m
        bgr = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
    bright = int(np.count_nonzero(th == 255))
    total = int(th.size)
    if total <= 0:
        return None
    pct = 100.0 * bright / float(total)
    return {"bright_pct": round(pct, 2), "flashy": bool(pct > 2.0)}

