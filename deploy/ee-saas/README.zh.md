# EE SaaS Kubernetes 部署入口

这套目录只服务我们自部署的 EE SaaS，不是 CE 或外部用户的最低部署路径。

## 边界

- CE / private single-host 继续使用 `./start.sh single-host` 和 `docker-compose.single-host.yml`。
- EE SaaS 必须使用 `EDITION=ee`、`EE_SAAS_MODE=true`、`BROWSER_RUNTIME_PROVIDER=kubernetes`。
- backend 镜像和 browser runtime 镜像都必须使用 digest，不能使用 floating tag。
- SaaS tenant 不能自定义 runtime image、namespace、nodeSelector 或 tolerations。
- 每个 tenant 使用独立 runtime namespace，namespace、RBAC、ResourceQuota、LimitRange、NetworkPolicy 基线由 Helm/deploy scripts 管理。

## 部署前置条件

目标 namespace 里必须已有以下 Secret：

- `browser-pilot-jwt`，包含 `JWT_SECRET`。
- `browser-pilot-database`，包含 `DATABASE_URL`。

如果 values 里修改了 secret 名称，同步设置：

```bash
export BROWSER_PILOT_JWT_SECRET_NAME=<jwt-secret-name>
export BROWSER_PILOT_DATABASE_SECRET_NAME=<database-secret-name>
```

如果要把部署动作写入 platform audit，额外设置：

```bash
export BROWSER_PILOT_PLATFORM_API_URL=https://browser.example.com
export BROWSER_PILOT_PLATFORM_TOKEN=<platform-auth-token>
```

## values

从示例复制一份内部环境 values：

```bash
cp deploy/ee-saas/values.example.yaml /tmp/browser-pilot-ee-saas.values.yaml
```

必须替换：

- `image.digest`：EE backend 镜像 digest。
- `runtime.approvedImages[].imageDigest`：CI/CD 构建、扫描、批准后的 runtime 镜像 digest。
- `ingress.hosts`、`ingress.tls`、`env.publicOrigins`、`env.apiBaseUrl`。
- `runtime.tenants[]`：platform 创建 tenant 后得到的 tenant id 和 runtime namespace。

## 命令

```bash
export BROWSER_PILOT_VALUES=/tmp/browser-pilot-ee-saas.values.yaml

deploy/ee-saas/deploy.sh plan
deploy/ee-saas/deploy.sh apply
deploy/ee-saas/deploy.sh verify
deploy/ee-saas/deploy.sh status
deploy/ee-saas/deploy.sh rollback <revision>
```

`plan` 会执行 Helm lint/render，并拒绝没有 approved runtime image digest、使用 placeholder digest 或不是 `sha256:<64 hex>` 的配置。`apply` 会检查必须的 Secret，执行 `helm upgrade --install --atomic --wait`，并在失败时采集 Helm 状态、工作负载状态和最近事件。`verify` 会在集群侧检查 backend SaaS env、ValidatingAdmissionPolicy、tenant namespace 的 ResourceQuota / LimitRange / NetworkPolicy / RoleBinding / locked session ServiceAccount，并用 server-side dry-run 确认不安全 session Pod 会被 admission 拒绝。设置 platform audit 环境变量后，`plan`、`apply`、`rollback` 和 `reconcile-namespaces` 会把部署结果写入 platform audit。

## tenant 变更流程

第一版不让 session 创建路径临时拼 namespace 基线。创建或调整 tenant quota、suspend tenant、delete tenant、批准 runtime image 后，由 platform API 导出 Helm runtime values，再由 deploy script 执行 namespace 基线 reconciliation：

```bash
export BROWSER_PILOT_PLATFORM_API_URL=https://browser.example.com
export BROWSER_PILOT_PLATFORM_TOKEN=<platform-token>
export BROWSER_PILOT_VALUES=/path/to/base-values.yaml
deploy/ee-saas/deploy.sh sync-values
deploy/ee-saas/deploy.sh reconcile-namespaces
```

`sync-values` 会调用 `/api/platform/deploy/runtime-values`，生成只包含 `runtime.approvedImages` 和 `runtime.tenants` 的覆盖 values。`reconcile-namespaces` 会把基础 values 与 platform 生成 values 一起交给 Helm，更新 per-tenant namespace、ResourceQuota、LimitRange、RBAC、locked session ServiceAccount 和 NetworkPolicy 基线。

这个边界必须保持：deploy/platform 负责 namespace 基线，session runtime provider 只负责受管 namespace 内的 session Pod、Service、Secret、ConfigMap、NetworkPolicy 和 egress gateway。

tenant delete 是软删除：platform API 会先强制 revoke runtime，成功后写入 `retention_until`。保留期结束后只能由 platform admin 显式登记 purge request；DB/file object purge 也必须确认没有未结束 runtime placement，不能绕过 platform audit。K8s namespace 和集群残留资源清理仍由 EE provider/controller 负责。
