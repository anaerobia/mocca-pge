# MAAP DPS image for the MOCCA L2 PGE.
#
# The base image has the PGE, its Python env and its static files. MAAP runs
# the run command /app/mocca-pge/run_mocca.sh, so this image adds the
# repository at that path.
#
#   docker buildx build --platform linux/amd64 -t anaerobia/mocca:v295-maap --push .
FROM docker.io/anaerobia/mocca:v295

COPY . /app/mocca-pge/
RUN chmod +x /app/mocca-pge/run_mocca.sh /app/mocca-pge/build-env.sh
