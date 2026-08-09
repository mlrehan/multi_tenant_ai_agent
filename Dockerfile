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
# No [dev] extra -- pytest/ruff/mypy have no business in a runtime image.
RUN pip install --no-cache-dir --upgrade pip \
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
