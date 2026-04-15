---
name: Bug Report
about: Report a bug to help us improve
title: "[Bug] "
labels: bug
---

## Description

A clear and concise description of the bug.

## Steps to Reproduce

1. ...
2. ...
3. ...

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened.

## Environment

- OS: [e.g. macOS 15, Ubuntu 24.04]
- Docker version: [e.g. 27.0]
- Architecture: [x86_64 / ARM]
- Deployment: [docker compose / local dev via start.sh]

## Logs

If applicable, attach relevant logs:

```bash
# CDP event log from the browser container
docker exec bp-<session_id_prefix> tail -100 /tmp/cdp-events.jsonl

# Backend logs
docker compose logs backend --tail=50
```

## Screenshots

If applicable, add screenshots or noVNC viewer captures.
