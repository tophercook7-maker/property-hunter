"""Uploaded files: Topher's own photos, voice notes and documents (spec 30/42/43/44).

Everything lands under data/files/<property_id>/<kind>/ with a safe generated
name. Nothing here is fetched from the internet - these are the things a person
brings back from a drive-by, and they are stored as exactly that.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from . import db, store
from .config import FILES_DIR
from .db import utcnow

PHOTO_KINDS = {"street", "aerial", "satellite", "listing", "county", "code", "owner",
               "inspection", "rehab", "before", "during", "after"}
DOC_CATEGORIES = {"deed", "tax", "gis", "zoning", "code", "lien", "auction", "listing",
                  "inspection", "contractor", "receipt", "financing", "closing", "lease",
                  "other"}
MAX_BYTES = 60 * 1024 * 1024
SAFE_EXT = re.compile(r"^[a-z0-9]{1,8}$")


def _dest(prop_id: int, kind: str, original: str) -> Path:
    ext = (Path(original or "").suffix.lstrip(".").lower() or "bin")
    if not SAFE_EXT.match(ext):
        ext = "bin"
    d = FILES_DIR / str(prop_id) / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{utcnow()[:10]}-{uuid.uuid4().hex[:10]}.{ext}"


def _save(prop_id: int, kind: str, upload, original: str) -> tuple[Path, int]:
    dest = _dest(prop_id, kind, original)
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = upload.read(1024 * 512)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError(f"file larger than {MAX_BYTES // (1024*1024)} MB")
            out.write(chunk)
    return dest, size


def rel(path: Path) -> str:
    return str(path.relative_to(FILES_DIR))


def save_photo(prop_id: int, upload, original: str, kind: str = "inspection",
               caption: str = "", author: str = "Topher") -> dict:
    kind = kind if kind in PHOTO_KINDS else "inspection"
    dest, size = _save(prop_id, "photos", upload, original)
    cur = db.ex(
        "INSERT INTO photos(property_id,kind,url,local_path,source,source_url,license,"
        "captured_at,confidence,caption,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (prop_id, kind, f"/files/{rel(dest)}", str(dest), f"{author} - own photo", None,
         "owner", utcnow(), "HIGH", caption or None, utcnow()))
    store.add_timeline(prop_id, "photo", f"Photo added ({kind})", caption or "")
    store.store_evidence(prop_id, [{
        "field": f"photo:{kind}", "value": caption or f"{kind} photo on file",
        "evidence_type": "OBSERVATION", "confidence": "HIGH",
        "source": "topher_photo", "source_name": f"{author}'s own photo",
        "source_url": f"/files/{rel(dest)}",
        "raw_ref": "A photo shows what was visible at that moment. It does not say "
                   "what is behind the wall or under the roof."}])
    return {"id": cur.lastrowid, "url": f"/files/{rel(dest)}", "kind": kind, "bytes": size}


def transcribe(path: Path) -> str | None:
    """Best-effort local transcription. Returns None when it cannot run."""
    exe = shutil.which("whisper")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, str(path), "--model", "base", "--language", "en",
             "--output_format", "txt", "--output_dir", str(path.parent), "--fp16", "False"],
            capture_output=True, text=True, timeout=180)
        txt = path.with_suffix(".txt")
        if out.returncode == 0 and txt.exists():
            # whisper writes one segment per line; a note reads better as prose
            text = " ".join(line.strip() for line in txt.read_text().splitlines()
                            if line.strip())
            txt.unlink(missing_ok=True)
            return text or None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def save_voice(prop_id: int, upload, original: str, transcript: str = "",
               author: str = "Topher") -> dict:
    dest, size = _save(prop_id, "voice", upload, original)
    text = (transcript or "").strip()
    auto = False
    if not text:
        got = transcribe(dest)
        if got:
            text, auto = got, True
    body = text or "(voice note - not yet transcribed)"
    cur = db.ex(
        "INSERT INTO notes(property_id,kind,body,author,confidence,audio_path,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (prop_id, "voice_note", body, author, "UNVERIFIED", f"/files/{rel(dest)}", utcnow()))
    store.add_timeline(prop_id, "note", "Voice note added", body[:200])
    # P3A: the transcript is a NOTE (kept in `notes`, confidence UNVERIFIED); it is never an evidence row.

    return {"id": cur.lastrowid, "url": f"/files/{rel(dest)}", "bytes": size,
            "transcript": text, "auto_transcribed": auto,
            "note": (None if text else
                     "No transcript. Local whisper is not installed or could not read "
                     "the file - you can type one on the note.")}


def save_document(prop_id: int, upload, original: str, category: str = "other",
                  title: str = "", notes: str = "") -> dict:
    category = category if category in DOC_CATEGORIES else "other"
    dest, size = _save(prop_id, "documents", upload, original)
    title = title or Path(original or "document").name
    cur = db.ex(
        "INSERT INTO documents(property_id,category,title,path,url,notes,added_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (prop_id, category, title, str(dest), f"/files/{rel(dest)}", notes or None, utcnow()))
    store.add_timeline(prop_id, "document", f"Document added: {title}", category)
    return {"id": cur.lastrowid, "url": f"/files/{rel(dest)}", "category": category,
            "title": title, "bytes": size}
