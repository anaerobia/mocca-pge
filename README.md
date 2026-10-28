# mocca-pge

MAAP DPS algorithm for the AIRS L2 MOCCA PGE (SPSS v02.96.01, v02.96.00
and v02.95.00). MOCCA
aggregates MODIS MYD03 and MYD06 cloud fields onto the AIRS L1B footprints.

The PGE, its Python environment and its static files are in the container
image `anaerobia/mocca:<tag>`. This repository has the MAAP run and build
commands, and a Dockerfile that adds this repository to that image at
`/app/mocca-pge`. MAAP uses the result, `anaerobia/mocca:<tag>-maap`.

| SPSS | Base image | MAAP image | Registration | MAAP process |
|---|---|---|---|---|
| v02.96.01 | `anaerobia/mocca:v29601` | `anaerobia/mocca:v29601-maap` | `algorithm.json` (`mocca-v29601`) | |
| v02.96.00 | `anaerobia/mocca:v296` | `anaerobia/mocca:v296-maap` | `algorithm-v296.json` (`mocca-v296`) | 197 |
| v02.95.00 | `anaerobia/mocca:v295` | `anaerobia/mocca:v295-maap` | `algorithm-v295.json` (`mocca-v295`) | 164 |

```bash
docker buildx build --platform linux/amd64 --build-arg MOCCA_TAG=v29601 \
    -t anaerobia/mocca:v29601-maap --push .
```

## MAAP registration

| Field | Value |
|---|---|
| Repository URL | `https://github.com/anaerobia/mocca-pge` |
| Run Command | `mocca-pge/run_mocca.sh` |
| Build Command | `mocca-pge/build-env.sh` |
| Container | `docker.io/anaerobia/mocca:v29601-maap` (older: `...:v296-maap`, `...:v295-maap`) |

To register, send `algorithm.json` to the MAAP build API from a MAAP
Jupyter notebook:

```python
import json, requests
from maap.maap import MAAP
maap = MAAP()
r = requests.post(maap.config.maap_api_root + "build",
                  headers=maap._get_api_header(content_type="application/json"),
                  data=open("algorithm.json").read())
print(r.status_code, r.text)
```

## Inputs

All inputs are strings.

| Input | Required | Description |
|---|---|---|
| `config_file` | yes | MOCCA PGE config (XML) |
| `l1b_file` | yes | AIRS L1B radiance granule (HDF) |
| `modis03_file_1`, `modis03_file_2` | yes | MODIS MYD03 geolocation granules |
| `modis06_file_1`, `modis06_file_2` | yes | MODIS MYD06_L2 cloud granules |
| `modis03_file_3` | no | Third MYD03 granule (mocca-v29601 only) |
| `modis06_file_3` | no | Third MYD06_L2 granule (mocca-v29601 only) |
| `log_filename` | no | Log file name. A relative name goes in `./output`. |

The third MODIS granule (`modis03_file_3`, `modis06_file_3`) is optional in
`mocca-v29601` (type `Directory?`). The older registrations (`mocca-v296`,
`mocca-v295`) take exactly two granules of each type.

Note: earlier, the MAAP DPS input stager failed when an optional
`Directory?` input had no value (`Invalid URL 'None'`). Test a job without
the third granule before you depend on it.

The script copies the config and sets these fields in the copy:

- `AirsL1bFile`: the `l1b_file` path.
- `Modis03Files`: the `modis03_file_1` and `modis03_file_2` paths.
- `Modis06L2Files`: the `modis06_file_1` and `modis06_file_2` paths.
- `SFIFFilename`: the SFIF in the image.
- `ProductionDateTime`: the current UTC time (`yymmddHHMMSS`).

If an input is a directory (STAC staging), the script uses the first `.hdf`
file in it. An empty input (`""`) is the same as no input.

## Outputs

`./output` contains the product, its `.cas` metadata file, the log, and the
STAC metadata:

```
output/catalog.json
output/<ProductName without .nc>/<ProductName without .nc>.json   # STAC Item
```

In `mocca-v29601`, the product and the `.cas` file have the `ProductName` in
the `.cas` file, for example:

- `AIRS.AQUA.MODIS.20260104T1441.m06.g147.L2_MOCCA.std.v02_95_00.J.261027184906.nc`
- `AIRS.AQUA.MODIS.20260104T1441.m06.g147.L2_MOCCA.std.v02_95_00.J.261027184906.nc.cas`

The PGE writes `SNDR.AQUA.MOCCA.101.nc` (`L2OutputFile` in the config). After
a successful run, the image entrypoint renames the two files. If the PGE
fails, the files keep their names. The older registrations (`mocca-v296`,
`mocca-v295`) do not rename the files. The STAC Item takes its id from
`ProductName`, so the id is the same under all three registrations; only the
asset hrefs follow the file names on disk.

`make_stac.py` writes the STAC files after a successful PGE run. It reads
the start and end times, the product name, `BuildId`, `ProcessingLevel` and
the granule fields from the `.cas` file. The `.cas` file has no bounding
coordinates, so the footprint comes from the latitude and longitude
variables in the NetCDF product (read with `h5py`). Granules that cross the
antimeridian get a MultiPolygon. Internal keys such as `FileLocation`,
`NodeInfo` and `JobId` are not copied into the STAC Item.

The STAC collection ID is the product's short name, read from the NetCDF
global attribute `shortname` (or `ShortName`, `short_name`). It is the same
short name the product will have in CMR. Set `MOCCA_COLLECTION` to use a
different ID. If no short name is found, the item has no collection and the
script prints a warning.

MAAP requires absolute asset hrefs. Give the archive location of the
product files with `--asset-base` or the `MOCCA_ASSET_BASE` environment
variable; each asset href is that location plus the file name:

```bash
python3 make_stac.py --asset-base=s3://<bucket>/<prefix> output
```

Without it, the hrefs are relative to the item file and the script prints a
warning. `created_datetime` comes from `ProductionDateTime` in the `.cas`
file.

When the DPS job context is in the folder (`<dataset>.context.json`, which
DPS uploads with the outputs, or `../_context.json` inside a running job),
the item also gets a `derived_from` link to the STAC item of each input
granule (L1B, MYD03, MYD06) and `mocca:container_image`, the PGE image the
job ran. The config file input is not linked, because it is an internal path.

To see every key in the `.cas` file and every NetCDF global attribute:

```bash
python3 make_stac.py --dump output
```

## Source

The PGE source and the container build files are in the SIPS repository
`SIPS/MOCCA-v29601`, `SIPS/MOCCA-v296` and `SIPS/MOCCA-v295`
(`src/sips_pge/l2_mocca/docker`).
