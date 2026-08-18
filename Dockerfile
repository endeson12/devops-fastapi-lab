# syntax=docker/dockerfile:1
FROM python:3.14.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app app \
    && mkdir -p /app/data && chown -R app:app /app
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN python -m pip install --upgrade "pip==25.2" && python -m pip install .

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
