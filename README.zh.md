[English](README.md)

# browser-pilot

面向 AI Agent 的远程浏览器自动化。每个会话运行在独立的 Docker 容器中，内置 Chrome、Selenium、反检测隐身机制和 noVNC 查看器 — 可通过 REST API、CLI 或内置 Web UI 控制。

![Session Viewer](docs/screenshots/session-viewer.png)

## 快速开始

需要 **Docker**（含 Compose v2）。

```bash
git clone https://github.com/NoDeskAI/browser-pilot.git
cd browser-pilot

# 构建镜像并启动服务
docker compose build && docker compose up -d
```

打开 **[http://localhost:8000](http://localhost:8000)** — 即可看到带会话管理和实时浏览器查看器（noVNC）的 Web UI。

![Dashboard](docs/screenshots/dashboard.png)

### Apple Silicon / ARM 用户

构建前先创建 `.env` 文件：

```bash
echo 'SELENIUM_BASE_IMAGE=seleniarm/standalone-chromium:latest' > .env
```

## 命令行工具

安装由 Browser Pilot 后端提供的零依赖 `bpilot` 命令行工具，可以从终端驱动浏览器，也可以对接 OpenClaw 等外部 Agent 框架。Web UI 中有一个 **CLI Access** 按钮，可一键生成面向人类或 AI Agent 的 CLI 接入文档。

![CLI Access](docs/screenshots/cli-access.png)

```bash
curl -fsSL http://localhost:8000/api/cli/install | bash
```

配置并使用：

```bash
bpilot config set api-url http://localhost:8000

bpilot session create --name "My Task"
bpilot session create --name "Mobile" --device iphone-16
bpilot session create --name "Proxied" --proxy socks5://host:port
bpilot session use <session-id>

bpilot session set-device iphone-16    # 切换设备（会重启容器）
bpilot session set-proxy socks5://h:p  # 设置代理（会重启容器）

bpilot navigate https://example.com
bpilot observe                    # 查看页面元素及坐标
bpilot click 640 380              # 按坐标点击
bpilot type "hello world"         # 向当前焦点输入框输入文字
bpilot screenshot --output page.png
```

加 `--json` 可输出机器可读格式（供 AI Agent 使用）。

## 架构

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

每个浏览器会话拥有独立的 Docker 容器，包含：

- 隔离的 Chrome 实例，内置反检测隐身（指纹伪装、拟人化输入模式）
- Selenium WebDriver 自动化
- noVNC（端口 7900）实时查看
- CDP 事件日志用于调试
- **设备预设**：在桌面分辨率（1920×1080 到 1280×720）和移动设备模拟（iPhone、iPad、Galaxy、Pixel）之间切换，自动适配 UA 和视口
- **网络出口配置**：为每个会话复用部署侧的外部代理、Clash 或 OpenVPN 出口，可在 UI 中随时切换

## 本地开发

不使用 Docker 运行后端的本地开发方式：

```bash
cp .env.example .env
# 生产/公网部署前必须修改数据库密码。
# ARM 用户：取消 SELENIUM_BASE_IMAGE 注释。

./start.sh          # 前台模式（Ctrl+C 停止）
./start.sh -d       # 后台守护进程模式
./start.sh stop     # 停止后台进程
./start.sh status   # 检查进程状态
```

该脚本会在 Docker 中启动 PostgreSQL、构建 Selenium 镜像，并在宿主机上运行后端（uvicorn，端口 8000）和前端开发服务器（Vite，端口 9874）。

## 配置项


| Variable              | Default                                                        | Description                                                           |
| --------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------- |
| `DATABASE_URL`        | 必须在 `.env` 中设置；见 `.env.example`                         | 本地后端开发使用的 PostgreSQL 连接字符串，需和 `POSTGRES_*` 保持一致。                  |
| `POSTGRES_USER`       | 必须在 `.env` 中设置；见 `.env.example`                         | Docker Compose 和本地开发使用的 PostgreSQL 用户名。                              |
| `POSTGRES_PASSWORD`   | 必须在 `.env` 中设置；见 `.env.example`                         | PostgreSQL 密码，生产/公网部署前必须修改。                                      |
| `POSTGRES_DB`         | 必须在 `.env` 中设置；见 `.env.example`                         | PostgreSQL 数据库名。                                                       |
| `SELENIUM_BASE_IMAGE` | `selenium/standalone-chrome:latest`                            | 浏览器容器基础镜像。ARM 用户使用 `seleniarm/standalone-chromium:latest`             |
| `DOCKER_HOST_ADDR`    | `localhost`                                                    | 后端访问浏览器容器的地址。Docker 部署时设为 `host.docker.internal`（docker-compose 自动配置） |
| `OPENAI_API_KEY`      | —                                                              | 可选。设置后会用 LLM 在首次导航时自动命名会话，未设置则以页面标题命名                                 |
| `LOG_LEVEL`           | `INFO`                                                         | 后端日志级别。排查问题时可设为 `DEBUG`                                               |
| `NETWORK_EGRESS_DOCKER_NETWORK` | `browser-pilot-net` | 浏览器容器和托管网络出口容器共用的 Docker bridge 网络。 |
| `NETWORK_EGRESS_CONFIG_DIR` | `data/network-egress` | 托管 Clash/OpenVPN 出口配置的私有存储目录。 |
| `NETWORK_EGRESS_CLASH_IMAGE` | `ghcr.io/metacubex/mihomo:latest` | 托管 Clash 出口使用的容器镜像。 |
| `NETWORK_EGRESS_CLASH_PROXY_PORT` | `7890` | 托管 Clash 容器在内部 Docker 网络暴露的代理端口。 |
| `NETWORK_EGRESS_OPENVPN_IMAGE` | `browser-pilot-openvpn-egress:latest` | 托管 OpenVPN 出口使用的容器镜像。默认镜像会在首次使用时从 `services/network-egress-openvpn` 构建。 |
| `NETWORK_EGRESS_OPENVPN_PROXY_PORT` | `8888` | 托管 OpenVPN 容器在内部 Docker 网络暴露的 HTTP 代理端口。 |

### 数据库迁移

Browser Pilot 后端启动时会自动执行 Alembic 迁移。正常升级只需要重启新版本，用户不需要手动执行数据库迁移命令。

如果迁移失败，后端会保持 `/healthz` 存活，但 `/readyz` 会返回不可用，前端会显示数据库更新错误。降级不会自动回滚数据库结构；请使用兼容当前数据库的应用版本，或恢复匹配版本的备份。

### 网络出口

Browser Pilot 可以在 **设置 > 网络出口** 中配置部署侧出口，并让会话通过指定出口访问内网：

- `直连`：不设置浏览器代理，保持当前默认行为。
- `外部代理`：使用已有的 HTTP/HTTPS/SOCKS 代理地址。
- `Clash`：运行托管的 Clash 兼容容器，并让浏览器会话连接其内部代理端口。
- `OpenVPN`：运行托管 OpenVPN 容器和 HTTP 代理封装。该模式要求 Docker 宿主机允许 `/dev/net/tun` 和 `NET_ADMIN`。

网络出口属于部署侧能力。它不会自动复用用户笔记本上已经连接的 VPN，除非该 VPN 配置也能放到当前部署环境中。

## 安全说明

Docker Compose 部署会将 `/var/run/docker.sock` 挂载到后端容器中，使其对宿主机 Docker 守护进程拥有完全控制权。**请勿将此服务暴露在不受信任的网络上。** 远程部署时请使用带认证的反向代理。

## 许可证

Apache License 2.0 — 详见 [LICENSE](LICENSE)。
