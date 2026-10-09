#!/bin/bash
#
# run_mocca.sh -- MAAP DPS run command for the MOCCA L2 PGE.
#
# The PGE and its environment are in the container image
# (anaerobia/mocca:v295). This script starts the image entrypoint,
# /usr/local/bin/run_mocca.sh, with the same arguments.
#
# Usage:
#   run_mocca.sh --config_file <f> --l1b_file <f> \
#                --modis03_file_1 <f> --modis03_file_2 <f> [--modis03_file_3 <f>] \
#                --modis06_file_1 <f> --modis06_file_2 <f> [--modis06_file_3 <f>] \
#                [--log_filename <f>]
#
# The product, the .cas file and the log go in ./output.
#
exec /usr/local/bin/run_mocca.sh "$@"
