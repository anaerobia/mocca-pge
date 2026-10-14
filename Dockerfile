# MAAP DPS image for the MOCCA L2 PGE.
#
# The base image has the PGE, its Python env and its static files. MAAP runs
# the run command /app/mocca-pge/run_mocca.sh, so this image adds the
# repository at that path.
#
#   docker buildx build --platform linux/amd64 --build-arg MOCCA_TAG=v296 \
#       -t anaerobia/mocca:v296-maap --push .
#
# MOCCA_TAG selects the base image: v295 (SPSS v02.95.00) or v296
# (SPSS v02.96.00).
ARG MOCCA_TAG=v296
FROM docker.io/anaerobia/mocca:${MOCCA_TAG}

COPY . /app/mocca-pge/
RUN chmod +x /app/mocca-pge/run_mocca.sh /app/mocca-pge/build-env.sh
