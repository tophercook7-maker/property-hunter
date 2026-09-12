"""Arkansas GIS Office statewide parcel layer (CAMP).

This is the backbone source: real county-assessor tax-roll parcels republished
by the State of Arkansas as an open Esri FeatureServer. It gives us owner,
situs address, assessed / land / improvement values, acreage, subdivision,
legal description, parcel-type code and the date the county data was current.

Everything here is a FACT with a stated effective date; anything we *derive*
(e.g. "looks vacant") is written as an OBSERVATION or ESTIMATE, never a fact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from .. import geo
from ..config import TERRITORIES
from ..http import arcgis_count, arcgis_query
from ..normalize import normalize_address, squash, title_case
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

SERVICE = ("https://gis.arkansas.gov/arcgis/rest/services/"
           "FEATURESERVICES/Planning_Cadastre/FeatureServer")
LAYER = 6                      # PARCEL_POLYGON_CAMP
PAGE = 200                     # the service's maxRecordCount

FIELDS = ("parcelid,parcellgl,ownername,adrnum,predir,pstrnam,pstrtype,psufdir,"
          "adrcity,adrzip5,adrlabel,parceltype,assessvalue,impvalue,landvalue,"
          "totalvalue,subdivision,nbhd,section,township,range,str,taxarea,"
          "county,countyfips,sourceref,sourcedate,camadate,pubdate,camakey")

# Parcel-type codes. First letter is the class; second letter is the state of
# improvement. The class letters are unambiguous in the data; the second letter
# is INFERRED from the improvement-value distribution and is marked LOW
# confidence - the scanner raises a manual task to confirm the official table.
CLASS_CODES = {"R": "residential", "C": "commercial", "A": "agricultural",
               "I": "industrial", "E": "exempt", "P": "public"}
IMPROVE_CODES = {"I": "improved", "V": "vacant", "M": "manufactured_or_mobile"}


def _ms_to_date(value) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return None


def _territory(key: str) -> dict:
    for t in TERRITORIES:
        if t["key"] == key:
            return t
    raise KeyError(key)


class ArkansasParcels(PropertySource):
    name = "ar_gis_parcels"
    label = "Arkansas GIS Office - statewide parcels (county assessor CAMA)"
    kind = "parcel"
    url = SERVICE
    access = AUTOMATED

    # -- health ----------------------------------------------------------
    def health_check(self) -> SourceResult:
        try:
            n = arcgis_count(SERVICE, LAYER, "countyfips='05051'")
            return SourceResult(status=OK, detail=f"{n:,} Garland County parcels published")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="Arkansas GIS parcel service did not answer")

    def count(self, where: str) -> int:
        return arcgis_count(SERVICE, LAYER, where)

    # -- discovery -------------------------------------------------------
    def discover(self, *, territory: str = "garland_ar", where_extra: str = "",
                 limit: int | None = None, geometry: bool = True,
                 progress=None, **kwargs) -> SourceResult:
        terr = _territory(territory)
        where = f"countyfips='{terr['county_fips']}'"
        if where_extra:
            where = f"{where} AND ({where_extra})"
        records: list[Record] = []
        try:
            total = arcgis_count(SERVICE, LAYER, where)
            target = min(total, limit) if limit else total
            offset = 0
            while offset < target:
                take = min(PAGE, target - offset)
                data = arcgis_query(SERVICE, LAYER, where=where, out_fields=FIELDS,
                                    geometry=geometry, result_offset=offset,
                                    result_record_count=take)
                feats = data.get("features", [])
                if not feats:
                    break
                for f in feats:
                    rec = self._to_record(f, terr)
                    if rec:
                        records.append(rec)
                offset += len(feats)
                if progress:
                    progress(offset, target)
                if len(feats) < take:
                    break
            return SourceResult(status=OK, records=records,
                                detail=f"{len(records):,} parcels read of {total:,} matching")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, records=records, error=str(exc),
                                detail=f"failed after {len(records)} records: {exc}")

    # -- mapping ---------------------------------------------------------
    def _to_record(self, feature: dict, terr: dict) -> Record | None:
        a = feature.get("attributes") or {}
        parcel_id = (a.get("parcelid") or "").strip()
        if not parcel_id:
            return None

        rings = (feature.get("geometry") or {}).get("rings") or []
        lat = lon = None
        acreage_geo = None
        perimeter = None
        if rings:
            c = geo.centroid(rings)
            if c:
                lon, lat = c
            biggest = max(rings, key=len)
            acreage_geo = round(geo.acres(geo.ring_area_m2(biggest)), 3)
            perimeter = round(geo.ring_perimeter_m(biggest), 1)

        addr = squash(a.get("adrlabel"))
        city = (a.get("adrcity") or "").strip()
        if city.lower() == "rural":
            city = "Unincorporated"
        zip5 = a.get("adrzip5")
        ptype = (a.get("parceltype") or "").strip().upper()
        cls = CLASS_CODES.get(ptype[:1], "unknown") if ptype else "unknown"
        improve = IMPROVE_CODES.get(ptype[1:2], "unknown") if len(ptype) > 1 else "unknown"

        imp = a.get("impvalue") or 0.0
        land = a.get("landvalue") or 0.0
        total = a.get("totalvalue") or 0.0
        acreage = a.get("taxarea")
        eff = _ms_to_date(a.get("sourcedate")) or _ms_to_date(a.get("camadate"))
        pub = _ms_to_date(a.get("pubdate"))

        improved = None
        if improve == "improved":
            improved = 1
        elif improve == "vacant":
            improved = 0
        elif imp and imp > 1000:
            improved = 1

        prop_type = self._property_type(cls, improve, imp)

        src_url = (f"{SERVICE}/{LAYER}/query?where=parcelid%3D%27{parcel_id}%27"
                   f"+AND+countyfips%3D%27{terr['county_fips']}%27&outFields=*&f=json")

        fields: dict[str, Any] = {
            "parcel_id": parcel_id,
            "county_fips": a.get("countyfips") or terr["county_fips"],
            "territory": terr["key"],
            "address": title_case(addr) if addr else None,
            "address_norm": normalize_address(addr) or None,
            "city": city or None,
            "zip": str(zip5) if zip5 else None,
            "lat": lat, "lon": lon,
            "subdivision": (a.get("subdivision") or "").strip() or None,
            "legal": (a.get("parcellgl") or "").strip() or None,
            "acreage": acreage if acreage else acreage_geo,
            "owner_name": (a.get("ownername") or "").strip() or None,
            "parcel_type": ptype or None,
            "property_type": prop_type,
            "improved": improved,
            "land_value": land, "imp_value": imp, "total_value": total,
        }

        ev = []
        def add(field_name, value, etype="FACT", conf="HIGH", note=None):
            if value in (None, ""):
                return
            ev.append(self.ev(field_name, value, etype=etype, confidence=conf,
                              source=self.name, source_name=self.label,
                              source_url=src_url, effective_date=eff, raw_ref=note))

        add("parcel_id", parcel_id)
        add("owner_name", fields["owner_name"])
        add("address", fields["address"])
        add("subdivision", fields["subdivision"])
        add("legal_description", fields["legal"])
        add("acreage", acreage, note="taxarea from county tax roll")
        add("land_value", land)
        add("improvement_value", imp)
        add("total_assessed_value", total)
        add("parcel_type_code", ptype)
        if cls != "unknown":
            add("property_class", cls, etype="FACT", conf="HIGH")
        if improve != "unknown":
            add("improvement_state", improve, etype="ESTIMATE", conf="LOW",
                note="second letter of the parcel-type code inferred from the "
                     "improvement-value distribution; official code table not yet verified")
        if acreage_geo is not None:
            add("acreage_from_geometry", acreage_geo, etype="CALCULATION", conf="MEDIUM",
                note="computed from the published parcel polygon")
        if perimeter is not None:
            add("parcel_perimeter_m", perimeter, etype="CALCULATION", conf="MEDIUM")
        if lat is not None:
            add("coordinates", f"{lat:.6f},{lon:.6f}", etype="CALCULATION", conf="MEDIUM",
                note="centroid of the published parcel polygon")
        if pub:
            add("gis_publication_date", pub)

        timeline = []
        if eff:
            timeline.append({"event_date": eff, "kind": "assessment",
                             "title": "County assessor record current as of this date",
                             "detail": f"Total assessed ${total:,.0f} "
                                       f"(land ${land:,.0f} / improvements ${imp:,.0f})",
                             "source": self.name, "source_url": src_url})

        return Record(source=self.name,
                      identity={"parcel_id": parcel_id, "address": addr,
                                "lat": lat, "lon": lon,
                                "legal": fields["legal"],
                                "subdivision": fields["subdivision"],
                                "owner": fields["owner_name"],
                                "county_fips": fields["county_fips"]},
                      fields=fields, evidence=ev, timeline=timeline,
                      raw={"attributes": a})

    @staticmethod
    def _property_type(cls: str, improve: str, imp_value: float) -> str:
        if improve == "vacant" or (imp_value or 0) < 500:
            return "lot"
        if cls == "commercial":
            return "commercial"
        if cls == "industrial":
            return "commercial"
        if cls == "residential":
            return "house"
        if cls in ("exempt", "public"):
            return "unknown"
        if cls == "agricultural":
            return "lot" if (imp_value or 0) < 5000 else "house"
        return "unknown"


ARKANSAS_PARCELS = register(ArkansasParcels())
