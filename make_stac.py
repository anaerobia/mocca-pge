#!/usr/bin/env python3
"""
make_stac.py -- write STAC metadata for the MOCCA product.

The MOCCA PGE writes the product (SNDR.AQUA.MOCCA.101.nc) and an OODT CAS
metadata file (.cas, XML) to ./output. This script reads the .cas file,
fills any missing values from the NetCDF global attributes (ACDD names),
and writes a STAC Item and a STAC Catalog that points to it:

    output/catalog.json
    output/<item id>/<item id>.json

Only the Python standard library is required. netCDF4 or h5py is used for
the NetCDF fallback when it is installed.

Usage:
    make_stac.py [output_dir]          # default: ./output
    make_stac.py --dump [output_dir]   # print every .cas key and value

Edit the *_KEYS lists below to match the key names in the real .cas file.
Each list is tried in order; the first key that has a value wins.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

COLLECTION = "MOCCA"
STAC_VERSION = "1.0.0"

# .cas key names (first match wins), then NetCDF global attribute names.
# The Sounder SIPS .cas file has the name, times and versions, but no
# bounding coordinates, so the footprint comes from the NetCDF file.
ID_KEYS = ["ProductName", "Filename"]
START_KEYS = ["StartDateTime", "time_coverage_start"]
END_KEYS = ["EndDateTime", "time_coverage_end"]
WEST_KEYS = ["geospatial_lon_min"]
EAST_KEYS = ["geospatial_lon_max"]
SOUTH_KEYS = ["geospatial_lat_min"]
NORTH_KEYS = ["geospatial_lat_max"]
VERSION_KEYS = ["BuildId"]
LEVEL_KEYS = ["ProcessingLevel"]
# NetCDF variable names searched (case-insensitive, in any group) when the
# global attributes have no geospatial_* bounds.
LAT_NAMES = ["latitude", "lat", "Latitude"]
LON_NAMES = ["longitude", "lon", "Longitude"]
# Copied into item properties as "mocca:<key>" when present. Internal keys
# (FileLocation, NodeInfo, JobId, TaskId, RequestId) are left out on
# purpose: they name internal hosts and paths.
EXTRA_KEYS = ["GranuleNumber", "NominalDate", "ProductType", "DataVersion",
              "CollectionLabel", "AutomaticQualityFlag",
              "ProductionDateTime", "ProductionLocation"]


def read_cas(path):
    """Return {key: value or [values]} from an OODT CAS met XML file."""
    root = ET.parse(path).getroot()
    met = {}
    for kv in root.iter():
        if not kv.tag.endswith("keyval"):
            continue
        key, vals = None, []
        for child in kv:
            tag = child.tag.rsplit("}", 1)[-1]   # drop any namespace
            text = (child.text or "").strip()
            if tag == "key":
                key = text
            elif tag == "val" and text:
                vals.append(text)
        if key:
            met[key] = vals[0] if len(vals) == 1 else (vals or None)
    return met


def read_nc_attrs(path):
    """Return the NetCDF global attributes, or {} if no reader is installed."""
    try:
        import netCDF4
        with netCDF4.Dataset(path) as ds:
            return {k: ds.getncattr(k) for k in ds.ncattrs()}
    except ImportError:
        pass
    try:
        import h5py
        with h5py.File(path, "r") as f:
            out = {}
            for k, v in f.attrs.items():
                if isinstance(v, bytes):
                    v = v.decode()
                elif hasattr(v, "tolist"):
                    v = v.tolist()
                    if isinstance(v, list) and len(v) == 1:
                        v = v[0]
                out[k] = v
            return out
    except ImportError:
        return {}


def first(meta, keys, name, required=True):
    for k in keys:
        v = meta.get(k)
        if v not in (None, "", []):
            return v[0] if isinstance(v, list) else v
    if required:
        raise SystemExit(f"make_stac: no value for {name}; tried {keys}. "
                         f"Run with --dump to see the available keys.")
    return None


def to_iso(value):
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_latlon(path):
    """Return (lats, lons) as flat lists of valid values from the NetCDF file,
    or (None, None) if no reader or no lat/lon variables are found."""
    try:
        import h5py
    except ImportError:
        h5py = None
    if h5py is None:
        return None, None
    found = {}

    def visit(name, obj):
        if not isinstance(obj, h5py.Dataset):
            return
        base = name.rsplit("/", 1)[-1].lower()
        for kind, names in (("lat", LAT_NAMES), ("lon", LON_NAMES)):
            if kind not in found and base in [n.lower() for n in names]:
                found[kind] = obj[()].ravel().tolist()

    with h5py.File(path, "r") as f:     # NetCDF-4 files are HDF5 files
        f.visititems(visit)
    if "lat" not in found or "lon" not in found:
        return None, None
    pairs = [(y, x) for y, x in zip(found["lat"], found["lon"])
             if -90 <= y <= 90 and -180 <= x <= 180]   # drops fill values
    if not pairs:
        return None, None
    return [p[0] for p in pairs], [p[1] for p in pairs]


def bounds_from_arrays(lats, lons):
    """West, south, east, north from footprint arrays. Handles granules that
    cross the antimeridian (west > east in the result)."""
    s, n = min(lats), max(lats)
    w, e = min(lons), max(lons)
    if e - w > 180:                      # likely crosses 180: shift and retry
        shifted = [x + 360 if x < 0 else x for x in lons]
        w2, e2 = min(shifted), max(shifted)
        if e2 - w2 < e - w:
            w = w2 - 360 if w2 > 180 else w2
            e = e2 - 360 if e2 > 180 else e2
    return w, s, e, n


def footprint(w, s, e, n):
    w, s, e, n = (float(x) for x in (w, s, e, n))
    if w <= e:
        ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
        return [w, s, e, n], {"type": "Polygon", "coordinates": [ring]}
    # Crosses the antimeridian: split into two boxes (STAC best practice).
    left = [[w, s], [180, s], [180, n], [w, n], [w, s]]
    right = [[-180, s], [e, s], [e, n], [-180, n], [-180, s]]
    return [w, s, e, n], {"type": "MultiPolygon",
                          "coordinates": [[left], [right]]}


def build_item(out_dir):
    ncs = sorted(glob.glob(os.path.join(out_dir, "*.nc")))
    if not ncs:
        raise SystemExit(f"make_stac: no .nc product in {out_dir}")
    nc = ncs[0]
    cas = nc + ".cas" if os.path.exists(nc + ".cas") else (
        sorted(glob.glob(os.path.join(out_dir, "*.cas"))) or [None])[0]

    cas_met = read_cas(cas) if cas else {}
    nc_met = read_nc_attrs(nc)
    meta = {**nc_met, **cas_met}          # .cas values take priority

    item_id = first(meta, ID_KEYS, "id", required=False) \
        or os.path.splitext(os.path.basename(nc))[0]
    item_id = os.path.splitext(os.path.basename(str(item_id)))[0]
    start = to_iso(first(meta, START_KEYS, "start time"))
    end = to_iso(first(meta, END_KEYS, "end time"))
    box = [first(meta, k, n, required=False) for k, n in
           ((WEST_KEYS, "west"), (SOUTH_KEYS, "south"),
            (EAST_KEYS, "east"), (NORTH_KEYS, "north"))]
    if None in box:
        lats, lons = read_latlon(nc)
        if lats is None:
            raise SystemExit("make_stac: no bounds in the metadata and no "
                             f"lat/lon variables {LAT_NAMES}/{LON_NAMES} "
                             f"readable in {nc}")
        box = bounds_from_arrays(lats, lons)
    bbox, geom = footprint(*box)

    props = {
        "datetime": None,
        "start_datetime": start,
        "end_datetime": end,
        "platform": "aqua",
        "instruments": ["airs", "modis"],
        "processing:level": first(meta, LEVEL_KEYS, "level",
                                  required=False) or "L2",
    }
    version = first(meta, VERSION_KEYS, "version", required=False)
    if version:
        props["processing:software"] = {"MOCCA_PGE": str(version)}
    for k in EXTRA_KEYS:
        if meta.get(k) not in (None, "", []):
            props[f"mocca:{k}"] = meta[k]

    # Asset hrefs are relative to the item file, so they stay valid
    # after DPS copies the output directory to S3.
    assets = {"data": {"href": f"../{os.path.basename(nc)}",
                       "type": "application/x-netcdf",
                       "title": "MOCCA product",
                       "roles": ["data"]}}
    if cas:
        assets["metadata"] = {"href": f"../{os.path.basename(cas)}",
                              "type": "application/xml",
                              "title": "OODT CAS metadata",
                              "roles": ["metadata"]}
    for log in glob.glob(os.path.join(out_dir, "*.log")):
        assets["log"] = {"href": f"../{os.path.basename(log)}",
                         "type": "text/plain", "roles": ["metadata"]}

    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [
            "https://stac-extensions.github.io/processing/v1.2.0/schema.json"],
        "id": item_id,
        "geometry": geom,
        "bbox": bbox,
        "properties": props,
        "assets": assets,
        "links": [{"rel": "root", "href": "../catalog.json",
                   "type": "application/json"},
                  {"rel": "parent", "href": "../catalog.json",
                   "type": "application/json"}],
    }


def main(argv):
    dump = "--dump" in argv
    args = [a for a in argv if a != "--dump"]
    out_dir = args[0] if args else "output"

    if dump:
        for cas in glob.glob(os.path.join(out_dir, "*.cas")):
            print(f"# {cas}")
            for k, v in read_cas(cas).items():
                print(f"{k} = {v}")
        return

    item = build_item(out_dir)
    item_dir = os.path.join(out_dir, item["id"])
    os.makedirs(item_dir, exist_ok=True)
    item_path = os.path.join(item_dir, item["id"] + ".json")
    with open(item_path, "w") as f:
        json.dump(item, f, indent=2)

    catalog = {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": f"{COLLECTION}-{item['id']}",
        "description": "MOCCA product from a MAAP DPS job",
        "links": [{"rel": "root", "href": "./catalog.json",
                   "type": "application/json"},
                  {"rel": "item", "href": f"./{item['id']}/{item['id']}.json",
                   "type": "application/geo+json"}],
    }
    with open(os.path.join(out_dir, "catalog.json"), "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"make_stac: wrote {item_path} and {out_dir}/catalog.json")


if __name__ == "__main__":
    main(sys.argv[1:])
