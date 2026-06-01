# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

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

### Runtime Worker / Docker Socket

The default `docker-compose.yml` runs Docker operations through an internal `runtime-worker` service. The public backend no longer mounts `/var/run/docker.sock` directly; it calls the worker over the private Compose network with `BROWSER_RUNTIME_CONTROL_TOKEN`. The worker still mounts `/var/run/docker.sock` and therefore has full control over the host Docker daemon.

**Mitigations:**

- Never expose the service directly to the public internet without authentication.
- The bundled single-host Compose stack uses Nginx as the only public 80/443 entrypoint; keep authentication in front of the public backend when deploying remotely.
- Never publish the runtime-worker port outside the private service network.
- Set a long random `BROWSER_RUNTIME_CONTROL_TOKEN` before public deployment.
- Consider running the runtime worker in a dedicated VM or namespace to limit the blast radius.

### Browser Session Isolation

Each browser session runs in its own Docker container with no shared state. However:

- Containers share the host Docker network by default.
- Session data (cookies, local storage) persists within a container's lifetime.
- Hibernated (paused) containers retain their full memory state.

### API Keys

- The `OPENAI_API_KEY` (used for session auto-naming) is stored as an environment variable, never in the database.
- The `.env` file is git-ignored by default. Never commit it.
- S3 storage credentials are stored in the application database — ensure the database is not publicly accessible.
