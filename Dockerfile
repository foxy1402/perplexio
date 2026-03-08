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
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install SearxNG from git and supervisor
RUN pip install --no-cache-dir \
    "searxng @ git+https://github.com/searxng/searxng.git" \
    supervisor

COPY app ./app
COPY media ./media
COPY searxng ./searxng
COPY supervisord.conf .

EXPOSE 8000

CMD ["supervisord", "-c", "/app/supervisord.conf"]
