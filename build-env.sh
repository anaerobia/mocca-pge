#!/bin/bash
#
# build-env.sh -- MAAP DPS build command for the MOCCA L2 PGE.
#
# The container image (anaerobia/mocca:v295) has the PGE, its Python env,
# the static files and getBuildId. Nothing is installed here. The script
# only makes sure that the image is correct.
#
set -euo pipefail

test -x /usr/local/bin/run_mocca.sh
test -x /opt/spss/src/pge_wrapper/utils/bin/getBuildId
/opt/spss/src/pge_wrapper/utils/bin/getBuildId ''
echo "Build environment check complete."
