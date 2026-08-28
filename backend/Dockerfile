FROM python:3.11-slim

# ffmpeg/ffprobe do the audio work directly — durations, silence, concatenation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BOOKTALKS_DATA_DIR=/data

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# Shell form so a host-provided $PORT (Render, etc.) overrides the default.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
