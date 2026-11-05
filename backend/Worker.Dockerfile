FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/gitresolve/backend
WORKDIR /opt/gitresolve
COPY pyproject.toml README.md ./
COPY backend/app ./backend/app
COPY agent ./agent
COPY githubapp ./githubapp
COPY sandbox ./sandbox
RUN pip install --no-cache-dir '.[firestore]' && useradd --create-home --uid 10001 appuser
USER 10001
CMD ["python", "-m", "app.worker.runner"]
