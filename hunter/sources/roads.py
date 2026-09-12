"""Arkansas GIS Office road centerlines (ROADS_ACF).

The authoritative 911 addressing centerline file. Two orders of magnitude faster
than querying a shared Overpass instance, and it carries the highway alias
(Central Ave is also AR 7), which is the best traffic proxy we can get without a
real traffic count.

What this tells us: there is a mapped road next to the parcel, what it is called
and what class it is. What it does NOT tell us: whether you have a legal right to
use it. That is in the deed, and the evidence we write says so.
"""
from __future__ import annotations

import json
import re

from ..http import arcgis_query
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

SERVICE = ("https://gis.arkansas.gov/arcgis/rest/services/"
           "FEATURESERVICES/Transportation/FeatureServer")
LAYER = 18                      # ROADS_ACF
FIELDS = ("pstr_fulnam,pstr_nam,pstr_type,pre_dir,psuf_dir,a1_str,a1_styp,"
          "a2_str,a2_styp,city_l,city_r,zip5_l,rd_class,rd_surftyp,date_ed")

# How much passing traffic a road implies. Same 0-9 scale the snow-cone and
# business models use.
INTERSTATE, US_HWY, STATE_HWY, MAJOR_ST, THROUGH_ST, LOCAL_ST, MINOR, NONE = (
    9, 8, 7, 6, 5, 3, 2, 0)

RANK_LABEL = {
    9: "interstate/limited access", 8: "US highway", 7: "state highway",
    6: "major street", 5: "through street", 3: "local street",
    2: "alley or service drive", 0: "no mapped road",
}

STREET_TYPE_RANK = {
    "HWY": STATE_HWY, "EXPY": INTERSTATE, "FWY": INTERSTATE,
    "BLVD": MAJOR_ST, "PKWY": MAJOR_ST, "AVE": THROUGH_ST,
    "ST": LOCAL_ST, "DR": LOCAL_ST, "RD": LOCAL_ST, "LN": LOCAL_ST,
    "CIR": MINOR, "CT": MINOR, "PL": MINOR, "TER": MINOR, "TRL": MINOR,
    "WAY": MINOR, "CV": MINOR, "ALY": MINOR, "PATH": MINOR, "LOOP": LOCAL_ST,
}

# "I 30", "US 270", "AR 7", "HWY 7"
RE_INTERSTATE = re.compile(r"^\s*(I|IH)[\s-]*\d+", re.I)
RE_US = re.compile(r"^\s*(US|U\.S\.)[\s-]*\d+", re.I)
RE_STATE = re.compile(r"^\s*(AR|SR|STATE (HWY|HIGHWAY)|HWY)[\s-]*\d+", re.I)


def classify(attrs: dict) -> tuple[int, str]:
    """Return (rank, label) for one road segment."""
    alias = " ".join(str(attrs.get(k) or "") for k in ("a1_str", "a1_styp")).strip()
    alias2 = " ".join(str(attrs.get(k) or "") for k in ("a2_str", "a2_styp")).strip()
    name = (attrs.get("pstr_fulnam") or "").strip()

    for candidate in (alias, alias2, name):
        if not candidate:
            continue
        if RE_INTERSTATE.match(candidate):
            return INTERSTATE, candidate
        if RE_US.match(candidate):
            return US_HWY, candidate
        if RE_STATE.match(candidate):
            return STATE_HWY, candidate

    stype = (attrs.get("pstr_type") or "").strip().upper()
    rank = STREET_TYPE_RANK.get(stype, LOCAL_ST)
    return rank, name or stype or "unnamed road"


class ArkansasRoads(PropertySource):
    name = "ar_gis_roads"
    label = "Arkansas GIS Office - road centerlines (911 addressing)"
    kind = "access"
    url = SERVICE
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            arcgis_query(SERVICE, LAYER, where="1=2", out_fields="pstr_fulnam")
            return SourceResult(status=OK, detail="road centerline layer answering")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="road centerline layer did not answer")

    def enrich(self, prop: dict, radius_m: float = 90.0, **kwargs) -> SourceResult:
        lat, lon = prop.get("lat"), prop.get("lon")
        if lat is None or lon is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        d = radius_m / 111000.0
        env = {"xmin": lon - d, "ymin": lat - d, "xmax": lon + d, "ymax": lat + d,
               "spatialReference": {"wkid": 4326}}
        try:
            data = arcgis_query(SERVICE, LAYER, where="1=1", out_fields=FIELDS,
                                extra={"geometry": json.dumps(env),
                                       "geometryType": "esriGeometryEnvelope",
                                       "inSR": 4326,
                                       "spatialRel": "esriSpatialRelIntersects"})
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail=f"road query failed: {exc}")

        feats = data.get("features") or []
        best_rank, best_name = NONE, None
        names = []
        edited = None
        for f in feats:
            a = f.get("attributes") or {}
            rank, name = classify(a)
            edited = edited or a.get("date_ed")
            if name:
                names.append(name)
            if rank > best_rank:
                best_rank, best_name = rank, name

        label = RANK_LABEL.get(best_rank, "local street")
        src_url = f"{SERVICE}/{LAYER}"
        eff = None
        if edited and len(str(edited)) == 8:
            e = str(edited)
            eff = f"{e[:4]}-{e[4:6]}-{e[6:]}"

        ev = [self.ev("road_access",
                      f"{label}" + (f" - {best_name}" if best_name else ""),
                      etype="OBSERVATION", confidence="HIGH" if feats else "MEDIUM",
                      source=self.name, source_name=self.label, source_url=src_url,
                      effective_date=eff,
                      raw_ref=f"nearest mapped centerline within {radius_m:.0f} m of the "
                              f"parcel centroid. A road on the map is NOT the same as a "
                              f"legal right to use it - that is in the deed or the plat.")]
        if not feats:
            ev.append(self.ev("legal_access", "no road mapped near this parcel",
                              etype="OBSERVATION", confidence="MEDIUM",
                              source=self.name, source_name=self.label,
                              source_url=src_url,
                              raw_ref="absence from the 911 centerline file is a strong "
                                      "hint, not proof. Check the plat and the deed for "
                                      "a recorded easement before writing it off."))
        if len(names) > 1:
            ev.append(self.ev("road_frontage_candidates",
                              ", ".join(sorted(set(names))[:6]),
                              etype="OBSERVATION", confidence="MEDIUM",
                              source=self.name, source_name=self.label,
                              source_url=src_url,
                              raw_ref="more than one road near the parcel - it may be a "
                                      "corner lot, which is worth more for a business"))

        fields = {"road_class": label if feats else None}
        return SourceResult(
            status=OK, detail=f"{label}" + (f" ({best_name})" if best_name else ""),
            records=[Record(source=self.name, identity={"id": prop["id"]},
                            fields=fields, evidence=ev,
                            raw={"road_rank": best_rank, "road_name": best_name,
                                 "segments": len(feats), "names": sorted(set(names))[:12]})])


AR_ROADS = register(ArkansasRoads())


def rank_from_label(label: str | None) -> int:
    """Map a stored road_access sentence back onto the 0-9 traffic scale.

    One vocabulary, defined here, used by the scorer, the money models and the
    front end - so nobody has to guess what "through street" is worth.
    """
    v = (label or "").lower()
    for rank in sorted(RANK_LABEL, reverse=True):
        if RANK_LABEL[rank].lower() in v:
            return rank
    return LOCAL_ST
