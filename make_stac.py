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
    make_stac.py --asset-base s3://<bucket>/<prefix> [output_dir]
    make_stac.py --dump [output_dir]   # print every .cas key and NetCDF attribute

Edit the *_KEYS lists below to match the key names in the real .cas file.
Each list is tried in order; the first key that has a value wins.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# The collection ID is the product's short name, read from the NetCDF
# global attributes (SHORTNAME_KEYS). Set MOCCA_COLLECTION in the
# environment to override it.
STAC_API = "https://stac.maap-project.org"

# Where the product files are archived, e.g. s3://<bucket>/<prefix>.
# MAAP requires absolute asset hrefs, so set this with --asset-base or the
# MOCCA_ASSET_BASE environment variable. Without it, hrefs are relative
# to the item file and the script prints a warning.
ASSET_BASE_ENV = "MOCCA_ASSET_BASE"
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
SHORTNAME_KEYS = ["shortname", "ShortName", "short_name", "SHORTNAME"]
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


def clean(v):
    """Turn a NetCDF/HDF5 attribute value into a plain Python value.
    numpy arrays and scalars become lists or numbers, bytes become str,
    one-element lists become their element, and empty values become None."""
    if hasattr(v, "tolist"):             # numpy array or numpy scalar
        v = v.tolist()
    if isinstance(v, bytes):
        v = v.decode(errors="replace")
    if isinstance(v, list):
        v = [x.decode(errors="replace") if isinstance(x, bytes) else x
             for x in v]
        if len(v) == 0:
            return None
        if len(v) == 1:
            return v[0]
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def read_nc_attrs(path):
    """Return the NetCDF global attributes as plain Python values,
    or {} if no reader is installed."""
    try:
        import netCDF4
        with netCDF4.Dataset(path) as ds:
            return {k: clean(ds.getncattr(k)) for k in ds.ncattrs()}
    except ImportError:
        pass
    try:
        import h5py
        with h5py.File(path, "r") as f:
            return {k: clean(v) for k, v in f.attrs.items()}
    except ImportError:
        return {}


def first(meta, keys, name, required=True):
    for k in keys:
        v = clean(meta.get(k))
        if v is not None:
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
    or (None, None) if no reader or no lat/lon variables are found.
    Uses h5py if installed, otherwise netCDF4."""
    lat_names = [n.lower() for n in LAT_NAMES]
    lon_names = [n.lower() for n in LON_NAMES]
    found = {}

    def take(base, read):
        base = base.lower()
        for kind, names in (("lat", lat_names), ("lon", lon_names)):
            if kind not in found and base in names:
                found[kind] = read()

    try:
        import h5py

        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                take(name.rsplit("/", 1)[-1],
                     lambda: obj[()].ravel().tolist())

        with h5py.File(path, "r") as f:   # NetCDF-4 files are HDF5 files
            f.visititems(visit)
    except ImportError:
        try:
            import netCDF4
        except ImportError:
            return None, None

        def walk(group):
            for vname, var in group.variables.items():
                take(vname, lambda: var[:].filled(float("nan")).ravel().tolist()
                     if hasattr(var[:], "filled") else var[:].ravel().tolist())
            for sub in group.groups.values():
                walk(sub)

        with netCDF4.Dataset(path) as ds:
            walk(ds)

    if "lat" not in found or "lon" not in found:
        return None, None
    # The range test drops fill values and NaN (NaN fails every comparison).
    pairs = [(y, x) for y, x in zip(found["lat"], found["lon"])
             if -90 <= y <= 90 and -180 <= x <= 180]
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


def build_item(out_dir, asset_base=None):
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
    produced = first(meta, ["ProductionDateTime", "date_created"],
                     "production time", required=False)
    if produced:
        props["created_datetime"] = to_iso(produced)
    version = first(meta, VERSION_KEYS, "version", required=False)
    if version:
        props["processing:software"] = {"MOCCA_PGE": str(version)}
    for k in EXTRA_KEYS:
        v = clean(meta.get(k))
        if v is not None:
            props[f"mocca:{k}"] = v

    def href(path):
        name = os.path.basename(path)
        return f"{asset_base.rstrip('/')}/{name}" if asset_base else f"../{name}"

    if not asset_base:
        print(f"make_stac: warning: no asset base set ({ASSET_BASE_ENV} or "
              "--asset-base); asset hrefs are relative. MAAP needs absolute "
              "s3:// hrefs.", file=sys.stderr)
    assets = {"data": {"href": href(nc),
                       "type": "application/x-netcdf",
                       "title": "MOCCA product",
                       "roles": ["data"]}}
    if cas:
        assets["metadata"] = {"href": href(cas),
                              "type": "application/xml",
                              "title": "OODT CAS metadata",
                              "roles": ["metadata"]}
    for log in glob.glob(os.path.join(out_dir, "*.log")):
        assets["log"] = {"href": href(log),
                         "type": "text/plain", "roles": ["metadata"]}

    collection = (os.environ.get("MOCCA_COLLECTION")
                  or first(meta, SHORTNAME_KEYS, "short name", required=False))
    links = [{"rel": "root", "href": "../catalog.json",
              "type": "application/json"},
             {"rel": "parent", "href": "../catalog.json",
              "type": "application/json"}]
    if collection:
        links.append({"rel": "collection",
                      "href": f"{STAC_API}/collections/{collection}",
                      "type": "application/json"})
    else:
        print("make_stac: warning: no short name found "
              f"(tried {SHORTNAME_KEYS}); the item has no collection. "
              "Run with --dump to see the NetCDF attributes.",
              file=sys.stderr)

    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [
            "https://stac-extensions.github.io/processing/v1.2.0/schema.json"],
        "id": item_id,
        "collection": collection,
        "geometry": geom,
        "bbox": bbox,
        "properties": props,
        "assets": assets,
        "links": links,
    }
    if not collection:
        del item["collection"]
    return item


def main(argv):
    dump = "--dump" in argv
    asset_base = os.environ.get(ASSET_BASE_ENV)
    args = []
    rest = list(argv)
    while rest:
        a = rest.pop(0)
        if a == "--dump":
            continue
        if a.startswith("--asset-base="):
            asset_base = a.split("=", 1)[1]
        elif a == "--asset-base":
            if not rest:
                raise SystemExit("make_stac: --asset-base needs a value")
            asset_base = rest.pop(0)
        elif a.startswith("--"):
            raise SystemExit(f"make_stac: unknown option {a}")
        else:
            args.append(a)
    out_dir = args[0] if args else "output"

    if dump:
        for cas in glob.glob(os.path.join(out_dir, "*.cas")):
            print(f"# {cas}")
            for k, v in read_cas(cas).items():
                print(f"{k} = {v}")
        for nc in glob.glob(os.path.join(out_dir, "*.nc")):
            print(f"# {nc} (global attributes)")
            for k, v in read_nc_attrs(nc).items():
                print(f"{k} = {v}")
        return

    item = build_item(out_dir, asset_base)
    item_dir = os.path.join(out_dir, item["id"])
    os.makedirs(item_dir, exist_ok=True)
    item_path = os.path.join(item_dir, item["id"] + ".json")
    with open(item_path, "w") as f:
        json.dump(item, f, indent=2)

    catalog = {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": f"{item.get('collection', 'MOCCA')}-{item['id']}",
        "description": "MOCCA product from a MAAP DPS job",
        "links": [{"rel": "root", "href": "./catalog.json",
                   "type": "application/json"},
                  {"rel": "item", "href": f"./{item['id']}/{item['id']}.json",
                   "type": "application/geo+json"}],
    }
    with open(os.path.join(out_dir, "catalog.json"), "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"make_stac: wrote {item_path} and {out_dir}/catalog.json")
    print(f"make_stac: asset base: {asset_base or '(none, relative hrefs)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
