[中文](README.zh.md)

# browser-pilot

Remote browser automation for AI Agents. Each session runs in an isolated Docker container with Chrome, Selenium, anti-bot stealth, and a noVNC viewer — controllable via REST API, CLI, or the built-in web UI.

![Session Viewer](docs/screenshots/session-viewer.png)

## Quick Start

Requires **Docker** (with Compose v2).

```bash
git clone https://github.com/NoDeskAI/browser-pilot.git
cd browser-pilot

cp .env.example .env
# Edit passwords before public deployment.

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

bpilot network-egress list --json
bpilot network-egress create --name "Office" --type clash --config-file ./clash.yaml

bpilot session create --name "My Task" --network-egress direct
bpilot session use <session-id>
bpilot --session <session-id> session set-network <egress-id|direct>
bpilot session delete <session-id>  # delete session; completed files are kept in Files by default
bpilot session delete <session-id> --delete-files # also delete all completed files

bpilot navigate https://example.com
bpilot observe                    # see page elements with coordinates
bpilot click 640 380              # click at coordinates
bpilot type "hello world"         # type into focused input
bpilot screenshot                 # store screenshot and print a signed file.url
bpilot screenshot --output page.png # stored in FileStore, then exported locally
bpilot files list --json          # list session files with status
bpilot files upload ./input.csv   # upload a local file into the session
bpilot files get <file-id> -o out.csv # save a completed session file locally
bpilot files rename <file-id> final.csv
bpilot files delete <file-id>
```

Add `--json` for machine-readable output (for AI Agents). Use `bpilot session list --json` to inspect each session's `networkEgress*` fields. New sessions return 12-character short ids; existing UUID session ids remain valid. Use `bpilot files list --json` to inspect session files; each item includes `status` so agents can distinguish in-progress files from `completed` files.

For Agent integrations, open **Docs > Agent CLI Access** in the web UI. Browser Pilot exposes each Session as an Agent Device: the session id is the device id, browser side-effect commands require an active exclusive DeviceLease, and every browser action returns an `agentDevice` contract with `executionStatus`, `sideEffectStatus`, `auditStatus`, `evidenceStatus`, `failureCategory`, and `nextStep`. Agents should copy the full API-returned id into `--session`; new ids are usually 12 characters, while old UUID ids still work. Browser Pilot currently supports Agent Device Level 1 Device Governance only; Level 2 control transfer, `request_intervention`, handoff, and human takeover are not supported.

## Architecture

```mermaid
graph TB
  subgraph compose ["docker compose up"]
    Backend["backend:8000 — FastAPI + Web UI"]
    RuntimeWorker["runtime-worker:8001 — private Docker control"]
    Postgres["postgres:5432"]
    ObjectStore["S3-compatible object storage"]
  end
  subgraph dynamic ["Created on demand"]
    B1["bp-xxx — Chrome + Selenium"]
    B2["bp-yyy — Chrome + Selenium"]
  end
  User["Browser"] -->|"http://localhost:8000"| Backend
  User -->|"VNC WebSocket"| B1
  CLI["bpilot CLI"] -->|"REST API"| Backend
  Backend -->|"runtime control token / private network"| RuntimeWorker
  RuntimeWorker -->|"Docker socket"| dynamic
  Backend --> Postgres
  Backend --> ObjectStore
```



Each browser session gets its own Docker container with:

- Isolated Chrome instance with anti-bot stealth (fingerprint spoofing, human-like input patterns)
- Selenium WebDriver for automation
- noVNC (port 7900) for live viewing
- CDP event logger for debugging
- **Device presets**: Switch between desktop resolutions (1920×1080 to 1280×720) and mobile device emulation (iPhone, iPad, Galaxy, Pixel) with automatic UA and viewport switching
- **Network egress profiles**: Route sessions through Direct, Clash, or OpenVPN exits, changeable at any time in the UI

## Development

For local development without Docker for the backend:

```bash
cp .env.example .env
# Edit database credentials before public deployment.
# ARM users: uncomment SELENIUM_BASE_IMAGE.

./start.sh          # foreground mode (Ctrl+C to stop)
./start.sh -d       # background daemon mode
./start.sh ce       # force CE mode
./start.sh ee -d    # force EE mode in daemon mode
./start.sh stop     # stop background processes
./start.sh status   # check process status
```

When no edition argument is provided, `start.sh` auto-detects EE by checking for both `ee/backend/__init__.py` and `ee/frontend/index.ts`; if either file is missing, it runs CE.

This starts PostgreSQL and bundled S3-compatible object storage in Docker, initializes the default bucket, builds the Selenium image, and runs the backend (uvicorn, port 8000) + frontend dev server (Vite, port 9874) on the host.

For a single-host Docker Compose deployment:

```bash
./start.sh single-host ce -d
./start.sh single-host status
./start.sh single-host stop
```

`./start.sh prod` is intentionally removed. Use `./start.sh single-host` for the bundled Docker Compose public boundary.

## Configuration


| Variable              | Default                                                        | Description                                                                                                                        |
| --------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`        | Required in `.env`; see `.env.example`                         | PostgreSQL connection string for local backend development. Keep it aligned with `POSTGRES_*`.                                     |
| `EDITION`             | `ce` in Docker Compose; auto-detected by `start.sh` when no edition argument is provided | Product edition. Use `ce` for Community Edition or `ee` for Enterprise Edition. EE requires the `ee/` sources to be present.       |
| `POSTGRES_USER`       | Required in `.env`; see `.env.example`                         | PostgreSQL user used by Docker Compose and local development.                                                                      |
| `POSTGRES_PASSWORD`   | Required in `.env`; see `.env.example`                         | PostgreSQL password. Change it before public deployment.                                                                           |
| `POSTGRES_DB`         | Required in `.env`; see `.env.example`                         | PostgreSQL database name.                                                                                                         |
| `MINIO_ROOT_USER`     | Required in `.env`; see `.env.example`                         | Root user for the bundled S3-compatible storage service.                                                                           |
| `MINIO_ROOT_PASSWORD` | Required in `.env`; see `.env.example`                         | Root password for the bundled S3-compatible storage service. Change it before public deployment.                                  |
| `MINIO_BUCKET`        | Required in `.env`; see `.env.example`                         | Bucket created automatically by Docker Compose and preconfigured as the default S3 storage bucket.                                |
| `MINIO_ENDPOINT`      | `http://localhost:9000` for `start.sh`; container-internal endpoint in Docker Compose | Endpoint used by the backend to reach the bundled S3-compatible storage service.                                      |
| `MINIO_PUBLIC_ENDPOINT` | `http://localhost:9000` in Docker Compose                    | Public endpoint embedded in S3 signed download URLs. It must be reachable by browsers and CLI clients.                            |
| `APP_ENV`             | `development` locally; `production` in `docker-compose.single-host.yml` | Runtime environment. Single-host public mode enables stricter public-boundary validation.                                         |
| `APP_PUBLIC_ORIGINS`  | Required for single-host public deployment                    | Comma-separated browser origins allowed to use the public backend, for example `https://browser.example.com`.                      |
| `API_BASE_URL`        | `http://localhost:8000`                                       | Public backend URL used in built-in signed file URLs. Set it to the external HTTPS origin in single-host public deployment.        |
| `NGINX_SERVER_NAME`   | Required for `./start.sh single-host`                         | Hostname served by the bundled single-host Nginx reverse proxy, for example `browser.example.com`.                                |
| `NGINX_TLS_CERT_FILE` | `fullchain.pem`                                                | TLS certificate filename under `deploy/nginx/certs/` for the single-host Nginx reverse proxy.                                     |
| `NGINX_TLS_KEY_FILE`  | `privkey.pem`                                                  | TLS private key filename under `deploy/nginx/certs/`. Never commit certificate or key files.                                      |
| `BROWSER_RUNTIME_PROVIDER` | `docker`                                                 | Runtime provider selector. Non-Docker providers require EE sources and fail closed when the provider is unavailable.               |
| `BROWSER_RUNTIME_ACCESS_MODE` | `private` in app config; local `start.sh` uses `published` | Runtime container reachability mode. Single-host public deployment must stay `private`; direct published browser ports are blocked. |
| `BROWSER_VNC_PASSWORD_SECRET` | Required for single-host public deployment             | Secret used to derive per-session browser viewer credentials. Set a long random value before exposing the service publicly.        |
| `VIEWER_TICKET_TTL_SECONDS` | `60`                                                     | Lifetime for browser viewer tickets. Public-boundary validation allows 10-300 seconds.                                             |
| `FILE_DOWNLOAD_URL_TTL_SECONDS` | `300`                                                | Lifetime for generated file download URLs. Public-boundary validation allows 30-3600 seconds.                                      |
| `SELENIUM_BASE_IMAGE` | `selenium/standalone-chrome:latest`                            | Base image for browser containers. ARM users: `seleniarm/standalone-chromium:latest`                                               |
| `BROWSER_GL_MODE`     | `auto`                                                         | Browser WebGL runtime mode: `auto`, `swiftshader`, `angle-swiftshader`, `angle`, `egl`, or `native`. `auto` resolves to `angle-swiftshader` for ARM Chromium and `swiftshader` elsewhere. |
| `DOCKER_HOST_ADDR`    | `localhost`                                                    | How the backend reaches browser containers. Set to `host.docker.internal` in Docker deployment (auto-configured by docker-compose) |
| `BROWSER_RUNTIME_BACKEND_URL` | `http://host.docker.internal:8000` | Backend URL injected into browser runtime agents for internal file ingest callbacks. |
| `BROWSER_RUNTIME_CONTROL_URL` | — | Optional internal runtime-worker URL. Docker Compose sets this to `http://runtime-worker:8001` so the public backend does not mount Docker socket directly. |
| `BROWSER_RUNTIME_CONTROL_TOKEN` | — | Shared bearer token used between backend and runtime-worker. Set a long random value before public deployment. |
| `BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT` | `3600` | Maximum timeout, in seconds, accepted for runtime-worker Docker commands. Large first-time runtime image builds can need a longer timeout. |
| `CLOAK_BROWSER_IMAGE_NAME` | `browser-pilot-cloak:latest` | Optional Cloak Chromium runtime image used by sessions created with `browserRuntime=cloak_chromium`. |
| `BROWSER_HOME_URL` | `https://www.google.com/` | Home page opened automatically when a newly started browser is still on a blank/new-tab page. Set empty to disable. |
| `BP_LEGACY_DOCKER_DOWNLOAD_WATCHER` | `false` | Temporary fallback for old Selenium images without `file-capture-agent`. When enabled, backend uses Docker copy commands and reports a degraded warning. |
| `OPENAI_API_KEY`      | —                                                              | Optional. When set, uses LLM to auto-name sessions on first navigation. Without it, sessions are named by page title.              |
| `LOG_LEVEL`           | `INFO`                                                         | Backend log verbosity. Set to `DEBUG` for troubleshooting.                                                                         |
| `JWT_EXPIRE_MINUTES`  | `30`                                                           | Short-lived access JWT lifetime in minutes.                                                                                       |
| `REMEMBER_ME_DAYS`    | `7`                                                            | Duration for the revocable remember-me cookie used to restore short-lived access tokens.                                           |
| `NETWORK_EGRESS_DOCKER_NETWORK` | `browser-pilot-net`; `browser-pilot-single-host-net` in single-host Compose | Docker bridge network used by browser containers and managed egress containers. |
| `NETWORK_EGRESS_CONFIG_DIR` | `data/network-egress` | Private config storage for managed Clash/OpenVPN egress profiles. |
| `NETWORK_EGRESS_CLASH_IMAGE` | `ghcr.io/metacubex/mihomo:latest` | Container image used for managed Clash egress profiles. |
| `NETWORK_EGRESS_CLASH_PROXY_PORT` | `7890` | Proxy port exposed by managed Clash containers on the internal Docker network. |
| `NETWORK_EGRESS_OPENVPN_IMAGE` | `browser-pilot-openvpn-egress:latest` | Container image used for managed OpenVPN egress profiles. The default image is built from `services/network-egress-openvpn` on first use. |
| `NETWORK_EGRESS_OPENVPN_PROXY_PORT` | `8888` | HTTP proxy port exposed by managed OpenVPN containers on the internal Docker network. |

### File storage

Docker Compose starts a bundled S3-compatible object storage service and preconfigures it as regular S3 storage on first backend startup. The storage settings page still only exposes two modes: **S3 Storage** and **Built-in Storage**. To use AWS S3, Cloudflare R2, OSS, or another S3-compatible provider, edit the S3 fields in the settings page; existing database settings are never overwritten by the Compose defaults.

Browser downloads are captured inside the Selenium/Chrome runtime by `file-capture-agent`. The agent listens to Chrome download completion events and uploads finished files to the backend ingest API; storage provider credentials stay only in the backend. The older backend Docker watcher is disabled by default and should only be enabled temporarily for old browser images that do not contain the runtime agent.

Session files are managed through the backend FileStore. Users and session-scoped API tokens can list, upload, read, rename, and delete active session files through `/api/sessions/{sessionId}/files`; delete responses distinguish backend object deletion from file-list record deletion. When a session is deleted, completed files are either archived into the global Files page or explicitly deleted by the user. User-level tokens can manage global files through `/api/files`; session-scoped tokens cannot access global file management or request archived file URLs. File DTOs return 15-minute signed download URLs: Built-in storage uses signed backend `/api/files/...` URLs, while S3 storage returns S3 pre-signed URLs generated by the backend. S3 credentials remain backend-only.

### Database migrations

Browser Pilot runs Alembic migrations automatically when the backend starts. Normal upgrades only require restarting the new version; users do not need to run migration commands manually.

If migration fails, the backend keeps `/healthz` alive but reports `/readyz` as unavailable and the frontend shows a generic service startup failure while detailed migration errors stay in backend logs. Downgrades do not automatically roll back schema changes; use an app version compatible with the current database or restore a matching backup.

### Browser Runtimes

Sessions default to `standard_chrome`, which uses the existing Selenium Chrome container and full Browser Pilot feature set. For sites with stricter browser automation checks, create a session with `browserRuntime=cloak_chromium` or use:

```bash
bpilot session create --name "Cloak test" --runtime cloak_chromium
```

The Cloak runtime is optional and uses a separate `browser-pilot-cloak:latest` image. Build it before first use:

```bash
docker compose --profile build build cloak
```

The first Cloak build may spend most of its time pulling `cloakhq/cloakbrowser:latest`. If the UI build appears slow or your network is unreliable, you can pre-build the same image in a terminal and then refresh **Settings > Browser Images**:

```bash
docker pull cloakhq/cloakbrowser:latest
docker build -t browser-pilot-cloak:latest services/cloak-chromium-runtime
```

The browser image settings page reports the current build stage, elapsed time, and an estimated progress percentage. The percentage is an estimate because Docker does not expose portable layer-level progress through the runtime-worker command API.

Cloak Chromium keeps the same Browser Pilot API surface and noVNC port shape (`4444` control, `7900` noVNC) through a lightweight WebDriver-compatible shim. It is intended for authorized automation, testing, and self-owned account workflows; it does not solve CAPTCHAs and does not guarantee bypassing every anti-bot system.

### Network Egress

Browser Pilot can route a session through a deployment-side egress profile from **Settings > Network Egress**:

- `Direct`: no browser proxy, current default behavior.
- `Clash`: run a managed Clash-compatible container and point browser sessions at its internal proxy port.
- `OpenVPN`: run a managed OpenVPN container with an HTTP proxy wrapper. This mode requires the Docker host to allow `/dev/net/tun` and `NET_ADMIN`.

Egress profiles are deployment-side. They do not automatically reuse a VPN already connected on a user's laptop unless that VPN configuration is also available to this deployment.
| `BP_VISION_BACKEND`   | `yolo`                                                         | Vision observe backend. The default uses a YOLOv8 UI detector weight. Use `omniparser` to parse screenshots with Microsoft OmniParser. |
| `BP_UI_DETECTOR_MODEL` | —                                                             | Optional absolute path to the YOLOv8 UI detector weight. If unset, Browser Pilot looks for `backend/models/noah-real-yolov8n-ui.pt`. |
| `BP_OMNIPARSER_URL`   | —                                                              | Optional OmniParser server URL, for example `http://127.0.0.1:8001`. The backend calls `POST /parse/`.                             |
| `BP_OMNIPARSER_REPO`  | —                                                              | Optional local OmniParser repo path when not using a server. Requires OmniParser requirements and weights installed separately.     |


### Default YOLO vision backend

`observe --mode vision` uses a YOLOv8 UI detector by default. Model weights are not vendored in this repository. On startup/use, Browser Pilot checks whether the weight exists locally and returns a download hint if it is missing.

Recommended local install:

```bash
mkdir -p backend/models
curl -L \
  -o backend/models/noah-real-yolov8n-ui.pt \
  https://huggingface.co/Noah03064515s22/yolov8-ui-detection-models/resolve/main/models/real_yolov8n.pt
```

Alternatively set:

```bash
export BP_UI_DETECTOR_MODEL=/absolute/path/to/noah-real-yolov8n-ui.pt
```

`observe --mode mix` uses the visual-anchor fusion path: YOLO vision boxes and groups define the primary clickable regions, then DOM and Chrome AX candidates enrich those boxes with text, roles, and href hints. The response still returns a single `mixedCandidates` list in click-viewport coordinates.

### OmniParser vision backend

`observe --mode vision` and the vision leg of `observe --mode mix` can use Microsoft OmniParser V2:

```bash
export BP_VISION_BACKEND=omniparser

# Option A: connect to a separate OmniParser server
export BP_OMNIPARSER_URL=http://127.0.0.1:8001

# Option B: load a local OmniParser clone and weights
export BP_OMNIPARSER_REPO=/path/to/OmniParser
```

Example OmniParser server launch from an upstream clone:

```bash
cd /path/to/OmniParser/omnitool/omniparserserver
python omniparserserver.py \
  --host 127.0.0.1 \
  --port 8001 \
  --device cpu \
  --som_model_path ../../weights/icon_detect/model.pt \
  --caption_model_name florence2 \
  --caption_model_path ../../weights/icon_caption_florence \
  --BOX_TRESHOLD 0.05
```

OmniParser code and weights are not vendored in this repository. Check the OmniParser model licenses before redistribution; its icon detection weights inherit the YOLO license noted by the upstream project.

## Security

The single-host Docker Compose deployment exposes only the bundled Nginx reverse proxy on ports 80/443. Browser container operations run through an internal `runtime-worker` service; the public backend talks to this worker over the private Compose network with `BROWSER_RUNTIME_CONTROL_TOKEN`, and only the worker mounts `/var/run/docker.sock`. Do not publish the worker port, and set a long random runtime control token before public deployment.

The runtime worker still has full control over the Docker daemon. Treat it as privileged infrastructure: keep it on a dedicated host or VM boundary for SaaS workloads, restrict access to the private service network, and keep authentication in front of the public backend when deploying remotely.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
