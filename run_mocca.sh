#!/bin/bash
#
# run_mocca.sh -- MAAP DPS run command for the MOCCA L2 PGE.
#
# The PGE and its environment are in the container image
# (anaerobia/mocca:<tag>). This script starts the image entrypoint,
# /usr/local/bin/run_mocca.sh, with the same arguments.
#
# Usage:
#   run_mocca.sh --config_file <f> --l1b_file <f> \
#                --modis03_file_1 <f> --modis03_file_2 <f> [--modis03_file_3 <f>] \
#                --modis06_file_1 <f> --modis06_file_2 <f> [--modis06_file_3 <f>] \
#                [--log_filename <f>]
#
# The product, the .cas file and the log go in ./output. After a successful
# run, make_stac.py writes a STAC catalog and item for the product there.
#
/usr/local/bin/run_mocca.sh "$@"
rc=$?
if [ $rc -ne 0 ]; then
    exit $rc
fi

python3 "$(dirname "$0")/make_stac.py" output || {
    echo "run_mocca.sh: STAC metadata generation failed" >&2
    exit 1
}
