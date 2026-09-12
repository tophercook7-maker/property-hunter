"""FEMA National Flood Hazard Layer - flood zone at a point."""
from __future__ import annotations

from ..http import arcgis_query
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
FLOOD_HAZARD_ZONES = 28        # S_FLD_HAZ_AR

# Zones beginning with A or V are Special Flood Hazard Areas.
def is_sfha(zone: str | None) -> bool:
    return bool(zone) and zone.strip().upper()[:1] in ("A", "V")


class FemaFlood(PropertySource):
    name = "fema_nfhl"
    label = "FEMA National Flood Hazard Layer"
    kind = "flood"
    url = NFHL
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            arcgis_query(NFHL, FLOOD_HAZARD_ZONES, where="1=2", out_fields="FLD_ZONE")
            return SourceResult(status=OK, detail="NFHL flood hazard layer answering")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="FEMA NFHL did not answer")

    def zone_at(self, lat: float, lon: float) -> dict | None:
        geom = {"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}
        data = arcgis_query(
            NFHL, FLOOD_HAZARD_ZONES, where="1=1",
            out_fields="FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DFIRM_ID",
            extra={"geometry": __import__("json").dumps(geom),
                   "geometryType": "esriGeometryPoint",
                   "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
        feats = data.get("features") or []
        return feats[0]["attributes"] if feats else None

    def enrich(self, prop: dict, **kwargs) -> SourceResult:
        lat, lon = prop.get("lat"), prop.get("lon")
        if lat is None or lon is None:
            return SourceResult(status=UNAVAILABLE,
                                detail="no coordinates - cannot check flood zone")
        try:
            attrs = self.zone_at(lat, lon)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail=f"FEMA lookup failed: {exc}")
        src_url = (f"https://msc.fema.gov/portal/search?AddressQuery="
                   f"{lat:.6f},{lon:.6f}")
        if not attrs:
            ev = [self.ev("flood_zone", "no mapped flood hazard area at this point",
                          etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                          source_name=self.label, source_url=src_url,
                          raw_ref="FEMA NFHL returned no polygon; the parcel may still "
                                  "sit partly in a mapped zone, and unmapped does not "
                                  "mean it cannot flood")]
            return SourceResult(status=OK, detail="no mapped flood zone",
                                records=[Record(source=self.name,
                                                identity={"id": prop["id"]},
                                                fields={"flood_zone": "X (unmapped at centroid)"},
                                                evidence=ev)])
        zone = attrs.get("FLD_ZONE")
        sub = attrs.get("ZONE_SUBTY")
        label = f"{zone}" + (f" ({sub})" if sub else "")
        ev = [self.ev("flood_zone", label, etype="FACT", confidence="HIGH",
                      source=self.name, source_name=self.label, source_url=src_url,
                      raw_ref=f"DFIRM {attrs.get('DFIRM_ID')}; SFHA={attrs.get('SFHA_TF')}; "
                              f"checked at the parcel centroid only")]
        if is_sfha(zone):
            ev.append(self.ev("flood_risk", "Special Flood Hazard Area", etype="FACT",
                              confidence="HIGH", source=self.name, source_name=self.label,
                              source_url=src_url))
        return SourceResult(status=OK, detail=f"flood zone {label}",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields={"flood_zone": label}, evidence=ev,
                                            raw=attrs)])


FEMA_FLOOD = register(FemaFlood())
