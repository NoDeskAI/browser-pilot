# NoDeskPane — AI Browser Agent

在网页中通过 AI Agent 实时操控运行在 Docker 容器内的远程浏览器。基于 Selenium Grid + noVNC 实现远程浏览器的实时显示，集成 ReAct Agent 实现自然语言驱动的浏览器自动化。

## 功能特性

- **AI 浏览器操控** — 通过自然语言指令操控远程 Chrome 浏览器（导航、点击、输入、滚动等）
- **实时画面同步** — 通过 noVNC 实时观看 AI Agent 的所有操作，所见即所得
- **ReAct Agent** — 基于 Vercel AI SDK 的 ReAct 循环，支持多步推理和工具调用
- **多 LLM 支持** — 兼容 OpenAI、Anthropic、MiniMax 等 API（前端可配置切换）
- **Docker 管理** — 前端一键启停 Selenium 容器服务
- **可中断交互** — Agent 运行期间可随时输入新指令打断

## 技术架构

```
┌──────────────────────────────────────────────────────────┐
│            NoDeskPane · Vue 3 Frontend                    │
│              http://localhost:9874                         │
│  ┌──────────────────┐  ┌────────────────┐                │
│  │   noVNC Viewer    │  │   AI Chat UI   │                │
│  │ (iframe :7900)    │  │  (SSE stream)  │                │
│  └────────┬─────────┘  └───────┬────────┘                │
└───────────┼─────────────────────┼────────────────────────┘
            │                     │
            │              ┌──────▼──────┐
            │              │  Vite 后端   │
            │              │ ReAct Agent  │
            │              │ (AI SDK +    │
            │              │  WebDriver)  │
            │              └──────┬──────┘
            │                     │ WebDriver API
            │                     │ (:4444)
     ┌──────▼─────────────────────▼──────┐
     │        Selenium Grid (Docker)      │
     │  Chrome + noVNC + WebDriver        │
     │  :4444 (API)  :7900 (VNC)          │
     └────────────────────────────────────┘
```

## 快速开始

### 前置要求

- Docker Desktop（建议 4GB+ 内存）
- Node.js 18+

### 1. 启动 Selenium 容器

```bash
docker compose up -d
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 **http://localhost:9874**

### 3. 配置 AI

点击右上角 **AI 助手** 按钮，在设置面板中配置：

| 配置项 | 说明 |
|--------|------|
| API 类型 | OpenAI 兼容 / Anthropic |
| Base URL | API 服务地址 |
| API Key | 你的 API 密钥 |
| Model | 模型名称 |

支持的 API 服务示例：

| 服务 | Base URL | API 类型 |
|------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | OpenAI 兼容 |
| Anthropic | `https://api.anthropic.com` | Anthropic |
| MiniMax | `https://api.minimaxi.com/anthropic` | Anthropic |
| DeepSeek | `https://api.deepseek.com/v1` | OpenAI 兼容 |

### 4. 使用

在 AI 聊天框中输入自然语言指令：

```
打开哔哩哔哩，搜索"二次元刀哥"，点击播放量最高的视频
```

Agent 会自动执行：导航到 B 站 → 输入搜索词 → 回车搜索 → 点击排序 → 点击视频播放。所有操作通过左侧 noVNC 实时可见。

## 项目结构

```
nodeskpane/
├── docker-compose.yml              # Selenium 容器编排
├── frontend/
│   ├── vite-plugin-ai-chat.ts      # AI Agent 后端（ReAct + WebDriver tools）
│   ├── vite-plugin-docker-api.ts   # Docker 管理 API
│   ├── lib/docker.ts               # Docker Compose 操作封装
│   ├── src/
│   │   ├── App.vue                 # 主布局（noVNC viewer + AI panel）
│   │   ├── components/
│   │   │   ├── AiChat.vue          # AI 聊天 UI（SSE 解析、工具卡片）
│   │   │   └── SolutionCard.vue    # 服务状态卡片
│   │   ├── composables/useDocker.ts
│   │   └── solutions.ts            # Selenium 方案配置
│   └── vite.config.ts
└── services/
    └── selenium-chrome/            # Selenium Grid Docker 镜像
        ├── Dockerfile
        ├── browser.conf
        └── start-browser.sh
```

## AI Agent 工具列表

| 工具 | 说明 |
|------|------|
| `browser_navigate` | 导航到指定 URL |
| `browser_observe` | 获取页面结构（URL、标题、可交互元素及坐标） |
| `browser_click` | 按坐标点击 |
| `browser_click_element` | 按 CSS 选择器点击元素 |
| `browser_type` | 输入文本 |
| `browser_key` | 按键（Enter、Tab、Escape 等） |
| `browser_scroll` | 滚动页面 |
| `browser_get_page_info` | 获取当前页面 URL 和标题 |
| `docker_status` | 查询容器运行状态 |
| `docker_start` | 启动 Selenium 服务 |
| `docker_stop` | 停止 Selenium 服务 |

## 关键技术

- **Vercel AI SDK 6.x** — ReAct agent loop、tool calling、SSE streaming
- **Selenium WebDriver** — 浏览器自动化（HTTP API）
- **noVNC** — VNC over WebSocket，实时画面传输
- **Vue 3 + Vite** — 前端框架 + 开发服务器（插件即后端）
- **Zod** — 工具输入参数校验

## 停止服务

```bash
docker compose down
```

## 注意事项

- 浏览器进程运行在 Docker 容器内，不会在主机启动浏览器
- 首次启动需构建镜像，约需 2-3 分钟
- Agent 的操作质量取决于所用 LLM 的 tool calling 能力
