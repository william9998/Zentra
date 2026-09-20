FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv
ENV UV_PROJECT_ENVIRONMENT=/usr/local PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY alembic.ini ./
COPY apps/__init__.py ./apps/__init__.py
COPY apps/api ./apps/api
