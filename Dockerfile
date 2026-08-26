# syntax=docker/dockerfile:1
# Single image for the web Deployment (frisch-web), the collector CronJob
# (frisch-collect) and the admin CLI (frisch) -- same image, different command.

# --- Stage 1: build the SPA -------------------------------------------------
FROM node:26-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ------------------------------------------------
FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRISCH_WEB_STATIC=/app/web-static \
    FRISCH_GIT_CACHE_DIR=/var/cache/frisch/git

# The git collectors shell out to git against bare mirror clones.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package. setuptools-scm derives the version from git, but .git is
# not in the build context, so the Makefile passes the git-derived version in and
# we hand it to setuptools-scm. ARG (not ENV) keeps it out of the final image's
# environment.
ARG VERSION
COPY pyproject.toml README.md ./
COPY frisch/ ./frisch/
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_FRISCH="$VERSION" pip install .

# Drop in the built SPA; the web app picks it up via FRISCH_WEB_STATIC.
COPY --from=frontend /build/dist/ /app/web-static/

# Run unprivileged. The git cache dir is the PVC mount point in k8s; created here
# so local runs work too.
RUN useradd --system --uid 42420 app \
    && mkdir -p /var/cache/frisch/git /var/lib/frisch \
    && chown -R app:app /var/cache/frisch /var/lib/frisch
USER app

EXPOSE 8080
CMD ["frisch-web", "--host", "0.0.0.0", "--port", "8080"]
