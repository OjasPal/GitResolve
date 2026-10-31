FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox \
    && pip install --no-cache-dir pytest

WORKDIR /workspace
USER 10001:10001