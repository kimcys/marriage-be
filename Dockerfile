FROM python:3.12-slim AS builder

ARG MARRIAGE_OCR_GIT_URL=https://github.com/kimcys/marriage-ocr.git
ARG MARRIAGE_OCR_GIT_REF=06902e69447dae6bd47f8829842e9e68d1e96296

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

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
    && pip install .

FROM python:3.12-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/marriage-ocr /opt/marriage-ocr
COPY --from=builder /build /app

RUN mkdir -p /app/storage \
    && chown -R app:app /app /opt/venv /opt/marriage-ocr

USER app

EXPOSE 8000

CMD ["uvicorn", "marriage_ocr_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

