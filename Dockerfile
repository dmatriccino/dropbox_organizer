# syntax=docker/dockerfile:1
# Read-only Dropbox duplicate-media report tool.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    HOME=/home/app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY dropbox_organizer ./dropbox_organizer
# The token dir, data/ and reports/ are bind-mounted by the Makefile; these are
# just mount points. The container runs as your host uid (see Makefile).
RUN mkdir -p /home/app/.config/dropbox_organizer /work/data /work/reports \
    && chmod -R a+rwX /home/app /work

# Tests + lint, run with `make test` / `make lint`.
FROM base AS dev
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
CMD ["sh", "-c", "ruff check --no-cache . && ruff format --no-cache --check . && pytest -q -p no:cacheprovider"]

# Default image.
FROM base AS runtime
WORKDIR /work
ENTRYPOINT ["python", "-m", "dropbox_organizer"]
CMD ["--help"]
