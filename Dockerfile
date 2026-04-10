# Stage 1: Build frontend
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build CLI wheel
FROM python:3.12-slim AS cli-builder
WORKDIR /build
COPY cli/ ./
RUN pip wheel --no-deps . -w /dist/

# Stage 3: Python backend + Docker CLI
FROM python:3.12-slim

COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY backend/ ./
RUN pip install --no-cache-dir .

COPY --from=frontend /build/dist /app/static
COPY --from=cli-builder /dist/*.whl /app/cli-dist/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
