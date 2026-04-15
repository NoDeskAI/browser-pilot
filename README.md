# browser-pilot

Remote browser automation for AI Agents. Each session runs in an isolated Docker container with Chrome, Selenium, anti-bot stealth, and a noVNC viewer — controllable via REST API, CLI, or the built-in web UI.

## Quick Start

Requires **Docker** (with Compose v2).

```bash
git clone https://github.com/NoDeskAI/browser-pilot.git
cd browser-pilot

# Build all images and start services
docker compose build && docker compose up -d
```

Open **http://localhost:8000** — you'll see the web UI with session management and a live browser viewer (noVNC).

### Apple Silicon / ARM users

Before building, create a `.env` file:

```bash
echo 'SELENIUM_BASE_IMAGE=seleniarm/standalone-chromium:latest' > .env
```

## CLI

Install the `bpilot` command-line tool to drive the browser from your terminal or integrate with external Agent frameworks like OpenClaw.

```bash
pip install bpilot-cli           # from PyPI
# or
pip install ./cli             # from source
```

Configure and use:

```bash
bpilot config set api-url http://localhost:8000

bpilot session create --name "My Task"
bpilot session create --name "Mobile" --device iphone-16
bpilot session create --name "Proxied" --proxy socks5://host:port
bpilot session use <session-id>

bpilot session set-device iphone-16    # switch device (restarts container)
bpilot session set-proxy socks5://h:p  # set proxy (restarts container)

bpilot navigate https://example.com
bpilot observe                    # see page elements with coordinates
bpilot click 640 380              # click at coordinates
bpilot type "hello world"         # type into focused input
bpilot screenshot --output page.png
```

Add `--json` for machine-readable output (for AI Agents).

## Architecture

```mermaid
graph TB
  subgraph compose ["docker compose up"]
    Backend["backend:8000 — FastAPI + Web UI"]
    Postgres["postgres:5432"]
  end
  subgraph dynamic ["Created on demand"]
    B1["bp-xxx — Chrome + Selenium"]
    B2["bp-yyy — Chrome + Selenium"]
  end
  User["Browser"] -->|"http://localhost:8000"| Backend
  User -->|"VNC WebSocket"| B1
  CLI["bpilot CLI"] -->|"REST API"| Backend
  Backend -->|"Docker socket"| dynamic
  Backend --> Postgres
```

Each browser session gets its own Docker container with:
- Isolated Chrome instance with anti-bot stealth (fingerprint spoofing, human-like input patterns)
- Selenium WebDriver for automation
- noVNC (port 7900) for live viewing
- CDP event logger for debugging
- **Device presets**: Switch between desktop resolutions (1920×1080 to 1280×720) and mobile device emulation (iPhone, iPad, Galaxy, Pixel) with automatic UA and viewport switching
- **Per-session proxy**: Configure HTTP/HTTPS/SOCKS4/SOCKS5 proxy per session, changeable at any time via the UI or CLI

## Development

For local development without Docker for the backend:

```bash
cp .env.example .env
# Edit .env as needed (ARM users: uncomment SELENIUM_BASE_IMAGE)

./start.sh          # foreground mode (Ctrl+C to stop)
./start.sh -d       # background daemon mode
./start.sh stop     # stop background processes
./start.sh status   # check process status
```

This starts PostgreSQL in Docker, builds the Selenium image, and runs the backend (uvicorn, port 8000) + frontend dev server (Vite, port 9874) on the host.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://nodeskpane:nodeskpane@localhost:5432/nodeskpane` | PostgreSQL connection string |
| `SELENIUM_BASE_IMAGE` | `selenium/standalone-chrome:latest` | Base image for browser containers. ARM users: `seleniarm/standalone-chromium:latest` |
| `DOCKER_HOST_ADDR` | `localhost` | How the backend reaches browser containers. Set to `host.docker.internal` in Docker deployment (auto-configured by docker-compose) |
| `OPENAI_API_KEY` | — | Optional. When set, uses LLM to auto-name sessions on first navigation. Without it, sessions are named by page title. |
| `LOG_LEVEL` | `INFO` | Backend log verbosity. Set to `DEBUG` for troubleshooting. |

## Security

The Docker Compose deployment mounts `/var/run/docker.sock` into the backend container, giving it full control over the host Docker daemon. **Do not expose this service on untrusted networks.** Use a reverse proxy with authentication if deploying remotely.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
