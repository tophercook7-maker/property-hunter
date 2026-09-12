"""The rules that matter more than the features (spec 52, 70, 81, 88)."""
import inspect
import re
from pathlib import Path

import pytest

from conftest import make_record
from hunter import ai, analyzers, distress, scoring, store

SRC = Path(__file__).resolve().parent.parent / "hunter"


def test_ai_prompt_forbids_invention():
    s = ai.SYSTEM.lower()
    for phrase in ("only the evidence", "never invent", "i do not know" if False else "i don't know",
                   "never state a legal conclusion", "delinquent taxes",
                   "a parcel class is not zoning", "a mapped road is not legal access"):
        assert phrase in s, f"the AI system prompt is missing: {phrase}"


def test_ai_falls_back_without_inventing():
    pid, _, _ = store.ingest(make_record())
    p = store.get_property(pid)
    distress.refresh(p)
    p = store.get_property(pid)
    out = analyzers.explain(p, use_ai=False)
    assert out["model"] == ""
    assert "no local ai model" in out["text"].lower()
    # it may only repeat numbers it was given
    for n in re.findall(r"\$([\d,]+)", out["text"]):
        assert n.replace(",", "") in {"24250", "23200", "1050"}


def test_evidence_block_marks_what_was_never_checked():
    pid, _, _ = store.ingest(make_record())
    p = store.get_property(pid)
    block = ai.evidence_block(p, store.evidence_for(pid))
    assert "NOT CHECKED" in block
    assert "THINGS NOBODY HAS CHECKED YET" in block
    assert "title" in block.lower()


def test_no_module_claims_tax_payment_creates_ownership():
    """The single most dangerous thing this app could get wrong."""
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for m in re.finditer(r"[^.]*paying[^.]*tax[^.]*\.", text, re.I):
            sentence = m.group(0)
            assert re.search(r"not|never|does not", sentence, re.I), \
                f"{path.name}: {sentence.strip()[:160]}"


def test_deal_or_trap_is_conservative_when_evidence_is_thin():
    pid, _, _ = store.ingest(make_record())
    p = store.get_property(pid)
    distress.refresh(p)
    p = store.get_property(pid)
    scoring.compute(p)
    d = analyzers.deal_or_trap(store.get_property(pid))
    assert d["verdict"] in ("DEAL", "MAYBE", "PROBLEMATIC", "TRAP")
    assert "conservative" in d["note"].lower()


def test_next_steps_always_start_with_ownership_taxes_and_title():
    pid, _, _ = store.ingest(make_record())
    steps = analyzers.next_steps(store.get_property(pid))
    titles = " ".join(s["title"].lower() for s in steps[:4])
    assert "owns" in titles and "tax" in titles and "title" in titles


def test_business_use_never_promises_zoning_approval():
    pid, _, _ = store.ingest(make_record())
    p = store.get_property(pid)
    scoring.compute(p)
    u = analyzers.business_use_analysis(store.get_property(pid))
    assert "would approve" in u["warning"]
    assert u["warning"].lower().startswith("none of this means")


def test_every_distress_signal_says_how_to_confirm_it():
    pid, _, _ = store.ingest(make_record(owner_name="JONES ESTATE", imp_value=900.0))
    signals = distress.analyse(store.get_property(pid))
    assert signals
    for s in signals:
        assert s["verify"], f"signal {s['key']} has no way to confirm it"
        assert s["confidence"] in ("HIGH", "MEDIUM", "LOW", "NONE")
        assert s["kind"] in ("distress", "risk", "opportunity", "conflict")


def test_approval_list_covers_every_money_and_commitment_action():
    from hunter.config import APPROVAL_REQUIRED_ACTIONS
    for action in ("make_offer", "sign_contract", "spend_money", "borrow_money",
                   "contact_seller", "send_message", "transfer_property", "publish",
                   "legal_commitment"):
        assert action in APPROVAL_REQUIRED_ACTIONS


def test_no_hardcoded_secrets():
    pattern = re.compile(r"(api[_-]?key|secret|password|token)\s*=\s*[\"'][A-Za-z0-9/_+-]{16,}",
                         re.I)
    for path in SRC.rglob("*.py"):
        assert not pattern.search(path.read_text()), f"possible secret in {path}"


def test_demo_and_real_data_are_distinguishable():
    real, _, _ = store.ingest(make_record(parcel_id="300-1"))
    demo, _, _ = store.ingest(make_record(parcel_id="300-2", address="9 Demo St"),
                              data_class="demo")
    assert store.get_property(real)["data_class"] == "real"
    assert store.get_property(demo)["data_class"] == "demo"
