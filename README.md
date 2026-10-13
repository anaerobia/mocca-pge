# mocca-pge

MAAP DPS algorithm for the AIRS L2 MOCCA PGE (SPSS v02.95.00). MOCCA
aggregates MODIS MYD03 and MYD06 cloud fields onto the AIRS L1B footprints.

The PGE, its Python environment and its static files are in the container
image `anaerobia/mocca:v295`. This repository has the MAAP run and build
commands, and a Dockerfile that adds this repository to that image at
`/app/mocca-pge`. MAAP uses the result, `anaerobia/mocca:v295-maap`.

```bash
docker buildx build --platform linux/amd64 -t anaerobia/mocca:v295-maap --push .
```

## MAAP registration

| Field | Value |
|---|---|
| Repository URL | `https://github.com/anaerobia/mocca-pge` |
| Run Command | `mocca-pge/run_mocca.sh` |
| Build Command | `mocca-pge/build-env.sh` |
| Container | `docker.io/anaerobia/mocca:v295-maap` |

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
<!--
| `modis03_file_3` | no | Third MYD03 granule |
| `modis06_file_3` | no | Third MYD06_L2 granule |
-->

The third MODIS granule (`modis03_file_3`, `modis06_file_3`) is disabled.
As an optional STAC input it must have the type `Directory?`. MAAP DPS
registers that type, but the DPS input stager fails when the input has no
value (`Invalid URL 'None'`). Until MAAP fixes this, the algorithm takes
exactly two granules of each type, and `run_mocca.sh` ignores a third one.
| `log_filename` | no | Log file name. A relative name goes in `./output`. |

The script copies the config and sets these fields in the copy:

- `AirsL1bFile`: the `l1b_file` path.
- `Modis03Files`: the `modis03_file_1` and `modis03_file_2` paths.
- `Modis06L2Files`: the `modis06_file_1` and `modis06_file_2` paths.
- `SFIFFilename`: the SFIF in the image.
- `ProductionDateTime`: the current UTC time (`yymmddHHMMSS`).

If an input is a directory (STAC staging), the script uses the first `.hdf`
file in it. An empty input (`""`) is the same as no input.

## Outputs

`./output` contains the product `SNDR.AQUA.MOCCA.101.nc`, its `.cas`
metadata file and the log.

## Source

The PGE source and the container build files are in the SIPS repository
`SIPS/MOCCA-v295` (`src/sips_pge/l2_mocca/docker`).
