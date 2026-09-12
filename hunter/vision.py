"""Conservative image analysis with a local vision model (spec 31).

Only two kinds of image ever reach the model: aerials exported from the State's
own image service, and photos Topher took himself. Nothing scraped.

Every finding is stored as AI_OPINION at LOW confidence and is phrased as a
possibility - "possible roof deterioration visible" - never as a fact and never
as a dollar figure. Results are cached by image hash so the same photo is never
paid for twice.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import httpx

from . import db, store
from .config import AI_ENABLED, OLLAMA_URL
from .db import utcnow

VISION_MODELS = ["llava:latest", "llava", "llama3.2-vision", "moondream", "bakllava"]

PROMPT_AERIAL = """You are looking at an aerial photograph of one property, centred in the
frame, flown in {year}. Describe ONLY what is plainly visible. Answer as a JSON
object with these keys:

  "structures": how many buildings you can see near the centre (a number or "unclear")
  "roof": one short phrase about the main roof if visible (e.g. "intact", "possible
          damage or tarps", "unclear")
  "vegetation": "maintained", "overgrown", "wooded", or "unclear"
  "debris": "yes", "no", or "unclear"
  "driveway": "visible", "not visible", or "unclear"
  "vehicles": a number or "unclear"
  "notes": one sentence of anything else plainly visible

Rules: if you cannot tell, say "unclear". Do not guess condition from shadows.
Do not estimate any cost. JSON only."""

PROMPT_PHOTO = """You are looking at a photograph taken by the owner's prospective buyer at a
property. Describe ONLY what is plainly visible. Answer as a JSON object:

  "subject": what the photo mainly shows (e.g. "front of house", "roof", "interior room")
  "visible_concerns": a list of up to 4 short phrases for things that look like
                      possible problems (e.g. "possible roof sag", "boarded window",
                      "overgrown lot", "peeling paint"), or an empty list
  "looks_occupied": "yes", "no", or "unclear"
  "notes": one sentence

Rules: say "unclear" when unsure. Do not diagnose structure you cannot see.
Do not estimate any cost. JSON only."""


def available_model() -> str | None:
    if not AI_ENABLED:
        return None
    try:
        names = [m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=6)
                 .json().get("models", [])]
    except Exception:
        return None
    for want in VISION_MODELS:
        for n in names:
            if n == want or n.startswith(want + ":"):
                return n
    return None


def _cached(key: str) -> dict | None:
    row = db.q1("SELECT response FROM ai_cache WHERE key=?", (key,))
    return json.loads(row["response"]) if row else None


def analyse_image(path: Path, kind: str, year: str | None = None) -> dict:
    """Return {"model", "findings": {...}, "cached": bool} or {"error": ...}."""
    model = available_model()
    if not model:
        return {"error": "no local vision model (e.g. llava) is running"}
    data = path.read_bytes()
    key = "vision:" + hashlib.sha256(data + model.encode() + kind.encode()).hexdigest()[:40]
    hit = _cached(key)
    if hit:
        return {"model": model, "findings": hit, "cached": True}
    prompt = PROMPT_AERIAL.format(year=year or "an unknown year") if kind == "aerial" \
        else PROMPT_PHOTO
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(data).decode()]}],
            "options": {"temperature": 0.1, "num_predict": 300}}, timeout=240)
        r.raise_for_status()
        text = (r.json().get("message") or {}).get("content", "")
    except Exception as exc:
        return {"error": f"vision model failed: {exc}"}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        findings = json.loads(m.group(0)) if m else {"notes": text.strip()[:300]}
    except ValueError:
        findings = {"notes": text.strip()[:300]}
    db.ex("INSERT OR REPLACE INTO ai_cache(key,model,prompt,response,created_at) "
          "VALUES(?,?,?,?,?)", (key, model, f"[image {kind}] {prompt[:200]}",
                                json.dumps(findings), utcnow()))
    return {"model": model, "findings": findings, "cached": False}


def _phrase(findings: dict, kind: str) -> list[str]:
    """Turn the JSON into conservative sentences. Never a fact, never a cost."""
    out = []
    unclear = lambda v: v in (None, "", "unclear")
    if kind == "aerial":
        s = findings.get("structures")
        if not unclear(s):
            out.append(f"About {s} structure(s) visible from above")
        roof = findings.get("roof")
        if not unclear(roof) and roof.lower() not in ("intact", "normal", "fine"):
            out.append(f"Possible roof concern visible in aerial imagery: {roof}")
        veg = findings.get("vegetation")
        if veg == "overgrown":
            out.append("Vegetation looks overgrown in the aerial - often a sign nobody is "
                       "keeping the place up")
        elif veg == "wooded":
            out.append("Parcel reads as mostly wooded from above")
        if findings.get("debris") == "yes":
            out.append("Possible debris or stored material visible on the lot")
        if findings.get("driveway") == "not visible":
            out.append("No obvious driveway visible - check access on the ground")
        v = findings.get("vehicles")
        if not unclear(v) and str(v) not in ("0", "none"):
            out.append(f"{v} vehicle(s) visible - somebody may be using the property")
    else:
        for c in (findings.get("visible_concerns") or [])[:4]:
            if c and str(c).lower() != "unclear":
                out.append(f"Possible issue visible in your photo: {c}")
        occ = findings.get("looks_occupied")
        if occ in ("yes", "no"):
            out.append("Looks occupied in your photo" if occ == "yes"
                       else "Does not look occupied in your photo")
    notes = findings.get("notes")
    if notes and not unclear(notes):
        out.append(str(notes)[:200])
    return out


def analyse_photo_record(photo_id: int) -> dict:
    row = db.q1("SELECT * FROM photos WHERE id=?", (photo_id,))
    if not row:
        return {"error": "no such photo"}
    path = Path(row["local_path"] or "")
    if not path.exists():
        return {"error": "image file is not on disk"}
    kind = "aerial" if row["kind"] == "aerial" else "photo"
    res = analyse_image(path, kind, row["captured_at"] if kind == "aerial" else None)
    if "error" in res:
        return res
    sentences = _phrase(res["findings"], kind)
    label = (f"aerial imagery flown {row['captured_at']}" if kind == "aerial"
             else f"your {row['kind']} photo")
    ev = [{
        "field": f"vision:{row['kind']}:{photo_id}",
        "value": (s + " - LOW CONFIDENCE"),
        "evidence_type": "AI_OPINION", "confidence": "LOW",
        "source": "local_vision_model", "source_name": f"{res['model']} reading {label}",
        "source_url": row["url"],
        "raw_ref": "A model looking at pixels. It cannot see inside, cannot measure, and "
                   "is wrong often enough that nothing here should change a decision on "
                   "its own. Go and look.",
    } for s in sentences]
    if not sentences:
        ev.append({"field": f"vision:{row['kind']}:{photo_id}",
                   "value": "nothing notable stood out to the model - LOW CONFIDENCE",
                   "evidence_type": "AI_OPINION", "confidence": "LOW",
                   "source": "local_vision_model",
                   "source_name": f"{res['model']} reading {label}",
                   "source_url": row["url"]})
    store.store_evidence(row["property_id"], ev)
    caption_note = "; ".join(sentences[:2]) if sentences else "model saw nothing notable"
    db.ex("UPDATE photos SET caption=? WHERE id=? AND (caption IS NULL OR caption='' "
          "OR caption LIKE 'Aerial,%')",
          ((row["caption"] + " | " if row["caption"] else "") + f"AI (low confidence): {caption_note}",
           photo_id))
    return {"model": res["model"], "cached": res["cached"], "observations": sentences,
            "raw": res["findings"],
            "caveat": "Low-confidence machine observations, stored as AI_OPINION. "
                      "They are prompts to go and look, not findings."}
