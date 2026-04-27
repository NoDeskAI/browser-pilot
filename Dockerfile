ARG EDITION=ce

# Stage 0: Collect context (ensures ee/ always exists for COPY)
FROM alpine AS context
WORKDIR /ctx
COPY . .
RUN mkdir -p ee/frontend ee/backend

# Stage 1: Build frontend (with optional EE components)
FROM node:22-slim AS frontend
ARG EDITION
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY --from=context /ctx/ee/frontend/ /ee/frontend/
RUN EDITION=$EDITION npm run build

# Stage 2: Python backend + Docker CLI
FROM python:3.12-slim
ARG EDITION

COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
ENV PROJECT_ROOT=/app
ENV EDITION=${EDITION}
COPY backend/ ./
RUN pip install --no-cache-dir .

COPY --from=context /ctx/ee/ /app/ee/
RUN if [ -f /app/ee/backend/requirements.txt ]; then pip install --no-cache-dir -r /app/ee/backend/requirements.txt; fi
COPY --from=frontend /build/dist /app/static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
