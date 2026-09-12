FROM python:3.12-slim AS builder

ARG MARRIAGE_OCR_GIT_URL=https://github.com/kimcys/marriage-ocr.git
ARG MARRIAGE_OCR_GIT_REF=5d478933b1d7ddae73ee436f88ceeca0c86c5921

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN git clone "${MARRIAGE_OCR_GIT_URL}" /opt/marriage-ocr \
    && cd /opt/marriage-ocr \
    && git checkout --detach "${MARRIAGE_OCR_GIT_REF}"

RUN pip install --upgrade pip \
    && pip install /opt/marriage-ocr \
    && pip install ".[dev]"

# Browser binary only here (no --with-deps): this stage is discarded, and
# the runtime OS libraries it would apt-get install don't survive into the
# final image's COPY --from=builder anyway. install-deps runs for real in
# the final stage below, after /opt/venv (and the playwright package with
# it) is copied over.
RUN python -m playwright install chromium

FROM python:3.12-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/marriage-ocr /opt/marriage-ocr
COPY --from=builder /opt/pw-browsers /opt/pw-browsers
COPY --from=builder /build /app
COPY tests /app/tests

# Only the OS-level shared libraries headless Chromium needs, matched to
# the browser binary already unpacked at /opt/pw-browsers above -- the
# binary itself isn't re-downloaded here.
RUN python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/storage \
    && ln -sf /opt/venv/bin/alembic /usr/local/bin/alembic \
    && ln -sf /opt/venv/bin/mypy /usr/local/bin/mypy \
    && ln -sf /opt/venv/bin/pytest /usr/local/bin/pytest \
    && ln -sf /opt/venv/bin/ruff /usr/local/bin/ruff \
    && ln -sf /opt/venv/bin/uvicorn /usr/local/bin/uvicorn \
    && chown -R app:app /app /opt/venv /opt/marriage-ocr /opt/pw-browsers

USER app

EXPOSE 8000

CMD ["uvicorn", "marriage_ocr_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
