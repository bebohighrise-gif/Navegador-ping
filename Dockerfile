FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin bebo
WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi 'uvicorn[standard]' pydantic asyncpg
COPY --chown=bebo:bebo . .

USER bebo
EXPOSE 10000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
