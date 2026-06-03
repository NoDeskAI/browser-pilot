# EE SaaS Billing / Plans / Quotas / Usage Metering Implementation PRD

Status: Implemented for EE SaaS manual billing v1
Owner: Browser Pilot product/backend/frontend
Target repo: Browser Pilot
Last updated: 2026-06-03

## 1. 背景与问题

Browser Pilot 已经具备多租户、用户、API token、session、agent device lease/audit、文件归档、runtime provider、网络出口、CE/EE edition hook 等基础能力，但 EE SaaS 所需的计费 / 套餐 / 配额 / 用量计量目前完全缺位。

本 PRD 的边界已经收敛为：计费配额功能只做在 EE SaaS 部分。CE 不提供 billing API、不创建 billing 表、不展示套餐页面、不执行套餐配额限制。主仓 CE 代码只允许增加少量可选 edition hook，用来让 EE SaaS 在现有 session/browser/file/token/network-egress 入口插入计量和拦截；这些 hook 在 `EDITION != "ee"` 或 EE 子模块不存在时必须是 no-op。

当前代码证据：

- `backend/app/models.py` 只有 `Tenant`、`User`、`ApiToken`、`Session`、`SessionFile`、`SessionRuntimeToken`、`SessionRuntimeStatus`、`AgentDeviceLease`、`AgentDeviceAuditEvent`、`NetworkEgressProfile` 等 CE 基础模型，没有 subscription、plan、quota、usage 表；这应保持为 CE 行为。
- `backend/app/routes/sessions.py` 的 `/api/sessions`、`/api/sessions/{id}/container/start|stop|pause|unpause` 会创建或启动真实浏览器 runtime，但没有 tenant 级 session 上限、runtime 秒数或并发限制。
- `backend/app/routes/browser.py` 的 navigate/observe/click/type/key/scroll/tabs/screenshot 都是天然用量事件入口，其中 screenshot 已经通过 `file_service.save_bytes()` 进入 `session_files`。
- `backend/app/routes/files.py` 和 `backend/app/file_service.py` 已统一处理用户上传、runtime 下载 ingest、截图文件记录，是 storage quota 与 upload/download metering 的主要落点。
- `backend/app/agent_devices.py` 已经记录 action/audit 生命周期，可复用为用量计量的事实来源或补充证据，但不应直接承担计费账本职责。
- `backend/app/edition.py` 已经提供 `register_ee()` / hook 模式，EE SaaS billing 应沿这个机制扩展，不能把支付 provider、套餐模型、配额表直接放进 CE 默认路径。

问题不是“缺一个价格页”，而是缺少一套可执行的 entitlement + usage ledger + quota enforcement。没有它，公网 SaaS 无法控制成本、无法按套餐差异化、无法向客户解释用量，也无法对异常 agent 行为做账务级限流。

## 2. 目标

### 2.1 产品目标

1. 租户能看到当前套餐、有效期、关键配额、当前周期用量和超额状态。
2. 管理员能给租户分配套餐、调整 quota override、查看用量明细。
3. 系统能在高成本入口执行硬配额，避免无限创建 runtime、无限执行动作、无限存储文件。
4. 功能只在 EE SaaS 启用；CE 裸 clone 没有 billing 表、billing API、billing UI 和 provider 依赖。
5. EE SaaS 部署可以接入 Stripe/Paddle/自研支付网关，但支付集成不能成为 CE 启动、CE 构建或 CE 数据库迁移的前置条件。

### 2.2 工程目标

1. 在 EE backend 中建立独立 billing service，避免把配额逻辑散落在 CE 路由里。
2. 所有 metering event 可幂等写入，避免重试造成重复计费。
3. 用量聚合可按 tenant + period + metric 快速查询，不依赖扫描 audit 表。
4. quota check 能在关键入口前置执行，失败返回结构化 402/429 风格响应。
5. 主仓只暴露可选 hook；EE SaaS 负责按 user、API token、session、runtime、source 分析用量。

## 3. 非目标

- 本 PRD 不实现价格策略最终定稿。文档给出第一版套餐结构，价格金额可后续由商业侧调整。
- 不把 `agent_device_audit_events` 当作唯一计费账本。audit 是行为审计，billing usage 是可聚合、可幂等、可重算的账本。
- 不要求第一版必须接 Stripe。第一版先在 EE SaaS 完成内部 entitlement、计量、配额和 UI；支付 provider 作为后续 adapter。
- 不在 CE 中实现 billing 数据模型、billing API、billing UI、套餐默认值或计费用量聚合。
- 不在 CE 中强依赖 EE import、支付 SDK 或 `ee/` 目录存在。

## 4. 核心概念

### 4.1 Plan

套餐模板，定义默认权益与 quota。

字段示例：

- `code`: `free` / `starter` / `pro` / `team` / `enterprise`
- `name`
- `edition`: 固定为 `ee_saas`
- `status`: `active` / `archived`
- `entitlements`: JSONB，功能开关
- `quotas`: JSONB，配额定义

### 4.2 Subscription

租户当前订阅状态。即使没有外部支付，也必须有一条 subscription，来源可以是 `manual`。

状态：

- `trialing`
- `active`
- `past_due`
- `paused`
- `canceled`
- `expired`

### 4.3 Entitlement

租户被允许使用什么能力。来源优先级：

1. `tenant_quota_overrides` / `tenant_entitlement_overrides`
2. 当前 subscription 的 plan
3. EE SaaS 默认 Free/Internal plan

### 4.4 Usage Event

不可变事实事件，表示发生了一次可计量行为。每个 event 必须有幂等键。

### 4.5 Usage Aggregate

周期聚合表，服务 UI 查询和配额判断。它可以从 usage events 重算。

### 4.6 Quota Check / Reservation

硬配额入口先尝试 reservation，成功后执行动作，完成后 commit 实际用量，失败或异常 rollback/release。对于无法预知大小的上传文件，可先检查当前剩余额度，再在保存后按实际 size commit，超过额度时删除对象并返回失败。

## 5. 第一版套餐建议

第一版计费口径收敛为四个核心维度：网页直播流量、会话运行时长、同时运行上限、文件储存空间。其它动作量、vision observe、API token、network egress profile 先作为内部观测或后续套餐限制，不作为第一版计费主轴。

| Metric | Free | Starter | Pro | Team | Enterprise |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sessions.running.max` | 1 | 2 | 5 | 20 | custom |
| `browser.runtime_seconds.monthly` | 3600 | 36000 | 180000 | 900000 | custom |
| `viewer.streaming_bytes.monthly` | 1 GiB | 50 GiB | 500 GiB | 5 TiB | custom |
| `files.storage_bytes.current` | 1 GiB | 10 GiB | 100 GiB | 1 TiB | custom |

说明：

- `sessions.running.max` 是并发硬上限，应按 Docker/runtime 状态与 DB session 状态共同判断。
- `browser.runtime_seconds.monthly` 是 session runtime 处于 running 状态的累计秒数，按月重置。
- `viewer.streaming_bytes.monthly` 是网页直播流量。默认按 noVNC/RFB 代理侧 `bytesToViewer + bytesFromViewer` 计量；若商业口径只收下行，可把公式改为 `bytesToViewer`，但字段仍保留双向明细。
- `files.storage_bytes.current` 是当前已存储对象总量，不按月重置，删除文件后减少。
- `sessions.created.monthly`、`browser.actions.monthly`、`browser.vision_observe.monthly`、`files.upload_bytes.monthly`、`network_egress.profiles.max`、`api_tokens.max` 暂不作为第一版计费项，可保留为风控、告警或后续套餐限制。

## 6. 数据模型

新增 EE Alembic migration，例如 `ee/backend/alembic/versions/ee_0001_billing_usage_quotas.py`。不要在 `backend/alembic/versions/` 新增 billing 表迁移；CE 数据库 schema 不应出现 billing 表。

### 6.1 `billing_plans`

```sql
CREATE TABLE billing_plans (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    edition TEXT NOT NULL DEFAULT 'ee_saas',
    status TEXT NOT NULL DEFAULT 'active',
    entitlements JSONB NOT NULL DEFAULT '{}'::jsonb,
    quotas JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 6.2 `tenant_subscriptions`

```sql
CREATE TABLE tenant_subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    plan_id TEXT NOT NULL REFERENCES billing_plans(id),
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    provider TEXT,
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end TIMESTAMPTZ NOT NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    trial_end TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

索引：

- `UNIQUE (tenant_id) WHERE status IN ('trialing', 'active', 'past_due', 'paused')`
- `provider, provider_subscription_id`

### 6.3 `tenant_quota_overrides`

```sql
CREATE TABLE tenant_quota_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    metric TEXT NOT NULL,
    limit_value BIGINT,
    behavior TEXT NOT NULL DEFAULT 'hard',
    reason TEXT,
    expires_at TIMESTAMPTZ,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, metric)
);
```

`limit_value = NULL` 表示 unlimited。

### 6.4 `tenant_entitlement_overrides`

```sql
CREATE TABLE tenant_entitlement_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    entitlement_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL,
    value JSONB,
    reason TEXT,
    expires_at TIMESTAMPTZ,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, entitlement_key)
);
```

用途：

- 临时打开某个 enterprise feature。
- 对试点客户关闭高风险 feature。
- 让 entitlement resolution 有明确 override 数据源，而不是把所有功能差异塞进 plan JSON。

### 6.5 `usage_events`

```sql
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    session_id TEXT,
    api_token_id TEXT,
    metric TEXT NOT NULL,
    quantity BIGINT NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, metric, idempotency_key)
);
```

索引：

- `(tenant_id, metric, period_start, period_end)`
- `(tenant_id, occurred_at DESC)`
- `(session_id, occurred_at DESC)`

### 6.6 `usage_aggregates`

```sql
CREATE TABLE usage_aggregates (
    tenant_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    quantity BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, metric, period_start, period_end)
);
```

### 6.7 `quota_reservations`

```sql
CREATE TABLE quota_reservations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    quantity BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    idempotency_key TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    committed_event_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, metric, idempotency_key)
);
```

### 6.8 `billing_provider_events`

```sql
CREATE TABLE billing_provider_events (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tenant_id TEXT,
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    processed_at TIMESTAMPTZ,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_event_id)
);
```

用途：

- webhook 幂等处理。
- 支付侧事件审计。
- provider 更新 subscription 失败时保留可重放证据。

## 7. Backend 实现

### 7.0 Edition 边界

实现分为两层：

1. EE SaaS 层：`ee/backend/billing/`、EE Alembic migration、EE routes、EE provider adapter、EE frontend billing 页面。所有 billing 数据、API、UI 和 provider 集成都在这里。
2. CE 主仓 hook 层：只在现有高成本入口调用 `app.edition` 里的可选 hook。默认 CE/no EE 时 hook 返回 no-op。主仓不保存 billing 数据、不知道套餐表结构、不注册 `/api/billing/*`。

主仓允许新增的 hook 示例：

- `before_session_create(user, body)`
- `after_session_created(user, session_id, body)`
- `before_session_runtime_start(user, session_id)`
- `after_session_runtime_stopped(user, session_id, elapsed_hint)`
- `before_browser_action(user, session_id, action, dimensions)`
- `after_browser_action(user, session_id, action, outcome, dimensions)`
- `before_file_store(user, session_id, source, size_hint)`
- `after_file_stored(user, file_payload)`
- `after_file_deleted(user, file_row)`
- `before_api_token_create(user, body)`
- `before_network_egress_profile_create(user, body)`

这些 hook 应由 `backend/app/edition.py` 负责加载 EE hooks；CE 下所有 hook 必须是 no-op，且不能 import `ee.*`。

### 7.1 新增模块

新增 `ee/backend/billing/`：

- `models.py`: Pydantic DTO，不重复定义 SQLAlchemy 表也可以，当前项目大量使用 raw SQL。
- `plans.py`: 默认 plan seed、quota merge、entitlement resolve。
- `usage.py`: `record_usage_event()`、`increment_aggregate()`、`current_usage()`
- `quotas.py`: `check_quota()`、`reserve_quota()`、`commit_reservation()`、`release_reservation()`
- `routes.py`: billing API router。
- `providers/base.py`: 支付 provider 抽象。
- `providers/manual.py`: 默认 manual provider。
- `providers/stripe.py`: 后续可选；只在 EE SaaS provider 启用时懒加载。

`ee/backend/register_routes(app)` 注册 `billing_router`。支付 provider 相关 router 只有显式配置且依赖存在时启用。`backend/app/main.py` 仍只调用 `register_ee(app)`，不直接引用 billing router。

### 7.2 默认数据 seed

在 EE migration 或 EE bootstrap 完成后 seed `billing_plans`：

- `free`
- `starter`
- `pro`
- `team`
- `enterprise`

首个租户创建时：

- EE SaaS hook 在 `backend/app/auth/routes.py:setup()` 创建 tenant/user 后补齐 `tenant_subscriptions`，默认 `free`，source=`manual`。
- CE setup 不创建 subscription。

历史租户补齐：

- EE migration 对所有没有 active subscription 的 tenant 插入 free subscription。
- CE migration 不处理 billing。

### 7.3 Quota Enforcement Hook

新增统一响应：

```json
{
  "ok": false,
  "error": "quota_exceeded",
  "metric": "viewer.streaming_bytes.monthly",
  "limit": 53687091200,
  "used": 53687091200,
  "requested": 1,
  "periodEnd": "2026-06-30T23:59:59Z",
  "upgradePath": "/account/billing"
}
```

建议 HTTP 状态：

- 402: subscription inactive / plan expired / paid feature unavailable
- 429: quota exceeded / rate-like hard limit
- 403: entitlement disabled

### 7.4 Enforcement 点

#### Session 创建

CE hook 调用点：`backend/app/routes/sessions.py`

EE 逻辑位置：`ee/backend/billing/quotas.py`

入口：`create_session()`

检查：

- `sessions.created.monthly` reserve + commit 1
- 如果 create 后立即启动 runtime，则同时检查 `sessions.running.max`

#### Session runtime 启动 / unpause

CE hook 调用点：

- `backend/app/routes/sessions.py:start_session_container()`
- `backend/app/routes/sessions.py:unpause_session_container()`
- `backend/app/container.py:ensure_container_running()`

EE 逻辑位置：`ee/backend/billing/quotas.py`

检查：

- `sessions.running.max`
- subscription status must be usable

说明：

- 路由层先做 tenant 级 check。
- `ensure_container_running()` 可加防御性 check，避免未来新增入口绕过路由。
- running count 不能只查 DB session；要结合 `get_all_container_statuses()` 或 runtime status，至少以当前 route 返回的 container status 为准。

#### Runtime seconds

CE hook 调用点：

- `backend/app/routes/sessions.py:stop_session_container()`
- `backend/app/routes/sessions.py:pause_session_container()`
- `backend/app/container.py:stop_container()` / `pause_container()`

EE 逻辑位置：`ee/backend/billing/runtime_meter.py`

实现：

- 新增 `session_runtime_meter_state` 表或复用 `SessionRuntimeStatus` 扩展字段，记录每个 session 最近 running start。
- start/unpause 时写 `running_started_at`。
- stop/pause/delete 时按 now - running_started_at 记录 `browser.runtime_seconds.monthly`。
- 后台 reconciliation job 每 5-10 分钟对仍 running 的 session 切片入账，避免长时间运行直到 stop 才被发现。

#### Viewer streaming bytes

CE hook 调用点：

- `backend/app/routes/sessions.py:proxy_session_vnc()`
- `backend/app/rfb_proxy.py:bridge_websockets()`

EE 逻辑位置：`ee/backend/billing/viewer_meter.py`

现有代码证据：

- `rfb_proxy.bridge_websockets()` 已返回 `downstream_bytes` 与 `upstream_bytes`。
- `proxy_session_vnc()` 已在 `session.viewer.connect` audit details 中记录 `bytesFromViewer`、`bytesToViewer`、`durationMs`、viewer `mode`。

规则：

- 每次 viewer websocket 关闭时记录 `viewer.streaming_bytes.monthly`。
- 默认计费 quantity = `bytesToViewer + bytesFromViewer`。
- usage event dimensions 必须保留：
  - `bytesToViewer`
  - `bytesFromViewer`
  - `durationMs`
  - `mode`: `control` / `view`
  - `viewOnly`
  - `viewerTicketId`
  - `auditEventId`
- 如果后续商业口径只按下行流量收费，billing summary 仍可用 `bytesToViewer` 计算 invoice quantity，但原始 usage event 不丢失双向字节。
- viewer ticket 验证失败、websocket 未 accept、不产生 RFB bridge 的连接不计流量。

#### Browser actions

CE hook 调用点：`backend/app/routes/browser.py`

EE 逻辑位置：`ee/backend/billing/usage.py`、`ee/backend/billing/quotas.py`

入口：

- `api_navigate`
- `api_current`
- `api_observe`
- `api_click`
- `api_click_element`
- `api_type`
- `api_key`
- `api_scroll`
- `api_tabs`
- `api_switch_tab`
- `api_screenshot`

规则：

- 第一版计费不按 action 收费。
- EE 可继续把 action、vision observe、screenshot 作为 internal usage event 记录，用于风控、成本分析和后续套餐扩展。
- `api_screenshot` 保存文件后必须增加 `files.storage_bytes.current`；是否记录 `files.upload_bytes.monthly` 仅作为内部观测。
- 失败是否计量：
  - auth/session access/quota rejected 不计。
  - 已经调用 WebDriver/CDP 的动作按 action 计，即使目标页面失败。

#### 文件上传和下载 ingest

CE hook 调用点：

- `backend/app/routes/files.py:upload_session_file()`
- `backend/app/routes/files.py:ingest_session_file()`
- `backend/app/file_service.py:save_file()` / `save_bytes()`

EE 逻辑位置：`ee/backend/billing/files.py`

规则：

- 保存前检查 `files.storage_bytes.current` 剩余额度。
- 保存成功后按实际 bytes 增加 `files.storage_bytes.current`。
- 删除文件时减少 current storage aggregate，但不减少 monthly upload aggregate。
- 文件去重返回 existing 时不重复记录 upload usage。

实现建议：

- 在 `file_service._insert_file_record()` 成功后统一调用 `billing.usage.record_file_stored()`。
- 删除路径 `_delete_file_row()` 成功后调用 `billing.usage.record_file_deleted()` 或更新 `files.storage_bytes.current`。
- 为避免 billing 异常导致对象已保存但 DB 未记录，billing commit 应发生在 DB file row 成功后；如果 billing commit 失败，返回 warning 并由 reconciliation 修复，而不是丢文件。

#### FileStore object key 改造

当前实现的对象 key 是 `files/{session_id}/{file_id}/{filename}`，`docs/storage-file-contract.md` 也建议 session 维度。但 EE SaaS 计费和租户隔离需要从存储路径上也体现 tenant/user/session 归属。

目标 key：

```text
files/tenants/{tenantId}/users/{userId}/sessions/{sessionId}/{fileId}/{filename}
```

归档后仍保持原 object key，不因为 session 删除而移动对象；文件归属通过 `session_files.archived_session_id`、`archived_session_name`、`tenant_id`、`user_id` 表达。对于系统生成但没有 user 的文件，不加入 `users` 段，使用：

```text
files/tenants/{tenantId}/sessions/{sessionId}/{fileId}/{filename}
```

对于历史或异常无 tenant 的 CE 兼容对象，继续允许旧 key：

```text
files/{sessionId}/{fileId}/{filename}
```

实现要求：

- `backend/app/file_service.py:save_bytes()` 和 `save_file()` 先读取 session context，再基于 `tenant_id/user_id/session_id/file_id` 生成 object key。
- 旧对象不迁移也不改 URL；读取、删除继续依赖 `session_files.object_key`。
- 新 key 中的 `tenantId`、`userId`、`sessionId`、`fileId` 必须只使用已存在的 ID，不从用户可控文件名推导路径。
- filename 继续使用 `_safe_filename()`，只作为最后一级展示名。
- 更新 `docs/storage-file-contract.md`，把 S3 key 建议从 session-only 改为 tenant/user/session。
- storage quota 不依赖 object key 解析，仍以 `session_files.size_bytes` + `tenant_id/user_id` 为事实源；path 改造是为了可观测性、对象生命周期策略和 S3 prefix 级成本分析。

#### API Token

CE hook 调用点：`backend/app/auth/routes.py`

EE 逻辑位置：`ee/backend/billing/quotas.py`

入口：

- create token

检查：

- 第一版不作为计费项；可作为后续套餐限制或风控指标。

#### Network egress profile

CE hook 调用点：`backend/app/routes/network_egress.py`

EE 逻辑位置：`ee/backend/billing/quotas.py`

入口：

- create profile

检查：

- 第一版不作为计费项；可作为后续套餐限制或风控指标。

### 7.5 Usage Reconciliation

EE SaaS 新增定时任务或 startup background loop：

1. 每 5 分钟刷新 runtime seconds。
2. 每 10 分钟从 `session_files` 汇总 `files.storage_bytes.current` 当前值，修正 aggregate。
3. 每小时扫描 `quota_reservations` 过期 reservation，置为 `expired`。

注意：

- Browser Pilot 当前 `main.py` 已经用 lifespan 启动 DB init 和 shutdown watcher；EE 可通过 `register_ee(app)` / EE middleware / EE startup hook 在 DB ready 后启动 billing reconciliation task。
- reconciliation 必须容忍 DB 未 ready。

## 8. API 设计

以下 API 仅由 EE SaaS 注册。CE 下 `/api/billing/*` 不存在。

### 8.1 租户当前账单概览

`GET /api/billing/summary`

权限：当前 tenant 的 superadmin/admin/member 都可读，但 member 不显示 provider customer id。

返回：

```json
{
  "subscription": {
    "planCode": "pro",
    "planName": "Pro",
    "status": "active",
    "currentPeriodStart": "...",
    "currentPeriodEnd": "...",
    "cancelAtPeriodEnd": false
  },
  "quotas": [
    {
      "metric": "viewer.streaming_bytes.monthly",
      "limit": 53687091200,
      "used": 12884901888,
      "remaining": 40802189312,
      "unit": "bytes",
      "behavior": "hard"
    }
  ],
  "entitlements": {
    "billing.manage": false,
    "runtime.cloak_chromium": true
  }
}
```

### 8.2 用量明细

`GET /api/billing/usage?period=current&metric=viewer.streaming_bytes.monthly`

返回按 day/hour/session/user 维度聚合。

### 8.3 套餐列表

`GET /api/billing/plans`

### 8.4 管理员调整订阅

`PATCH /api/billing/subscription`

权限：superadmin/admin。第一版仅 manual source。

Body：

```json
{
  "planCode": "team",
  "status": "active",
  "currentPeriodEnd": "2026-07-02T00:00:00Z"
}
```

### 8.5 管理员 quota override

`PUT /api/billing/quotas/{metric}/override`

权限：superadmin/admin。

### 8.6 Provider webhook

`POST /api/billing/webhooks/{provider}`

要求：

- 只有 provider 配置完整时注册。
- 校验签名。
- webhook event 先写 `billing_provider_events`，再幂等更新 subscription。

## 9. Frontend 实现

Frontend 也只在 EE SaaS 中实现。CE 前端不增加 Billing route、不增加 Billing nav、不打包 provider UI。

### 9.1 导航

当前 CE `frontend/src/components/AppHeader.vue` 顶部导航已经有 sessions/files/agent-devices/users/settings/docs/account。EE SaaS 第一版不必增加顶栏拥挤入口，建议通过 EE route/nav extension 在 account dropdown 或 `/account` 内增加 Billing tab。

如要给 admin 增加独立入口，使用 lucide icon，例如 `CreditCard`，并遵循现有 compact nav 样式。

### 9.2 页面

新增：

- `ee/frontend/views/BillingView.vue` 或 EE 对 `AccountView` 的 billing section 扩展。
- `ee/frontend/routes` 增加 `/billing` 或 `/account/billing`，通过现有 `@ee/routes` 注入。
- `ee/frontend/locales` 或 EE locale merge 增加所有可见文案。

页面模块：

1. 当前套餐：状态、周期、续费/到期。
2. 配额表：metric、used、limit、remaining、progress。
3. 用量趋势：按 metric 切换，默认本周期。
4. Admin controls：manual plan switch、quota override。
5. Provider state：只有 provider enabled 时显示 checkout/customer portal。

### 9.3 UX 要求

- 配额接近 80% 显示 warning，100% 显示 blocked。
- quota exceeded 的 API error 在 session/browser/files 相关 UI 中转译成人类可读提示。
- 不把计费页做成营销落地页；这是运维型 SaaS 管理界面，保持密集、可扫描。

## 10. 支付 Provider 策略

### 10.1 第一版

仅 manual subscription：

- SaaS 运维或 admin 手动设置 plan。
- 可用来马上解决配额和用量缺口。
- 不引入 Stripe SDK，不新增外部依赖。

### 10.2 第二版

Provider adapter：

- `BILLING_PROVIDER=manual|stripe|paddle`
- `BILLING_STRIPE_SECRET_KEY`
- `BILLING_STRIPE_WEBHOOK_SECRET`
- `BILLING_CHECKOUT_SUCCESS_URL`
- `BILLING_CHECKOUT_CANCEL_URL`

这些变量只在 EE SaaS 生效。如果新增环境变量，必须同步：

- `.env.example`
- `README.md` Configuration 表
- `backend/app/config.py`，使用 `_env()`
- 如影响 Docker，更新 `docker-compose.yml`

支付 SDK import 必须懒加载或 provider-specific 模块内加载。CE 没有 billing provider，不能要求支付依赖存在。

CE 不是 manual provider。CE 是没有 billing provider；manual provider 是 EE SaaS 的内部运营模式。

## 11. 分阶段交付

### Phase 0: Current-state proof

交付：

- 增加一组缺口测试，证明当前 CE 没有 billing API、没有 quota enforcement。
- 明确 EE SaaS 是新增功能边界。
- 不修改行为。

验收：

- CE 下 `/api/billing/*` 不存在。
- CE 下创建 session、浏览器动作、文件上传不受套餐配额限制。
- `EDITION=ce npm run build` 不解析 EE billing 页面或 provider 代码。

### Phase 1: EE SaaS entitlement + plan seed

交付：

- EE Alembic migration 新增 plan/subscription/override 表。
- EE 默认 plan seed。
- EE setup hook 和历史 tenant 自动获得 free subscription。
- EE `GET /api/billing/summary` 可返回套餐与 quota。
- CE 主仓只新增必要 no-op hook，不新增 billing 表。

验收：

- EE SaaS 首次 setup 后可读取 billing summary。
- CE 裸环境首次 setup 后不会创建 subscription，不会出现 billing summary。
- 没有 provider env 时 EE SaaS 可使用 manual source；CE 完全不感知 provider。

### Phase 2: Usage ledger + aggregates

交付：

- EE `usage_events` / `usage_aggregates` / `quota_reservations`。
- `record_usage_event()` 幂等写入和 aggregate upsert。
- EE hook 对 viewer streaming bytes、runtime seconds、file stored/deleted 计量。
- browser actions、vision observe、file upload monthly 只做内部观测，不进入第一版计费 quota。

验收：

- 重复 idempotency key 不重复增加 aggregate。
- viewer websocket 关闭后产生 `viewer.streaming_bytes.monthly` usage event，dimensions 保留上下行字节。
- runtime stop/pause/delete 或 reconciliation 后产生 `browser.runtime_seconds.monthly` usage event。
- screenshot/upload/download ingest 增加 `files.storage_bytes.current`。
- 文件删除减少 `files.storage_bytes.current`。

### Phase 3: Hard quota enforcement

交付：

- `sessions.running.max`
- `browser.runtime_seconds.monthly`
- `viewer.streaming_bytes.monthly`
- `files.storage_bytes.current`

验收：

- EE SaaS 超过 running session 上限时，create/start/unpause 返回 quota error。
- EE SaaS 超过 runtime seconds 月额度时，不能继续 start/unpause 新 runtime。
- EE SaaS 超过 viewer streaming 月额度时，不能签发新的 viewer ticket 或建立 viewer websocket。
- EE SaaS 超过 file storage 时，上传/截图/下载 ingest 失败且对象不残留。
- CE 下同样入口仍按原行为执行，不出现 quota error。

### Phase 4: EE Frontend billing UX

交付：

- EE Billing 页面或 Account billing section。
- quota exceeded toast/error copy。
- admin manual plan/override 控件。

验收：

- superadmin/admin 可调整 plan 和 override。
- member 只读。
- 中英文文案完整。

### Phase 5: Provider adapter

交付：

- EE Stripe/Paddle adapter。
- webhook event 幂等表。
- checkout/customer portal API。

验收：

- provider 未配置时 EE manual source 可用。
- provider 未配置时 CE 无 billing provider 代码路径。
- webhook 重放不重复更新。

## 12. 测试计划

Backend：

- `ee/backend/tests/test_billing_plans.py`
- `ee/backend/tests/test_usage_metering.py`
- `ee/backend/tests/test_quota_enforcement.py`
- `ee/backend/tests/test_billing_files.py`
- `ee/backend/tests/test_billing_runtime_seconds.py`
- `ee/backend/tests/test_billing_viewer_streaming.py`
- `backend/tests/test_file_store_object_keys.py`
- `backend/tests/test_ee_billing_hooks_noop.py`

重点用例：

1. EE SaaS setup 后默认 free subscription 存在。
2. EE admin 可以切换 plan。
3. EE member 不能修改 subscription/override。
4. EE viewer websocket 关闭后按 `bytesToViewer + bytesFromViewer` 记录 streaming usage。
5. EE runtime start/stop/pause/reconciliation 按 running 秒数记录 runtime usage。
6. EE upload/screenshot/download ingest 保存成功后增加 storage current；保存失败不入账。
7. duplicate idempotency key 不重复计量。
8. running session count 在 stopped container 不计入。
9. 新文件 object key 使用 `files/tenants/{tenantId}/users/{userId}/sessions/{sessionId}/{fileId}/{filename}`。
10. 旧对象 key 仍可读取和删除，不要求迁移。
11. provider env 缺失时 EE app 启动不失败并使用 manual source。
12. CE 下 edition hook no-op，不注册 billing route，不创建 billing table。

Frontend：

- EE Billing summary render。
- EE quota warning/blocked 状态。
- EE admin controls 权限。
- EE quota exceeded error copy。
- CE build 不包含 Billing route/nav/provider UI。

验证命令：

- `cd backend && uv run pytest`
- `cd frontend && EDITION=ce npm run build`
- EE SaaS 环境补跑 EE backend/frontend 测试与 build。

如果后续触及 EE import 或 provider SDK，还要额外验证 CE 裸 clone build 不解析 EE/provider-only 模块。

## 13. 风险与处理

### 13.1 Runtime seconds 不准确

风险：容器异常退出或后端重启导致 running start 丢失。

处理：

- start/unpause 写 DB state。
- reconciliation 根据 Docker status 修正。
- 长任务分片入账。

### 13.2 Audit 与 billing 双写不一致

风险：action audit 成功但 usage 写入失败。

处理：

- billing usage 独立幂等。
- dimensions 记录 audit event id。
- 定期 reconciliation 可以从 audit 补账，但 billing 不依赖 audit 实时成功。

### 13.3 文件对象和 quota 入账不一致

风险：对象已保存但 usage event 失败。

处理：

- `session_files` 是文件事实源。
- storage current 可从 `session_files` 重算。
- upload monthly 以 usage event 为准，必要时从 `session_files.uploaded_at` 回补。

### 13.4 CE/EE/provider 依赖污染

风险：CE 构建解析 EE 或支付 SDK。

处理：

- provider adapter 懒加载。
- CE 没有 billing provider；manual provider 只属于 EE SaaS。
- 新增 env 走 `_env()` 和文档同步。
- 前端 provider-specific UI 通过 API capability 而不是编译期硬 import 判断。

## 14. 验收标准

第一轮完整 EE SaaS 实现通过以下标准：

1. EE SaaS tenant 都有可查询的 subscription 和 plan。
2. EE SaaS 的 viewer streaming bytes、runtime seconds、running session count、file storage current 都有用量事件和聚合。
3. EE SaaS 的 `viewer.streaming_bytes.monthly`、`browser.runtime_seconds.monthly`、`sessions.running.max`、`files.storage_bytes.current` 能实际阻断行为。
4. EE frontend 能展示当前套餐、周期、quota used/limit/remaining。
5. 新增 FileStore 对象 key 包含 tenant/user/session；旧 key 兼容读取和删除。
6. CE 裸环境没有 billing provider、billing route、billing table、billing UI；setup、登录、创建 session 仍按原行为运行。
7. EE billing 测试、主仓 backend 测试和 CE 前端构建通过。

## 15. 待确认问题

1. 第一版已经按 manual plan 管理实现；公开 checkout / customer portal / Stripe/Paddle adapter 延后到 provider 集成版本。
2. EE SaaS managed runtime 下仍沿用现有 EE runtime policy：browser image API 和 runtime shell tools 禁用，运行时镜像由平台控制面管理。
3. `viewer.streaming_bytes.monthly` 第一版按 `bytesToViewer + bytesFromViewer` 双向流量计量，usage event dimensions 保留上下行明细，后续可在 invoice 层改为只按下行出账。
4. 第一版默认 hard quota；quota override 支持 `hard` / `soft` / `disabled`，其中 `soft` 只展示不阻断，`disabled` 直接 403。
5. Enterprise 第一版仍按 tenant 维度，不做 workspace/project 拆账。

## 16. 实现状态

本 PRD 的第一版 EE SaaS manual billing 已落地：

- 主仓只增加 CE-safe edition hook 和文件 object key 改造；`EDITION != "ee"` 时 hook no-op，不注册 billing route。
- EE 子模块新增 billing migration、usage ledger、quota reservation、provider event 幂等表、billing service、routes、runtime reconciliation task 和 Billing 页面。
- 第一版四个计费/配额维度均已实现：`sessions.running.max`、`browser.runtime_seconds.monthly`、`viewer.streaming_bytes.monthly`、`files.storage_bytes.current`。
- 文件新 object key 使用 `files/tenants/{tenantId}/users/{userId}/sessions/{sessionId}/{fileId}/{filename}`；系统生成且无 user 时使用 `files/tenants/{tenantId}/sessions/{sessionId}/{fileId}/{filename}`；旧 key 继续按 `session_files.object_key` 读取和删除。
- Provider adapter 第一版不引入外部支付 SDK；`billing_provider_events` 表已预留 webhook 幂等账本，真实 Stripe/Paddle checkout 与签名校验作为后续版本。

验证命令：

- `cd backend && python -m pytest tests/test_edition_billing_hooks.py tests/test_file_service.py tests/test_session_network_behavior.py tests/test_browser_file_routes.py tests/test_download_watcher.py`
- `python -m pytest ee/backend/tests`
- `cd frontend && EDITION=ee npm run build`
- `cd frontend && EDITION=ce npm run build`
