"""Plain-English questions -> structured answers (spec 50 / 73).

This is the surface Daniel (or anybody) talks to. It is deliberately
deterministic: a question is matched to an intent, the intent calls the same
functions the UI uses, and the answer is the real data plus a sentence about
it. No model is asked to invent an answer; the local model is only used, when
the rules cannot parse the question, to map it onto search filters.
"""
from __future__ import annotations

import re

from . import analyzers, db, learning, nlsearch, reports, scanner, scoring, store

INTENTS = [
    ("whats_new", r"\b(what'?s new|new (properties|opportunities|things)|anything new)\b"),
    ("what_changed", r"\b(what('?s| has)? changed|any changes|what moved)\b"),
    ("investigate", r"\b(which|what) (property |one )?(should i|to) (investigate|look at)"
                    r"|investigate (first|next)|where (should|do) i start\b"),
    ("best", r"\b(best|top) (current )?(opportunity|deal|pick|property|one)\b|\bmy best\b"),
    ("avoid", r"\b(avoid|stay away|worst|traps?)\b"),
    ("why_score", r"\bwhy (did|has) (the )?(score|ranking) (change|move|drop|rise)"
                  r"|\bscore (changed|change)\b"),
    ("more_attractive", r"\b(more attractive|improved|gotten better|moved up)\b"),
    ("scan_status", r"\b(scan|scanning|last scan|is it running|status)\b"),
    ("tasks", r"\b(to ?do|tasks?|what do i need to check|next actions?)\b"),
    ("explain", r"\b(explain|tell me about|what'?s going on with)\b"),
    ("search", r".*"),
]


def _prop_id_from(question: str) -> int | None:
    m = re.search(r"\b(?:property|id)\s*#?(\d+)\b", question, re.I)
    if m:
        return int(m.group(1))
    # an address in the question
    m = re.search(r"\b(\d{1,6})\s+([A-Za-z][A-Za-z .]{2,40})", question)
    if m:
        from .normalize import normalize_address
        norm = normalize_address(f"{m.group(1)} {m.group(2)}")
        row = db.q1("SELECT id FROM properties WHERE excluded=0 AND address_norm LIKE ? "
                    "LIMIT 1", (norm.split(" ")[0] + " " + norm.split(" ")[1] + "%",))
        if row:
            return row["id"]
    return None


def _card(pid: int) -> dict:
    p = store.get_property(pid) or {}
    sc = scoring.scores_for(pid)
    return {"id": pid, "address": p.get("address") or p.get("parcel_id"),
            "city": p.get("city"), "recommendation": p.get("recommendation"),
            "overall": (sc.get("overall") or {}).get("score"),
            "risk": (sc.get("risk") or {}).get("score"),
            "signals": [s["label"] for s in (p.get("distress") or [])][:4],
            "link": f"/#property/{pid}"}


def ask(question: str, use_ai: bool = False) -> dict:
    q = question.strip()
    intent = "search"
    for name, pat in INTENTS:
        if re.search(pat, q, re.I):
            intent = name
            break
    out = {"question": q, "intent": intent, "answer": "", "results": [], "links": []}

    if intent == "whats_new":
        rows = db.q("""SELECT p.id FROM properties p JOIN scores s ON s.property_id=p.id
                       AND s.kind='overall' WHERE p.excluded=0
                       AND p.first_seen >= datetime('now','-2 day')
                       ORDER BY s.score DESC LIMIT 8""")
        out["results"] = [_card(r["id"]) for r in rows]
        out["answer"] = (f"{len(out['results'])} new in the last two days." if rows
                         else "Nothing new in the last two days.")
        out["links"] = ["/#newprops"]

    elif intent == "what_changed":
        rows = db.q("""SELECT c.*, p.address FROM changes c JOIN properties p ON p.id=c.property_id
                       WHERE p.excluded=0 ORDER BY c.id DESC LIMIT 12""")
        out["results"] = [{"property_id": r["property_id"], "address": r["address"],
                           "field": r["field"], "from": r["old_value"], "to": r["new_value"],
                           "severity": r["severity"], "when": r["detected_at"],
                           "link": f"/#property/{r['property_id']}"} for r in rows]
        out["answer"] = (f"{len(rows)} recent changes; the most important are listed first."
                         if rows else "Nothing has changed since the last scan.")
        out["links"] = ["/#alerts"]

    elif intent in ("investigate", "best"):
        picks = analyzers.topher_picks(3)
        out["results"] = picks
        if picks:
            b = picks[0]
            out["answer"] = (f"I'd start with {b['address']} (score {b['score']:.0f}, "
                             f"risk {b['risk']:.0f}). Why: {'; '.join(b['why'][:2])}. "
                             f"Still unknown: {', '.join(b['unknown'][:2]) or 'title, condition'}. "
                             f"First step: {b['next_step']}.")
            out["links"] = [f"/#property/{b['id']}"]
        else:
            out["answer"] = "Nothing is scored yet - run a scan first."

    elif intent == "avoid":
        rows = db.q("""SELECT p.id FROM properties p JOIN scores s ON s.property_id=p.id
                       AND s.kind='risk' WHERE p.excluded=0 AND s.score>=60
                       ORDER BY s.score DESC LIMIT 5""")
        out["results"] = [_card(r["id"]) for r in rows]
        out["answer"] = (f"{len(rows)} carry enough risk that I would not spend time on them "
                         f"yet." if rows else "Nothing currently scores as a trap.")

    elif intent == "why_score":
        pid = _prop_id_from(q)
        if not pid:
            out["answer"] = "Tell me which property (an address or 'property 63')."
        else:
            sc = scoring.scores_for(pid).get("overall") or {}
            changes = db.rows_to_dicts(db.q(
                "SELECT field,old_value,new_value,detected_at FROM changes "
                "WHERE property_id=? ORDER BY id DESC LIMIT 6", (pid,)))
            out["results"] = [{"score_lines": sc.get("lines", []),
                               "recent_changes": changes}]
            out["answer"] = (f"The score is built from {len(sc.get('lines', []))} lines; "
                             + (f"{len(changes)} underlying facts changed recently."
                                if changes else "no underlying facts have changed - "
                                "the score moved because the weights or the evidence "
                                "on file did."))
            out["links"] = [f"/#property/{pid}"]

    elif intent == "more_attractive":
        rows = db.q("""SELECT DISTINCT c.property_id FROM changes c JOIN properties p
                       ON p.id=c.property_id WHERE p.excluded=0 AND c.field IN
                       ('list_price','tax_status','owner_name','listing_status')
                       ORDER BY c.id DESC LIMIT 8""")
        out["results"] = [_card(r["property_id"]) for r in rows]
        out["answer"] = (f"{len(rows)} had a price, tax, listing or owner change recently - "
                         "those are the ones that get more attractive." if rows
                         else "No price, tax or owner changes recorded yet.")

    elif intent == "scan_status":
        cur = scanner.current()
        last = scanner.last_scan()
        if scanner.is_running() and cur:
            running = [s for s in cur.stages if s.status == "running"]
            out["answer"] = (f"A {cur.mode} scan is running - on '{running[0].label}'."
                             if running else "A scan is running.")
        elif last:
            out["answer"] = (f"Last scan ({last['mode']}) finished {last['status']} at "
                             f"{last.get('finished_at') or last.get('started_at')}; "
                             f"{(last.get('stats') or {}).get('records_examined', 0)} records "
                             f"examined.")
        else:
            out["answer"] = "No scan has run yet."
        out["results"] = [last or {}]
        out["links"] = ["/#scan"]

    elif intent == "tasks":
        rows = db.q("""SELECT t.id,t.title,t.property_id,p.address FROM tasks t
                       LEFT JOIN properties p ON p.id=t.property_id
                       WHERE t.status='open' ORDER BY t.priority, t.id LIMIT 8""")
        out["results"] = db.rows_to_dicts(rows)
        out["answer"] = f"{len(rows)} things to check, highest priority first."
        out["links"] = ["/#tasks"]

    elif intent == "explain":
        pid = _prop_id_from(q)
        if not pid:
            out["answer"] = "Which property? Give me an address or 'property 63'."
        else:
            p = store.get_property(pid)
            ex = analyzers.explain(p, use_ai=use_ai)
            out["answer"] = ex["text"]
            out["results"] = [_card(pid)]
            out["links"] = [f"/#property/{pid}"]
            out["model"] = ex.get("model", "")

    else:  # search
        parsed = nlsearch.parse_with_ai(q) if use_ai else nlsearch.parse(q)
        from .api import api_properties
        f = dict(parsed["filters"])
        sort = f.pop("sort", "overall")
        r = api_properties(sort=sort, limit=12, **f)
        out["results"] = [_card(p["id"]) for p in r["properties"]]
        out["interpreted"] = parsed["interpreted"]
        out["answer"] = (f"{r['total']} match" + (f" ({', '.join(parsed['interpreted'])})."
                                                   if parsed["interpreted"] else
                                                   " - I could not read any filters, so this is "
                                                   "everything, best first."))
        if r.get("preferences", {}).get("influenced"):
            out["answer"] += " Your past passes influenced this order."
        out["links"] = ["/#props"]

    out["disclaimer"] = ("Research, not advice. Hot Springs Village and Diamondhead are "
                         "always excluded.")
    return out
