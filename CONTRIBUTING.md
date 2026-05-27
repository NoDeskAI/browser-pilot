# Contributing

Thanks for your interest in contributing! This guide will help you get started.

## Getting Started

### Prerequisites

- Docker (with Compose v2)
- Python 3.10+
- Node.js 18+

### Local Development

```bash
cp .env.example .env
# Edit .env as needed (ARM users: uncomment SELENIUM_BASE_IMAGE)

./start.sh          # foreground mode (Ctrl+C to stop)
```

This starts PostgreSQL in Docker, builds the Selenium image, and runs the backend (port 8000) + frontend dev server (port 9874) on the host.

For Docker-based development:

```bash
docker compose build && docker compose up -d
```

### Project Structure

```
backend/          Python (FastAPI) — API server
frontend/         Vue 3 + TypeScript + Tailwind — Web UI
services/
  selenium-chrome/  Browser container image (Dockerfile + stealth extensions)
```

## How to Contribute

### Reporting Bugs

- Use the [Bug Report](https://github.com/NoDeskAI/browser-pilot/issues/new?template=bug_report.md) template.
- Include: OS, Docker version, browser version, steps to reproduce, expected vs actual behavior.
- Attach relevant logs (`docker exec bp-xxx tail -100 /tmp/cdp-events.jsonl`).

### Suggesting Features

- Use the [Feature Request](https://github.com/NoDeskAI/browser-pilot/issues/new?template=feature_request.md) template.
- Describe the problem you're trying to solve, not just the solution.

### Submitting Pull Requests

1. Fork the repo and create a branch from `main`.
2. Make your changes — keep PRs focused on a single concern.
3. Ensure the application builds and runs correctly.
4. Fill out the PR template.

## Code Style

### Backend (Python)

- FastAPI + Pydantic for API routes and models.
- Use type hints everywhere.
- Follow existing patterns in `backend/app/routes/` for new endpoints.

### Frontend (Vue 3 + TypeScript)

- Composition API with `<script setup>`.
- Tailwind CSS for styling.
- i18n: all user-facing strings go through `vue-i18n` (`frontend/src/locales/`).

### Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>
```

`type` must be one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `revert`, or `build`.
`scope` is optional. The subject should briefly describe the change in Chinese or English.

Enable the repository commit hook before committing:

```bash
scripts/setup-git-hooks.sh
```

Examples:

```
feat(start): 支持通过参数选择 CE/EE 版本
fix(novnc): 修复中文剪贴板编码问题
docs(cli): 补齐 Agent Device 接入文档
feat(start): add CE/EE edition selection
```

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
