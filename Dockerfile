# Combined image for a single-service deployment (Render, Railway, Fly, etc.):
# one FastAPI process serves the API and the built frontend from one origin,
# on one URL. Build context is the repo root.
#
# For local development, `docker-compose.yml` runs the two-service setup
# instead (api + nginx) — this file exists for hosting, not local use.

FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

# ffmpeg/ffprobe do the audio work directly — durations, silence, concatenation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BOOKTALKS_DATA_DIR=/data \
    BOOKTALKS_STATIC_DIR=/srv/frontend/dist

WORKDIR /srv

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /app/dist ./frontend/dist

EXPOSE 8000
# Shell form so a host-provided $PORT (Render, etc.) overrides the default.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
