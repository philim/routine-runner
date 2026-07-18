# Two-stage build (technical plan §9): the Caddy image with the Cloudflare DNS
# plugin is built separately (see caddy.Dockerfile). This image is the app.
FROM python:3.11-slim

WORKDIR /app

# uv for fast, lockfile-based installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY app ./app
COPY scripts ./scripts

ENV RR_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

# Single worker — the in-process event bus requires it (spec §8.2).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
