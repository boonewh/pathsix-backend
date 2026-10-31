# Dockerfile for Quart + Hypercorn
FROM python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07

WORKDIR /app

# Install PostgreSQL client tools and GPG for database backups
RUN apt-get update && apt-get install -y \
    postgresql-client \
    gnupg \
    ca-certificates \
    libbrotli1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip==26.2.2 && /venv/bin/pip install --require-hashes -r requirements.txt && /venv/bin/pip check

COPY . .

ENV PATH="/venv/bin:$PATH"

RUN python scripts/verify_security_runtime.py

CMD ["hypercorn", "asgi:app", "--bind", "0.0.0.0:8000"]
