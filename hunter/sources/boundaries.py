"""US Census TIGERweb boundaries.

Used for the hard geographic exclusion layer: Hot Springs Village (a Census
Designated Place) and Diamondhead (an incorporated city) both have official
published polygons, so exclusion is a point-in-polygon test rather than a
hopeful string match (spec 17).
"""
from __future__ import annotations

from .. import db, geo
from ..db import jdump, utcnow
from ..http import arcgis_query
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, SourceResult, register

TIGERWEB = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
            "Places_CouSub_ConCity_SubMCD/MapServer")
INCORPORATED_PLACES = 4
CENSUS_DESIGNATED_PLACES = 5


class CensusBoundaries(PropertySource):
    name = "census_boundaries"
    label = "US Census TIGERweb - place & CDP boundaries"
    kind = "boundary"
    url = TIGERWEB
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            data = arcgis_query(TIGERWEB, INCORPORATED_PLACES,
                                where="STATE='05' AND NAME='Diamondhead city'",
                                out_fields="NAME,GEOID")
            n = len(data.get("features", []))
            return SourceResult(status=OK if n else UNAVAILABLE,
                                detail=f"{n} matching boundary feature(s)")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="TIGERweb did not answer")

    def fetch_boundary(self, layer: int, geoid: str, name: str) -> dict | None:
        data = arcgis_query(TIGERWEB, layer, where=f"GEOID='{geoid}'",
                            out_fields="NAME,GEOID,STATE,AREALAND",
                            geometry=True, timeout=90)
        feats = data.get("features") or []
        if not feats:
            return None
        f = feats[0]
        rings = (f.get("geometry") or {}).get("rings") or []
        if not rings:
            return None
        return {
            "name": f["attributes"].get("NAME") or name,
            "geoid": geoid,
            "rings": rings,
            "bbox": list(geo.rings_bbox(rings)),
            "source_url": f"{TIGERWEB}/{layer}/query?where=GEOID%3D%27{geoid}%27&f=json",
        }

    def sync_exclusion_boundaries(self, exclusions: list[dict]) -> SourceResult:
        """Download and cache every configured exclusion polygon."""
        stored, problems = [], []
        for rule in exclusions:
            spec = rule.get("boundary") or {}
            if spec.get("service") != "tigerweb":
                continue
            try:
                b = self.fetch_boundary(spec["layer"], spec["geoid"], spec.get("name", ""))
            except Exception as exc:
                problems.append(f"{rule['label']}: {exc}")
                continue
            if not b:
                problems.append(f"{rule['label']}: no boundary returned for "
                                f"GEOID {spec['geoid']}")
                continue
            db.ex(
                "INSERT INTO geographies(kind,key,name,state,county_fips,geoid,"
                "boundary_json,bbox_json,source,source_url,retrieved_at) "
                "VALUES('exclusion',?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(kind,key) DO UPDATE SET name=excluded.name, "
                "boundary_json=excluded.boundary_json, bbox_json=excluded.bbox_json, "
                "source_url=excluded.source_url, retrieved_at=excluded.retrieved_at",
                (rule["key"], b["name"], "AR", "05051", b["geoid"],
                 jdump({"rings": b["rings"]}), jdump(b["bbox"]),
                 self.name, b["source_url"], utcnow()),
            )
            stored.append(f"{b['name']} ({sum(len(r) for r in b['rings'])} pts)")
        if problems and not stored:
            return SourceResult(status=UNAVAILABLE, error="; ".join(problems),
                                detail="no exclusion boundaries could be refreshed")
        detail = "cached: " + ", ".join(stored) if stored else "nothing cached"
        if problems:
            detail += " | problems: " + "; ".join(problems)
        return SourceResult(status=OK, detail=detail)

    def discover(self, **kwargs) -> SourceResult:
        from ..config import EXCLUSIONS
        return self.sync_exclusion_boundaries(EXCLUSIONS)


CENSUS_BOUNDARIES = register(CensusBoundaries())
