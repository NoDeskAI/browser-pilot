[中文](README.zh.md)

# browser-pilot

Remote browser automation for AI Agents. Each session runs in an isolated Docker container with Chrome, Selenium, anti-bot stealth, and a noVNC viewer — controllable via REST API, CLI, or the built-in web UI.

![Session Viewer](docs/screenshots/session-viewer.png)

## Quick Start

Requires **Docker** (with Compose v2).

```bash
git clone https://github.com/NoDeskAI/browser-pilot.git
cd browser-pilot

# Build all images and start services
docker compose build && docker compose up -d
```

Open **[http://localhost:8000](http://localhost:8000)** — you'll see the web UI with session management and a live browser viewer (noVNC).

![Dashboard](docs/screenshots/dashboard.png)

### Apple Silicon / ARM users

Before building, create a `.env` file:

```bash
echo 'SELENIUM_BASE_IMAGE=seleniarm/standalone-chromium:latest' > .env
```

## CLI

Install the zero-dependency `bpilot` command-line tool served by the Browser Pilot backend to drive the browser from your terminal or integrate with external Agent frameworks like OpenClaw. The web UI includes a **CLI Access** button that generates a ready-to-paste command reference for humans or AI agents.

![CLI Access](docs/screenshots/cli-access.png)

```bash
curl -fsSL http://localhost:8000/api/cli/install | bash
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
- **Network egress profiles**: Reuse deployment-side external proxy, Clash, or OpenVPN exits per session, changeable at any time in the UI

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


| Variable              | Default                                                        | Description                                                                                                                        |
| --------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`        | `postgresql://bpilot:bpilot@localhost:5432/bpilot` | PostgreSQL connection string                                                                                                       |
| `SELENIUM_BASE_IMAGE` | `selenium/standalone-chrome:latest`                            | Base image for browser containers. ARM users: `seleniarm/standalone-chromium:latest`                                               |
| `DOCKER_HOST_ADDR`    | `localhost`                                                    | How the backend reaches browser containers. Set to `host.docker.internal` in Docker deployment (auto-configured by docker-compose) |
| `OPENAI_API_KEY`      | —                                                              | Optional. When set, uses LLM to auto-name sessions on first navigation. Without it, sessions are named by page title.              |
| `LOG_LEVEL`           | `INFO`                                                         | Backend log verbosity. Set to `DEBUG` for troubleshooting.                                                                         |
| `NETWORK_EGRESS_DOCKER_NETWORK` | `browser-pilot-net` | Docker bridge network used by browser containers and managed egress containers. |
| `NETWORK_EGRESS_CONFIG_DIR` | `data/network-egress` | Private config storage for managed Clash/OpenVPN egress profiles. |
| `NETWORK_EGRESS_CLASH_IMAGE` | `ghcr.io/metacubex/mihomo:latest` | Container image used for managed Clash egress profiles. |
| `NETWORK_EGRESS_CLASH_PROXY_PORT` | `7890` | Proxy port exposed by managed Clash containers on the internal Docker network. |
| `NETWORK_EGRESS_OPENVPN_IMAGE` | `browser-pilot-openvpn-egress:latest` | Container image used for managed OpenVPN egress profiles. The default image is built from `services/network-egress-openvpn` on first use. |
| `NETWORK_EGRESS_OPENVPN_PROXY_PORT` | `8888` | HTTP proxy port exposed by managed OpenVPN containers on the internal Docker network. |

### Network Egress

Browser Pilot can route a session through a deployment-side egress profile from **Settings > Network Egress**:

- `Direct`: no browser proxy, current default behavior.
- `External Proxy`: use an existing HTTP/HTTPS/SOCKS proxy URL.
- `Clash`: run a managed Clash-compatible container and point browser sessions at its internal proxy port.
- `OpenVPN`: run a managed OpenVPN container with an HTTP proxy wrapper. This mode requires the Docker host to allow `/dev/net/tun` and `NET_ADMIN`.

Egress profiles are deployment-side. They do not automatically reuse a VPN already connected on a user's laptop unless that VPN configuration is also available to this deployment.


## Security

The Docker Compose deployment mounts `/var/run/docker.sock` into the backend container, giving it full control over the host Docker daemon. **Do not expose this service on untrusted networks.** Use a reverse proxy with authentication if deploying remotely.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
