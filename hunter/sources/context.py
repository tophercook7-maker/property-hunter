"""Context sources: OpenStreetMap (roads / nearby uses) and building footprints.

These answer the questions the tax roll cannot: is there a road on this lot's
frontage, how busy is it, is there already a snow-cone stand across the street,
and is there actually a building standing on this parcel.
"""
from __future__ import annotations

import json

from .. import geo
from ..http import arcgis_query, get
from .base import (AUTOMATED, DEGRADED, OK, UNAVAILABLE, PropertySource, Record,
                   SourceResult, register)

# Public Overpass mirrors, tried in order. We always identify ourselves with a
# real User-Agent; overpass-api.de currently answers 406 to our client, so we
# fall through to a mirror that accepts it rather than disguising who we are.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api",
    "https://overpass-api.de/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
]

class OpenStreetMapContext(PropertySource):
    """Who and what is nearby: competition for a food stand, and the traffic
    generators that make a corner worth something.

    Road access comes from the Arkansas 911 centerline file instead - it is
    authoritative and answers in under a second. Overpass is a shared free
    service that takes the better part of a minute, so it is used sparingly and
    only where local knowledge is the point."""

    name = "osm_overpass"
    label = "OpenStreetMap (Overpass API) - nearby businesses & anchors"
    kind = "context"
    url = "https://www.openstreetmap.org/copyright"
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        probe = '[out:json][timeout:20];node(34.513,-93.054,34.514,-93.053);out count;'
        try:
            self._query(probe, timeout=30)
            return SourceResult(status=OK, detail=f"answering via {self.last_mirror}")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="no Overpass mirror answered")

    last_mirror = OVERPASS_MIRRORS[0]
    _cache: dict[str, dict] = {}

    def _query(self, ql: str, timeout: float = 45) -> dict:
        problems = []
        for mirror in OVERPASS_MIRRORS:
            try:
                r = get(f"{mirror}/interpreter", params={"data": ql}, as_json=True,
                        timeout=timeout, check_robots=False)
                self.last_mirror = mirror
                return r.json or {}
            except Exception as exc:
                problems.append(f"{mirror}: {type(exc).__name__}")
        raise RuntimeError("all Overpass mirrors failed - " + "; ".join(problems))

    def enrich(self, prop: dict, radius_m: int = 150, **kwargs) -> SourceResult:
        lat, lon = prop.get("lat"), prop.get("lon")
        if lat is None or lon is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        # Parcels within ~150 m of each other get the same road and the same
        # neighbours, so one Overpass round trip answers for a whole block.
        cache_key = f"{round(lat, 3)},{round(lon, 3)}"
        data = self._cache.get(cache_key)
        if data is None:
            ql = (f"[out:json][timeout:40];("
                  f'way(around:{radius_m},{lat},{lon})["highway"];'
                  f'node(around:350,{lat},{lon})'
                  f'["amenity"~"^(fast_food|cafe|ice_cream|restaurant|fuel|school|'
                  f'place_of_worship)$"];'
                  f");out tags center 60;")
            try:
                data = self._query(ql)
                self._cache[cache_key] = data
            except Exception as exc:
                return SourceResult(status=UNAVAILABLE, error=str(exc),
                                    detail=f"Overpass query failed: {exc}")

        nearby_food, nearby_shops, nearby_anchors = [], [], []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            amenity = tags.get("amenity")
            if amenity in ("fast_food", "cafe", "ice_cream", "restaurant"):
                nearby_food.append(tags.get("name") or amenity)
            elif tags.get("shop"):
                nearby_shops.append(tags.get("name") or tags["shop"])
            elif amenity in ("school", "place_of_worship", "fuel"):
                nearby_anchors.append(tags.get("name") or amenity)

        src_url = f"https://www.openstreetmap.org/#map=18/{lat:.5f}/{lon:.5f}"
        ev = []
        if not (nearby_food or nearby_anchors):
            ev.append(self.ev("nearby_businesses", "nothing mapped nearby",
                              etype="OBSERVATION", confidence="LOW", source=self.name,
                              source_name=self.label, source_url=src_url,
                              raw_ref="OpenStreetMap is volunteer data - an empty answer "
                                      "means nobody has mapped it, not that nothing is there"))
        if nearby_food:
            ev.append(self.ev("nearby_food_business", ", ".join(sorted(set(nearby_food))[:8]),
                              etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                              source_name=self.label, source_url=src_url))
        if nearby_anchors:
            ev.append(self.ev("nearby_traffic_anchors", ", ".join(sorted(set(nearby_anchors))[:8]),
                              etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                              source_name=self.label, source_url=src_url))

        raw = {"food": sorted(set(nearby_food)), "shops": sorted(set(nearby_shops))[:20],
               "anchors": sorted(set(nearby_anchors))}
        detail = (f"{len(set(nearby_food))} food businesses, "
                  f"{len(set(nearby_anchors))} traffic anchors nearby")
        return SourceResult(status=OK, detail=detail,
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields={}, evidence=ev, raw=raw)])


FOOTPRINTS = ("https://gis.arkansas.gov/arcgis/rest/services/"
              "FEATURESERVICES/Structure/FeatureServer")
FOOTPRINT_LAYER = 54


class BuildingFootprints(PropertySource):
    name = "ar_gis_footprints"
    label = "Arkansas GIS Office - building footprints (composite)"
    kind = "structure"
    url = FOOTPRINTS
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            arcgis_query(FOOTPRINTS, FOOTPRINT_LAYER, where="1=2", out_fields="*")
            return SourceResult(status=OK, detail="footprint layer answering")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="footprint layer did not answer")

    def enrich(self, prop: dict, radius_m: float = 35.0, **kwargs) -> SourceResult:
        lat, lon = prop.get("lat"), prop.get("lon")
        if lat is None or lon is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        d = radius_m / 111000.0
        env = {"xmin": lon - d, "ymin": lat - d, "xmax": lon + d, "ymax": lat + d,
               "spatialReference": {"wkid": 4326}}
        try:
            data = arcgis_query(FOOTPRINTS, FOOTPRINT_LAYER, where="1=1", out_fields="*",
                                geometry=True,
                                extra={"geometry": json.dumps(env),
                                       "geometryType": "esriGeometryEnvelope",
                                       "inSR": 4326,
                                       "spatialRel": "esriSpatialRelIntersects"})
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail=f"footprint query failed: {exc}")
        feats = data.get("features") or []
        total_sqft = 0.0
        count = 0
        for f in feats:
            rings = (f.get("geometry") or {}).get("rings") or []
            if not rings:
                continue
            m2 = geo.ring_area_m2(max(rings, key=len))
            if m2 < 9:          # ignore sheds under ~100 sqft
                continue
            count += 1
            total_sqft += m2 * 10.7639
        src_url = f"{FOOTPRINTS}/{FOOTPRINT_LAYER}"
        if not count:
            ev = [self.ev("structure_present", "no building footprint found near the parcel centroid",
                          etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                          source_name=self.label, source_url=src_url,
                          raw_ref=f"searched {radius_m:.0f} m around the centroid; the "
                                  f"footprint layer can lag new construction")]
            return SourceResult(status=OK, detail="no footprint",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                evidence=ev, raw={"count": 0})])
        ev = [self.ev("structure_present", f"{count} building footprint(s) near centroid",
                      etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                      source_name=self.label, source_url=src_url),
              self.ev("building_footprint_sqft", round(total_sqft),
                      etype="CALCULATION", confidence="LOW", source=self.name,
                      source_name=self.label, source_url=src_url,
                      raw_ref="ground-floor footprint area measured from the published "
                              "polygon; this is not heated living area and says nothing "
                              "about the number of storeys")]
        return SourceResult(status=OK, detail=f"{count} footprint(s), ~{total_sqft:,.0f} sqft",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields={"building_sqft": round(total_sqft)},
                                            evidence=ev,
                                            raw={"count": count, "sqft": round(total_sqft)})])


OSM_CONTEXT = register(OpenStreetMapContext())
AR_FOOTPRINTS = register(BuildingFootprints())
