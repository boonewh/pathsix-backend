# Dockerfile for Quart + Hypercorn
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip && /venv/bin/pip install -r requirements.txt

COPY . .

ENV PATH="/venv/bin:$PATH"

CMD ["hypercorn", "asgi:app", "--bind", "0.0.0.0:8000"]
