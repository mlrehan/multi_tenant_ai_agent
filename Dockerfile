# Production image -- docs/22-deployment-and-operations.md.
#
# Multi-stage so the runtime layer carries neither build toolchain nor dev
# dependencies: argon2-cffi and asyncpg compile C extensions, and shipping gcc
# in the runtime image would add attack surface for no benefit.

# ---------- build stage ----------
FROM python:3.13-slim-bookworm AS builder

# build-essential is needed for the C extensions above; it stays in this stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency install is a separate layer from the source copy so editing code
# doesn't invalidate the (slow) dependency layer on every rebuild.
COPY pyproject.toml README.md* ./
COPY src ./src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# torch is installed **CPU-only, first, from PyTorch's own index**, and only
# then the project.
#
# `docling-ibm-models` requires torch and torchvision unconditionally (the
# layout and TableFormer models behind PDF parsing), and torch's default PyPI
# wheel drags in the entire CUDA stack -- nvidia-cudnn, nccl, cublas, cusparse,
# cusolver, cufft, nvrtc, triton. That is roughly 3 GB of GPU libraries in an
# image with no GPU, on a workload this Dockerfile already pins to eager CPU
# execution via TORCH_COMPILE_DISABLE. Measured on the build this replaced:
# 1.4 GB downloaded in the first 62 minutes and still going, most of it CUDA.
#
# Installing torch first means the resolver sees the requirement already
# satisfied when `pip install .` runs, so it never reaches for the PyPI build.
# Versions are pinned to exactly what the 522-test suite was verified against
# rather than floating, so the image and the tested tree cannot drift apart.
#
# If a worker is ever moved to a GPU host to speed up docling parsing, this is
# the line to revisit -- the CUDA wheels stop being dead weight at that point.
# No [dev] extra -- pytest/ruff/mypy have no business in a runtime image.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.13.0 torchvision==0.28.0 \
    && pip install --no-cache-dir .

# ---------- runtime stage ----------
FROM python:3.13-slim-bookworm AS runtime

# libpq isn't required (asyncpg speaks the wire protocol directly), so the
# runtime layer needs no database client libraries at all.

# OpenCV, however, is required -- and it needs system libraries this slim image
# does not carry. `docling_ibm_models` imports `cv2` when it constructs the
# TableFormer model, and the `opencv-python` wheel (the full build, which is
# what docling depends on) links against X11/GL shared objects. Without these,
# *every* rich-document parse dies at import with
# `ImportError: libxcb.so.1: cannot open shared object file` -- so PDF, DOCX,
# XLSX, PPTX and image ingestion would all fail in production while CSV, JSON
# and XML kept working. Found by building the image and running the model
# warm-up, not by reading the dependency tree.
#
# These are runtime *shared libraries*, not a build toolchain: the Phase 9
# rationale for the builder/runtime split (keep compilers and headers out of
# the runtime image) is untouched. `--no-install-recommends` keeps it to the
# handful actually dlopen'd.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root, and with no login shell: if the process is compromised, the
# attacker lands as an unprivileged user that cannot write to the app
# directory (owned by root, mode 555 by default for copied files).
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Docling's document models are fetched from Hugging Face on first use.
    # Left at the default (`~/.cache`), the `app` user has no home directory,
    # and any cache written into the container layer dies with the container
    # -- so every pod restart would re-download several hundred MB before it
    # could parse its first PDF, and a worker on a network without egress to
    # huggingface.co could never parse one at all. Baked into the image below
    # instead, at a path the runtime only ever reads.
    HF_HOME=/opt/models/huggingface \
    # Same reasoning as HF_HOME, for the browser Playwright drives. The default
    # is `~/.cache/ms-playwright`, and the `app` user has no home directory --
    # so without this the browser would be installed somewhere the runtime user
    # cannot read, or not found at all. A fixed path under /opt is baked in
    # below and only ever read at runtime.
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    # TorchInductor generates and compiles C++ at *parse* time. This image
    # deliberately carries no compiler (that's the point of the builder-stage
    # split), so the compiled path cannot work here and would fail every
    # PDF/DOCX/XLSX/PPTX/image with `InvalidCxxCompiler` while CSV/JSON/XML
    # kept working. Eager execution is slower per document and ingestion is
    # already a background job. `infrastructure/parsing/rich_documents.py`
    # sets the same variables for non-container runs.
    TORCH_COMPILE_DISABLE=1 \
    TORCHDYNAMO_DISABLE=1

# Pre-fetch the docling models so the first parse in a fresh container is not
# a multi-hundred-megabyte download. Runs as root, before `USER app`, so the
# cache is owned by root and read-only to the runtime user.
#
# `HF_HUB_OFFLINE` is deliberately NOT set: if docling later needs a model this
# warm-up didn't pull (a format-specific OCR model, say), offline mode would
# turn that into a hard failure everywhere rather than a slow first parse where
# egress exists. Operators running workers on a network with no route to
# huggingface.co should set `HF_HUB_OFFLINE=1` themselves, to fail fast and
# loudly instead of stalling on a fetch that can never succeed.
# `DocumentConverter()` alone downloads nothing -- construction is lazy and the
# models are only fetched when a document is actually converted, so a warm-up
# that just builds the converter silently produces an empty cache and moves the
# download back to the first real parse. `initialize_pipeline` is what forces
# it. (Found the obvious way: the build failed on `chmod`, because the
# directory it was meant to populate did not exist.)
RUN python -c "import cv2; print('cv2 ok', cv2.__version__)" \
    && python -c "\
from docling.datamodel.base_models import InputFormat; \
from docling.document_converter import DocumentConverter; \
c = DocumentConverter(); \
c.initialize_pipeline(InputFormat.PDF); \
c.initialize_pipeline(InputFormat.IMAGE)\
" && chmod -R a+rX "$HF_HOME"

# Install the browser Playwright drives, and the shared libraries it dlopens.
#
# **`pip install playwright` does not install a browser.** It installs the
# Python client; the Chromium binary is a separate download. Without this step
# `crawl4ai` raises at crawl time, so website ingestion would fail in
# production while file uploads kept working -- the same partial failure the
# OpenCV and TorchInductor problems above produced, and one that reads like
# "that site can't be crawled" rather than "this deployment is broken". It went
# unnoticed because the development machine has browsers in
# `~/.cache/ms-playwright` from an earlier manual install, so crawling worked
# everywhere it was actually tried.
#
# `--with-deps` rather than a hand-written apt list: Chromium's shared-library
# set is long, version-dependent, and exactly the kind of thing that rots. The
# OpenCV list above had to be discovered empirically one missing `.so` at a
# time; there is no reason to repeat that when the vendor ships the answer.
#
# Runs as root before `USER app` so the browser is root-owned and read-only to
# the runtime user, matching the model cache.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

WORKDIR /app
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
# One-off ops scripts (e.g. bootstrap_platform_admin.py) -- run via
# `docker compose run --rm migrate python scripts/...`, never by the CMD
# below. Small and dependency-free, so it costs nothing to carry in the
# runtime image rather than requiring a separate maintenance image.
COPY --chown=root:root scripts ./scripts

USER app
EXPOSE 8000

# Liveness only -- readiness is the orchestrator's job via /readyz, and a
# container-level healthcheck that probed dependencies would restart the
# container during a database blip (see api/v1/system/router.py).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=2).status == 200 else 1)"

# Migrations are NOT run here -- see docs/22-deployment-and-operations.md.
# They run as a separate job with the migrator role's credentials, so the
# long-lived app container never holds DDL privileges.
#
# Not `uvicorn iam_platform.asgi:app`: the app is built asynchronously (secret
# resolution does I/O), so it's constructed inside the serving loop by this
# module's own launcher rather than imported as a module-level object.
CMD ["python", "-m", "iam_platform.asgi"]
