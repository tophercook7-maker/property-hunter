"""Arkansas GIS Office image services: aerial photos and terrain.

Two things a tax roll can never tell you: what the place actually looks like
from above, and whether the land is flat enough to build on. Both come from
the State of Arkansas's own published image services.

  IMAGERY_9IN_2023   9-inch orthoimagery flown 2023
  IMAGERY_1FT_2017   1-foot orthoimagery flown 2017   (then vs now)
  DEM_1M_2018        1-metre bare-earth elevation      (slope)

The images are written to local files and recorded in the photo gallery with
their source, the year they were flown, and the exact export URL. We do not
pretend they are current - a 2023 photo shows 2023.
"""
from __future__ import annotations

import json
import math
import urllib.parse
from pathlib import Path

from .. import db, geo
from ..config import FILES_DIR
from ..db import utcnow
from ..http import fetch_bytes, get
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

BASE = "https://gis.arkansas.gov/arcgis/rest/services/ImageServices"
LAYERS = [
    ("aerial_2023", "IMAGERY_9IN_2023", "2023", "9-inch orthoimagery, flown 2023"),
    ("aerial_2017", "IMAGERY_1FT_2017", "2017", "1-foot orthoimagery, flown 2017"),
]
DEM = "DEM_1M_2018"
LICENSE = ("Arkansas GIS Office public web service. State-published data; confirm "
           "reuse terms with the GIS Office before republishing outside this app.")


def _bbox_for(prop: dict, pad: float = 1.7) -> tuple[float, float, float, float]:
    """A square around the parcel sized from its acreage, so a 10-acre tract
    and a town lot each fill the frame."""
    lat, lon = prop["lat"], prop["lon"]
    acres = float(prop.get("acreage") or 0.25)
    side_m = max(60.0, min(900.0, math.sqrt(max(acres, 0.05) * 4046.86) * pad))
    dlat = side_m / 2 / 110540.0
    dlon = side_m / 2 / (111320.0 * math.cos(math.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def export_url(service: str, bbox, size: int = 900) -> str:
    q = urllib.parse.urlencode({
        "bbox": ",".join(f"{v:.6f}" for v in bbox), "bboxSR": 4326, "imageSR": 3857,
        "size": f"{size},{size}", "format": "jpg", "f": "image"})
    return f"{BASE}/{service}/ImageServer/exportImage?{q}"


class ArkansasImagery(PropertySource):
    name = "ar_gis_imagery"
    label = "Arkansas GIS Office - aerial imagery (2017 & 2023)"
    kind = "imagery"
    url = f"{BASE}/IMAGERY_9IN_2023/ImageServer"
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            r = get(f"{BASE}/IMAGERY_9IN_2023/ImageServer?f=json", as_json=True,
                    check_robots=False, timeout=25)
            px = (r.json or {}).get("pixelSizeX")
            return SourceResult(status=OK, detail=f"2023 imagery answering ({px} m pixels)")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="image service did not answer")

    def enrich(self, prop: dict, force: bool = False, **kwargs) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        bbox = _bbox_for(prop)
        saved, errors, ev = [], [], []
        for key, service, year, desc in LAYERS:
            have = db.q1("SELECT id FROM photos WHERE property_id=? AND kind='aerial' "
                         "AND source LIKE ?", (prop["id"], f"%{service}%"))
            if have and not force:
                saved.append(f"{year} already on file")
                continue
            url = export_url(service, bbox)
            try:
                raw = fetch_bytes(url)
            except Exception as exc:
                errors.append(f"{year}: {exc}")
                continue
            if not raw.startswith(b"\xff\xd8"):
                errors.append(f"{year}: service returned something that is not a JPEG")
                continue
            d = FILES_DIR / str(prop["id"]) / "aerial"
            d.mkdir(parents=True, exist_ok=True)
            dest = d / f"{year}-{service.lower()}.jpg"
            dest.write_bytes(raw)
            rel = dest.relative_to(FILES_DIR)
            db.ex("INSERT INTO photos(property_id,kind,url,local_path,source,source_url,"
                  "license,captured_at,confidence,caption,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (prop["id"], "aerial", f"/files/{rel}", str(dest),
                   f"Arkansas GIS Office {service}", url, LICENSE, year, "HIGH",
                   f"Aerial, {desc}. Frame is ~{int(2*abs(bbox[2]-bbox[0])*111320*math.cos(math.radians(prop['lat']))/2):d} m across, centred on the parcel.",
                   utcnow()))
            saved.append(year)
            ev.append(self.ev(f"imagery:{key}", f"aerial photo on file, flown {year}",
                              etype="FACT", confidence="HIGH", source=self.name,
                              source_name=self.label, source_url=url, effective_date=year,
                              raw_ref="What is visible is what stood there in that year, "
                                      "not necessarily today."))
        if not saved and errors:
            return SourceResult(status=UNAVAILABLE, error="; ".join(errors),
                                detail="no imagery could be exported")
        return SourceResult(status=OK, detail=f"aerials: {', '.join(saved)}"
                            + (f" | problems: {'; '.join(errors)}" if errors else ""),
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            evidence=ev, raw={"saved": saved})])


class ArkansasTerrain(PropertySource):
    name = "ar_gis_terrain"
    label = "Arkansas GIS Office - 1 m elevation model (slope)"
    kind = "terrain"
    url = f"{BASE}/{DEM}/ImageServer"
    access = AUTOMATED

    def health_check(self) -> SourceResult:
        try:
            get(f"{BASE}/{DEM}/ImageServer?f=json", as_json=True, check_robots=False,
                timeout=25)
            return SourceResult(status=OK, detail="elevation service answering")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail="elevation service did not answer")

    def samples(self, lat: float, lon: float, arm_m: float = 30.0) -> list[float | None]:
        dlat = arm_m / 110540.0
        dlon = arm_m / (111320.0 * math.cos(math.radians(lat)))
        pts = [[lon, lat], [lon + dlon, lat], [lon - dlon, lat], [lon, lat + dlat], [lon, lat - dlat]]
        geom = {"points": pts, "spatialReference": {"wkid": 4326}}
        r = get(f"{BASE}/{DEM}/ImageServer/getSamples",
                params={"geometry": json.dumps(geom), "geometryType": "esriGeometryMultipoint",
                        "returnFirstValueOnly": "true", "f": "json"},
                as_json=True, check_robots=False, timeout=30)
        out: list[float | None] = []
        for s in (r.json or {}).get("samples", []):
            v = s.get("value")
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(None)
        return out

    def enrich(self, prop: dict, **kwargs) -> SourceResult:
        if prop.get("lat") is None:
            return SourceResult(status=UNAVAILABLE, detail="no coordinates")
        try:
            s = self.samples(prop["lat"], prop["lon"])
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail=f"elevation lookup failed: {exc}")
        if len(s) < 5 or any(v is None for v in s):
            return SourceResult(status=UNAVAILABLE, detail="no elevation data at this point")
        c, e, w, n, so = s
        ew = (e - w) / 60.0
        ns = (n - so) / 60.0
        slope = math.hypot(ew, ns) * 100.0
        aspect_deg = (math.degrees(math.atan2(-ew, -ns)) + 360) % 360
        aspect = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((aspect_deg + 22.5) // 45) % 8]
        if slope < 5:
            word = "nearly flat"
        elif slope < 10:
            word = "gentle slope"
        elif slope < 20:
            word = "moderate slope - grading and drainage will cost something"
        else:
            word = "steep - building here means retaining walls or a lot of dirt work"
        src_url = f"{BASE}/{DEM}/ImageServer"
        ev = [self.ev("slope_pct", round(slope, 1), etype="CALCULATION", confidence="MEDIUM",
                      source=self.name, source_name=self.label, source_url=src_url,
                      effective_date="2018",
                      raw_ref=f"gradient across a 60 m cross centred on the parcel centroid; "
                              f"elevation {c:.1f} m; falls toward {aspect}. One cross does "
                              f"not describe a whole tract."),
              self.ev("terrain", f"{word} ({slope:.0f}%, facing {aspect})",
                      etype="OBSERVATION", confidence="MEDIUM", source=self.name,
                      source_name=self.label, source_url=src_url, effective_date="2018")]
        return SourceResult(status=OK, detail=f"{word} ({slope:.0f}%)",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            evidence=ev,
                                            raw={"slope_pct": round(slope, 1), "aspect": aspect,
                                                 "elevation_m": round(c, 1)})])


AR_IMAGERY = register(ArkansasImagery())
AR_TERRAIN = register(ArkansasTerrain())
