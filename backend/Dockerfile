FROM node:22-slim AS frontend

WORKDIR /frontend

RUN npm install -g pnpm

COPY package.json pnpm-lock.yaml ./
COPY apps/desktop ./apps/desktop

RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

RUN pnpm build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_STATIC_DIR=/app/static

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/.env.example ./.env.example
COPY --from=frontend /frontend/dist ./static

RUN pip install --no-cache-dir --no-build-isolation .

EXPOSE 4224

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-4224}"]
