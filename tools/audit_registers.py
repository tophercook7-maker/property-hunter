"""Read-only audit: does each City register / lien RPID sit on the parcel the app says?

For every RPID the app holds, fetch the City feature's own polygon centroid,
ask the City's roll copy which parcel contains that point, and compare with
the app's parcel for that property. Prints mismatches; changes nothing.
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hunter.db import init_db, q
from hunter.http import arcgis_query

ORG = "https://services1.arcgis.com/lCwVhIwyitVebu0v/arcgis/rest/services"

def centroids(svc, lid, field="RPID"):
    out = {}
    offset = 0
    while True:
        data = arcgis_query(f"{ORG}/{svc}/FeatureServer", lid, where="1=1", out_fields=field, geometry=False,
                            extra={"returnCentroid": "true", "outSR": 4326, "resultOffset": offset,
                                   "resultRecordCount": 1000})
        feats = data.get("features", [])
        for f in feats:
            c = f.get("centroid")
            if c:
                out.setdefault(str(f["attributes"][field]), (c["y"], c["x"]))
        if len(feats) < 1000:
            break
        offset += 1000
    return out

def parcel_at(lat, lon):
    data = arcgis_query(f"{ORG}/Housing_Liens_WFL1/FeatureServer", 0, where="1=1", out_fields="ParcelId,AdrLabel,OwnerName",
                        geometry=False, extra={"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                                               "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
    f = data.get("features", [])
    return f[0]["attributes"] if f else None

def main():
    init_db()
    rp = {}
    for r in q("SELECT p.id, p.address, p.parcel_id, a.alias_value rpid FROM property_aliases a "
               "JOIN properties p ON p.id=a.property_id WHERE a.alias_type='rpid' AND p.excluded=0"):
        rp.setdefault(r["rpid"], []).append(dict(r))
    vac = centroids("Vacant_Structures_view", 99)
    liens = centroids("Housing_Lien_Parcels", 90)
    print(f"app RPIDs {len(rp)} | register {len(vac)} | lien parcels {len(liens)}", flush=True)
    mismatches, checked, unknown = [], 0, 0
    for rpid, props in sorted(rp.items()):
        c = vac.get(rpid) or liens.get(rpid)
        if not c:
            continue
        truth = parcel_at(*c)
        checked += 1
        if not truth:
            unknown += 1
            continue
        for p in props:
            if p["parcel_id"] and p["parcel_id"] != truth["ParcelId"]:
                mismatches.append({"id": p["id"], "address": p["address"], "app_parcel": p["parcel_id"],
                                   "rpid": rpid, "city_parcel": truth["ParcelId"],
                                   "city_addr": (truth["AdrLabel"] or "").strip(), "city_owner": truth["OwnerName"]})
    print(f"checked {checked} RPIDs, {unknown} with no parcel under the centroid, {len(mismatches)} mismatches")
    for m in mismatches:
        print(json.dumps(m))
    json.dump(mismatches, open(os.path.expanduser("~/Projects/property-hunter/data/register_audit.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
