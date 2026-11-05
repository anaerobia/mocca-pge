# MAAP DPS image for the MOCCA L2 PGE.
#
# The base image has the PGE, its Python env and its static files. MAAP runs
# the run command /app/mocca-pge/run_mocca.sh, so this image adds the
# repository at that path.
#
#   docker buildx build --platform linux/amd64 --build-arg MOCCA_TAG=spss261104 \
#       -t anaerobia/mocca:spss261104-maap --push .
#
# MOCCA_TAG selects the base image: spss<YYMMDD> (SPSS development build,
# for example spss261104), v29601 (SPSS v02.96.01), v296 (SPSS v02.96.00)
# or v295 (SPSS v02.95.00).
ARG MOCCA_TAG=spss261104
FROM docker.io/anaerobia/mocca:${MOCCA_TAG}

# make_stac.py needs pystac. The PGE's Python (3.6) is too old for it and
# the base image has no python3 on PATH, so build a separate environment.
# pystac 1.10.x writes STAC 1.0.0, the version the MAAP STAC API serves.
RUN dnf -y install python3.11 \
    && dnf clean all \
    && python3.11 -m venv /opt/stac-env \
    && /opt/stac-env/bin/pip install --no-cache-dir \
        pystac==1.10.1 h5py==3.12.1

COPY . /app/mocca-pge/
RUN chmod +x /app/mocca-pge/run_mocca.sh /app/mocca-pge/build-env.sh
