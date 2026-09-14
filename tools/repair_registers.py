"""Repair City register records that were attached to the wrong parcel.

The State parcel layer's `camakey` is the county RPID, so every register and
lien record has ONE exact parcel. For each property whose RPID resolves to a
different parcel than the one it carries, this script:

  1. strips the register-derived rows (evidence, timeline, aliases, the rpid
     column, register-supplied address) from the wrong property,
  2. resets that property's address to what the State roll says for its parcel,
  3. logs the correction in `changes`,

and then a `city_registers` scan re-ingests the register with the fixed
`attach_parcel`, which lands each record on its true parcel.

    python3 tools/repair_registers.py          # dry run, prints the plan
    python3 tools/repair_registers.py --apply  # do it
"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q, ex  # noqa: E402
from hunter.sources.hot_springs import parcel_for_rpid  # noqa: E402
from hunter.sources.ar_parcels import SERVICE, LAYER  # noqa: E402
from hunter.http import arcgis_query  # noqa: E402
from hunter.normalize import normalize_address, title_case  # noqa: E402

REGISTER_SOURCES = ("hs_gis_vacant", "hs_gis_liens", "hs_gis_owner_mailing", "hs_gis_code_cases")


def state_address(parcel_id: str) -> str | None:
    data = arcgis_query(SERVICE, LAYER, where=f"parcelid='{parcel_id}'", out_fields="adrlabel")
    f = data.get("features") or []
    if not f:
        return None
    label = " ".join((f[0]["attributes"].get("adrlabel") or "").split())
    return title_case(label) if label else None


def plan():
    rows = q("""SELECT p.id, p.address, p.address_norm, p.parcel_id, p.rpid, a.alias_value AS arpid
                FROM properties p JOIN property_aliases a ON a.property_id=p.id AND a.alias_type='rpid'
                WHERE p.excluded=0 AND p.parcel_id IS NOT NULL""")
    fixes = []
    for r in rows:
        truth = parcel_for_rpid(r["arpid"])
        if truth and truth != r["parcel_id"]:
            fixes.append({"id": r["id"], "address": r["address"], "wrong_parcel": r["parcel_id"],
                          "rpid": r["arpid"], "true_parcel": truth})
    return fixes


def apply(fixes):
    for f in fixes:
        pid, rpid = f["id"], f["rpid"]
        # rows that came from the register for THIS rpid
        ex("DELETE FROM evidence WHERE property_id=? AND source IN (?,?,?,?) AND "
           "(value LIKE ? OR raw_ref LIKE ? OR field IN ('parcel_id','owner_name','owner_mailing_address','absentee_owner'))",
           (pid, *REGISTER_SOURCES, f"%RPID {rpid}%", f"%{rpid}%"))
        ex("DELETE FROM evidence WHERE property_id=? AND source IN ('hs_gis_vacant','hs_gis_liens') "
           "AND field IN ('vacant_structure','cleanup_lien','cleanup_lien_amount','vacant_per_lien_record',"
           "'zoning_on_lien_record','additional_rpid')", (pid,))
        ex("DELETE FROM timeline WHERE property_id=? AND source IN ('hs_gis_vacant','hs_gis_liens')", (pid,))
        ex("DELETE FROM property_aliases WHERE property_id=? AND alias_type='rpid'", (pid,))
        ex("UPDATE properties SET rpid=NULL WHERE id=?", (pid,))
        # the address: keep only what the State roll says for the parcel we are keeping
        addr = state_address(f["wrong_parcel"])
        norm = normalize_address(addr) if addr else None
        ex("DELETE FROM property_aliases WHERE property_id=? AND alias_type='address'", (pid,))
        ex("UPDATE properties SET address=?, address_norm=? WHERE id=?", (addr, norm, pid))
        if addr and norm:
            ex("INSERT INTO property_aliases(property_id,alias_type,alias_value,source) VALUES (?,?,?,?)",
               (pid, "address", f"05051|{norm}", "repair_registers"))
        ex("INSERT INTO changes(property_id,field,old_value,new_value,source,severity,detected_at) "
           "VALUES (?,?,?,?,?,?,datetime('now'))",
           (pid, "register_attachment", f"RPID {rpid} (wrong: register polygon guess)",
            f"moved to parcel {f['true_parcel']} via State camakey", "repair_registers", "high"))
    # forget every polygon-based guess; the exact rpid cells stay
    ex("DELETE FROM parcel_lookup WHERE cell NOT LIKE 'rpid:%'")


if __name__ == "__main__":
    init_db()
    fixes = plan()
    print(f"{len(fixes)} register attachments point at the wrong parcel")
    for f in fixes:
        print(json.dumps(f))
    json.dump(fixes, open(os.path.join(ROOT, "data", "register_repair.json"), "w"), indent=1)
    if "--apply" in sys.argv:
        apply(fixes)
        print("applied; now run a city_registers scan")
