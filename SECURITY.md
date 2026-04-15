# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue.**
2. Email us at **security@nodeskai.com** with:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
3. You will receive acknowledgment within **48 hours**.
4. We aim to release a fix within **7 days** for critical issues.

## Known Security Considerations

### Docker Socket

The default `docker-compose.yml` mounts `/var/run/docker.sock` into the backend container, granting full control over the host Docker daemon. This is required for managing browser session containers.

**Mitigations:**

- Never expose the service directly to the public internet without authentication.
- Use a reverse proxy (Nginx, Caddy, Traefik) with authentication when deploying remotely.
- Consider running in a dedicated VM or namespace to limit the blast radius.

### Browser Session Isolation

Each browser session runs in its own Docker container with no shared state. However:

- Containers share the host Docker network by default.
- Session data (cookies, local storage) persists within a container's lifetime.
- Hibernated (paused) containers retain their full memory state.

### API Keys

- The `OPENAI_API_KEY` (used for session auto-naming) is stored as an environment variable, never in the database.
- The `.env` file is git-ignored by default. Never commit it.
- S3 storage credentials are stored in the application database — ensure the database is not publicly accessible.
