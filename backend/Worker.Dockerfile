FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/gitresolve/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY agent /opt/gitresolve/agent
RUN pip install --no-cache-dir '.[firestore,agent]' \
    && useradd --create-home --uid 10001 worker
USER 10001
CMD ["python", "-m", "app.worker.runner"]
