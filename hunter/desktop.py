"""Keep the Desktop folder current (Topher's front door to all of this).

After every scheduled scan, the folder gets today's briefing as plain text and
a fresh PDF for each Topher Pick. Old pick PDFs are removed so the folder never
shows a stale "PICK 1". Nothing else in the folder is touched.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import PORT

DESKTOP_DIR = Path(os.environ.get("PH_DESKTOP_DIR",
                                  Path.home() / "Desktop" / "🏠 Property Hunter"))
PICK_RE = re.compile(r"^PICK \d+ - .*\.pdf$")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -" else "" for c in name).strip()[:60]


def refresh(folder: Path | None = None, picks: int = 3) -> dict:
    """Rewrite briefing + pick PDFs. Returns what was written, honestly."""
    from . import pdf, reports
    from .analyzers import topher_picks
    folder = Path(folder or DESKTOP_DIR)
    if not folder.exists():
        return {"written": [], "skipped": "Desktop folder does not exist"}
    written, problems = [], []
    # briefing
    r = reports.morning_report()
    lines = [f"{r['greeting']} {r['headline']}", ""]
    if r["best_deal"]:
        b = r["best_deal"]
        lines.append(f"INVESTIGATE FIRST: {b['address']} (score {b['score']:.0f}, risk {b['risk']:.0f})")
        lines += [f"  + {w}" for w in b["why"][:3]]
        lines.append(f"  ? still unknown: {', '.join(b['unknown'][:3]) or '-'}")
        lines.append(f"  > next: {b['next_step']}")
        lines.append("")
    if r["new_opportunities"]:
        lines.append(f"{len(r['new_opportunities'])} NEW since yesterday:")
        lines += [f"  - {x['address'] or x['parcel_id']} (score {x['score']:.0f})"
                  for x in r["new_opportunities"][:6]]
        lines.append("")
    if r["important_changes"]:
        lines.append(f"{len(r['important_changes'])} IMPORTANT CHANGES:")
        lines += [f"  - {c['address'] or c['parcel_id']}: {c['field']} {c['old_value']} -> {c['new_value']}"
                  for c in r["important_changes"][:6]]
        lines.append("")
    if r["avoid"]:
        lines.append("LOOKS CHEAP BUT I'D AVOID:")
        lines += [f"  - {x['address'] or x['parcel_id']} (risk {x['score']:.0f})" for x in r["avoid"][:3]]
        lines.append("")
    lines.append("YOUR NEXT THREE ACTIONS:")
    lines += [f"  {i}. {t['title']}" + (f" - {t['property']}" if t.get("property") else "")
              for i, t in enumerate(r["next_three_actions"], 1)] or ["  (nothing queued)"]
    lines += ["", f"Open the app: http://127.0.0.1:{PORT}", f"Generated {r['generated_at']}", "",
              r["disclaimer"]]
    (folder / "THIS MORNING.txt").write_text("\n".join(lines))
    written.append("THIS MORNING.txt")
    # picks
    for old in folder.iterdir():
        if PICK_RE.match(old.name):
            old.unlink()
    for i, p in enumerate(topher_picks(picks), 1):
        try:
            data = pdf.html_to_pdf(reports.dossier_html(p["id"]))
        except Exception as exc:
            problems.append(f"{p['address']}: {exc}")
            continue
        name = f"PICK {i} - {_safe(p['address'] or p['id'])}.pdf"
        (folder / name).write_bytes(data)
        written.append(name)
    return {"written": written, "problems": problems, "folder": str(folder)}
