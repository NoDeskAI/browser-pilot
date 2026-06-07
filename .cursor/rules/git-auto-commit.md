## 自动提交规则

完成一个独立功能、修复或重构后，主动执行 `git add` + `git commit`，无需等待用户指示。

### 触发时机

- 完成一个 plan 中的所有 todo 后
- 完成用户请求的单个功能或 bug 修复后
- 完成一组逻辑相关的文件改动后

### 不提交的情况

- 改动尚未完成（中间状态）
- 存在已知的 lint 错误未修复
- 用户明确说「先不要提交」

### Commit Message 格式

```
<type>(<scope>): <subject>
```

- `type`: feat / fix / docs / style / refactor / perf / test / chore / revert / build
- `scope`: 选填，表示作用范围（模块名、目录名）
- `subject`: 必填，中文简述改动内容

示例：

```
feat(ai-chat): 增加模型列表动态拉取功能
fix(selenium): 修复 noVNC 带宽统计导致连接断开
refactor(frontend): 设置面板改为 Dialog 弹窗
```

### 粒度原则

- 一个逻辑完整的改动对应一次 commit
- 如果一次任务涉及多个不相关的改动，拆分为多次 commit
- 不要把无关文件混入同一次 commit
