"""The published snapshot must never carry an owner's street mailing address.

The county namelist is, functionally, a mailing list for every property owner
in Garland County. Its derived facts are worth publishing -- which state a tax
bill goes to, how many parcels one owner holds -- and the street line is not.
This got published once already and was removed by hand; this test is what
stops it coming back the next time a builder gains a field.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"

# "123 MAIN ST" / "PO BOX 41912" as a JSON *value*, which is what a leaked
# mailing address looks like. Situs addresses live under their own keys and are
# meant to be public, so this looks for the value shape, then filters by key.
STREET = re.compile(r"^(?:\d+\s+[A-Z0-9].*|P\.?O\.?\s*BOX\s*\d+.*)$", re.I)
MAIL_KEYS = {"m", "mail", "mailing", "mailing_address", "MailingAdd", "owner_mailing",
             "ADDRESS1", "ADDRESS2", "CSZ"}


def _published_json():
    for p in sorted(DOCS.rglob("*.json")):
        yield p


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
            if k in MAIL_KEYS and isinstance(v, str) and STREET.match(v.strip()):
                yield f"{path}.{k}", v
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5000]):
            yield from _walk(v, f"{path}[{i}]")


def test_no_owner_mailing_addresses_in_published_json():
    offenders = []
    for p in _published_json():
        try:
            doc = json.loads(p.read_text())
        except Exception:
            continue
        for where, val in _walk(doc):
            offenders.append(f"{p.relative_to(DOCS)}{where} = {val!r}")
            if len(offenders) > 5:
                break
        if len(offenders) > 5:
            break
    assert not offenders, "owner mailing addresses published:\n  " + "\n  ".join(offenders)


def test_owners_file_publishes_state_but_not_street():
    f = DOCS / "data" / "owners.json"
    if not f.exists():
        pytest.skip("owners.json not built yet")
    doc = json.loads(f.read_text())
    blob = json.dumps(doc)
    for h in doc.get("top_holders", []):
        assert set(h) <= {"owner", "parcels", "appraised", "state", "village",
                          "located", "entity", "sample"}, f"unexpected field on a holder: {sorted(h)}"
        assert h.get("state") is None or len(h["state"]) == 2, "state must be a 2-letter code, not an address"
    assert "ADDRESS1" not in blob and "CSZ" not in blob


def test_owners_file_counts_the_village_by_polygon_not_by_name():
    f = DOCS / "data" / "owners.json"
    if not f.exists():
        pytest.skip("owners.json not built yet")
    doc = json.loads(f.read_text())
    assert "polygon" in (doc.get("village_note") or "").lower()
    # a holder whose parcels were never located must report village as unknown, not 0
    for h in doc.get("top_holders", []):
        if h.get("located") == 0:
            assert h.get("village") is None, "unlocated parcels must not be reported as 'not in the Village'"
