#!/usr/bin/env python3
"""Publish one workup's Property File as a PUBLIC SAMPLE: docs/samples/<slug>.json + docs/samples/index.json.

Deliberate, per file, by the license holder. Everything private to the license is removed before it is written:
owner mailing address and every reading derived from it, the manual research log (tasks, documents, manual
evidence values), outreach material, Bee text, notes, license and session identifiers, search ids, task ids.
What stays is what the public roll and public sources already say, with the same sources and dates.

    python3 tools/publish_sample.py --workup 5 [--slug darby-aly-lot] [--note "..."]
    python3 tools/publish_sample.py --list
    python3 tools/publish_sample.py --remove darby-aly-lot
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter import db, workup  # noqa: E402
from hunter.db import utcnow  # noqa: E402

SAMPLES = os.path.join(ROOT, "docs", "samples")
PRIVATE_FIELDS = {"owner_mailing_address", "owner_mailing_check", "absentee_owner", "owner_occupancy", "manual:mailing_address"}
PRIVATE_PREFIXES = ("manual:", "photo:")
PUBLIC_EVENT_CLASSES = {"SIGNAL RECEIVED", "INVESTIGATION OPENED", "SOURCE CHECK", "QUESTION ANSWERED", "QUESTION REMAINS UNKNOWN", "WORKUP STARTED", "WORKUP COMPLETED",
                        "INVESTIGATION CHECK STARTED", "INVESTIGATION CHECK PRODUCED EVIDENCE", "INVESTIGATION CHECK SUCCEEDED", "INVESTIGATION CHECK FAILED", "INVESTIGATION CHECK BLOCKED", "QUESTION REFRESHED"}


def _private_item(i: dict) -> bool:
    f = i.get("field") or ""
    return f in PRIVATE_FIELDS or f.startswith(PRIVATE_PREFIXES) or i.get("origin") == "MANUAL_VERIFICATION"


def _public_question(q: dict) -> dict:
    """A question a person answered is shown as UNKNOWN in public: the person's answer is theirs, not the record's."""
    if q.get("checked_by") == "manual":
        return dict(q, state="UNKNOWN", answer="answered by the license holder's own research (private)", source=None, checked_at=None, refs=[], checked_by=None)
    return dict(q, refs=q.get("refs") or [])


def redact(f: dict, w: dict, *, note: str) -> dict:
    ident = dict(f["identity"])
    ident.pop("search_id", None); ident.pop("actor", None)
    ident["verified_by"] = "AUTOMATED_SOURCE" if ident.get("verified_by") == "AUTOMATED_SOURCE" else "HUMAN_SELECTION"
    sections = {}
    for k, s in f["sections"].items():
        items = [i for i in s["items"] if not _private_item(i)]
        sec = {"label": s["label"], "check": s["check"], "items": items, "known": [i for i in items if i["state"] == "FOUND"], "checked_not_found": [i for i in items if i["state"] == "NOT_FOUND"],
               "questions": [_public_question(q) for q in s["questions"]], "attempt": {kk: s["attempt"].get(kk) for kk in ("domain", "label", "source", "check_type", "status", "failure_category", "reused", "ms")} if s.get("attempt") else None}
        if k == "OWNER_MAILING":
            sec["attempt"] = dict(sec["attempt"] or {}, status="PRIVATE", failure_category=None) if sec["attempt"] else None
            sec["items"] = [i for i in sec["items"] if i["field"] == "owner_name"]; sec["known"] = [i for i in sec["known"] if i["field"] == "owner_name"]; sec["checked_not_found"] = []
            sec["questions"] = [dict(q, state="PRIVATE" if q["key"] == "mailing_address" else q["state"], answer="mailing details are private to the license holder" if q["key"] == "mailing_address" else q["answer"]) for q in sec["questions"]]
        for kk in ("legal_access", "status"):
            if kk in s:
                sec[kk] = s[kk]
        sections[k] = sec
    tax = dict(f["tax"])
    listing = dict(f["listing"]); listing["private_listing"] = {"state": "PRIVATE"}
    ledger = [{kk: a.get(kk) for kk in ("domain", "label", "source", "check_type", "status", "failure_category", "evidence_count", "reused", "ms")} for a in f["checks_performed"]]
    for a in ledger:
        if a["domain"] == "OWNER_MAILING":
            a["status"] = "PRIVATE"; a["failure_category"] = None; a["evidence_count"] = None
    failures = [{kk: x.get(kk) for kk in ("domain", "source", "status", "category", "at")} for x in f["source_failures"]]
    actions = [{kk: a.get(kk) for kk in ("domain", "question", "wording", "what", "why", "resolves", "source_status")} for a in f["next_actions"] if a["question"] != "mailing_address"]
    # open questions: a person's own wording never leaves the license; answered-by-person questions are shown as open
    unknown = [dict(q, answer=("answered by the license holder's own research (private)" if str(q.get("answer") or "").startswith("MANUAL VERIFICATION") else q.get("answer"))) for q in f["what_we_dont_know"]]
    events = [{kk: e.get(kk) for kk in ("at", "cls", "title")} for e in f["timeline"] if e.get("cls") in PUBLIC_EVENT_CLASSES]
    evidence = [{kk: e.get(kk) for kk in ("ref", "field", "value", "source_name", "date", "recorded_at", "etype", "conf", "state", "verification", "superseded", "conflict")}
                for e in f["evidence"] if not _private_item({"field": e.get("field"), "origin": e.get("origin")})]
    known = [i for i in f["what_we_know"] if not _private_item(i)]
    cnf = [i for i in f["checked_not_found"] if not _private_item(i)]
    wv = {"workup_id": "sample", "status": w["status"], "status_label": w["status_label"], "version": w["version"], "started_at": w["started_at"], "completed_at": w["completed_at"], "actor": "license holder",
          "timings": {"total_ms": (w.get("timings") or {}).get("total_ms")}, "error": None, "progress": [{"domain": x["domain"], "label": x["label"], "done": x["done"], "status": ("PRIVATE" if x["domain"] == "OWNER_MAILING" else x["status"])} for x in w["progress"]]}
    return {"sample": True, "sample_note": note, "published_at": utcnow(), "workup_view": wv,
            "workup": {kk: wv[kk] for kk in ("status", "status_label", "version", "started_at", "completed_at", "actor", "timings", "error")},
            "identity": ident, "what_we_know": known, "checked_not_found": cnf, "tax": tax, "sections": sections, "listing": listing, "what_we_dont_know": unknown,
            "source_failures": failures, "conflicts": [{kk: c.get(kk) for kk in ("field", "source_a", "date_a", "source_b", "date_b", "status")} for c in f["conflicts"] if not str(c.get("field", "")).startswith("manual:")],
            "checks_performed": ledger, "next_actions": actions, "outreach_gate": None, "manual_research": None,
            "investigation": ({"id": None, "status": f["investigation"]["status"], "counts": f["investigation"]["counts"], "signals": f["investigation"]["signals"]} if f.get("investigation") else None),
            "timeline": events, "evidence": evidence,
            "links": {"investigation": None, "roll_file": f["links"]["roll_file"], "search": None}}


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60]


def bee_read(case_id: int, *, run: bool) -> dict | None:
    """Bee's latest OK analysis of the case, or a fresh one when asked. Private field values never appear (Bee's snapshot
    already redacts them). Shown on the public sample as AI OPINION: it explains and proposes; it establishes nothing."""
    from hunter import bee
    if run:
        try:
            bee.run(case_id, actor="publisher")
        except Exception as exc:                                  # a slow or absent model is not a reason to fail publishing
            print("bee run skipped:", exc)
    v = bee.view(case_id)
    a = v.get("latest") if (v.get("latest") or {}).get("status") == "OK" else v.get("latest_ok")
    if not a:
        return None
    out = a.get("output") or {}
    checks = [{"question": c.get("question"), "what": c.get("what") or c.get("check") or c.get("label"), "why": c.get("why") or c.get("reason")} for c in (out.get("proposed_checks") or [])[:6]]
    return {"origin": "AI_OPINION", "label": "Bee's read (AI opinion; explains and proposes, establishes nothing)", "model": a.get("model"), "at": a.get("created_at"),
            "summary": (out.get("summary") or "")[:900], "unknown": [str(x.get("text") if isinstance(x, dict) else x)[:200] for x in (out.get("unknown") or [])[:6]],
            "conflicts": [str(x.get("text") if isinstance(x, dict) else x)[:200] for x in (out.get("conflicts") or [])[:4]], "proposed_checks": checks}


def publish(workup_id: int, slug: str | None, note: str, *, bee: bool = False, bee_run: bool = False) -> dict:
    w = workup.view(workup_id)
    if not w:
        raise SystemExit(f"no workup {workup_id}")
    if w["status"] in ("RUNNING", "NOT_STARTED", "FAILED"):
        raise SystemExit(f"workup {workup_id} is {w['status']}; publish a completed workup")
    f = workup.property_file(workup_id)
    ident = f["identity"]
    slug = _slug(slug or f"{ident.get('situs') or ident.get('parcel_id')}-{ident.get('county')}")
    out = redact(f, w, note=note)
    out["bee"] = bee_read(w["investigation_id"], run=bee_run) if (bee and w.get("investigation_id")) else None
    os.makedirs(SAMPLES, exist_ok=True)
    path = os.path.join(SAMPLES, f"{slug}.json")
    text = json.dumps(out, indent=1)
    # no person's name leaves the license: every actor seen on this case is replaced in the text
    # people, not sources: the names people typed as actors on workups, research tasks and searches for this property
    names = {w["actor"]} | {r["actor"] for r in db.q("SELECT DISTINCT actor FROM research_tasks WHERE property_id=?", (w["property_id"],)) if r["actor"]} | \
            {r["actor"] for r in db.q("SELECT DISTINCT actor FROM address_searches WHERE selected_property_id=?", (w["property_id"],)) if r["actor"]} | \
            {r["selected_actor"] for r in db.q("SELECT DISTINCT selected_actor FROM address_searches WHERE selected_property_id=?", (w["property_id"],)) if r["selected_actor"]}
    for n in sorted((x for x in names if x and len(x) > 2 and x.lower() not in ("property_hunter", "evidence", "user", "license holder")), key=len, reverse=True):
        text = text.replace(json.dumps(n)[1:-1], "the license holder")
    open(path, "w").write(text)
    out = json.loads(text)
    idx_path = os.path.join(SAMPLES, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else {"samples": []}
    counts = f["investigation"]["counts"] if f.get("investigation") else {}
    entry = {"slug": slug, "title": f"{ident.get('situs') or 'parcel ' + str(ident.get('parcel_id'))}, {ident.get('city') or ''}".strip(", "), "county": ident.get("county"), "parcel_id": ident.get("parcel_id"), "rpid": ident.get("rpid"),
             "status": w["status"], "workup_completed_at": w["completed_at"], "published_at": out["published_at"],
             "found": counts.get("found"), "not_found": counts.get("not_found"), "unknown": counts.get("unknown"),
             "headline": f"Tax state {f['tax']['tax_state'].replace('_', ' ')} · {f['listing']['sale_state'].replace('_', ' ')} · {len(f['source_failures'])} source failure{'s' if len(f['source_failures']) != 1 else ''} · {len(actions_public(out))} open actions"}
    idx["samples"] = [e for e in idx["samples"] if e["slug"] != slug] + [entry]
    idx["built_at"] = utcnow()
    json.dump(idx, open(idx_path, "w"), indent=1)
    return {"slug": slug, "path": path, "bytes": os.path.getsize(path)}


def actions_public(out: dict) -> list:
    return out["next_actions"]


def remove(slug: str) -> None:
    idx_path = os.path.join(SAMPLES, "index.json")
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path)); idx["samples"] = [e for e in idx["samples"] if e["slug"] != slug]; json.dump(idx, open(idx_path, "w"), indent=1)
    p = os.path.join(SAMPLES, f"{slug}.json")
    if os.path.exists(p):
        os.remove(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workup", type=int)
    ap.add_argument("--slug")
    ap.add_argument("--note", default="A real parcel; the file is exactly what the workup produced, with private research and mailing details removed.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove")
    ap.add_argument("--bee", action="store_true", help="include Bee's latest OK read of the case as AI OPINION")
    ap.add_argument("--bee-run", action="store_true", help="ask Bee for a fresh read first (needs the local model)")
    a = ap.parse_args()
    if a.list:
        for r in db.q("SELECT id, property_id, status, completed_at FROM workups ORDER BY id DESC LIMIT 20"):
            print(dict(r))
    elif a.remove:
        remove(a.remove); print("removed", a.remove)
    elif a.workup:
        print(publish(a.workup, a.slug, a.note, bee=a.bee or a.bee_run, bee_run=a.bee_run))
    else:
        ap.print_help()
