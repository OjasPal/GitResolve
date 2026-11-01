FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/gitresolve/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY githubapp /opt/gitresolve/githubapp

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[firestore]"

RUN useradd --create-home --uid 10001 appuser
USER 10001

EXPOSE 8080
# `exec` lets uvicorn receive SIGTERM from Cloud Run for a graceful shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
