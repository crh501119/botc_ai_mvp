FROM python:3.12-slim AS backend

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install
COPY frontend ./
RUN pnpm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY botc_ai ./botc_ai
COPY config ./config
COPY alembic ./alembic
COPY main.py ./
COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV MOCK_AI=true
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "botc_ai.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
