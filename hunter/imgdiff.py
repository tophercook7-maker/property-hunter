"""Then-vs-now aerial change detection (spec 13: "new imagery where detectable").

Deterministic, no model. Both aerials are exported on the same frame, so a
plain per-pixel comparison of downsampled greyscale tells us how much of the
parcel's appearance changed between flights. It cannot say WHAT changed - a
roof, a cleared lot, a new shed - only that something did, and roughly how
much. That is recorded as a CALCULATION and, above a threshold, as a change
worth a human look.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

SIZE = 160          # downsample: seasonal leaf noise averages out, buildings do not
BLUR = 1.2
CHANGED_PIXEL = 48  # 0-255 grey delta that counts as "different"


def _robust_stats(im: Image.Image) -> tuple[float, float]:
    """Median and inter-quartile range over the whole frame.

    Robust statistics: a new roof covering a tenth of the frame, or a bright
    strip along one edge, barely moves the median, so the exposure match
    corrects lighting without erasing the change we are looking for."""
    px = sorted(im.getdata())
    n = len(px)
    med = px[n // 2]
    iqr = (px[3 * n // 4] - px[n // 4]) or 1.0
    return med, iqr


def _match_exposure(ref: Image.Image, im: Image.Image) -> Image.Image:
    m_ref, r_ref = _robust_stats(ref)
    m_im, r_im = _robust_stats(im)
    gain = max(0.7, min(1.4, r_ref / r_im))
    return im.point(lambda v: int(max(0, min(255, (v - m_im) * gain + m_ref))))


def compare(path_a: Path, path_b: Path) -> dict:
    a = ImageOps.grayscale(Image.open(path_a)).resize((SIZE, SIZE)).filter(
        ImageFilter.GaussianBlur(BLUR))
    b = ImageOps.grayscale(Image.open(path_b)).resize((SIZE, SIZE)).filter(
        ImageFilter.GaussianBlur(BLUR))
    # Equalise exposure so a sunnier flight does not read as change, using
    # robust statistics so that the change itself does not get normalised away.
    b = _match_exposure(a, b)
    diff = ImageChops.difference(a, b)
    px = list(diff.getdata())
    changed = sum(1 for v in px if v >= CHANGED_PIXEL)
    frac = changed / len(px)
    mean = sum(px) / len(px)
    # where in the frame - the centre is the parcel, the edges are neighbours
    w = SIZE
    centre = [px[y * w + x] for y in range(w // 4, 3 * w // 4) for x in range(w // 4, 3 * w // 4)]
    centre_frac = sum(1 for v in centre if v >= CHANGED_PIXEL) / len(centre)
    if centre_frac >= 0.35:
        word = "large change at the centre of the frame"
    elif centre_frac >= 0.18:
        word = "noticeable change near the parcel"
    elif frac >= 0.25:
        word = "change mostly around the edges - probably neighbours or tree growth"
    else:
        word = "looks much the same"
    return {"changed_fraction": round(frac, 3), "centre_changed_fraction": round(centre_frac, 3),
            "mean_delta": round(mean, 1), "summary": word,
            "notable": centre_frac >= 0.18}
