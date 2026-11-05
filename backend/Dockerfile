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
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
