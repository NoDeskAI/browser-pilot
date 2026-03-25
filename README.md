# NoDeskPane — AI Browser Agent

<p align="center">
  <strong>自然语言驱动的远程浏览器自动化平台</strong>
</p>

通过自然语言指令操控运行在 Docker 容器内的 Chromium 浏览器。前后端分离架构：**Vue 3** 前端 + **Java Spring Boot / LangChain4j** 后端 + **Docker 微服务集群**。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **AI 浏览器操控** | 自然语言驱动 Chrome — 导航、点击、输入、滚动、截图等 |
| **实时画面同步** | 多种可视化方案：noVNC、CDP WebSocket、MJPEG 流、DOM Diff、rrweb 录制回放 |
| **ReAct Agent** | 基于 LangChain4j 的 ReAct 循环，支持多步推理和 Tool Calling |
| **多 LLM 支持** | 兼容 OpenAI / Anthropic / DeepSeek / SiliconFlow 等 API（前端动态切换） |
| **反幻觉系统** | 多层防护：系统提示词约束 + 后端幻觉检测 + 前端历史格式优化 + 自动重试 |
| **错误自动恢复** | API 限流/连接异常时指数退避重试，SSL/TLS 兼容处理 |
| **远程缩放** | 通过 xdotool 发送 Ctrl+/- 直接控制容器内浏览器页面缩放 |
| **Docker 管理** | 前端一键启停容器服务，实时状态监控 |
| **可中断交互** | Agent 运行中可随时发送新指令打断当前任务 |
| **WebSocket 通信** | 前后端 WebSocket 实时双向通信，工具调用与结果实时流式推送 |

---

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│               Vue 3 Frontend (:9874)                        │
│  ┌──────────────────┐  ┌─────────────────┐                  │
│  │  Remote Viewer   │  │   AI Chat UI    │                  │
│  │  noVNC / CDP /   │  │  (WebSocket)    │                  │
│  │  MJPEG / rrweb   │  │                 │                  │
│  └────────┬─────────┘  └───────┬─────────┘                  │
└───────────┼─────────────────────┼───────────────────────────┘
            │ VNC (:7900)         │ ws://localhost:8180/ws/agent
            │              ┌──────▼───────────┐
            │              │  Spring Boot     │
            │              │  Backend (:8180) │
            │              │  LangChain4j     │
            │              │  ReAct Agent     │
            │              └──────┬───────────┘
            │                     │ Selenium WebDriver (:4444)
     ┌──────▼─────────────────────▼───────┐
     │         Docker Services            │
     │  selenium  (:4444 / :7900)         │
     │  cdp-proxy (:3100)                 │
     │  mjpeg-stream (:3200)              │
     │  dom-diff-proxy (:3300)            │
     │  rrweb-proxy (:3400)               │
     └────────────────────────────────────┘
```

### 数据流

```
用户输入 → [前端 AiChat.vue]
           → buildApiMessages() 构建上下文（含历史压缩）
           → WebSocket 发送到后端
           → [AgentService.java] ReAct Agent 循环
              → LLM 生成响应 + Tool Calling
              → 幻觉检测 (isHallucinatingText)
              → 工具执行 (BrowserTools → Selenium WebDriver)
              → 结果流式回传前端
           → 前端实时渲染工具调用状态与结果
```

---

## 快速开始

### 前置要求

- **Docker Desktop**（建议 4GB+ 内存）
- **Node.js** 18+
- **Java** 17+
- **Maven** 3.9+（或使用项目自带的 `./mvnw`）

### 1. 启动 Docker 服务

```bash
# 首次启动需构建镜像，约 3-5 分钟
docker compose up -d
```

验证服务状态：

```bash
docker compose ps
```

启动后可访问：
- Selenium Grid 控制台：http://localhost:4444
- noVNC 浏览器画面（裸）：http://localhost:7900

### 2. 启动后端

```bash
cd backend
./mvnw spring-boot:run
```

后端启动在 **http://localhost:8180**，WebSocket 端点 `ws://localhost:8180/ws/agent`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 **http://localhost:9874**

### 4. 配置 AI

点击右上角 ⚙ 齿轮图标，配置 LLM API：

| 配置项 | 说明 |
|--------|------|
| **API 类型** | OpenAI 兼容 / Anthropic |
| **Base URL** | API 服务地址 |
| **API Key** | API 密钥 |
| **Model** | 模型名称（需支持 Tool Calling） |

**支持的 API 服务：**

| 服务 | Base URL | API 类型 | 推荐模型 |
|------|----------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | OpenAI 兼容 | `gpt-4o` / `gpt-4o-mini` |
| Anthropic | `https://api.anthropic.com` | Anthropic | `claude-sonnet-4-20250514` |
| DeepSeek | `https://api.deepseek.com/v1` | OpenAI 兼容 | `deepseek-chat` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | OpenAI 兼容 | `Qwen/Qwen2.5-7B-Instruct` |

> **注意：** 模型必须支持 Function/Tool Calling。不支持 Tool Calling 的模型（如纯 chat 模型）将无法正常操控浏览器。推荐 Claude 3.5+ 或 GPT-4o 系列。

### 5. 使用

在 AI 聊天框中输入自然语言指令：

```
打开哔哩哔哩，搜索"虎哥"，找到播放量最高的视频，一键三连
```

Agent 会自动执行：导航到 B 站 → 搜索 → 筛选结果 → 进入视频 → 一键三连。所有操作通过左侧 noVNC 画面实时可见。

---

## 项目结构

```
nodeskpane/
├── docker-compose.yml                  # 全部容器服务编排
│
├── frontend/                           # Vue 3 前端
│   ├── vite.config.ts                  # Vite 配置（端口 9874）
│   ├── vite-plugin-docker-api.ts       # Docker 管理 + 远程缩放 API（Vite 插件）
│   ├── src/
│   │   ├── App.vue                     # 主布局（左右分栏）
│   │   ├── components/
│   │   │   ├── AiChat.vue              # AI 聊天 UI + WebSocket 通信 + 历史压缩
│   │   │   ├── NoVNCViewer.vue         # noVNC 远程桌面（含远程缩放控件）
│   │   │   ├── CDPViewer.vue           # CDP WebSocket 方案
│   │   │   ├── MJPEGViewer.vue         # MJPEG 视频流方案
│   │   │   ├── DOMDiffViewer.vue       # DOM Diff 方案
│   │   │   ├── RrwebViewer.vue         # rrweb 录制回放方案
│   │   │   └── SolutionCard.vue        # 服务状态卡片
│   │   ├── composables/useDocker.ts    # Docker 操作封装
│   │   └── solutions.ts               # 方案配置
│   └── package.json
│
├── backend/                            # Java Spring Boot 后端
│   ├── pom.xml                         # Maven 依赖（LangChain4j 1.12 + Apache HttpClient）
│   ├── Dockerfile                      # 多阶段构建镜像
│   └── src/main/java/com/nodeskai/agent/
│       ├── AgentApplication.java       # Spring Boot 启动类
│       ├── config/
│       │   └── WebSocketConfig.java    # WebSocket 配置
│       ├── factory/
│       │   └── LlmFactory.java         # LLM 工厂（OpenAI/Anthropic + Apache HttpClient + TLS）
│       ├── model/
│       │   └── ChatRequest.java        # 请求模型
│       ├── service/
│       │   ├── AgentService.java       # ReAct Agent 核心 + 幻觉检测 + 错误重试
│       │   ├── WebDriverClient.java    # Selenium WebDriver HTTP 封装
│       │   └── StrayToolCallParser.java # 文本格式工具调用解析器
│       ├── tool/
│       │   └── BrowserTools.java       # 浏览器操作工具集（@Tool 注解）
│       └── websocket/
│           ├── AgentWebSocketHandler.java  # WebSocket 消息处理
│           └── WsMessage.java
│
└── services/                           # Docker 微服务
    ├── selenium-chrome/                # Selenium + Chromium + noVNC + xdotool
    │   └── Dockerfile                  # 基于 seleniarm/standalone-chromium
    ├── cdp-proxy/                      # CDP 协议代理
    ├── mjpeg-stream/                   # MJPEG 视频流
    ├── dom-diff-proxy/                 # DOM Diff 代理
    └── rrweb-proxy/                    # rrweb 录制代理
```

---

## 端口一览

| 端口 | 服务 | 说明 |
|------|------|------|
| **9874** | Vite Dev Server | 前端开发服务器 |
| **8180** | Spring Boot | 后端 API + WebSocket |
| **4444** | Selenium Grid | WebDriver HTTP API |
| **7900** | noVNC | 远程浏览器 VNC 画面 |
| 3100 | cdp-proxy | CDP WebSocket 代理 |
| 3200 | mjpeg-stream | MJPEG 视频流 |
| 3300 | dom-diff-proxy | DOM Diff 代理 |
| 3400 | rrweb-proxy | rrweb 录制回放 |

---

## AI Agent 工具列表

| 工具 | 说明 |
|------|------|
| `browser_navigate` | 导航到指定 URL |
| `browser_observe` | 获取页面结构（URL、标题、可交互元素及坐标） |
| `browser_click` | 按坐标点击（最后备选） |
| `browser_click_text` | 按可见文字 / aria-label 点击（首选） |
| `browser_click_element` | 按 CSS 选择器点击元素 |
| `browser_type` | 向当前焦点元素输入文本 |
| `browser_key` | 按键（Enter、Tab、Escape、Ctrl+A 等） |
| `browser_scroll` | 滚动页面（上/下/左/右） |
| `browser_get_page_info` | 获取当前页面 URL 和标题 |
| `browser_list_tabs` | 列出所有浏览器标签页 |
| `browser_switch_tab` | 切换到指定标签页 |
| `browser_triple_like` | B站一键三连（长按点赞按钮触发点赞+投币+收藏） |

---

## 技术栈

| 层 | 技术 |
|----|------|
| **前端** | Vue 3 + Vite 7 + TailwindCSS 4 + TypeScript |
| **后端** | Java 17 + Spring Boot 3.5 + LangChain4j 1.12 |
| **HTTP 客户端** | Apache HttpClient 5（连接池 + TLS 1.2 + 超时管理） |
| **浏览器自动化** | Selenium WebDriver（HTTP API 调用 Chromium） |
| **远程显示** | noVNC / CDP / MJPEG / DOM Diff / rrweb（5 种方案） |
| **容器** | Docker Compose（6 个微服务） |

---

## 核心机制详解

### 反幻觉系统（Anti-Hallucination）

LLM 在长对话中容易产生"幻觉"——输出描述性文字（如"已点击"、"页面显示了…"）而不实际调用工具。本项目通过三层防护解决：

| 层 | 位置 | 机制 |
|----|------|------|
| **系统提示词** | `system-prompt.txt` | 明确禁止文字描述操作、禁止 `[tools: ...]` 格式、强制每次回复调用工具 |
| **后端检测** | `AgentService.java` | `isHallucinatingText()` 匹配 24 种幻觉短语 + 检测伪工具调用文本，触发纠正重试 |
| **前端历史格式** | `AiChat.vue` | `summarizeAssistantMsg()` 使用 `(did: observe,click)` 格式代替 `[tools: ...]`，避免 LLM 模仿 |

### 错误重试机制

| 错误类型 | 处理方式 |
|----------|----------|
| API 限流 (429 / 503) | 指数退避重试，最多 4 次（2s → 4s → 8s → 15s） |
| 连接异常 (reset / timeout / protocol error) | 同上，自动重连 |
| 未知 API 错误 (500 / 502 / unknown error) | 同上 |
| Agent 未调用工具 | 发送纠正提示词重试，最多 5 次 |
| Agent 产生幻觉 | 发送反幻觉提示词重试，最多 5 次 |

前端仅显示一次重试提示，避免重复刷屏。

### 对话历史压缩

前端 `buildApiMessages()` 自动压缩长对话：

- 保留最近 5 轮对话完整内容
- 更早的消息提取摘要（用户指令 + 工具执行状态）
- 首条用户消息作为"原始任务"注入上下文
- `clearChat()` 时将摘要存入 localStorage，新对话可继承上轮上下文

---

## 停止服务

```bash
# 停止 Docker 容器
docker compose down

# 后端/前端：在对应终端按 Ctrl+C
```

---

## 常见问题

### 端口冲突

如果 8180 端口被占用，修改以下两处：

1. `backend/src/main/resources/application.yml` → `server.port`
2. `docker-compose.yml` → `agent-backend.ports`
3. `frontend/src/components/AiChat.vue` → WebSocket URL 中的端口号

### Agent 不调用工具

- 确认所用模型支持 **Function/Tool Calling**（纯 chat 模型不行）
- 推荐使用 Claude 3.5+、GPT-4o、DeepSeek-Chat
- 清除对话重新开始

### SSL/TLS 连接错误

后端已内置 TLS 1.2 兼容配置和信任所有证书（开发模式）。如仍报 `SSLHandshakeException`：

```bash
cd backend
./mvnw spring-boot:run -Dspring-boot.run.jvmArguments="-Dhttps.protocols=TLSv1.2,TLSv1.3"
```

### Docker 容器内浏览器窗口问题

项目已通过 Fluxbox 配置隐藏任务栏，并移除了默认主页设置。如果容器浏览器异常：

```bash
docker compose restart selenium
```

---

## 注意事项

- 浏览器运行在 Docker 容器内，**不会在主机启动浏览器**
- 首次构建 Docker 镜像约需 3-5 分钟
- 后端使用 Apache HttpClient 5 连接池（最大 20 连接），适合高并发 LLM 调用
- Agent 的操作质量取决于所用 LLM 的 Tool Calling 能力
- `system-prompt.txt` 可按需调整 Agent 行为规则

---

## License

MIT
