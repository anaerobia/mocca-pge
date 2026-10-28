#!/usr/bin/env python3
"""
make_stac.py -- write STAC metadata for the MOCCA product.

The MOCCA PGE writes the product and an OODT CAS metadata file (.cas, XML)
to ./output. This script reads the .cas file, fills any missing values from
the NetCDF global attributes (ACDD names), and writes a STAC Item and a
STAC Catalog that points to it:

    output/catalog.json
    output/<item id>/<item id>.json

Only the Python standard library is required. netCDF4 or h5py is used for
the NetCDF fallback when it is installed.

Usage:
    make_stac.py [output_dir]          # default: ./output
    make_stac.py --asset-base s3://<bucket>/<prefix> [output_dir]
    make_stac.py --dump [output_dir]   # every .cas key and NetCDF attribute

run_mocca.sh calls this script after a successful PGE run and passes no
options, so MOCCA_COLLECTION and MOCCA_ASSET_BASE are how a DPS job
reaches the two settings that have no sensible default.


Where this fits in the MAAP STAC guidance
-----------------------------------------
MAAP asks that a DPS job describe its own output with STAC so the granule
can be registered and discovered. The field-by-field requirements are in
the user-data STAC metadata tutorial:

    https://docs.maap-project.org/en/hub/technical_tutorials/user_data/
    stac_metadata.html

That page lists a minimum set of item-level fields, filled here as follows:

    id              ProductName, else the .nc basename, with any extension
                    stripped. mocca-v29601 renames the product and its
                    .cas file to ProductName after a successful run, so the
                    id is the same under every registration; only the asset
                    hrefs follow the names on disk.
    collection      MOCCA_COLLECTION, else the short name from the NetCDF
                    attributes (SHORTNAME_KEYS); omitted with a warning
                    when neither is available. See "The collection" below.
    type            always "Feature"
    stac_version    STAC_VERSION
    geometry        a Polygon, or a MultiPolygon when the granule crosses
                    the antimeridian. See "The footprint" below.
    bbox            [west, south, east, north] from the same bounds
    datetime        null, because a MOCCA granule covers a time range;
                    start_datetime and end_datetime carry the extent,
                    which the guidance allows in place of datetime
    created_datetime
                    ProductionDateTime, the provider date UMM requires
    links           root and parent links to catalog.json, plus a
                    collection link into the MAAP STAC API when the
                    collection is known
    assets          the .nc product (roles ["data"]), the .cas file and
                    any .log file (roles ["metadata"]), each with href,
                    type and roles

The guidance also asks for absolute asset hrefs. DPS copies ./output to an
S3 prefix that is not known inside the container, so that prefix has to be
supplied from outside with --asset-base or MOCCA_ASSET_BASE. Without it the
hrefs stay relative to the item file, which keeps them valid on disk, and
the script warns.


The collection
--------------
The collection ID is the registered collection's short name, for example
SNDRAQVL2MOC. It carries no version: in CMR the version is a separate
field, so the short name does not change between collection versions.

It is not in the .cas file. A Sounder SIPS .cas carries CollectionLabel
("std") and Collection ("production"), neither of which is a short name,
and no shortname key of any kind, so SHORTNAME_KEYS can only ever match a
NetCDF global attribute. If the product does not carry one either, set
MOCCA_COLLECTION; otherwise the item ships with no collection, which the
MAAP guidance lists as required.


The footprint
-------------
Two routes, tried in this order.

1. Global attributes. WEST_KEYS, EAST_KEYS, SOUTH_KEYS and NORTH_KEYS name
   the ACDD attributes geospatial_lon_min, geospatial_lon_max,
   geospatial_lat_min and geospatial_lat_max. All four must have a value.

2. Footprint arrays. If any of the four is missing, read_latlon() walks the
   NetCDF file (every group, matching names case-insensitively) for the
   first variables named in LAT_NAMES and LON_NAMES, flattens them, and
   keeps only pairs with -90 <= lat <= 90 and -180 <= lon <= 180. That
   range test is what discards fill values and NaN, since NaN fails every
   comparison. bounds_from_arrays() then takes min/max of each.

Route 2 is the one that runs in practice. The .cas file has no bounding
coordinates at all, and a MOCCA product writes the geospatial_* attributes
but leaves at least one of them empty. clean() turns an empty attribute
into None, which counts as missing; an empty attribute used to crash the
run instead, which is why every value goes through clean() before it is
tested.

Falling back is all-or-nothing on purpose: mixing two declared edges with
two computed ones would give a footprint that is neither.

Antimeridian handling. A granule crossing 180 degrees has longitudes near
both -180 and +180, so a plain min/max spans almost the whole globe.
bounds_from_arrays() treats a longitude span wider than 180 degrees as a
likely crossing: it shifts negative longitudes by +360 into 0..360, takes
min/max again, and keeps the shifted result only when it is genuinely
narrower, mapping values above 180 back down by -360. The result is then
west > east, which is how a crossing is signalled to footprint(). Aqua
flies about 14.5 orbits a day in 6-minute granules, so several granules a
day cross the line.

footprint() turns those four numbers into geometry:

  * west <= east: one closed Polygon ring, corners in the order
    (w,s) (e,s) (e,n) (w,n) (w,s).
  * west > east: the box is split at the antimeridian into two rings, one
    from west to 180 and one from -180 to east, returned as a MultiPolygon.
    This is the STAC best practice; a single ring drawn from west to east
    would wrap the wrong way around the globe.

In both cases bbox stays [west, south, east, north], with west > east for a
crossing granule, which is the GeoJSON convention.

Both routes give an axis-aligned rectangle, which over-covers a tilted AIRS
swath. That matches the upstream product: CMR's record for the parent AIRS
L1B granule (AIRIBRAD v005) publishes a BoundingRectangle and no polygon,
so there is no tighter footprint available to copy. Tracing the swath edge
from the lat/lon arrays would be an improvement on both.


Filling in the metadata
-----------------------
The *_KEYS lists below are the only thing that normally needs editing. Each
is a list of candidate key names, tried in order, and the first key that
has a value wins (see first()). To adapt the script to another product:

1. Run the PGE once so ./output holds the .nc and .cas files.

2. Run "make_stac.py --dump output" to print every key in the .cas file and
   every NetCDF global attribute. That is the authoritative list of
   available names; the names below come from the Sounder SIPS .cas file
   and may not match another producer.

3. Put the real key names at the FRONT of the matching list, keeping the
   ACDD names as later entries so the NetCDF globals still act as a
   fallback. Metadata is merged as {**nc_met, **cas_met}, so a key present
   in both takes its value from the .cas file.

4. Re-run without --dump. A missing value for a required field aborts with
   the list of keys that were tried; the start and end times behave this
   way, and so do the bounds once the footprint fallback has also failed.
   Optional fields (id, version, level, the provider date and everything in
   EXTRA_KEYS) are left out when nothing matches.

EXTRA_KEYS are copied verbatim into item properties as "mocca:<key>".
FileLocation, NodeInfo, JobId, TaskId and RequestId are left out on
purpose: they record internal hostnames and paths that should not be
published.

Values hardcoded rather than read from metadata, and so needing an edit for
any other product: STAC_VERSION, STAC_API, the "platform" and "instruments"
properties, the "L2" default for processing:level, the "MOCCA_PGE" key
under processing:software, and the asset titles.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# The MAAP STAC API, used only to build the item's "collection" link.
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
# NetCDF global attributes holding the collection short name. The .cas
# file has none, so MOCCA_COLLECTION is the fallback; see the module
# docstring.
SHORTNAME_KEYS = ["shortname", "ShortName", "short_name", "SHORTNAME"]
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
    """Return the first non-empty value in meta for any of keys.

    Args:
        meta: Merged metadata, .cas values taking priority over the NetCDF
            global attributes.
        keys: Candidate key names, tried in order; the first key that has a
            value wins.
        name: Human-readable field name, used in the error message.
        required: When True, abort the run if no key has a value.

    Returns:
        The cleaned value, reduced to its first element if it is a list, or
        None when nothing matched and required is False.

    Raises:
        SystemExit: If required is True and none of the keys has a value.
            The message lists the keys tried and points at --dump.
    """
    for k in keys:
        v = clean(meta.get(k))
        if v is not None:
            return v[0] if isinstance(v, list) else v
    if required:
        raise SystemExit(f"make_stac: no value for {name}; tried {keys}. "
                         f"Run with --dump to see the available keys.")
    return None


def to_iso(value):
    """Normalise a .cas or ACDD timestamp to the UTC form STAC expects.

    Accepts a space or "T" between date and time, a trailing "Z", and an
    explicit UTC offset. A timestamp carrying no zone is assumed to be UTC
    rather than local time.

    Args:
        value: The timestamp as read from the metadata.

    Returns:
        The timestamp as "YYYY-MM-DDTHH:MM:SS.ffffffZ".

    Raises:
        ValueError: If the value is not an ISO 8601 timestamp.
    """
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_latlon(path):
    """Return (lats, lons) as flat lists of valid values from a NetCDF file.

    The footprint fallback, used when the global attributes carry no usable
    geospatial_* bounds. Every group is searched, and a variable matches
    when its own name (not its full path) is in LAT_NAMES or LON_NAMES,
    compared case-insensitively; the first match of each kind wins. Pairs
    outside -90..90 latitude or -180..180 longitude are dropped, which is
    what removes fill values and NaN. Uses h5py if installed, otherwise
    netCDF4; a NetCDF-4 file is an HDF5 file, so either reader works.

    Args:
        path: Path to the NetCDF product.

    Returns:
        A (lats, lons) tuple of equal-length lists, or (None, None) if no
        reader is installed, either variable is missing, or no pair
        survives the range test.
    """
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
    """West, south, east, north from footprint arrays.

    South and north are simply min/max of the latitudes. West and east
    start as min/max of the longitudes, but a span wider than 180 degrees
    is read as a likely antimeridian crossing: the negative longitudes are
    shifted by +360 into 0..360, min/max is taken again, and the shifted
    pair is kept only if it spans less than the unshifted one. Values above
    180 are then mapped back down by -360, which can leave west > east.
    That inversion is meaningful, and footprint() relies on it to split the
    geometry.

    Args:
        lats: Latitudes in degrees, fill values already removed.
        lons: Longitudes in degrees, same length and order as lats.

    Returns:
        A (west, south, east, north) tuple in degrees. West > east means
        the footprint crosses the antimeridian.
    """
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
    """Build the STAC bbox and geometry for a bounding box.

    A box that does not cross the antimeridian becomes one closed Polygon
    ring. A crossing box (west > east) is split at 180 degrees into a
    west-to-180 ring and a -180-to-east ring, returned as a MultiPolygon,
    because a single ring drawn from west to east would wrap the long way
    around the globe.

    Args:
        w: West longitude in degrees; may exceed e for a crossing box.
        s: South latitude in degrees.
        e: East longitude in degrees.
        n: North latitude in degrees.

    Returns:
        A (bbox, geometry) tuple, where bbox is [west, south, east, north]
        and geometry is a GeoJSON Polygon or MultiPolygon mapping. The bbox
        keeps west > east for a crossing box, as GeoJSON prescribes.
    """
    w, s, e, n = (float(x) for x in (w, s, e, n))
    if w <= e:
        ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
        return [w, s, e, n], {"type": "Polygon", "coordinates": [ring]}
    # Crosses the antimeridian: split into two boxes (STAC best practice).
    left = [[w, s], [180, s], [180, n], [w, n], [w, s]]
    right = [[-180, s], [e, s], [e, n], [-180, n], [-180, s]]
    return [w, s, e, n], {"type": "MultiPolygon",
                          "coordinates": [[left], [right]]}


def normalize_s3(uri):
    """Turn the DPS job-result form s3://<host>[:port]/<bucket>/<key> into the
    standard s3://<bucket>/<key>. Other values are returned unchanged."""
    if not uri or not uri.startswith("s3://"):
        return uri
    first_part, _, rest = uri[5:].partition("/")
    if rest and (":" in first_part or first_part.endswith("amazonaws.com")):
        return "s3://" + rest
    return uri


def read_context(out_dir):
    """Return the HySDS job context (dict) for a DPS output folder, or {}.
    After a job, DPS uploads it as <dataset>.context.json next to the
    outputs; inside a running job it is ../_context.json."""
    paths = sorted(glob.glob(os.path.join(out_dir, "*.context.json")))
    paths.append(os.path.join(out_dir, "..", "_context.json"))
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
    return {}


def derived_from(context):
    """STAC Item URLs of the job's inputs, from the context's provenance."""
    urls = [u[4:] if u.startswith("url:") else u
            for u in (context.get("_prov") or {}).get("wasDerivedFrom") or []]
    return [u for u in urls if u.startswith(("http://", "https://"))]


def build_item(out_dir, asset_base=None):
    """Assemble the STAC Item for the product found in out_dir.

    Takes the first .nc file in alphabetical order, pairs it with
    "<product>.cas" if that exists or else the first .cas file in the
    directory, merges the two metadata sources (.cas winning), and resolves
    the id, time range, footprint, properties, assets and links as the
    module docstring describes. Warns on stderr, rather than failing, when
    no asset base is set or no collection can be determined.

    Args:
        out_dir: Directory holding the PGE output.
        asset_base: Archive location of the product files, e.g.
            "s3://<bucket>/<prefix>". Each asset href becomes that location
            plus the file name. When None, hrefs are relative to the item
            file and a warning is printed.

    Returns:
        The STAC Item as a mapping ready for json.dump. The "collection"
        key is absent when no short name was found.

    Raises:
        SystemExit: If out_dir holds no .nc product, if a required field has
            no value, or if the bounds are absent from the metadata and no
            lat/lon variables can be read from the product.
    """
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

    context = read_context(out_dir)
    images = [d.get("container_image_name") for d in
              (context.get("job_specification") or {}).get("dependency_images") or []
              if d.get("container_image_name")]
    if images:
        props["mocca:container_image"] = images[0]

    collection = (os.environ.get("MOCCA_COLLECTION")
                  or first(meta, SHORTNAME_KEYS, "short name", required=False))
    links = [{"rel": "root", "href": "../catalog.json",
              "type": "application/json"},
             {"rel": "parent", "href": "../catalog.json",
              "type": "application/json"}]
    for url in derived_from(context):
        links.append({"rel": "derived_from", "href": url,
                      "type": "application/geo+json"})
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
    """Write the STAC Item and Catalog, or dump the available metadata.

    With --dump, prints every key of each .cas file and every global
    attribute of each .nc file in the directory, and writes nothing; use it
    to discover the names to put in the *_KEYS lists. Otherwise writes
    "<out_dir>/<item id>/<item id>.json" and "<out_dir>/catalog.json",
    creating the item directory if needed.

    The asset base comes from --asset-base (either "--asset-base VALUE" or
    "--asset-base=VALUE") or the MOCCA_ASSET_BASE environment variable, the
    flag winning. The collection comes from MOCCA_COLLECTION or the
    product's short name; see the module docstring.

    Args:
        argv: Command-line arguments without the program name. The first
            non-flag argument is the output directory, default "output".

    Returns:
        None.

    Raises:
        SystemExit: On an unknown option, on "--asset-base" with no value,
            or from build_item() when the metadata is insufficient.
    """
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
    asset_base = normalize_s3(asset_base)
    if out_dir.startswith(("s3://", "http://", "https://")):
        raise SystemExit("make_stac: the directory must be a local path (for "
                         "example my-private-bucket/dps_output/...), not a URL. "
                         "Give the S3 location with --asset-base.")

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
