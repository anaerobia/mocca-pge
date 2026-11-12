#!/usr/bin/env python3
"""
make_stac.py -- write STAC metadata for the MOCCA product.

The MOCCA PGE writes the product and an OODT CAS metadata file (.cas, XML)
to ./output. This script reads the .cas file, fills any missing values from
the NetCDF global attributes (ACDD names), and uses pystac to write a STAC
Item and a self-contained STAC Catalog that points to it:

    output/catalog.json
    output/<item id>/<item id>.json

pystac is required. Pin pystac 1.10.x: it writes STAC 1.0.0, which is what
the MAAP STAC API serves, and later releases write STAC 1.1.0. h5py or
netCDF4 is used for the NetCDF fallback when it is installed. The PGE's own
Python (3.6) is too old for pystac, so the Dockerfile builds a separate
environment at /opt/stac-env and run_mocca.sh runs this script with it.

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
    stac_version    set by pystac (1.0.0 for pystac 1.10.x)
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

A granule whose north edge is 90 or whose south edge is -90 contains a
pole, and a box around a pole covers every longitude. footprint() then sets
west to -180 and east to 180, whatever the longitudes say.

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

EXTRA_KEYS are copied verbatim into item properties as "mocca:<key>",
except that INTEGER_KEYS values are written as JSON numbers.
FileLocation, NodeInfo, JobId, TaskId and RequestId are left out on
purpose: they record internal hostnames and paths that should not be
published.

Values hardcoded rather than read from metadata, and so needing an edit for
any other product: STAC_API, the "platform" and "instruments" properties,
the "L2" default for processing:level, the "MOCCA_PGE" key under
processing:software, and the asset titles.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pystac
from pystac.utils import datetime_to_str

# The MAAP STAC API, used only to build the item's "collection" link.
STAC_API = "https://stac.maap-project.org"

# Where the product files are archived, e.g. s3://<bucket>/<prefix>.
# MAAP requires absolute asset hrefs, so set this with --asset-base or the
# MOCCA_ASSET_BASE environment variable. Without it, hrefs are relative
# to the item file and the script prints a warning.
ASSET_BASE_ENV = "MOCCA_ASSET_BASE"
COLLECTION_ENV = "MOCCA_COLLECTION"

PROCESSING_EXT = "https://stac-extensions.github.io/processing/v1.2.0/schema.json"
NETCDF_MEDIA_TYPE = "application/x-netcdf"

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
PRODUCED_KEYS = ["ProductionDateTime", "date_created"]
# NetCDF variable names searched (case-insensitive, in any group) when the
# global attributes have no geospatial_* bounds.
LAT_NAMES = ["latitude", "lat", "Latitude"]
LON_NAMES = ["longitude", "lon", "Longitude"]
# NetCDF global attributes holding the collection short name. The .cas
# file has none, so MOCCA_COLLECTION is the fallback; see the module
# docstring.
SHORTNAME_KEYS = ["shortname", "ShortName", "short_name", "SHORTNAME"]
# Copied verbatim into item properties as "mocca:<key>" when present; a key
# missing from the metadata is skipped. Some (BuildId, StartDateTime,
# EndDateTime, ProcessingLevel, ProductName) also fill standard STAC fields
# above; these keep the raw .cas value too. Internal keys (FileLocation,
# NodeInfo, JobId, TaskId, RequestId) are left out on purpose: they name
# internal hosts and paths.
EXTRA_KEYS = ["MAAPBatchTag", "MAAPQueue", "MAAPProcessId",
              "MAAPManagerUser", "MAAPManagerJobIndex", "BuildId", "Collection",
              "SubCollection", "CollectionLabel", "DataGroup",
              "DataProvider", "DataVersion",
              "StartDateTime", "EndDateTime", "Filename", "GranuleNumber",
              "NominalDate", "ProcessingLevel", "ProductName", "ProductType",
              "ProductionDateTime", "AutomaticQualityFlag",
              "ProductionLocation"]

# EXTRA_KEYS written as JSON numbers, not strings. A value that is not an
# integer stays a string.
INTEGER_KEYS = {"MAAPManagerJobIndex"}

Meta = dict[str, Any]
BBox = tuple[float, float, float, float]


def warn(message: str) -> None:
    """Print a make_stac warning on stderr."""
    print(f"make_stac: warning: {message}", file=sys.stderr)


def read_cas(path: str) -> Meta:
    """Return {key: value or [values]} from an OODT CAS met XML file."""
    root = ET.parse(path).getroot()
    met: Meta = {}
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


def to_int(key: str, v: Any) -> Any:
    """Return v as an int, or v unchanged (with a warning) if it is not one."""
    try:
        return int(str(v).strip())
    except ValueError:
        warn(f"{key} is not an integer, kept as a string: {v!r}")
        return v


def clean(v: Any) -> Any:
    """Turn a NetCDF/HDF5 attribute value into a plain Python value.

    numpy arrays and scalars become lists or numbers, bytes become str,
    one-element lists become their element, and empty values become None.
    """
    if hasattr(v, "tolist"):             # numpy array or numpy scalar
        v = v.tolist()
    if isinstance(v, bytes):
        v = v.decode(errors="replace")
    if isinstance(v, list):
        v = [x.decode(errors="replace") if isinstance(x, bytes) else x
             for x in v]
        if not v:
            return None
        if len(v) == 1:
            return v[0]
    if isinstance(v, str):
        return v.strip() or None
    return v


def read_nc_attrs(path: str) -> Meta:
    """Return the NetCDF global attributes as plain Python values.

    Returns {} if neither netCDF4 nor h5py is installed.
    """
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


def first(meta: Meta, keys: list[str], name: str,
          required: bool = True) -> Any:
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


def parse_time(value: Any) -> datetime:
    """Parse a .cas or ACDD timestamp into an aware UTC datetime.

    Accepts a space or "T" between date and time, a trailing "Z", and an
    explicit UTC offset. A timestamp carrying no zone is assumed to be UTC
    rather than local time.

    Args:
        value: The timestamp as read from the metadata.

    Returns:
        The timestamp as a timezone-aware datetime in UTC.

    Raises:
        ValueError: If the value is not an ISO 8601 timestamp.
    """
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def read_latlon(path: str) -> tuple[list[float], list[float]] | None:
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
        A (lats, lons) tuple of equal-length lists, or None if no reader is
        installed, either variable is missing, or no pair survives the
        range test.
    """
    wanted = {"lat": {n.lower() for n in LAT_NAMES},
              "lon": {n.lower() for n in LON_NAMES}}
    found: dict[str, list[float]] = {}

    def take(base: str, read: Callable[[], list[float]]) -> None:
        base = base.lower()
        for kind, names in wanted.items():
            if kind not in found and base in names:
                found[kind] = read()

    try:
        import h5py

        def visit(name: str, obj: Any) -> None:
            if isinstance(obj, h5py.Dataset):
                take(name.rsplit("/", 1)[-1],
                     lambda: obj[()].ravel().tolist())

        with h5py.File(path, "r") as f:   # NetCDF-4 files are HDF5 files
            f.visititems(visit)
    except ImportError:
        try:
            import netCDF4
        except ImportError:
            return None

        def values(var: Any) -> list[float]:
            data = var[:]
            if hasattr(data, "filled"):    # masked array: fill -> NaN
                data = data.filled(float("nan"))
            return data.ravel().tolist()

        def walk(group: Any) -> None:
            for vname, var in group.variables.items():
                take(vname, lambda var=var: values(var))
            for sub in group.groups.values():
                walk(sub)

        with netCDF4.Dataset(path) as ds:
            walk(ds)

    if "lat" not in found or "lon" not in found:
        return None
    # The range test drops fill values and NaN (NaN fails every comparison).
    pairs = [(y, x) for y, x in zip(found["lat"], found["lon"])
             if -90 <= y <= 90 and -180 <= x <= 180]
    if not pairs:
        return None
    lats, lons = zip(*pairs)
    return list(lats), list(lons)


def bounds_from_arrays(lats: list[float], lons: list[float]) -> BBox:
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


def footprint(w: float, s: float, e: float,
              n: float) -> tuple[list[float], dict[str, Any]]:
    """Build the STAC bbox and geometry for a bounding box.

    A box that does not cross the antimeridian becomes one closed Polygon
    ring. A crossing box (west > east) is split at 180 degrees into a
    west-to-180 ring and a -180-to-east ring, returned as a MultiPolygon,
    because a single ring drawn from west to east would wrap the long way
    around the globe. A box that reaches latitude 90 or -90 contains a pole,
    so it covers every longitude: west and east become -180 and 180.

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
    if n >= 90.0 or s <= -90.0:          # contains a pole: all longitudes
        w, e = -180.0, 180.0
    bbox = [w, s, e, n]
    if w <= e:
        ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
        return bbox, {"type": "Polygon", "coordinates": [ring]}
    # Crosses the antimeridian: split into two boxes (STAC best practice).
    left = [[w, s], [180.0, s], [180.0, n], [w, n], [w, s]]
    right = [[-180.0, s], [e, s], [e, n], [-180.0, n], [-180.0, s]]
    return bbox, {"type": "MultiPolygon", "coordinates": [[left], [right]]}


def normalize_s3(uri: str | None) -> str | None:
    """Turn a DPS job-result S3 URL into the standard s3://<bucket>/<key>.

    DPS reports results as s3://<host>[:port]/<bucket>/<key>. Other values
    are returned unchanged.
    """
    if not uri or not uri.startswith("s3://"):
        return uri
    host, _, rest = uri[len("s3://"):].partition("/")
    if rest and (":" in host or host.endswith("amazonaws.com")):
        return "s3://" + rest
    return uri


def read_context(out_dir: str) -> Meta:
    """Return the HySDS job context for a DPS output folder, or {}.

    After a job, DPS uploads it as <dataset>.context.json next to the
    outputs; inside a running job it is ../_context.json.
    """
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


def derived_from(context: Meta) -> list[str]:
    """Return the STAC Item URLs of the job's inputs from its provenance."""
    sources = (context.get("_prov") or {}).get("wasDerivedFrom") or []
    urls = [u.removeprefix("url:") for u in sources]
    return [u for u in urls if u.startswith(("http://", "https://"))]


def container_image(context: Meta) -> str | None:
    """Return the first dependency container image named in the context."""
    spec = context.get("job_specification") or {}
    for dep in spec.get("dependency_images") or []:
        if dep.get("container_image_name"):
            return dep["container_image_name"]
    return None


def find_inputs(out_dir: str) -> tuple[str, str | None]:
    """Return the product and its .cas file in out_dir.

    The product is the first .nc file in alphabetical order. The .cas file
    is "<product>.cas" if it exists, else the first .cas file, else None.

    Raises:
        SystemExit: If out_dir holds no .nc product.
    """
    ncs = sorted(glob.glob(os.path.join(out_dir, "*.nc")))
    if not ncs:
        raise SystemExit(f"make_stac: no .nc product in {out_dir}")
    nc = ncs[0]
    if os.path.exists(nc + ".cas"):
        return nc, nc + ".cas"
    cases = sorted(glob.glob(os.path.join(out_dir, "*.cas")))
    return nc, cases[0] if cases else None


def resolve_bounds(meta: Meta, nc: str) -> BBox:
    """Return (west, south, east, north) from metadata or the lat/lon arrays.

    Raises:
        SystemExit: If the metadata lacks any edge and no lat/lon variables
            can be read from the product.
    """
    box = [first(meta, keys, name, required=False) for keys, name in
           ((WEST_KEYS, "west"), (SOUTH_KEYS, "south"),
            (EAST_KEYS, "east"), (NORTH_KEYS, "north"))]
    if None not in box:
        return tuple(float(x) for x in box)
    latlon = read_latlon(nc)
    if latlon is None:
        raise SystemExit("make_stac: no bounds in the metadata and no "
                         f"lat/lon variables {LAT_NAMES}/{LON_NAMES} "
                         f"readable in {nc}")
    return bounds_from_arrays(*latlon)


def build_properties(meta: Meta, context: Meta) -> Meta:
    """Return the item properties beyond the datetime fields."""
    props: Meta = {
        "platform": "aqua",
        "instruments": ["airs", "modis"],
        "processing:level": first(meta, LEVEL_KEYS, "level",
                                  required=False) or "L2",
    }
    produced = first(meta, PRODUCED_KEYS, "production time", required=False)
    if produced:
        props["created_datetime"] = datetime_to_str(parse_time(produced))
    version = first(meta, VERSION_KEYS, "version", required=False)
    if version:
        props["processing:software"] = {"MOCCA_PGE": str(version)}
    for k in EXTRA_KEYS:
        v = clean(meta.get(k))
        if v is None:
            continue
        if k in INTEGER_KEYS:
            v = to_int(k, v)
        props[f"mocca:{k}"] = v
    image = container_image(context)
    if image:
        props["mocca:container_image"] = image
    return props


def add_assets(item: pystac.Item, nc: str, cas: str | None, out_dir: str,
               asset_base: str | None) -> None:
    """Attach the product, .cas and log files to item as assets.

    With asset_base, each href is that location plus the file name.
    Without it, each href is the absolute local path; write_catalog() makes
    those relative to the item file when it saves.
    """
    def href(path: str) -> str:
        if asset_base:
            return f"{asset_base.rstrip('/')}/{os.path.basename(path)}"
        return os.path.abspath(path)

    item.add_asset("data", pystac.Asset(
        href(nc), title="MOCCA product", media_type=NETCDF_MEDIA_TYPE,
        roles=["data"]))
    if cas:
        item.add_asset("metadata", pystac.Asset(
            href(cas), title="OODT CAS metadata",
            media_type=pystac.MediaType.XML, roles=["metadata"]))
    logs = sorted(glob.glob(os.path.join(out_dir, "*.log")))
    if logs:
        item.add_asset("log", pystac.Asset(
            href(logs[0]), media_type=pystac.MediaType.TEXT,
            roles=["metadata"]))


def build_item(out_dir: str, asset_base: str | None = None) -> pystac.Item:
    """Assemble the STAC Item for the product found in out_dir.

    Pairs the product with its .cas file (see find_inputs()), merges the
    two metadata sources (.cas winning), and resolves the id, time range,
    footprint, properties, assets and links as the module docstring
    describes. Warns on stderr, rather than failing, when no asset base is
    set or no collection can be determined.

    Args:
        out_dir: Directory holding the PGE output.
        asset_base: Archive location of the product files, e.g.
            "s3://<bucket>/<prefix>". Each asset href becomes that location
            plus the file name. When None, hrefs are relative to the item
            file and a warning is printed.

    Returns:
        The STAC Item. It has no root or parent link yet; write_catalog()
        adds those. collection_id is None when no short name was found.

    Raises:
        SystemExit: If out_dir holds no .nc product, if a required field has
            no value, or if the bounds are absent from the metadata and no
            lat/lon variables can be read from the product.
    """
    nc, cas = find_inputs(out_dir)
    meta = {**read_nc_attrs(nc), **(read_cas(cas) if cas else {})}
    context = read_context(out_dir)

    name = first(meta, ID_KEYS, "id", required=False) or nc
    item_id = os.path.splitext(os.path.basename(str(name)))[0]
    bbox, geometry = footprint(*resolve_bounds(meta, nc))
    collection = (os.environ.get(COLLECTION_ENV)
                  or first(meta, SHORTNAME_KEYS, "short name",
                           required=False))

    item = pystac.Item(
        id=item_id,
        geometry=geometry,
        bbox=bbox,
        datetime=None,
        start_datetime=parse_time(first(meta, START_KEYS, "start time")),
        end_datetime=parse_time(first(meta, END_KEYS, "end time")),
        properties=build_properties(meta, context),
        stac_extensions=[PROCESSING_EXT],
        collection=collection,
    )

    if not asset_base:
        warn(f"no asset base set ({ASSET_BASE_ENV} or --asset-base); asset "
             "hrefs are relative. MAAP needs absolute s3:// hrefs.")
    add_assets(item, nc, cas, out_dir, asset_base)

    for url in derived_from(context):
        item.add_link(pystac.Link(pystac.RelType.DERIVED_FROM, url,
                                  media_type=pystac.MediaType.GEOJSON))
    if collection:
        item.add_link(pystac.Link(pystac.RelType.COLLECTION,
                                  f"{STAC_API}/collections/{collection}",
                                  media_type=pystac.MediaType.JSON))
    else:
        warn(f"no short name found (tried {SHORTNAME_KEYS}); the item has "
             "no collection. Run with --dump to see the NetCDF attributes.")
    return item


def write_catalog(item: pystac.Item, out_dir: str) -> pystac.Catalog:
    """Write item and a self-contained catalog for it under out_dir.

    The catalog goes to <out_dir>/catalog.json and the item to
    <out_dir>/<item id>/<item id>.json, with relative root, parent and item
    links. Local asset hrefs are made relative to the item file; absolute
    s3:// hrefs are kept.

    Returns:
        The saved catalog.
    """
    catalog = pystac.Catalog(
        id=f"{item.collection_id or 'MOCCA'}-{item.id}",
        description="MOCCA product from a MAAP DPS job",
    )
    # pystac types the item link application/json; an Item is GeoJSON.
    catalog.add_item(item).media_type = pystac.MediaType.GEOJSON
    # Set the hrefs directly: catalog.normalize_hrefs() would resolve the
    # item's collection link, i.e. fetch it from the MAAP STAC API.
    out_dir = os.path.abspath(out_dir)
    catalog.set_self_href(os.path.join(out_dir, "catalog.json"))
    item.set_self_href(os.path.join(out_dir, item.id, f"{item.id}.json"))
    catalog.make_all_asset_hrefs_relative()
    catalog.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
    return catalog


def dump(out_dir: str) -> None:
    """Print every .cas key and NetCDF global attribute in out_dir."""
    for cas in sorted(glob.glob(os.path.join(out_dir, "*.cas"))):
        print(f"# {cas}")
        for k, v in read_cas(cas).items():
            print(f"{k} = {v}")
    for nc in sorted(glob.glob(os.path.join(out_dir, "*.nc"))):
        print(f"# {nc} (global attributes)")
        for k, v in read_nc_attrs(nc).items():
            print(f"{k} = {v}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line; see the module docstring for usage."""
    parser = argparse.ArgumentParser(
        prog="make_stac.py",
        description="Write a STAC Item and Catalog for the MOCCA product.")
    parser.add_argument(
        "out_dir", nargs="?", default="output",
        help="local directory holding the PGE output (default: output)")
    parser.add_argument(
        "--asset-base", default=os.environ.get(ASSET_BASE_ENV),
        help="archive location of the files, e.g. s3://<bucket>/<prefix> "
             f"(default: ${ASSET_BASE_ENV})")
    parser.add_argument(
        "--dump", action="store_true",
        help="print every .cas key and NetCDF attribute; write nothing")
    args = parser.parse_args(argv)
    if args.out_dir.startswith(("s3://", "http://", "https://")):
        parser.error("the directory must be a local path (for example "
                     "my-private-bucket/dps_output/...), not a URL. Give the "
                     "S3 location with --asset-base.")
    args.asset_base = normalize_s3(args.asset_base)
    return args


def main(argv: list[str]) -> None:
    """Write the STAC Item and Catalog, or dump the available metadata.

    With --dump, prints every key of each .cas file and every global
    attribute of each .nc file in the directory, and writes nothing; use it
    to discover the names to put in the *_KEYS lists. Otherwise writes
    "<out_dir>/<item id>/<item id>.json" and "<out_dir>/catalog.json".

    The asset base comes from --asset-base or the MOCCA_ASSET_BASE
    environment variable, the flag winning. The collection comes from
    MOCCA_COLLECTION or the product's short name; see the module docstring.

    Args:
        argv: Command-line arguments without the program name.

    Raises:
        SystemExit: On a bad command line, or from build_item() when the
            metadata is insufficient.
    """
    args = parse_args(argv)
    if args.dump:
        dump(args.out_dir)
        return

    item = build_item(args.out_dir, args.asset_base)
    catalog = write_catalog(item, args.out_dir)
    print(f"make_stac: wrote {item.self_href} and {catalog.self_href}")
    print("make_stac: asset base: "
          f"{args.asset_base or '(none, relative hrefs)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
