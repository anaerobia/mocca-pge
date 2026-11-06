#!/bin/bash
#
# run_mocca.sh -- MAAP DPS run command for the MOCCA L2 PGE.
#
# The PGE and its environment are in the container image
# (anaerobia/mocca:<tag>). This script starts the base image entrypoint,
# /usr/local/bin/run_mocca.sh, with the same arguments.
#
# Usage:
#   run_mocca.sh --config_file <f> --l1b_file <f> \
#                --modis03_file_1 <f> --modis03_file_2 <f> [--modis03_file_3 <f>] \
#                --modis06_file_1 <f> --modis06_file_2 <f> [--modis06_file_3 <f>] \
#                [--log_filename <f>]
#
# The product, the .cas file and the log go in $OUTPUT_DIR (default
# ./output). After a successful DPS-mode run, make_stac.py writes a STAC
# catalog and item for the product there. Other modes (--shell, --acctest,
# <config.xml>) go straight to the base entrypoint.
#
# The -maap Dockerfile makes this script the image ENTRYPOINT. DPS runs jobs
# through CWL, which runs "docker run <image> <run_command> <args>"; with the
# base image's ENTRYPOINT the run command was only an argument to it, and
# this script, with the STAC step, never ran.
#
PGE_ENTRYPOINT=/usr/local/bin/run_mocca.sh

# Under the ENTRYPOINT, DPS passes the run command (this script) as the
# first argument. Drop it.
if [ $# -gt 0 ] && [ "$(basename -- "$1")" = "run_mocca.sh" ]; then
    shift
fi

case "${1:-}" in
    --shell|--acctest|--help|-h|"") exec "$PGE_ENTRYPOINT" "$@" ;;
    --*) ;;                                     # DPS mode: run, then STAC
    *) exec "$PGE_ENTRYPOINT" "$@" ;;
esac

"$PGE_ENTRYPOINT" "$@"
rc=$?
if [ $rc -ne 0 ]; then
    exit $rc
fi

# make_stac.py needs pystac, which is in /opt/stac-env (see the Dockerfile).
# Fall back to python3 if the image has no stac-env.
if [ -z "${STAC_PYTHON:-}" ]; then
    if [ -x /opt/stac-env/bin/python ]; then
        STAC_PYTHON=/opt/stac-env/bin/python
    else
        STAC_PYTHON=python3
    fi
fi
echo "[run_mocca] writing STAC metadata with $STAC_PYTHON"
"$STAC_PYTHON" "$(dirname "$0")/make_stac.py" "${OUTPUT_DIR:-output}" || {
    echo "run_mocca.sh: STAC metadata generation failed" >&2
    exit 1
}
