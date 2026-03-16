# NoDeskPane

> 给无桌面的远程浏览器一扇窗。

在网页中实时显示并操控运行在 Docker 容器内的远程浏览器。集成 7 种不同流式传输方案，通过 Vue 3 Playground 统一对比展示。

## 方案一览

| # | 方案 | 传输协议 | 端口 | 延迟 | 交互 | 多人 |
|---|------|----------|------|------|------|------|
| 1 | Selenium Grid | VNC / WebSocket (内置 noVNC) | 7900 | 100-300ms | 完整 | - |
| 2 | MJPEG 截屏流 | multipart/x-mixed-replace | 3200 | 300-500ms | 点击/键盘/滚轮 | - |
| 3 | n.eko | WebRTC (UDP) | 8080 | <100ms | 完整 | 支持 |
| 4 | noVNC + x11vnc | VNC / WebSocket | 6080 | 100-300ms | 完整 | - |
| 5 | KasmVNC | WebRTC + WASM + WebGL | 6901 | 50-150ms | 完整 | - |
| 6 | Browserless | Chrome DevTools Protocol | 3000 | 200-500ms | Canvas 转发 | - |
| 7 | CDP DIY 自研 | Page.captureScreenshot / WS | 3100 | 200-500ms | Canvas 转发 | - |

## 快速开始

### 前置要求

- Docker Desktop（建议分配 8GB+ 内存、4 核 CPU）
- Node.js 18+

### 1. 启动后端容器

```bash
# 全部启动
docker compose up -d

# 查看状态
docker compose ps
```

也可以按需单独启动：

```bash
docker compose up -d selenium                      # Selenium Grid
docker compose up -d mjpeg-stream                   # MJPEG 截屏流
docker compose up -d neko                           # n.eko (WebRTC)
docker compose up -d novnc-chrome                   # noVNC
docker compose up -d kasmvnc                        # KasmVNC
docker compose up -d browserless browserless-proxy  # Browserless (需两个服务)
docker compose up -d chrome-headless cdp-proxy      # CDP DIY (需两个服务)
```

### 2. 启动前端 Playground

```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 http://localhost:9874

### 3. 使用

- 左侧面板展示 7 个方案卡片，每张显示实时运行状态
- 点击卡片进入对应方案的浏览器实时画面
- 顶部地址栏可统一导航所有方案
- 支持从前端一键启停 Docker 服务

## 各方案直接访问地址

不使用 Playground 也可以直接访问：

| 方案 | 地址 | 备注 |
|------|------|------|
| Selenium Grid | http://localhost:7900/?autoconnect=1&resize=scale | 自动连接 |
| MJPEG 截屏流 | http://localhost:3200 | 纯 HTTP 流 |
| n.eko | http://localhost:8080 | 密码: neko / admin |
| noVNC | http://localhost:6080/vnc.html?autoconnect=true | 自动连接 |
| KasmVNC | http://localhost:6901 | 密码: password |
| Browserless | http://localhost:3000 | 调试器 UI |
| CDP Proxy | ws://localhost:3100 | WebSocket |

## 技术架构

```
┌─────────────────────────────────────────────────────────────────┐
│                 NoDeskPane · Vue 3 Playground                   │
│                   http://localhost:9874                          │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐       │
│  │  iframe   │  <img>   │  iframe   │  Canvas  │  Canvas  │       │
│  │(Selenium) │ (MJPEG)  │(VNC 系列) │(Browser- │(CDP DIY) │       │
│  │           │          │          │  less)   │          │       │
│  └─────┬─────┴─────┬────┴─────┬────┴─────┬────┴─────┬────┘       │
└────────┼───────────┼──────────┼──────────┼──────────┼───────────┘
         │           │          │          │          │
  ┌──────▼──────┐ ┌──▼───┐ ┌───▼───┐ ┌────▼────┐ ┌───▼───┐
  │  Selenium   │ │MJPEG │ │ neko  │ │Browser- │ │  CDP  │
  │   :7900     │ │:3200 │ │ :8080 │ │  less   │ │ Proxy │
  │  noVNC +    │ │Puppe-│ │WebRTC │ │ Proxy   │ │ :3100 │
  │  Chromium   │ │ teer │ │GStrea-│ │ :3001   │ │   ↕   │
  └─────────────┘ └──────┘ │  mer  │ │   ↕     │ │Chrome │
                           └───────┘ │Browser- │ │:9222  │
  ┌─────────────┐ ┌────────┐         │  less   │ └───────┘
  │   noVNC +   │ │ Kasm   │         │ :3000   │
  │   x11vnc    │ │  VNC   │         └─────────┘
  │   :6080     │ │ :6901  │
  └─────────────┘ └────────┘
                                         全部运行在 Docker 容器中
```

## 停止

```bash
docker compose down
```

## 注意事项

- 所有浏览器进程均在 Docker 容器内运行，不会在主机上启动浏览器
- n.eko 使用 WebRTC UDP 端口 52000-52100，确保防火墙放行
- 所有方案默认首页为百度 (https://www.baidu.com)
- 首次启动需要构建/拉取镜像，可能需要几分钟
