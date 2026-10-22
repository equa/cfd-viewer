# syntax=docker/dockerfile:1.10
#
# cfd-viewer's own image: the server in `server` mode, the client inside its
# wheel. Shaped like the equa-ci service archetype's Dockerfile (web, builder,
# runtime), built from the lock (openspec standalone-viewer, design D9, and the
# viewer-build spec "One dependency set for every deployment").
#
# Not yet what cfd-backend ships: its `cfd-viz` image still clones this repo and
# runs main.py (see docs/cfd-backend-switch.md for the switch). Until then this
# image is meant to match cfd-viz where it matters -- same Python base, same
# uid, no GL libraries -- so that the switch changes how the image is built,
# not what runs in it.
#
#   podman build --build-arg SERVICE_VERSION=0.0.0-dev -t cfd-viewer:dev .
#   podman run --rm -p 5003:5003 -v ./data/hotRoom:/data/hotRoom:ro cfd-viewer:dev

ARG PYTHON_IMAGE=python:3.14-slim-trixie
ARG NODE_IMAGE=node:24-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1

# ── web ──────────────────────────────────────────────────────────────────────
# The client. Public npm only; no token reaches this build.
FROM ${NODE_IMAGE} AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

# ── builder ──────────────────────────────────────────────────────────────────
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder

# The version this image claims, passed in by whoever builds it. No default:
# an unpinned build would produce an image whose contents do not match its tag.
# hatch-vcs reads it from here because the build context carries no git history.
ARG SERVICE_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SERVICE_VERSION}

COPY --from=uv /uv /usr/local/bin/uv
# The image's own Python, never a downloaded one; the venv is what ships.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /src
COPY pyproject.toml uv.lock README.md hatch_build.py ./
COPY src ./src
# hatch_build.py puts web/dist into the wheel as equa/cfd_viewer/web and
# refuses to build without it.
COPY --from=web /web/dist ./web/dist

# --frozen: exactly the locked versions, the set requirements.txt exports and
# every other deployment installs. --no-editable: the project goes in as a
# wheel, client included, so /src is not needed at runtime.
RUN set -eu; \
    test -n "${SERVICE_VERSION}" || { echo "SERVICE_VERSION is required" >&2; exit 1; }; \
    uv sync --frozen --no-dev --no-editable

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

ARG SERVICE_VERSION
LABEL org.opencontainers.image.source="https://github.com/equa/cfd-viewer" \
      org.opencontainers.image.version="${SERVICE_VERSION}" \
      org.opencontainers.image.description="cfd-viewer: OpenFOAM post-processing in the browser"

# NO libosmesa6 / libgl1: the server only extracts, and constructing the
# pipeline builds no render window (CLAUDE.md, "VTK packaging"; cfd-backend's
# tests/smoke/viz.sh test 2 guards the same thing for cfd-viz).

# The uid cfd-viz and the other cfd images use, so a shared CFD_HOME volume
# keeps consistent ownership when this image takes cfd-viz's place.
RUN useradd -u 2345 -m -s /usr/sbin/nologin -g users cfd

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER cfd
WORKDIR /home/cfd
EXPOSE 5003

# Python, not curl: the runtime layer carries no shell HTTP client.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5003/health', timeout=4).status == 200 else 1)"]

# Cases are read in place and never written to, so /data may be mounted
# read-only. An empty /data is not fatal: cases are picked up as they appear.
CMD ["cfd-viewer", "--mode", "server", "--data", "/data", "--host", "0.0.0.0", "--port", "5003"]
