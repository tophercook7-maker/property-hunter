"""City of Hot Springs GIS (ArcGIS Online, publicly shared feature services).

The City publishes, as open feature services, the records the spec singled out
as the strongest distress signals and I had first marked manual-only:

  Vacant_Structures_view/99            the vacant-structure register (polygons)
  Housing_Lien_Parcels/90              cleanup / demolition liens with amounts
  Addressing_Points_for_Housing_Cases  2025 code-enforcement cases (points)
  Zoning_/100                          current zoning districts (2024 update)
  Addresses_with_Zoning/110            address -> zoning code + RPID
  Water_Meters/12, Sewer_Gravity_Main  utilities actually at the address
  City_Owned_Property/91               government-owned parcels
  Central_Historic_District, Malvern_Overlay, Planned_Development_District,
  Census_Opportunity_Zones             overlays that change what you may do

Everything read here is a FACT with the City's own edit date. The City's GIS
disclaimer says the data is provided as-is at the user's risk; that is noted on
every piece of evidence, and the manual "confirm with the office" task stays -
now at low priority - because a map layer is not a permit.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .. import db, geo
from ..http import arcgis_query
from ..normalize import normalize_address, title_case
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

ORG = "https://services1.arcgis.com/lCwVhIwyitVebu0v/arcgis/rest/services"
DISCLAIMER = ("City of Hot Springs GIS: data compiled from various sources for the City's "
              "use; any other use is at the user's own risk (hotspringsar.gov/553).")
DISCLAIMER_URL = "https://www.hotspringsar.gov/553/GIS-Disclaimer"

ZONING_MEANING = {
    "RN-1": "single-family residential, large lots", "RN-2": "single-family residential",
    "RN-3": "residential, smaller lots", "RN-4": "residential, mixed housing types",
    "RN-5": "residential, higher density", "RN-6": "residential, highest density / multifamily",
    "R-R": "rural residential", "R-S": "suburban residential",
    "C-N": "neighbourhood commercial", "C-G": "general commercial",
    "C-R": "regional commercial", "C-MU": "commercial mixed use",
    "C-TR": "tourist / resort commercial", "CBD": "central business district",
    "I-L": "light industrial", "I-H": "heavy industrial", "I-MU": "industrial mixed use",
    "INST": "institutional", "AFC": "agricultural / forest / conservation",
}


def _ms(value) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return None


def _point_geom(lat: float, lon: float) -> dict:
    return {"geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects"}


def _envelope(lat: float, lon: float, m: float) -> dict:
    d = m / 111000.0
    return {"geometry": json.dumps({"xmin": lon - d, "ymin": lat - d, "xmax": lon + d,
                                    "ymax": lat + d, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects"}


def _url(svc: str, lid: int) -> str:
    return f"{ORG}/{svc}/FeatureServer/{lid}"


def _query(svc: str, lid: int, **kw) -> list[dict]:
    data = arcgis_query(f"{ORG}/{svc}/FeatureServer", lid, **kw)
    return data.get("features") or []


class _City(PropertySource):
    territory = "garland_ar"
    access = AUTOMATED
    svc = ""
    lid = 0

    def health_check(self) -> SourceResult:
        try:
            n = arcgis_query(f"{ORG}/{self.svc}/FeatureServer", self.lid, where="1=1",
                             extra={"returnCountOnly": "true"}).get("count")
            return SourceResult(status=OK, detail=f"{n:,} records published by the City")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="City GIS service did not answer")

    def fact(self, field, value, *, conf="HIGH", etype="FACT", eff=None, note=None,
             lid=None, svc=None):
        return self.ev(field, value, etype=etype, confidence=conf, source=self.name,
                       source_name=self.label,
                       source_url=_url(svc or self.svc, lid if lid is not None else self.lid),
                       effective_date=eff, raw_ref=(note + " " if note else "") + DISCLAIMER)


# ------------------------------------------------------------- vacancy ----

class HotSpringsVacantStructures(_City):
    name = "hs_gis_vacant"
    label = "City of Hot Springs GIS - vacant structure register"
    kind = "vacancy"
    svc, lid = "Vacant_Structures_view", 99
    url = _url("Vacant_Structures_view", 99)

    def discover(self, **kw) -> SourceResult:
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*", geometry=True)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="could not read the vacant-structure register")
        recs = []
        for f in feats:
            a = f["attributes"]
            rings = (f.get("geometry") or {}).get("rings") or []
            c = geo.centroid(rings) if rings else None
            addr = f"{(a.get('Street_Number') or '').strip()} {(a.get('Street_Name') or '').strip()}".strip()
            eff = _ms(a.get("last_edited_date")) or _ms(a.get("created_date"))
            fields = {"county_fips": "05051", "territory": "garland_ar",
                      "address": title_case(addr) if addr else None,
                      "city": "HOT SPRINGS", "rpid": str(a["RPID"]) if a.get("RPID") else None,
                      "lat": c[1] if c else None, "lon": c[0] if c else None}
            ev = [self.fact("vacant_structure",
                            f"on the City's vacant-structure register (RPID {a.get('RPID')})",
                            eff=eff,
                            note="Being on the register means the City has recorded the "
                                 "structure as vacant; it does not say why, or whether "
                                 "anybody is working on it.")]
            tl = [{"event_date": eff, "kind": "vacancy",
                   "title": "Recorded on the City's vacant-structure register",
                   "source": self.name, "source_url": self.url}]
            attach_parcel(fields)
            recs.append(Record(source=self.name,
                               identity={"rpid": fields["rpid"], "address": addr,
                                         "lat": fields["lat"], "lon": fields["lon"],
                                         "county_fips": "05051"},
                               fields=fields, evidence=ev, timeline=tl,
                               raw={"attributes": a}))
        return SourceResult(status=OK, records=recs,
                            detail=f"{len(recs)} vacant structures on the City register")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*",
                           extra=_point_geom(prop["lat"], prop["lon"]))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        if not feats:
            return SourceResult(status=OK, detail="not on the vacant-structure register",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                evidence=[self.fact(
                                                    "vacant_structure_check",
                                                    "not on the City's vacant-structure register",
                                                    conf="MEDIUM", etype="OBSERVATION",
                                                    note="Absence from the register is not "
                                                         "proof of occupancy.")])])
        a = feats[0]["attributes"]
        eff = _ms(a.get("last_edited_date")) or _ms(a.get("created_date"))
        ev = [self.fact("vacant_structure",
                        f"on the City's vacant-structure register (RPID {a.get('RPID')})", eff=eff)]
        return SourceResult(status=OK, detail="ON the vacant-structure register",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields={"rpid": str(a["RPID"]) if a.get("RPID") else None},
                                            evidence=ev,
                                            timeline=[{"event_date": eff, "kind": "vacancy",
                                                       "title": "Recorded on the City's "
                                                                "vacant-structure register",
                                                       "source": self.name, "source_url": self.url}],
                                            raw=a)])


# --------------------------------------------------------------- liens ----

LIEN_WORDS = {"VWL": "vacant-lot / weed-lot cleanup lien",
              "DEMO": "demolition lien",
              "Demo Admin Fees Only": "demolition administrative fee lien",
              "OTHER": "other City lien"}


class HotSpringsCleanupLiens(_City):
    name = "hs_gis_liens"
    label = "City of Hot Springs GIS - housing / cleanup / demolition liens"
    kind = "code"
    svc, lid = "Housing_Lien_Parcels", 90
    url = _url("Housing_Lien_Parcels", 90)

    def _evidence(self, a: dict) -> tuple[list, list, dict]:
        eff = _ms(a.get("Date_of_Lien"))
        kind = LIEN_WORDS.get((a.get("Type_of_Lien") or "").strip(), a.get("Type_of_Lien") or "lien")
        amt = a.get("Amount") or 0
        ev = [self.fact("cleanup_lien",
                        f"{kind} of ${amt:,.2f} filed {eff or 'date unknown'}", eff=eff,
                        note="A City lien is money the City already spent on this property. "
                             "It has to be paid or negotiated before a clean transfer."),
              self.fact("cleanup_lien_amount", round(float(amt), 2), eff=eff)]
        fields = {}
        z = (a.get("Zoning") or "").strip()
        if z and z.lower() != "none":
            ev.append(self.fact("zoning_on_lien_record", z, conf="MEDIUM", eff=eff,
                                note="zoning as noted on the lien record, which may predate "
                                     "the 2024 zoning update"))
        for utility, key in (("Water", "city_water"), ("Sewer", "city_sewer")):
            v = (a.get(utility) or "").strip()
            if v and v.lower() not in ("none", ""):
                ev.append(self.fact(key, "not available" if v.upper() == "NO"
                                    else f"line size {v} noted on the lien record",
                                    conf="MEDIUM", eff=eff))
        vac = (a.get("Vacant") or "").strip().upper()
        if vac.startswith("YES"):
            ev.append(self.fact("vacant_per_lien_record", "marked vacant on the lien record"
                                + (" (with a question mark)" if "?" in vac else ""),
                                conf="LOW" if "?" in vac else "MEDIUM", eff=eff))
        if a.get("Comments"):
            ev.append(self.fact("lien_comment", str(a["Comments"])[:200], conf="MEDIUM", eff=eff))
        tl = [{"event_date": eff, "kind": "lien", "title": f"City {kind}",
               "detail": f"${amt:,.2f}", "source": self.name, "source_url": self.url}]
        return ev, tl, fields

    def discover(self, **kw) -> SourceResult:
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*", geometry=True)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="could not read the lien parcels")
        recs = []
        for f in feats:
            a = f["attributes"]
            rings = (f.get("geometry") or {}).get("rings") or []
            c = geo.centroid(rings) if rings else None
            addr = (a.get("Address") or "").strip()
            ev, tl, extra = self._evidence(a)
            fields = {"county_fips": "05051", "territory": "garland_ar",
                      "address": title_case(addr) if addr else None, "city": "HOT SPRINGS",
                      "rpid": str(a["RPID"]) if a.get("RPID") else None,
                      "lat": c[1] if c else None, "lon": c[0] if c else None, **extra}
            attach_parcel(fields)
            recs.append(Record(source=self.name,
                               identity={"rpid": fields["rpid"], "address": addr,
                                         "lat": fields["lat"], "lon": fields["lon"],
                                         "county_fips": "05051"},
                               fields=fields, evidence=ev, timeline=tl, raw={"attributes": a}))
        return SourceResult(status=OK, records=recs,
                            detail=f"{len(recs)} parcels carry a City lien")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*",
                           extra=_point_geom(prop["lat"], prop["lon"]))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        if not feats:
            return SourceResult(status=OK, detail="no City lien on this parcel",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                evidence=[self.fact(
                                                    "cleanup_lien_check", "no City lien recorded",
                                                    conf="MEDIUM", etype="OBSERVATION",
                                                    note="Only City housing liens are in this "
                                                         "layer; mortgages, judgements and tax "
                                                         "liens live at the Circuit Clerk.")])])
        ev, tl, extra = [], [], {}
        total = 0.0
        for f in feats:
            e, t, x = self._evidence(f["attributes"])
            ev += e
            tl += t
            extra.update(x)
            total += float(f["attributes"].get("Amount") or 0)
        ev.append(self.fact("cleanup_lien_total", round(total, 2),
                            note=f"{len(feats)} lien record(s) summed"))
        return SourceResult(status=OK, detail=f"{len(feats)} City lien(s), ${total:,.2f}",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields=extra, evidence=ev, timeline=tl,
                                            raw={"liens": [f["attributes"] for f in feats]})])


# ----------------------------------------------------------- code cases ---

OPEN_STATUSES = {"active", "in progress", "referred to another department"}


class HotSpringsCodeCases(_City):
    name = "hs_gis_code_cases"
    label = "City of Hot Springs GIS - 2025 housing / code-enforcement cases"
    kind = "code"
    svc, lid = "Addressing_Points_for_Housing_Cases_2025", 33
    url = _url("Addressing_Points_for_Housing_Cases_2025", 33)

    def _ev(self, a: dict) -> tuple[list, list]:
        status = (a.get("Status") or "").strip()
        is_open = status.lower() in OPEN_STATUSES
        text = (f"code case {a.get('Enforcement')} - {status or 'status unknown'}, filed "
                f"{a.get('Filed') or '?'}" + (f", closed {a['Closed']}" if a.get("Closed") else ""))
        ev = [self.fact("code_case_open" if is_open else "code_case", text,
                        eff=a.get("Filed"),
                        note="A code case means the City sent somebody out. 'Complied' means "
                             "the owner fixed it; 'In Progress' means it is still live.")]
        tl = [{"event_date": a.get("Filed"), "kind": "code",
               "title": f"Code case {a.get('Enforcement')} ({status})",
               "source": self.name, "source_url": self.url}]
        return ev, tl

    def discover(self, **kw) -> SourceResult:
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*", geometry=True)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="could not read the code-case points")
        recs = []
        for f in feats:
            a = f["attributes"]
            g = f.get("geometry") or {}
            addr = (a.get("Address") or "").strip()
            ev, tl = self._ev(a)
            fields = {"county_fips": "05051", "territory": "garland_ar",
                      "address": title_case(addr) if addr else None, "city": "HOT SPRINGS",
                      "lat": g.get("y"), "lon": g.get("x")}
            attach_parcel(fields)
            recs.append(Record(source=self.name,
                               identity={"address": addr, "lat": g.get("y"), "lon": g.get("x"),
                                         "county_fips": "05051"},
                               fields=fields, evidence=ev, timeline=tl, raw={"attributes": a}))
        return SourceResult(status=OK, records=recs, detail=f"{len(recs)} code cases in 2025")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*",
                           extra=_envelope(prop["lat"], prop["lon"], 35))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        # points sit on the address, so also accept an exact address match
        norm = prop.get("address_norm")
        hits = [f for f in feats if not norm or normalize_address(f["attributes"].get("Address")) == norm
                or geo.haversine_m(prop["lon"], prop["lat"], f["geometry"]["x"] if f.get("geometry") else prop["lon"],
                                   f["geometry"]["y"] if f.get("geometry") else prop["lat"]) < 25]
        if not hits:
            return SourceResult(status=OK, detail="no 2025 code case at this address",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                evidence=[self.fact("code_case_check",
                                                                    "no 2025 code case at this address",
                                                                    conf="MEDIUM", etype="OBSERVATION",
                                                                    note="only 2025 cases are published")])])
        ev, tl = [], []
        for f in hits:
            e, t = self._ev(f["attributes"])
            ev += e
            tl += t
        return SourceResult(status=OK, detail=f"{len(hits)} code case(s) in 2025",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            evidence=ev, timeline=tl,
                                            raw={"cases": [f["attributes"] for f in hits]})])


# --------------------------------------------------------------- zoning ---

OVERLAYS = [
    ("Central_Historic_District", 0, "historic_district", "Central Historic District",
     "Historic districts add design review to exterior work."),
    ("Pleasant_Street_Historic_District", 0, "historic_district", "Pleasant Street Historic District",
     "Historic districts add design review to exterior work."),
    ("Malvern_Overlay", 0, "overlay", "Malvern Avenue overlay",
     "An overlay district changes what the base zoning allows along this corridor."),
    ("Planned_Development_District", 97, "overlay", "Planned development district",
     "A PDD has its own approved plan; uses follow that plan, not the base code."),
    ("Census_Opportunity_Zones", 1, "opportunity_zone", "federal Opportunity Zone",
     "Capital-gains tax treatment may apply to investment here - ask a CPA."),
]


class HotSpringsZoning(_City):
    name = "hs_gis_zoning"
    label = "City of Hot Springs GIS - zoning (2024 update) & overlays"
    kind = "zoning"
    svc, lid = "Zoning_", 100
    url = _url("Zoning_", 100)

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            feats = _query(self.svc, self.lid, where="1=1",
                           out_fields="Zoning_Code,Zoning_Description,Ordinance,last_edited_date",
                           extra=_point_geom(prop["lat"], prop["lon"]))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        ev, fields, tl = [], {}, []
        if not feats:
            ev.append(self.fact("zoning_check",
                                "outside the City's zoning map - likely outside city limits, "
                                "where Garland County has no zoning",
                                conf="MEDIUM", etype="OBSERVATION",
                                note="County land outside city limits is generally unzoned, "
                                     "which is not the same as 'anything goes' - subdivision "
                                     "covenants and health-department rules still apply."))
            detail = "not in the City zoning map"
        else:
            a = feats[0]["attributes"]
            code = (a.get("Zoning_Code") or "").strip()
            desc = (a.get("Zoning_Description") or ZONING_MEANING.get(code) or "").strip()
            eff = _ms(a.get("last_edited_date"))
            label = f"{code} - {desc}" if desc else code
            fields["zoning"] = label
            ev.append(self.fact("zoning", label, eff=eff,
                                note=f"ordinance {a.get('Ordinance') or '?'}. The district says "
                                     f"what is permitted by right; whether YOUR use needs a "
                                     f"conditional-use permit is a question for Planning."))
            if ZONING_MEANING.get(code):
                ev.append(self.fact("zoning_plain", ZONING_MEANING[code], conf="MEDIUM",
                                    etype="OBSERVATION"))
            detail = label
        # overlays
        for svc, lid, field, title, why in OVERLAYS:
            try:
                hit = _query(svc, lid, where="1=1", out_fields="*",
                             extra=_point_geom(prop["lat"], prop["lon"]))
            except Exception:
                continue
            if hit:
                ev.append(self.fact(field, title, svc=svc, lid=lid, note=why))
        # RPID via the address/zoning join, when we have an address
        if prop.get("address_norm") and not prop.get("rpid"):
            try:
                num = prop["address_norm"].split(" ")[0]
                stem = prop["address_norm"].split(" ")[1] if " " in prop["address_norm"] else ""
                if num.isdigit() and stem:
                    rows = _query("Addresses_with_Zoning", 110,
                                  where=f"ADR_LABEL LIKE '{num} {stem}%'",
                                  out_fields="ADR_LABEL,RPID,Zoning_Code")
                    for r in rows:
                        if normalize_address(r["attributes"].get("ADR_LABEL")) == prop["address_norm"]:
                            fields["rpid"] = str(r["attributes"].get("RPID") or "") or None
                            if fields["rpid"]:
                                ev.append(self.fact("rpid", fields["rpid"], svc="Addresses_with_Zoning",
                                                    lid=110))
                            break
            except Exception:
                pass
        return SourceResult(status=OK, detail=detail,
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields=fields, evidence=ev, timeline=tl)])


# ------------------------------------------------------------ utilities ---

class HotSpringsUtilities(_City):
    name = "hs_gis_utilities"
    label = "City of Hot Springs GIS - water meters & sewer mains"
    kind = "utilities"
    svc, lid = "Water_Meters", 12
    url = _url("Water_Meters", 12)

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        ev, fields = [], {}
        try:
            meters = _query("Water_Meters", 12, where="1=1",
                            out_fields="StreetNum,Street,Status,CustomerTy,InstallDat",
                            extra=_envelope(prop["lat"], prop["lon"], 45))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        norm = prop.get("address_norm") or ""
        at_addr = [m for m in meters if norm and
                   normalize_address(f"{m['attributes'].get('StreetNum')} {m['attributes'].get('Street')}") == norm]
        if at_addr:
            a = at_addr[0]["attributes"]
            ev.append(self.fact("city_water", f"City water meter at this address "
                                f"(status {a.get('Status')}, installed {_ms(a.get('InstallDat')) or '?'})",
                                eff=_ms(a.get("InstallDat")),
                                note="An active meter means service exists; it says nothing "
                                     "about whether the bill is current."))
        elif meters:
            ev.append(self.fact("city_water", f"{len(meters)} City water meter(s) within ~45 m - "
                                f"water is on this street", conf="MEDIUM", etype="OBSERVATION"))
        else:
            ev.append(self.fact("city_water", "no City water meter within ~45 m", conf="MEDIUM",
                                etype="OBSERVATION",
                                note="Outside the City system this may be a well or a rural "
                                     "water district - ask."))
        try:
            mains = _query("Sewer_Gravity_Main", 6, where="1=1", out_fields="Material,Condition",
                           extra=_envelope(prop["lat"], prop["lon"], 70))
            if mains:
                ev.append(self.fact("city_sewer", f"City sewer main within ~70 m "
                                    f"({len(mains)} segment(s))", conf="MEDIUM", etype="OBSERVATION",
                                    svc="Sewer_Gravity_Main", lid=6,
                                    note="A main nearby is not a tap; the connection and its "
                                         "fee are separate questions."))
            else:
                ev.append(self.fact("city_sewer", "no City sewer main within ~70 m - septic likely",
                                    conf="MEDIUM", etype="OBSERVATION", svc="Sewer_Gravity_Main",
                                    lid=6))
        except Exception:
            pass
        return SourceResult(status=OK, detail=" / ".join(e["value"][:40] for e in ev[:2]),
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields=fields, evidence=ev)])


# -------------------------------------------------------- city property ---

class HotSpringsCityProperty(_City):
    name = "hs_gis_city_property"
    label = "City of Hot Springs GIS - city-owned property"
    kind = "ownership"
    svc, lid = "City_Owned_Property", 91
    url = _url("City_Owned_Property", 91)

    def enrich(self, prop: dict, **kw) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            feats = _query(self.svc, self.lid, where="1=1", out_fields="*",
                           extra=_point_geom(prop["lat"], prop["lon"]))
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        if not feats:
            return SourceResult(status=OK, detail="not City-owned", records=[])
        a = feats[0]["attributes"]
        vac = (a.get("VACANT") or "").strip().lower() == "y"
        ev = [self.fact("city_owned", f"City of Hot Springs property"
                        + (f" - {a['DEPARTMENT']}" if a.get("DEPARTMENT") else "")
                        + (" - marked vacant" if vac else ""), eff=_ms(a.get("LastUpdate")),
                        note="City land is sold by a public process, if at all. Ask the City "
                             "Manager's office whether it is surplus.")]
        return SourceResult(status=OK, detail="City-owned" + (" (vacant)" if vac else ""),
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            evidence=ev, raw=a)])


# ------------------------------------------------------- parcel lookup ---

def parcel_at(lat: float, lon: float) -> dict | None:
    """Which county parcel contains this point, per the City's roll copy.

    Cached in the database by ~1 m coordinate cell: register polygons do not
    move, so after the first scan this is a local read, not a request.
    """
    key = f"{lat:.5f},{lon:.5f}"
    db.connect().execute(
        "CREATE TABLE IF NOT EXISTS parcel_lookup (cell TEXT PRIMARY KEY, parcel_id TEXT, "
        "owner_name TEXT, total_value REAL, land_value REAL, imp_value REAL, legal TEXT, "
        "parcel_type TEXT, mailing TEXT, looked_up_at TEXT)")
    row = db.q1("SELECT * FROM parcel_lookup WHERE cell=?", (key,))
    if row:
        return dict(row) if row["parcel_id"] else None
    try:
        feats = _query("Housing_Liens_WFL1", 0, where="1=1",
                       out_fields="ParcelId,OwnerName,MailingAdd,ParcelLgl,ImpValue,LandValue,"
                                  "TotalValue,ParcelType",
                       extra=_point_geom(lat, lon))
    except Exception:
        return None                      # do not cache a failure
    a = (feats[0]["attributes"] if feats else {}) or {}
    rec = {"cell": key, "parcel_id": (a.get("ParcelId") or "").strip() or None,
           "owner_name": (a.get("OwnerName") or "").strip() or None,
           "total_value": a.get("TotalValue"), "land_value": a.get("LandValue"),
           "imp_value": a.get("ImpValue"), "legal": (a.get("ParcelLgl") or "").strip() or None,
           "parcel_type": (a.get("ParcelType") or "").strip() or None,
           "mailing": (a.get("MailingAdd") or "").strip() or None,
           "looked_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.ex("INSERT OR REPLACE INTO parcel_lookup VALUES (?,?,?,?,?,?,?,?,?,?)",
          tuple(rec.values()))
    return rec if rec["parcel_id"] else None


def attach_parcel(fields: dict) -> dict:
    """Give a register record its parcel id (and the roll facts it lacked)
    BEFORE identity resolution, so two accounts on one parcel land on one
    property instead of fighting over it."""
    if fields.get("parcel_id"):
        return fields
    # An RPID belongs to exactly one parcel. If we already hold that RPID on a
    # property with a parcel id, that beats any point lookup - register
    # polygons can put their centroid a metre into the neighbour's lot.
    rpid = (str(fields.get("rpid") or "")).strip()
    if rpid:
        known = db.q1("SELECT parcel_id FROM properties WHERE rpid=? AND parcel_id IS NOT NULL "
                      "AND excluded=0 ORDER BY id LIMIT 1", (rpid,))
        if known:
            fields["parcel_id"] = known["parcel_id"]
            return fields
    if fields.get("lat") is None:
        return fields
    hit = parcel_at(fields["lat"], fields["lon"])
    if not hit:
        return fields
    # Identity only. The roll copy's owner and values are older than the State
    # layer's and must never overwrite them; a property that truly lacks them
    # gets them from the parcel_ids stage, which fills only what is missing.
    fields["parcel_id"] = hit["parcel_id"]
    return fields


# ------------------------------------------------------- owner mailing ---

GARLAND_ZIPS = {"71901", "71902", "71903", "71909", "71910", "71913", "71914", "71949",
                "71956", "71964", "71968", "72087"}


def parse_mailing(addr: str | None) -> dict:
    """'100 FOUR OAKS LN  HOT SPRINGS AR 71901' -> state/zip/po_box, best effort."""
    t = " ".join((addr or "").split())
    out = {"raw": t, "state": None, "zip": None, "po_box": bool(re.search(r"\bP\.?O\.? ?BOX\b", t, re.I))}
    m = re.search(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$", t)
    if m:
        out["state"], out["zip"] = m.group(1), m.group(2)
    return out


class HotSpringsOwnerMailing(_City):
    """The City's copy of the county roll carries the owner's mailing address,
    which the State's parcel layer does not. An owner who gets the tax bill in
    another state, or who has stopped living at the property, is the classic
    motivated seller - and an owner who lives there is not."""
    name = "hs_gis_owner_mailing"
    label = "City of Hot Springs GIS - county parcel copy with owner mailing address"
    kind = "ownership"
    svc, lid = "Housing_Liens_WFL1", 0
    url = _url("Housing_Liens_WFL1", 0)

    ROLL_FIELDS = ("ParcelId,OwnerName,MailingAdd,AdrLabel,AdrCity,AdrZip5,SourceDate,"
                   "ParcelLgl,AssesValue,ImpValue,LandValue,TotalValue,ParcelType")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        pid = (prop.get("parcel_id") or "").strip()
        try:
            if pid:
                feats = _query(self.svc, self.lid, where=f"ParcelId='{pid.replace(chr(39), '')}'",
                               out_fields=self.ROLL_FIELDS)
            elif prop.get("lat") is not None:
                # A register polygon told us WHERE it is but not which parcel it is.
                # The roll copy is a polygon layer, so the centroid answers that.
                feats = _query(self.svc, self.lid, where="1=1", out_fields=self.ROLL_FIELDS,
                               extra=_point_geom(prop["lat"], prop["lon"]))
            else:
                return SourceResult(status=UNAVAILABLE, detail="no parcel id or coordinates")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail=str(exc))
        if not feats:
            return SourceResult(status=OK, detail="parcel not in the City's copy of the roll",
                                records=[])
        a = feats[0]["attributes"]
        fields: dict = {}
        if not pid and a.get("ParcelId"):
            # Fill in what the register could not tell us. Values only where we
            # hold nothing - the State layer is the fresher source for those.
            fields["parcel_id"] = a["ParcelId"].strip()
            if not prop.get("owner_name") and a.get("OwnerName"):
                fields["owner_name"] = a["OwnerName"].strip()
            if not prop.get("legal") and a.get("ParcelLgl"):
                fields["legal"] = a["ParcelLgl"].strip()
            if prop.get("total_value") in (None, 0) and a.get("TotalValue"):
                fields.update(total_value=a.get("TotalValue"), land_value=a.get("LandValue"),
                              imp_value=a.get("ImpValue"))
            if not prop.get("parcel_type") and a.get("ParcelType"):
                fields["parcel_type"] = a["ParcelType"].strip()
        mail = parse_mailing(a.get("MailingAdd"))
        eff = _ms(a.get("SourceDate"))
        ev = []
        if fields.get("parcel_id"):
            ev.append(self.fact("parcel_id", fields["parcel_id"], eff=eff,
                                note="matched by the parcel polygon that contains this "
                                     "property's location"))
            if fields.get("owner_name"):
                ev.append(self.fact("owner_name", fields["owner_name"], eff=eff))
        if not mail["raw"] or mail["raw"] in ("AR 00000",):
            return SourceResult(status=OK, detail="no mailing address on the roll",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                fields=fields, evidence=ev)] if fields else [])
        ev.append(self.fact("owner_mailing_address", mail["raw"], eff=eff,
                            note="where the county sends the tax bill, per the roll copy "
                                 "the City holds"))
        situs_norm = normalize_address(a.get("AdrLabel"))
        mail_norm = normalize_address(re.sub(r"\s+[A-Z]{2}\s+\d{5}.*$", "", mail["raw"]))
        kind = None
        if situs_norm and mail_norm and (mail_norm == situs_norm
                                         or mail_norm.startswith(situs_norm + " ")):
            kind = "owner_occupied"
            ev.append(self.fact("owner_occupancy", "tax bill goes to the property itself - "
                                "owner-occupied or at least owner-addressed",
                                conf="MEDIUM", etype="OBSERVATION", eff=eff))
        elif mail["state"] and mail["state"] != "AR":
            kind = "out_of_state"
        elif mail["zip"] and mail["zip"] not in GARLAND_ZIPS:
            kind = "out_of_county"
        elif mail["po_box"]:
            kind = "po_box"
        if kind in ("out_of_state", "out_of_county", "po_box"):
            words = {"out_of_state": f"owner gets the tax bill in {mail['state']}",
                     "out_of_county": f"owner gets the tax bill outside Garland County ({mail['zip']})",
                     "po_box": "owner gets the tax bill at a PO box"}[kind]
            ev.append(self.fact("absentee_owner", words, conf="MEDIUM", etype="OBSERVATION",
                                eff=eff,
                                note="A mailing address elsewhere is a hint the owner is not "
                                     "here to look after it, not proof. Snowbirds and "
                                     "landlords with good managers both mail elsewhere."))
        return SourceResult(status=OK, detail=(kind or "mailing address on file").replace("_", " "),
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields=fields, evidence=ev,
                                            raw={"mailing": mail, "kind": kind})])


HS_VACANT = register(HotSpringsVacantStructures())
HS_LIENS = register(HotSpringsCleanupLiens())
HS_CODE = register(HotSpringsCodeCases())
HS_ZONING = register(HotSpringsZoning())
HS_UTILITIES = register(HotSpringsUtilities())
HS_CITY_PROPERTY = register(HotSpringsCityProperty())
HS_OWNER_MAILING = register(HotSpringsOwnerMailing())
