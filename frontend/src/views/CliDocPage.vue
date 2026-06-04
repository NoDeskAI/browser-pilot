<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Marked } from 'marked'
import { createHighlighter, type Highlighter } from 'shiki'
import { Copy, Check } from 'lucide-vue-next'
import { useSessions } from '../composables/useSessions'
import { api } from '../lib/api'
import { Button } from '@/components/ui/button'
import { Loader2 } from 'lucide-vue-next'
import { Switch } from '@/components/ui/switch'
import { toast } from 'vue-sonner'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'

const { t, locale } = useI18n()
const props = withDefaults(defineProps<{ mode?: 'manual' | 'agent' }>(), {
  mode: 'manual',
})

const { brand } = useSessions()
const apiUrl = computed(() => location.origin)
const cmd = computed(() => brand.cliCommandName)
const isAgentDoc = computed(() => props.mode === 'agent')
const pageTitle = computed(() => {
  if (isAgentDoc.value) {
    return locale.value === 'zh' ? 'Agent 自动接入' : 'Agent CLI Access'
  }
  return locale.value === 'zh' ? 'CLI 手动接入' : 'Manual CLI Access'
})
const includeToken = ref(false)
const cliAccessToken = ref('')
const tokenLoading = ref(false)
const tokenConfirmOpen = ref(false)

function tokenConfigLine(c: string): string {
  return includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token ${cliAccessToken.value}`
    : ''
}

function setupBlockEn(c: string, url: string, installShell: string): string {
  const tokenConfig = includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token "${cliAccessToken.value}"`
    : ''
  const verify = includeToken.value && cliAccessToken.value
    ? `\n# Verify API reachability and token auth.\n${c} session list`
    : ''
  return `# Cursor/Agent: run this whole block before any ${c} command in every fresh workspace, container, or shell session.
# Refresh the local CLI so stale ${c} wrappers left in PATH do not hide new commands.
${installShell}
${c} config set api-url "${url}"${tokenConfig}
${c} config show${verify}`
}

function setupBlockZh(c: string, url: string, installShell: string): string {
  const tokenConfig = includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token "${cliAccessToken.value}"`
    : ''
  const verify = includeToken.value && cliAccessToken.value
    ? `\n# 验证 API 连通性和 Token 认证。\n${c} session list`
    : ''
  return `# Cursor/Agent：在每个新的工作区、容器或 shell session 开始时，先完整运行本段，再执行任何 ${c} 命令。
# 这会刷新本地 CLI，避免 PATH 中残留旧版 ${c} wrapper。
${installShell}
${c} config set api-url "${url}"${tokenConfig}
${c} config show${verify}`
}

function manualCommandReferenceEn(c: string): string {
  return `## Session Management

${c} session list                    # List all sessions
${c} session create --name "Task"    # Create session
${c} session create --name "Task" --network-egress <egress-id|direct> # Create with network egress
${c} session create --name "Task" --runtime cloak_chromium # Create with Cloak Chromium runtime
${c} session use <session-id>        # Activate session
${c} session start <session-id>      # Start browser container
${c} session stop <session-id>       # Stop browser container
${c} session pause <session-id>      # Hibernate browser container
${c} session unpause <session-id>    # Resume hibernated browser container
${c} session set-network <egress-id|direct> # Switch active session network egress
${c} session delete <session-id>     # Delete session; completed files are kept in Files
${c} session delete <session-id> --delete-files # Also delete all completed files

## Network Egress

${c} network-egress list --json      # List Direct plus managed Clash/OpenVPN profiles
${c} network-egress create --name "Office" --type clash --config-file ./clash.yaml
${c} network-egress create --name "VPN" --type openvpn --config-url https://example.com/client.ovpn
${c} network-egress update <egress-id> --config-file ./clash.yaml
${c} network-egress update <egress-id> --disable
${c} network-egress check <egress-id>
${c} network-egress delete <egress-id>

\`${c} session list --json\` returns \`networkEgressId\`, \`networkEgressName\`, \`networkEgressType\`, \`networkEgressStatus\`, and \`networkEgressHealthError\`.

## Browser Commands (use active session or --session)

${c} navigate <url>                  # Go to URL
${c} observe                         # Get page elements with coordinates
${c} click <x> <y>                   # Click at coordinates
${c} click-element <css-selector>    # Click element by selector
${c} type <text>                     # Type into focused input
${c} key <key>                       # Press key (Enter, Tab, Escape …)
${c} scroll <delta_y>                # Scroll page (positive = down)
${c} tabs                            # List browser tabs
${c} switch-tab --index <n>          # Switch tab
${c} page-info                       # Current URL and title
${c} screenshot                      # Store screenshot and print signed file URL
${c} screenshot -o page.png          # Store screenshot and export local copy
${c} logs                            # View CDP event logs

## Agent Devices

Browser Pilot maps Session as Device and strictly supports Agent Device Level 1 Device Governance. Level 2 control transfer, request_intervention, handoff, and human takeover are not supported.

${c} devices                         # List governed browser-session devices
${c} device <device-id>              # Show one device visibility record
${c} lease acquire <device-id> [--mode session_bound|task_bound] [--task-id ID] [--ttl 1-1800|--expires-at ISO8601]
${c} lease renew <device-id> <lease-id> [--ttl 1-1800|--expires-at ISO8601]
${c} lease release <device-id> <lease-id>
${c} lease reclaim <device-id> [--ttl 1-1800|--expires-at ISO8601]
# Lease TTL defaults to 1800 seconds and each renew can extend by at most 1800 seconds.
${c} audit [--device <device-id>] [--limit N]

## Session Files

${c} files list --json               # List session files with status
${c} files upload ./report.csv       # Upload local file into active session
${c} files get <file-id> -o file.csv # Save a completed session file locally
${c} files rename <file-id> name.csv # Rename completed session file
${c} files delete <file-id>          # Delete file; response separates object and record deletion

## Flags

--json / -j                         Add to any command for JSON output
--session <id> / -s <id>            Target a session without \`session use\`
--api-url <url>                     Override API URL per-command
BPILOT_API_URL                      Override API URL for the current shell
BPILOT_API_TOKEN                    Use this token for the current shell

\`${c} session use <session-id>\` is a terminal convenience shortcut. Commands that need a session target can omit \`<session-id>\` only after a session is active.

## Example Workflow

${c} session create --name "My Task" --json
# → {"id": "k9f2m7q4z1pa", "name": "My Task"}
${c} session use k9f2m7q4z1pa
${c} session start                   # Uses active session k9f2m7q4z1pa
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "search query"
${c} key Enter
${c} screenshot --json
${c} files list --json
# → {"ok": true, "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png?expires=...&signature=..."}, "screenshot": null}
# → {"files": [{"id": "guid-1", "status": "downloading", ...}, {"id": "file-1", "status": "completed", "url": "http://localhost:8000/api/files/file-1.csv?expires=...&signature=...", ...}]}
${c} files upload ./input.csv --name input.csv
${c} files get file-1 -o result.csv`
}

function manualCommandReferenceZh(c: string): string {
  return `## 会话管理

${c} session list                    # 列出所有会话
${c} session create --name "任务"    # 创建会话
${c} session create --name "任务" --network-egress <egress-id|direct> # 创建时指定网络出口
${c} session create --name "任务" --runtime cloak_chromium # 使用 Cloak Chromium 运行时创建
${c} session use <session-id>        # 激活会话
${c} session start <session-id>      # 启动浏览器容器
${c} session stop <session-id>       # 停止浏览器容器
${c} session pause <session-id>      # 休眠浏览器容器
${c} session unpause <session-id>    # 恢复已休眠浏览器容器
${c} session set-network <egress-id|direct> # 切换当前会话网络出口
${c} session delete <session-id>     # 删除会话，已完成文件保留到文件页
${c} session delete <session-id> --delete-files # 同时删除全部已完成文件

## 网络出口

${c} network-egress list --json      # 列出 Direct 和托管 Clash/OpenVPN 出口
${c} network-egress create --name "Office" --type clash --config-file ./clash.yaml
${c} network-egress create --name "VPN" --type openvpn --config-url https://example.com/client.ovpn
${c} network-egress update <egress-id> --config-file ./clash.yaml
${c} network-egress update <egress-id> --disable
${c} network-egress check <egress-id>
${c} network-egress delete <egress-id>

\`${c} session list --json\` 会返回 \`networkEgressId\`、\`networkEgressName\`、\`networkEgressType\`、\`networkEgressStatus\`、\`networkEgressHealthError\`。

## 浏览器命令（使用 active session 或 --session）

${c} navigate <url>                  # 导航到指定 URL
${c} observe                         # 获取页面元素及坐标
${c} click <x> <y>                   # 点击指定坐标
${c} click-element <css-selector>    # 通过 CSS 选择器点击元素
${c} type <text>                     # 向当前聚焦的输入框输入文本
${c} key <key>                       # 按键（Enter、Tab、Escape …）
${c} scroll <delta_y>                # 滚动页面（正数 = 向下）
${c} tabs                            # 列出浏览器标签页
${c} switch-tab --index <n>          # 切换标签页
${c} page-info                       # 获取当前页面 URL 和标题
${c} screenshot                      # 存入文件存储并输出签名 file.url
${c} screenshot -o page.png          # 存入文件存储并导出本地副本
${c} logs                            # 查看 CDP 事件日志

## Agent Device

Browser Pilot 将 Session 作为 Device，当前严格支持 Agent Device Level 1 Device Governance；不支持 Level 2 的 control transfer、request_intervention、handoff 和 Human 接手。

${c} devices                         # 列出纳管的浏览器 Session 设备
${c} device <device-id>              # 查看单个设备可见性记录
${c} lease acquire <device-id> [--mode session_bound|task_bound] [--task-id ID] [--ttl 1-1800|--expires-at ISO8601]
${c} lease renew <device-id> <lease-id> [--ttl 1-1800|--expires-at ISO8601]
${c} lease release <device-id> <lease-id>
${c} lease reclaim <device-id> [--ttl 1-1800|--expires-at ISO8601]
# Lease TTL 默认 1800 秒；每次 renew 最多延长 1800 秒。
${c} audit [--device <device-id>] [--limit N]

## Session 文件管理

${c} files list --json               # 列出 Session 文件及状态
${c} files upload ./report.csv       # 上传本地文件到当前会话
${c} files get <file-id> -o file.csv # 保存已完成的 Session 文件到本地
${c} files rename <file-id> name.csv # 重命名已完成的 Session 文件
${c} files delete <file-id>          # 删除文件，返回对象删除和记录删除状态

## 通用参数

--json / -j                         任意命令后加此参数以 JSON 格式输出
--session <id> / -s <id>            直接指定会话 ID（无需先 session use）
--api-url <url>                     覆盖 API 地址（仅当次生效）
BPILOT_API_URL                      当前 shell 的 API 地址覆盖
BPILOT_API_TOKEN                    当前 shell 使用的 API Token

\`${c} session use <session-id>\` 是本机终端便捷方式。只有先激活会话后，需要会话目标的命令才可以省略 \`<session-id>\`。

## 使用示例

${c} session create --name "我的任务" --json
# → {"id": "k9f2m7q4z1pa", "name": "我的任务"}
${c} session use k9f2m7q4z1pa
${c} session start                   # 使用已激活的 k9f2m7q4z1pa
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "搜索内容"
${c} key Enter
${c} screenshot --json
${c} files list --json
# → {"ok": true, "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png?expires=...&signature=..."}, "screenshot": null}
# → {"files": [{"id": "guid-1", "status": "downloading", ...}, {"id": "file-1", "status": "completed", "url": "http://localhost:8000/api/files/file-1.csv?expires=...&signature=...", ...}]}
${c} files upload ./input.csv --name input.csv
${c} files get file-1 -o result.csv`
}

function agentSessionTargetEn(c: string): string {
  return `## Session Target (stateless)

# Browser Pilot maps each Session to an Agent Device. The session id is also the device id.
# New sessions return 12-character short ids; existing UUID sessions remain valid.
# Choose exactly one path, then copy the session id into each --session argument.

# Path A: reuse an existing session.
${c} session list --json
${c} device "<session-id>" --json

# Path B: create a new session, then copy the returned "id".
${c} session create --name "Agent Task" --json

# Optional: list managed network egress profiles and create or switch with --network-egress / session set-network.
${c} network-egress list --json

# Use the copied id directly:
${c} --session "<session-id>" session start`
}

function agentSessionTargetZh(c: string): string {
  return `## 会话目标（无状态）

# Browser Pilot 将每个 Session 映射为 Agent Device。session id 同时也是 device id。
# 新会话返回 12 位短 ID；已有 UUID 会话仍然有效。
# 只选择一种方案，然后把 session id 直接填进每条命令的 --session 参数。

# 方案 A：复用现有会话。
${c} session list --json
${c} device "<session-id>" --json

# 方案 B：创建新会话，然后复制返回的 "id"。
${c} session create --name "Agent Task" --json

# 可选：列出托管网络出口，通过 --network-egress 或 session set-network 创建/切换。
${c} network-egress list --json

# 直接使用复制出来的 id：
${c} --session "<session-id>" session start`
}

function agentDeviceModelEn(c: string): string {
  return `## Agent Device Governance Model

# Level 1 boundary: Session is Device. There is no separate device id table in the CLI contract.
# Query DeviceVisibility before long-running work, recovery, or reused sessions:
${c} device "<session-id>" --json

# Key visibility fields to read:
# - state: IDLE, OCCUPIED, RELEASING, ERROR, or QUARANTINED
# - compliance_level: level1_device_governance
# - provider: browser-pilot
# - context_id: tenant:<tenant_id>
# - lease / lease_id / current_operator / lease_mode
# - policy.leaseRequired, policy.exclusiveLease, unsupported_profiles

# Lease rules:
# - Browser side-effect commands require an active exclusive DeviceLease.
# - Newly created sessions start IDLE; session creation does not claim the device for the creator.
# - Lease TTL defaults to 1800 seconds; repeat renew to extend, with each renew capped at 1800 seconds.
# - If visibility is IDLE, acquire a lease before browser actions.
# - If visibility is OCCUPIED by another operator, do not keep issuing browser actions; wait, ask the owner/admin, or use reclaim only when authorized.
# - Level 2 control transfer is not supported: do not call request_intervention, handoff, or human takeover flows.

${c} lease acquire "<session-id>" --mode session_bound --json
${c} lease acquire "<session-id>" --mode task_bound --task-id "<task-id>" --json
${c} lease renew "<session-id>" "<lease-id>" --ttl 1800 --json
${c} lease release "<session-id>" "<lease-id>" --json
${c} lease reclaim "<session-id>" --json

# Every browser command returns agentDevice. Treat it as the action contract:
# - executionStatus tells whether the command succeeded, failed, or was rejected.
# - sideEffectStatus tells whether the external browser side effect was applied, not applied, unknown, or not applicable.
# - failureCategory and nextStep tell what to do after rejection or failure.
# - auditStatus and auditEventId tell whether the action entered the audit trail.
# - evidenceStatus and evidenceRefs tell whether governed evidence was captured.
# Do not treat a plain ok/status field as enough for Agent decisions; inspect agentDevice on every action.`
}

function agentDeviceModelZh(c: string): string {
  return `## Agent Device 治理模型

# Level 1 边界：Session 即 Device。CLI 契约里没有单独的 device id 表。
# 在长任务、恢复任务或复用已有会话前，先查询 DeviceVisibility：
${c} device "<session-id>" --json

# 需要读取的关键 visibility 字段：
# - state: IDLE、OCCUPIED、RELEASING、ERROR 或 QUARANTINED
# - compliance_level: level1_device_governance
# - provider: browser-pilot
# - context_id: tenant:<tenant_id>
# - lease / lease_id / current_operator / lease_mode
# - policy.leaseRequired、policy.exclusiveLease、unsupported_profiles

# 租约规则：
# - 会产生浏览器外部副作用的命令需要 active exclusive DeviceLease。
# - 新创建的 session 默认是 IDLE；创建动作不会替创建者占用设备。
# - Lease TTL 默认 1800 秒；可以重复 renew 续期，但每次最多 1800 秒。
# - 如果 visibility 是 IDLE，先 acquire lease，再执行浏览器动作。
# - 如果 visibility 是 OCCUPIED 且 operator 不是自己，不要继续盲目发浏览器动作；等待、找 owner/admin，或在有权限时 reclaim。
# - 当前只支持 Level 1，不支持 Level 2：不要调用 request_intervention、handoff 或 Human 接手流程。

${c} lease acquire "<session-id>" --mode session_bound --json
${c} lease acquire "<session-id>" --mode task_bound --task-id "<task-id>" --json
${c} lease renew "<session-id>" "<lease-id>" --ttl 1800 --json
${c} lease release "<session-id>" "<lease-id>" --json
${c} lease reclaim "<session-id>" --json

# 每个浏览器命令都会返回 agentDevice。Agent 要把它当成动作契约来读：
# - executionStatus 判断命令 succeeded / failed / rejected。
# - sideEffectStatus 判断浏览器外部副作用 applied / not_applied / unknown / not_applicable。
# - failureCategory 和 nextStep 决定被拒绝或失败后的下一步。
# - auditStatus 和 auditEventId 判断动作是否进入审计。
# - evidenceStatus 和 evidenceRefs 判断是否捕获到受治理证据。
# 不要只看普通 ok/status 字段就继续决策；每次动作后都要检查 agentDevice。`
}

function agentCommandReferenceEn(c: string): string {
  return `## Session Management

${c} session list --json                         # List all sessions
${c} session create --name "Task" --json         # Create session and read returned id
${c} session create --name "Task" --network-egress <egress-id|direct> --json # Create with network egress
${c} session create --name "Task" --runtime cloak_chromium --json # Create with Cloak Chromium runtime
${c} --session "<session-id>" session start      # Start browser container
${c} --session "<session-id>" session stop       # Stop browser container
${c} --session "<session-id>" session pause      # Hibernate browser container
${c} --session "<session-id>" session unpause    # Resume hibernated browser container
${c} --session "<session-id>" session set-network <egress-id|direct> # Switch network egress
${c} --session "<session-id>" session delete     # Delete session; completed files are kept in Files
${c} --session "<session-id>" session delete --delete-files # Also delete all completed files

## Network Egress

${c} network-egress list --json                  # List Direct plus managed Clash/OpenVPN profiles
${c} network-egress create --name "Office" --type clash --config-file ./clash.yaml --json
${c} network-egress update <egress-id> --config-file ./clash.yaml --json
${c} network-egress check <egress-id> --json
${c} network-egress delete <egress-id> --json

\`${c} session list --json\` returns the current \`networkEgress*\` fields for each session.

## Browser Commands (always pass --session)

${c} --session "<session-id>" navigate <url>                  # Go to URL
${c} --session "<session-id>" observe --json                  # Get page elements with coordinates
${c} --session "<session-id>" click <x> <y>                   # Click at coordinates
${c} --session "<session-id>" click-element <css-selector>    # Click element by selector
${c} --session "<session-id>" type <text>                     # Type into focused input
${c} --session "<session-id>" key <key>                       # Press key (Enter, Tab, Escape …)
${c} --session "<session-id>" scroll <delta_y>                # Scroll page (positive = down)
${c} --session "<session-id>" tabs --json                     # List browser tabs
${c} --session "<session-id>" switch-tab --index <n>          # Switch tab
${c} --session "<session-id>" page-info --json                # Current URL and title
${c} --session "<session-id>" screenshot --json               # Store screenshot and print signed file URL
${c} --session "<session-id>" screenshot -o page.png          # Store screenshot and export local copy
${c} --session "<session-id>" logs                            # View CDP event logs

## Agent Devices

# Browser Pilot maps Session as Device and strictly supports Agent Device Level 1 Device Governance.
# Level 2 control transfer, request_intervention, handoff, and human takeover are not supported.

${c} devices --json                                      # List governed browser-session devices
${c} device "<session-id>" --json                        # A session id is the device id
${c} lease acquire "<session-id>" --mode task_bound --task-id "<task-id>" --json
${c} lease renew "<session-id>" "<lease-id>" --ttl 1800 --json
${c} lease release "<session-id>" "<lease-id>" --json
${c} lease reclaim "<session-id>" --json                 # Owner/admin recovery path
${c} audit --device "<session-id>" --limit 50 --json

## Session Files

${c} --session "<session-id>" files list --json               # List session files with status
${c} --session "<session-id>" files upload ./report.csv       # Upload local file into session
${c} --session "<session-id>" files get <file-id> -o file.csv # Save completed session file locally
${c} --session "<session-id>" files rename <file-id> name.csv # Rename completed session file
${c} --session "<session-id>" files delete <file-id>          # Delete file; response separates object and record deletion

## Flags

--json / -j                         Add to any command for JSON output
--session <id> / -s <id>            Required for stateless Agent calls that target a session
--api-url <url>                     Override API URL per-command

## Example Workflow

${c} session create --name "Agent Task" --json
# Read the returned "id", then acquire a lease before browser side effects:
${c} --session "k9f2m7q4z1pa" session start
${c} device "k9f2m7q4z1pa" --json
${c} lease acquire "k9f2m7q4z1pa" --mode session_bound --json
# If state is OCCUPIED by another operator, do not continue until ownership is resolved.
${c} --session "k9f2m7q4z1pa" navigate https://example.com
# Read response.agentDevice.executionStatus, sideEffectStatus, auditStatus, evidenceStatus, and nextStep.
${c} --session "k9f2m7q4z1pa" observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} --session "k9f2m7q4z1pa" click 320 200
${c} --session "k9f2m7q4z1pa" type "search query"
${c} --session "k9f2m7q4z1pa" key Enter
${c} --session "k9f2m7q4z1pa" screenshot --json
${c} --session "k9f2m7q4z1pa" files list --json
# → {"ok": true, "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png?expires=...&signature=..."}, "screenshot": null}
# → {"files": [{"id": "guid-1", "status": "downloading", ...}, {"id": "file-1", "status": "completed", "url": "http://localhost:8000/api/files/file-1.csv?expires=...&signature=...", ...}]}
${c} --session "k9f2m7q4z1pa" files upload ./input.csv --name input.csv
${c} --session "k9f2m7q4z1pa" files get file-1 -o result.csv`
}

function agentCommandReferenceZh(c: string): string {
  return `## 会话管理

${c} session list --json                         # 列出所有会话
${c} session create --name "任务" --json         # 创建会话并读取返回的 id
${c} session create --name "任务" --network-egress <egress-id|direct> --json # 创建时指定网络出口
${c} session create --name "任务" --runtime cloak_chromium --json # 使用 Cloak Chromium 运行时创建
${c} --session "<session-id>" session start      # 启动浏览器容器
${c} --session "<session-id>" session stop       # 停止浏览器容器
${c} --session "<session-id>" session pause      # 休眠浏览器容器
${c} --session "<session-id>" session unpause    # 恢复已休眠浏览器容器
${c} --session "<session-id>" session set-network <egress-id|direct> # 切换网络出口
${c} --session "<session-id>" session delete     # 删除会话，已完成文件保留到文件页
${c} --session "<session-id>" session delete --delete-files # 同时删除全部已完成文件

## 网络出口

${c} network-egress list --json                  # 列出 Direct 和托管 Clash/OpenVPN 出口
${c} network-egress create --name "Office" --type clash --config-file ./clash.yaml --json
${c} network-egress update <egress-id> --config-file ./clash.yaml --json
${c} network-egress check <egress-id> --json
${c} network-egress delete <egress-id> --json

\`${c} session list --json\` 会返回每个会话当前的 \`networkEgress*\` 字段。

## 浏览器命令（始终传 --session）

${c} --session "<session-id>" navigate <url>                  # 导航到指定 URL
${c} --session "<session-id>" observe --json                  # 获取页面元素及坐标
${c} --session "<session-id>" click <x> <y>                   # 点击指定坐标
${c} --session "<session-id>" click-element <css-selector>    # 通过 CSS 选择器点击元素
${c} --session "<session-id>" type <text>                     # 向当前聚焦的输入框输入文本
${c} --session "<session-id>" key <key>                       # 按键（Enter、Tab、Escape …）
${c} --session "<session-id>" scroll <delta_y>                # 滚动页面（正数 = 向下）
${c} --session "<session-id>" tabs --json                     # 列出浏览器标签页
${c} --session "<session-id>" switch-tab --index <n>          # 切换标签页
${c} --session "<session-id>" page-info --json                # 获取当前页面 URL 和标题
${c} --session "<session-id>" screenshot --json               # 存入文件存储并输出签名 file.url
${c} --session "<session-id>" screenshot -o page.png          # 存入文件存储并导出本地副本
${c} --session "<session-id>" logs                            # 查看 CDP 事件日志

## Agent Device

# Browser Pilot 将 Session 作为 Device，当前严格支持 Agent Device Level 1 Device Governance。
# 不支持 Level 2 的 control transfer、request_intervention、handoff 和 Human 接手。

${c} devices --json                                      # 列出纳管的浏览器 Session 设备
${c} device "<session-id>" --json                        # session id 即 device id
${c} lease acquire "<session-id>" --mode task_bound --task-id "<task-id>" --json
${c} lease renew "<session-id>" "<lease-id>" --ttl 1800 --json
${c} lease release "<session-id>" "<lease-id>" --json
${c} lease reclaim "<session-id>" --json                 # owner/admin 的回收路径
${c} audit --device "<session-id>" --limit 50 --json

## Session 文件管理

${c} --session "<session-id>" files list --json               # 列出 Session 文件及状态
${c} --session "<session-id>" files upload ./report.csv       # 上传本地文件到会话
${c} --session "<session-id>" files get <file-id> -o file.csv # 保存已完成的 Session 文件到本地
${c} --session "<session-id>" files rename <file-id> name.csv # 重命名已完成的 Session 文件
${c} --session "<session-id>" files delete <file-id>          # 删除文件，返回对象删除和记录删除状态

## 通用参数

--json / -j                         任意命令后加此参数以 JSON 格式输出
--session <id> / -s <id>            Agent 无状态调用会话命令时必须显式传入
--api-url <url>                     覆盖 API 地址（仅当次生效）

## 使用示例

${c} session create --name "Agent 任务" --json
# 读取返回的 "id"，然后在产生浏览器副作用前 acquire lease：
${c} --session "k9f2m7q4z1pa" session start
${c} device "k9f2m7q4z1pa" --json
${c} lease acquire "k9f2m7q4z1pa" --mode session_bound --json
# 如果 state 是 OCCUPIED 且 operator 不是自己，不要继续执行，先解决归属。
${c} --session "k9f2m7q4z1pa" navigate https://example.com
# 读取 response.agentDevice.executionStatus、sideEffectStatus、auditStatus、evidenceStatus 和 nextStep。
${c} --session "k9f2m7q4z1pa" observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} --session "k9f2m7q4z1pa" click 320 200
${c} --session "k9f2m7q4z1pa" type "搜索内容"
${c} --session "k9f2m7q4z1pa" key Enter
${c} --session "k9f2m7q4z1pa" screenshot --json
${c} --session "k9f2m7q4z1pa" files list --json
# → {"ok": true, "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png?expires=...&signature=..."}, "screenshot": null}
# → {"files": [{"id": "guid-1", "status": "downloading", ...}, {"id": "file-1", "status": "completed", "url": "http://localhost:8000/api/files/file-1.csv?expires=...&signature=...", ...}]}
${c} --session "k9f2m7q4z1pa" files upload ./input.csv --name input.csv
${c} --session "k9f2m7q4z1pa" files get file-1 -o result.csv`
}

function buildManualDocEn(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Manual CLI Access — ${title}

## Install

${installShell}
${c} config set api-url ${url}${tokenConfigLine(c)}

${manualCommandReferenceEn(c)}`
}

function buildManualDocZh(c: string, url: string, installShell: string, title: string) {
  return `# ${c} CLI 手动接入 — ${title}

## 安装

${installShell}
${c} config set api-url ${url}${tokenConfigLine(c)}

${manualCommandReferenceZh(c)}`
}

function buildAgentDocEn(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Agent CLI Access — ${title}

## Bootstrap (run first)

${setupBlockEn(c, url, installShell)}

${agentSessionTargetEn(c)}

${agentDeviceModelEn(c)}

## Agent Rules

# Run the bootstrap block before any ${c} command in every fresh workspace, container, or shell session.
# Do not only save this document into a skill or memory; execute the bootstrap block.
# Do not use ${c} session use, shell variables, active_session, or BPILOT_ACTIVE_SESSION for session targeting.
# Copy the actual session id into every --session "<session-id>" argument.
# New sessions usually use 12-character ids such as "k9f2m7q4z1pa"; existing UUID session ids are still valid.
# Prefer --json for state-reading commands so the result is easy to parse.
# If no session id is known, list sessions or create one before browser actions.
# Before browser side effects on reused sessions, read DeviceVisibility and ensure the lease is active for this operator.
# After every browser action, inspect response.agentDevice before deciding whether to retry, continue, or escalate.
# After an action may create a session file, poll files list --json and use each file item's status.
# Do not infer file readiness from click success; completed files include a backend url.
# Use files upload/get/rename/delete only for explicit file-management tasks.
# Do not create another API token. Use the generated api-token config line or saved config.

${agentCommandReferenceEn(c)}`
}

function buildAgentDocZh(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Agent 自动接入 — ${title}

## 启动配置（先运行）

${setupBlockZh(c, url, installShell)}

${agentSessionTargetZh(c)}

${agentDeviceModelZh(c)}

## Agent 规则

# 每个新的工作区、容器或 shell session 开始时，先完整运行启动配置段，再执行任何 ${c} 命令。
# 不要只把本文档写进 skill 或记忆；必须实际执行启动配置段。
# 不要使用 ${c} session use、shell 变量、active_session 或 BPILOT_ACTIVE_SESSION 来指定会话。
# 把真实 session id 直接填进每条命令的 --session "<session-id>" 参数。
# 新会话通常使用类似 "k9f2m7q4z1pa" 的 12 位 ID；已有 UUID session id 仍然有效。
# 读取状态时优先使用 --json，方便解析结果。
# 如果还不知道 session id，先列出现有会话或创建新会话。
# 在复用会话执行浏览器副作用前，先读取 DeviceVisibility，并确认 lease 对当前 operator 有效。
# 每次浏览器动作后，先检查 response.agentDevice，再决定重试、继续或升级处理。
# 某个动作可能创建 Session 文件后，轮询 files list --json，并以每个文件 item 的 status 为准。
# 不要根据点击成功推断文件可用；completed 文件会包含后端 url。
# 只有明确需要管理文件时，才使用 files upload/get/rename/delete。
# 不要再创建新的 API Token，使用生成的 api-token 配置行或已保存的配置。

${agentCommandReferenceZh(c)}`
}

const fullDoc = computed(() => {
  const c = cmd.value
  const url = apiUrl.value
  const installShell = brand.cliInstallCommand
  const title = brand.appTitle
  if (isAgentDoc.value) {
    return locale.value === 'zh'
      ? buildAgentDocZh(c, url, installShell, title)
      : buildAgentDocEn(c, url, installShell, title)
  }
  return locale.value === 'zh'
    ? buildManualDocZh(c, url, installShell, title)
    : buildManualDocEn(c, url, installShell, title)
})
const renderedDoc = computed(() => fullDoc.value.replace(/^# .+(?:\r?\n)+/, ''))

function toRenderable(plain: string): string {
  const lines = plain.split('\n')
  const result: string[] = []
  let buf: string[] = []

  function flush() {
    while (buf.length && (buf[0] ?? '').trim() === '') buf.shift()
    while (buf.length && (buf[buf.length - 1] ?? '').trim() === '') buf.pop()
    if (buf.length) {
      result.push('', '```bash', ...buf, '```')
    }
    buf = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    const prevLine = lines[i - 1] ?? ''
    const isHeading = /^#{2,3} /.test(line) && (i === 0 || prevLine.trim() === '')
    if (isHeading) {
      flush()
      result.push('', line)
    } else {
      buf.push(line)
    }
  }
  flush()
  return result.join('\n')
}

const html = ref('')
const loading = ref(true)
const activeId = ref('')
const headings = ref<{ id: string; text: string }[]>([])
let intersectionObserver: IntersectionObserver | null = null

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^\w\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '')
}

function extractHeadings(md: string): { id: string; text: string }[] {
  const result: { id: string; text: string }[] = []
  for (const m of md.matchAll(/^## (.+)$/gm)) {
    const text = m[1] ?? ''
    result.push({ id: slugify(text), text })
  }
  return result
}

function setupScrollSpy() {
  if (intersectionObserver) intersectionObserver.disconnect()
  const sections = headings.value
    .map(h => document.getElementById(h.id))
    .filter((el): el is HTMLElement => !!el)
  if (!sections.length) return

  intersectionObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          activeId.value = entry.target.id
          break
        }
      }
    },
    { rootMargin: '-80px 0px -70% 0px', threshold: 0 }
  )
  sections.forEach(el => intersectionObserver!.observe(el))
}

async function render() {
  loading.value = true
  const plain = renderedDoc.value
  const md = toRenderable(plain)
  headings.value = extractHeadings(plain)

  let highlighter: Highlighter | null = null
  try {
    highlighter = await createHighlighter({
      themes: ['vitesse-dark'],
      langs: ['bash'],
    })
  } catch { /* fallback to plain rendering */ }

  const instance = new Marked()
  instance.use({
    renderer: {
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens)
        if (depth === 2) {
          const raw = tokens.map((t: any) => t.raw ?? t.text ?? '').join('')
          return `<h2 id="${slugify(raw)}">${text}</h2>\n`
        }
        return `<h${depth}>${text}</h${depth}>\n`
      },
      code({ text, lang }) {
        if (highlighter) {
          try {
            return highlighter.codeToHtml(text, { lang: lang || 'text', theme: 'vitesse-dark' })
          } catch { /* fall through */ }
        }
        const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<pre><code>${escaped}</code></pre>\n`
      },
    },
  })

  try {
    html.value = await instance.parse(md) as string
  } catch {
    const { marked } = await import('marked')
    html.value = await marked(md) as string
  }
  loading.value = false

  await nextTick()
  setupScrollSpy()
}

function scrollTo(id: string) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const docCopied = ref(false)
const manualCopyOpen = ref(false)
const manualCopyText = ref('')
const manualCopyRef = ref<HTMLTextAreaElement | null>(null)
let docCopyTimer: ReturnType<typeof setTimeout> | null = null

function selectManualCopyText() {
  window.setTimeout(() => {
    manualCopyRef.value?.focus()
    manualCopyRef.value?.select()
  }, 0)
}

function handleManualCopyFocus(event: FocusEvent) {
  if (event.target instanceof HTMLTextAreaElement) event.target.select()
}

function writeClipboardTextWithSelection(text: string): boolean {
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const selection = window.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i))
    : []
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.inset = '0 auto auto 0'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)

  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
    if (selection) {
      selection.removeAllRanges()
      ranges.forEach(range => selection.addRange(range))
    }
    activeElement?.focus()
  }
}

async function writeClipboardText(text: string): Promise<boolean> {
  if (writeClipboardTextWithSelection(text)) return true

  try {
    await navigator.clipboard?.writeText(text)
    return true
  } catch {
    return false
  }
}

async function copyDoc() {
  const copied = await writeClipboardText(fullDoc.value)
  if (!copied) {
    manualCopyText.value = fullDoc.value
    manualCopyOpen.value = true
    selectManualCopyText()
    return
  }

  if (docCopyTimer) clearTimeout(docCopyTimer)
  docCopied.value = true
  toast.success(t('cliDoc.copySuccess'))
  docCopyTimer = setTimeout(() => { docCopied.value = false }, 2000)
}

async function createCliAccessToken(): Promise<string | null> {
  tokenLoading.value = true
  try {
    const res = await api('/api/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'CLI Access Doc' }),
    })
    if (!res.ok) {
      toast.error(t('cliDoc.tokenCreateError'))
      return null
    }
    const data = await res.json()
    return data.token || null
  } catch {
    toast.error(t('cliDoc.tokenCreateError'))
    return null
  } finally {
    tokenLoading.value = false
  }
}

async function handleTokenToggle(checked: boolean) {
  if (!checked) {
    includeToken.value = false
    return
  }

  if (!cliAccessToken.value) {
    tokenConfirmOpen.value = true
    return
  }

  includeToken.value = true
}

async function confirmIncludeToken() {
  if (cliAccessToken.value) {
    includeToken.value = true
    tokenConfirmOpen.value = false
    return
  }

  const token = await createCliAccessToken()
  if (!token) {
    includeToken.value = false
    tokenConfirmOpen.value = false
    return
  }

  cliAccessToken.value = token
  includeToken.value = true
  tokenConfirmOpen.value = false
}

onMounted(render)
watch([locale, fullDoc], render)

onUnmounted(() => {
  if (intersectionObserver) intersectionObserver.disconnect()
})
</script>

<template>
  <div class="w-full max-w-5xl mx-auto overflow-x-hidden px-6 py-8">
    <div v-if="loading" class="flex items-center justify-center py-20">
      <Loader2 class="size-5 animate-spin text-muted-foreground" />
    </div>

    <div v-else class="flex min-w-0 gap-10">
      <div class="flex-1 min-w-0 max-w-3xl">
        <div class="mb-6 flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <h1 class="min-w-0 flex-1 break-words text-[1.75rem] font-bold leading-[1.3]">
            {{ pageTitle }}
          </h1>
          <div class="flex max-w-full flex-wrap items-center gap-2 sm:ml-auto sm:justify-end">
            <label class="flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground select-none">
              <Switch
                :model-value="includeToken"
                :disabled="tokenLoading"
                @update:model-value="handleTokenToggle"
              />
              <span>{{ tokenLoading ? t('cliDoc.tokenCreating') : t('cliDoc.includeToken') }}</span>
            </label>
            <Button
              @click="copyDoc"
              :variant="docCopied ? 'outline' : 'default'"
              :disabled="tokenLoading"
              size="sm"
              class="shrink-0 gap-1.5 whitespace-nowrap"
              :class="docCopied ? 'text-green-500 border-green-500/30 bg-background' : ''"
            >
              <Check v-if="docCopied" class="size-3.5" />
              <Copy v-else class="size-3.5" />
              {{ t('cliDoc.copyBtn') }}
            </Button>
          </div>
        </div>
        <article class="markdown-body" v-html="html" />
      </div>

      <nav class="hidden lg:block w-48 shrink-0">
        <div class="sticky top-8 space-y-1.5">
          <p class="font-medium text-xs text-muted-foreground uppercase tracking-wider mb-3">
            {{ locale === 'zh' ? '目录' : 'On this page' }}
          </p>
          <a
            v-for="h in headings"
            :key="h.id"
            href="javascript:void(0)"
            class="block py-1 text-[13px] leading-snug transition-colors border-l-2 pl-3"
            :class="activeId === h.id
              ? 'text-foreground font-medium border-foreground'
              : 'text-muted-foreground hover:text-foreground border-transparent'"
            @click="scrollTo(h.id)"
          >
            {{ h.text }}
          </a>
        </div>
      </nav>
    </div>
  </div>

  <AlertDialog
    :open="tokenConfirmOpen"
    @update:open="(v: boolean) => { if (!v && !tokenLoading) tokenConfirmOpen = false }"
  >
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('cliDoc.tokenConfirmTitle') }}</AlertDialogTitle>
        <AlertDialogDescription>{{ t('cliDoc.tokenConfirm') }}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel :disabled="tokenLoading">
          {{ t('cliDoc.tokenConfirmCancel') }}
        </AlertDialogCancel>
        <Button :disabled="tokenLoading" @click="confirmIncludeToken">
          <Loader2 v-if="tokenLoading" class="size-4 animate-spin" />
          {{ tokenLoading ? t('cliDoc.tokenCreating') : t('cliDoc.tokenConfirmAction') }}
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>

  <Dialog :open="manualCopyOpen" @update:open="(v: boolean) => { manualCopyOpen = v; if (v) selectManualCopyText() }">
    <DialogContent class="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{{ t('cliDoc.manualCopyTitle') }}</DialogTitle>
        <DialogDescription>{{ t('cliDoc.manualCopyDescription') }}</DialogDescription>
      </DialogHeader>
      <textarea
        ref="manualCopyRef"
        :value="manualCopyText"
        readonly
        class="h-80 w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        @focus="handleManualCopyFocus"
      />
      <DialogFooter>
        <Button type="button" variant="outline" @click="manualCopyOpen = false">
          {{ t('cliDoc.manualCopyClose') }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
