# ---- Stage 1: Build SearxNG + heavy native deps ----
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libyaml-dev \
    zlib1g-dev \
    libssl-dev \
    pkg-config \
    git \
    && rm -rf /var/lib/apt/lists/*

# Clone SearxNG and install deps first, then package
RUN git clone --depth 1 https://github.com/searxng/searxng.git /tmp/searxng
RUN pip install --no-cache-dir -r /tmp/searxng/requirements.txt
RUN pip install --no-cache-dir --no-deps /tmp/searxng
RUN pip install --no-cache-dir supervisor
RUN rm -rf /tmp/searxng

# ---- Stage 2: Final slim image ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SEARXNG_SETTINGS_PATH=/app/searxng/settings.yml

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    libxml2 \
    libxslt1.1 \
    libyaml-0-2 \
    && rm -rf /var/lib/apt/lists/*

# Copy all pre-built Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY media ./media
COPY searxng ./searxng
COPY supervisord.conf .

EXPOSE 8000

CMD ["supervisord", "-c", "/app/supervisord.conf"]
