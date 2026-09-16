"""Local AI layer (spec 51/52/63).

Hard rules enforced here, not just asked for in a prompt:
  * the model is only ever shown the evidence we actually hold
  * it is told to say "I don't know" and is given the words to do it
  * deterministic work (arithmetic, filtering, ranking) never reaches the model
  * every answer is cached, so we do not burn a 27B model on the same property
  * if no model is available the feature degrades to a written summary built
    from the evidence, and says so - it never fabricates
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx

from . import db
from .config import AI_ENABLED, AI_MODEL, AI_NUM_CTX, AI_TIMEOUT, OLLAMA_URL
from .db import utcnow

PREFERRED = ["qwen3.8:27b", "gemma2:27b", "gpt-oss:20b", "gemma4:latest",
             "qwen2.5:7b-instruct", "llama3.3:70b"]

SYSTEM = """You are the property analyst for one person, Topher, who is looking for
real-estate opportunities in Garland County, Arkansas.

How you talk:
- Plain everyday English. Short sentences. No real-estate jargon unless you
  immediately explain it.
- You are allowed, and expected, to say "I don't know", "we haven't verified
  that yet", "this is only an estimate", and "don't buy this yet".
- Be conservative. When the evidence is thin, say the evidence is thin.

Hard rules you must never break:
- Use ONLY the evidence given to you below. If a fact is not in the evidence,
  you do not know it.
- Never invent an owner, a price, a tax amount, a lien, a zoning district, a
  square footage, a year, a date, or a source.
- Never state a legal conclusion. Point at a lawyer or the right government
  office instead.
- Never say paying somebody's delinquent taxes makes you the owner.
- Do not repeat a number back with more precision than you were given.
- If you are guessing, label it as a guess in the sentence itself.
- A parcel class is not zoning. The assessor's residential / commercial /
  agricultural code is a tax category, not permission to do anything. Never say
  a property is "zoned" for something unless the evidence gives an actual
  zoning district from the City.
- A mapped road is not legal access. Legal access is a recorded easement or
  platted frontage, and it lives in the deed.
- The county's appraised value is the assessor's opinion, not a sale price and not an asking price.
"""

_available: list[str] | None = None


def available_models() -> list[str]:
    global _available
    if _available is not None:
        return _available
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=6)
        _available = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        _available = []
    return _available


def pick_model() -> str | None:
    if not AI_ENABLED:
        return None
    models = available_models()
    if not models:
        return None
    if AI_MODEL and AI_MODEL in models:
        return AI_MODEL
    for name in PREFERRED:
        if name in models:
            return name
    return models[0]


def status() -> dict:
    model = pick_model()
    return {"enabled": AI_ENABLED, "model": model,
            "available": len(available_models()),
            "backend": OLLAMA_URL,
            "detail": ("ready" if model else
                       "no local model reachable - explanations fall back to a "
                       "plain summary of the stored evidence")}


def _key(prompt: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()


def _cached(key: str) -> str | None:
    row = db.q1("SELECT response FROM ai_cache WHERE key=?", (key,))
    return row["response"] if row else None


def _cache(key: str, model: str, prompt: str, response: str) -> None:
    db.ex("INSERT OR REPLACE INTO ai_cache(key,model,prompt,response,created_at) "
          "VALUES(?,?,?,?,?)", (key, model, prompt[:4000], response, utcnow()))


def ask(prompt: str, *, system: str = SYSTEM, use_cache: bool = True,
        temperature: float = 0.2, max_tokens: int = 900) -> tuple[str, str]:
    """Returns (text, model_or_empty). Empty model means no AI ran."""
    model = pick_model()
    if not model:
        return "", ""
    key = _key(system + prompt, model)
    if use_cache:
        hit = _cached(key)
        if hit:
            return hit, model
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "stream": False,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}],
                  "options": {"temperature": temperature, "num_ctx": AI_NUM_CTX,
                              "num_predict": max_tokens}},
            timeout=AI_TIMEOUT)
        r.raise_for_status()
        text = (r.json().get("message") or {}).get("content", "").strip()
    except Exception as exc:
        return "", f"error:{exc}"
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text:
        _cache(key, model, system + prompt, text)
    return text, model


# ------------------------------------------------------------------ guard --

_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|million|thousand)\b)?", re.I)
_ANY_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_SQFT = re.compile(r"\b(\d[\d,]{2,})\s?(?:sq\.? ?ft|square feet|sqft)\b", re.I)


def _numbers_in(text: str) -> set[str]:
    """Every number that appears in the evidence, in a comparable form.

    Deliberately permissive: anything the model was shown is fair game, in any
    formatting. The strictness is applied to the model's OUTPUT, not here."""
    out = set()
    for m in _ANY_NUMBER.finditer(text):
        n = m.group(0).replace(",", "")
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))          # 24250.0 -> 24250
            try:
                out.add(str(int(round(float(n)))))
            except ValueError:
                pass
    return {n for n in out if n}


def guard(text: str, evidence_text: str) -> tuple[str, list[str]]:
    """Strip figures the model was never given (spec 52).

    Any dollar amount, year or square footage in the answer that does not
    appear in the evidence block is replaced with a marker, and the list of
    what was removed is returned so the UI can say so. Prompting for honesty
    is not the same as enforcing it.
    """
    allowed = _numbers_in(evidence_text)
    # tolerate rounding: "$24,000" for "$24,250" style is a lie too, so no.
    removed: list[str] = []

    def _check(m, raw_value):
        if raw_value in allowed:
            return m.group(0)
        removed.append(m.group(0).strip().rstrip(",.;:"))
        return "[figure not in our evidence - removed]"

    out = _MONEY.sub(lambda m: _check(m, re.sub(r"[^\d.]", "", m.group(0)).rstrip(".")), text)
    out = _SQFT.sub(lambda m: _check(m, m.group(1).replace(",", "")), out)
    out = _YEAR.sub(lambda m: _check(m, m.group(1)), out)
    return out, removed


def evidence_block(prop: dict, evidence: list[dict], limit: int = 70) -> str:
    """The ONLY thing the model is allowed to reason from."""
    lines = [
        "PROPERTY RECORD",
        f"  address: {prop.get('address') or 'no street address on the tax roll'}",
        f"  parcel id: {prop.get('parcel_id') or 'unknown'}",
        f"  city: {prop.get('city') or 'unknown'}",
        f"  acreage: {prop.get('acreage') if prop.get('acreage') is not None else 'unknown'}",
        f"  county appraised total: "
        f"{'$%s' % format(prop['total_value'], ',.0f') if prop.get('total_value') else 'unknown'}",
        f"  county land value: "
        f"{'$%s' % format(prop['land_value'], ',.0f') if prop.get('land_value') else 'unknown'}",
        f"  county improvement value: "
        f"{'$%s' % format(prop['imp_value'], ',.0f') if prop.get('imp_value') is not None else 'unknown'}",
        f"  owner of record: {prop.get('owner_name') or 'unknown'}",
        f"  flood zone: {prop.get('flood_zone') or 'not checked'}",
        f"  zoning: {prop.get('zoning') or 'NOT CHECKED - unknown'}",
        f"  tax status: {prop.get('tax_status') or 'NOT CHECKED - unknown'}",
        f"  listing status: {prop.get('listing_status') or 'not known to be listed'}",
        "",
        "EVIDENCE WE ACTUALLY HOLD (origin | source | confidence | verification | reference):",
        "  origin: AUTOMATED SOURCE = read from a public-record adapter; MANUAL VERIFICATION = a person checked and recorded it;",
        "  DERIVED = calculated by this app from other readings; AI OPINION = a model looking at pixels; NOTE = commentary, not evidence.",
    ]
    from . import store as _store
    seen = set()
    notes = []
    for e in evidence[:limit]:
        e = _store.with_origin(e)
        if e["origin"] == "NOTE":
            notes.append(e)
            continue
        k = (e.get("field"), e.get("value"))
        if k in seen:
            continue
        seen.add(k)
        lines.append(f"  - {e.get('field')}: {e.get('value')}   "
                     f"[{e['origin_label']} | {e.get('source')} | {e.get('confidence')} | {e.get('evidence_type')}"
                     + (f" | as of {e['effective_date']}" if e.get("effective_date") else "")
                     + (f" | {e['ref']}" if e.get("ref") else "")
                     + (" | superseded by a newer reading" if e.get("superseded") else "")
                     + (" | CONFLICT open" if e.get("conflict") else "")
                     + "]")
    human_notes = [n.get("body") for n in (prop.get("notes") or []) if n.get("body")] + [n.get("value") for n in notes]
    if human_notes:
        lines.append("")
        lines.append("NOTES (human commentary, UNVERIFIED - never evidence, never a fact):")
        for n in human_notes[:12]:
            lines.append(f"  - NOTE: {str(n)[:300]}")
    sigs = prop.get("distress") or []
    if sigs:
        lines.append("")
        lines.append("SIGNALS WE DERIVED (these are observations, not verified facts):")
        for s in sigs:
            lines.append(f"  - {s['label']} ({s['confidence']} confidence). {s['why']}")
    lines.append("")
    lines.append("THINGS NOBODY HAS CHECKED YET:")
    for unknown in _unknowns(prop):
        lines.append(f"  - {unknown}")
    return "\n".join(lines)


def _unknowns(prop: dict) -> list[str]:
    out = []
    if not prop.get("tax_status"):
        out.append("whether the taxes are current or delinquent")
    if not prop.get("zoning"):
        out.append("the zoning district and what uses it allows")
    out.append("title: deeds, liens, easements, judgements")
    out.append("the physical condition of anything standing on the parcel")
    if not prop.get("list_price"):
        out.append("whether it is for sale, and at what price")
    if prop.get("flood_zone") is None:
        out.append("the FEMA flood zone")
    out.append("legal access - a recorded easement or road frontage")
    return out


def fallback_summary(prop: dict) -> str:
    """Used when no model is reachable. Describes evidence, invents nothing."""
    bits = []
    where = prop.get("address") or f"parcel {prop.get('parcel_id')}"
    bits.append(f"Here is the simple version of what we hold on {where}.")
    if prop.get("total_value"):
        bits.append(f"The county assesses it at ${prop['total_value']:,.0f} "
                    f"(${prop.get('land_value') or 0:,.0f} land, "
                    f"${prop.get('imp_value') or 0:,.0f} improvements).")
    sigs = prop.get("distress") or []
    if sigs:
        bits.append("What caught our attention: "
                    + "; ".join(s["label"].lower() for s in sigs[:4]) + ".")
    else:
        bits.append("Nothing in the record stands out as distressed.")
    bits.append("What nobody has checked yet: " + "; ".join(_unknowns(prop)[:4]) + ".")
    bits.append("No local AI model is running right now, so this is a plain readout "
                "of the stored evidence rather than an analysis.")
    return " ".join(bits)

def ask_json(prompt: str, *, system: str = SYSTEM, model: str | None = None, temperature: float = 0.1,
             max_tokens: int = 1400, timeout: float | None = None) -> tuple[dict | None, dict]:
    """Structured answer through the established Ollama chat call (format=json). Never cached, never
    guessed: returns (parsed object or None, meta{model, error, raw}). A model that is not installed,
    an unreachable backend, a timeout, an empty or non-JSON reply all come back as an explicit error."""
    chosen = model or pick_model()
    if not chosen:
        return None, {"model": "", "error": "no local model reachable", "raw": ""}
    if chosen not in available_models():
        return None, {"model": chosen, "error": f"model {chosen!r} is not installed locally", "raw": ""}
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat",
                       json={"model": chosen, "stream": False, "format": "json",
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                             "options": {"temperature": temperature, "num_ctx": AI_NUM_CTX, "num_predict": max_tokens}},
                       timeout=timeout or AI_TIMEOUT)
        r.raise_for_status()
        text = (r.json().get("message") or {}).get("content", "").strip()
    except Exception as exc:
        return None, {"model": chosen, "error": f"{type(exc).__name__}: {exc}"[:300], "raw": ""}
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        return None, {"model": chosen, "error": "empty response", "raw": ""}
    try:
        obj = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
        except ValueError:
            obj = None
        if obj is None:
            return None, {"model": chosen, "error": "response was not valid JSON", "raw": text[:2000]}
    return obj, {"model": chosen, "error": None, "raw": text[:2000]}
