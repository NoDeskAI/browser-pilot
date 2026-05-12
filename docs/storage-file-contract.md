# Browser Pilot 文件存储契约

## 背景

Browser Pilot 支持 `Builtin` 和 `S3` 两种文件存储模式。启用 S3 时，文件的最终归属必须是 S3/FileStore，而不是宿主机临时目录、浏览器容器目录或某个 API 调用方本地路径。

当前实测暴露出两个不符合预期的行为：

- CLI 截图使用 `bpilot screenshot -o /tmp/example.png` 时，截图直接写到了调用方本地路径。
- 浏览器内下载文件后，文件落在浏览器容器内的 `/home/seluser/Downloads`，没有进入 S3。

这两个行为都不能作为启用 S3 后的最终语义。它们最多只能作为临时中转或用户显式导出的副本。

## 核心原则

启用 S3 时，Browser Pilot 的所有文件产物都必须先进入 FileStore，再由 FileStore 返回可访问的文件记录或下载入口。

这里的“文件产物”包括但不限于：

- API 截图
- CLI 截图
- 浏览器内下载文件
- 未来录屏、导出、任务产物、AI 生成文件、调试附件

不允许把以下位置当作 canonical storage：

- 宿主机 `/tmp`
- 调用 CLI 的当前目录
- 浏览器容器 `/tmp`
- 浏览器容器 `/home/seluser/Downloads`
- Selenium/Chrome profile 目录
- Docker anonymous volume 中的临时文件

这些位置可以短暂存在文件，但必须满足：

- 文件只是中转缓存或用户显式导出的本地副本。
- 文件进入 FileStore 后才能对外声明保存成功。
- 临时文件需要有清理策略，不能成为唯一副本。

## 目标语义

### API 截图

`GET /api/browser/screenshot` 在启用 S3 后应默认把截图写入 FileStore。

建议返回结构：

```json
{
  "ok": true,
  "file": {
    "id": "...",
    "type": "image/png",
    "name": "screenshot.png",
    "url": "http://localhost:8000/api/files/....png",
    "storage": "s3"
  }
}
```

Base64 截图只能作为兼容模式或显式参数使用，不能作为 S3 模式下的默认最终结果。

### CLI 截图

`bpilot screenshot -o ./page.png` 的语义应是：

1. 后端截图。
2. 后端把截图写入 FileStore/S3。
3. CLI 根据返回的 `file.url` 再下载一个本地副本到 `./page.png`。

因此，`-o` 指定的是“导出副本路径”，不是 canonical storage 路径。

CLI 输出应能区分：

```json
{
  "ok": true,
  "file": {
    "url": "http://localhost:8000/api/files/....png",
    "storage": "s3"
  },
  "localCopy": "./page.png"
}
```

### 浏览器内下载文件

用户在 noVNC/Chrome 内点击下载时，文件不能只停留在 `/home/seluser/Downloads`。

目标流程：

1. Chrome 下载到 session-local download staging 目录。
2. 后端或容器内 watcher 发现下载完成。
3. 上传到 FileStore/S3。
4. 在 session 文件列表或 API 中暴露文件记录。
5. 如需保留容器内文件，只能作为缓存副本，并需要清理策略。

下载完成判断必须避开未完成文件：

- `.crdownload` 不可上传。
- 文件大小稳定后才可上传。
- 同名文件需要保留原始文件名，同时 FileStore key 使用唯一 ID 防冲突。

### 未来文件产物

后续新增任何文件产物时，默认接入 FileStore，不允许新增独立的“写到本地目录”的产品语义。

如果某个功能确实需要本地落盘，只能作为实现细节，并且需要在同一次接口调用或后台任务中完成 FileStore 写入。

## FileStore 职责

FileStore 是文件产物的唯一持久化抽象。

它需要负责：

- 生成稳定文件 ID。
- 保存文件 bytes。
- 保存 content type、文件名、sessionId、createdAt、size 等元数据。
- 返回统一下载 URL。
- 对 S3 模式隐藏内部 endpoint，例如不能向浏览器暴露 `http://minio:9000/...`。
- 支持后端代理下载 `/api/files/...`。

S3 模式下，S3 对象 key 建议包含 session 维度：

```text
files/{sessionId}/{fileId}/{filename}
```

## UI / API 要求

需要提供 session 文件列表能力，至少覆盖浏览器下载文件：

- 文件名
- 文件类型
- 大小
- 来源：`screenshot` / `browser_download` / `recording` / `export` / `artifact`
- 创建时间
- 下载链接
- 存储模式：`s3` / `builtin`

API 可以后续设计，但不应要求用户进入容器查找文件。

## 验收标准

启用 S3 后：

- API 截图返回 FileStore 文件记录，S3 中存在对象。
- CLI 截图即使写了 `-o /tmp/a.png`，S3 中也存在对象；`/tmp/a.png` 只是 local copy。
- 浏览器内下载文件后，S3 中存在对象。
- 容器 `/home/seluser/Downloads` 中的文件不能是唯一副本。
- 前端或 API 能看到下载文件记录。
- 文件 URL 统一走后端 `/api/files/...`，不能暴露 `http://minio:9000`。
- Builtin 模式也走同一套 FileStore 抽象，只是底层存储不同。

## 非目标

本契约不要求所有文件永久保留。保留时长、配额、清理策略可以单独设计。

本契约不要求浏览器下载文件实时上传。允许短暂延迟，但最终必须可观测、可重试、可验证。

## 当前差距

当前实现要求：

- 截图 API 默认写入 FileStore，并继续用 `screenshot` base64 字段兼容旧客户端。
- `includeBase64=false` 可以只返回文件记录。
- CLI `-o` 先让后端写入 FileStore，再通过 `file.url` 下载一个本地副本。
- 浏览器下载通过 session download watcher 上传到 FileStore，并写入 `session_files`。
- `GET /api/sessions/{sessionId}/files` 返回当前 session 的文件记录。
- 文件下载 URL 统一通过后端 `/api/files/...` 代理，不暴露 MinIO 内部地址。

后续仍需单独设计：

- 文件保留时长、配额和清理策略。
- 前端完整文件管理页。
- 录屏、导出、AI 产物等更多文件来源接入。
