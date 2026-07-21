# Agent Loop 轨迹可视化 POC — 分支改动说明

**分支：** `youjinlin/grafana-dashboard`（相对 `origin/main` @ `177a836`）  
**目标：** 用 Grafana **内置** State Timeline / Table / Rows，把 `timeline.html` 的 agent loop 轨迹做成 Sample → Session → Trajectory → Turn 递进展示，并挂到 **`quick_start_demo`** 下，且不污染原有 `state_timeline`。

**说明：** `archives/*.tar.gz` 为本地下载的二进制包，与本 POC 业务改动无关，下文不计入。独立迭代盘（poc / browser / summary / detail / hierarchy）已删除，功能已合并进 `quick_start_demo`。

---

## 一、改动总览

| 类型 | 路径 | 说明 |
|------|------|------|
| **新增** | `examples/agent_loop_trajectory/` | 导入脚本 + 字段映射 + 使用说明 |
| **修改** | `rl_insight/.../dashboards/quick_start_demo.json` | 主交付：嵌套 agent loop 行 + 串盘隔离 |
| **修改** | `rl_insight/.../dashboards/verl_sync_mode_with_vllm_engine.json` | TraceQL 排除 POC 数据 |
| **修改** | `rl_insight/config/services/tempo/tempo.yaml` | 加长搜索窗口、加快 block 落盘 |

---

## 二、新增文件

### 1. `examples/agent_loop_trajectory/import_timeline_to_tempo.py`

**作用：** 解析 `timeline.html` → 每个 turn 写成一条 Tempo root span（OTLP HTTP `:4318`）。

**关键逻辑：**

| 函数 / 行为 | 具体改动 | 原因 |
|-------------|----------|------|
| `SERVICE_NAME_VALUE = "agent-loop-poc"` | 固定 resource service name | 与训练 demo 的 Tempo span 隔离，避免串盘 |
| `parse_timeline_html()` | 正则提取 `var DATA = {...}` | 从 HTML 读出 samples / turns / overview |
| `lane_id()` | `sample={s}/session={n}/traj={t}` | 对齐 rl-insight `state_lane_id` 泳道约定 |
| `state_name_for_turn()` | **直接用 `finish_reason`**（`length` / `stop` / `tool_calls`）作为 span name / `state_name` | Timeline 只展示截断/结束原因，不再用 Bash/Read/LLM_cut |
| `build_spans()` | turn → span；时长 = 本 turn `ts` → 下一 turn `ts`（末 turn +1s）；属性含 tools/content/reward 等 | 供 State Timeline + Turn 明细表查询 |
| `anchor_offset_seconds()` | 默认 `--anchor-now` 平移到「现在附近」 | 原 HTML 时间戳偏旧，Grafana `now-1h` 查不到 |
| `compress_span_times()` | 默认压缩进约 **720s（12 分钟）** 窗口 | 原始跨度约 50 分钟，早期 sample 会超出 Tempo ingester 默认可搜索范围 |
| `export_spans()` | `SimpleSpanProcessor` + 分批 sleep | 避免 Batch 导出时早期 span 丢失 |

**CLI 要点：** `--html`、`--endpoint`、`--anchor-now` / `--no-anchor-now`、`--compress-window-seconds`、`--dry-run`。

---

### 2. `examples/agent_loop_trajectory/FIELD_MAPPING.md`

**作用：** 文档化 HTML 字段 → Tempo attribute → Grafana 字段的映射。

**原因：** 记录时间锚定、压缩、`state_lane_id` / `state_name` 派生规则，便于复现与排查。

---

### 3. `examples/agent_loop_trajectory/README.md`

**作用：** 启动栈、导入、打开 `quick_start_demo`、清库、隔离说明、故障排查。

**原因：** 操作手册；强调真实 Tempo 路径为 `~/.rl-insight/data/tempo/`，重复导入会叠数据。

---

## 三、修改的已有文件

### 1. `rl_insight/config/services/grafana/dashboards/quick_start_demo.json`（主交付）

**相对 main 的结构变化：**

| 项 | 改前 | 改后 | 原因 |
|----|------|------|------|
| 布局 | 单层 `GridLayout`（4 面板） | 顶层 `RowsLayout` 两行 | 与 agent loop 同级并排，可折叠 |
| 元素数 | 4 | **222** | 嵌入 agent loop 面板 + session 轨迹总览 |
| `panel-1` TraceQL | `{span.state_lane_id != ""}` | 增加 `&& resource.service.name != "agent-loop-poc"` | **解决 POC 污染原 state_timeline** |
| 默认时间窗 | `now-5m` | `now-30m` | 覆盖压缩后的 POC 数据窗口 |
| tags | （空） | `agent-loop`, `quick-start` | 便于检索 |

**顶层两行：**

1. **`state_timeline`** — 原有 Timeline + metric_count / metric_value / metric_distribution（查询已排除 POC）
2. **`agent loop轨迹展示`** — 嵌套 Sample → Session → …

**Session 层内容：**

- **轨迹总览**（`agent-session-panel-{sample}-{session}`，共 32 个）：`hideHeader` 直接挂在 Session 下  
  - 查询：`{span.state_lane_id =~ "sample={s}/session={n}/.*" && resource.service.name="agent-loop-poc"}`  
  - `partitionByValues(state_lane_id)` → 每条 traj 一行
- 其下各 **Trajectory #N** 折叠行：单轨迹 Turn sequence + Turn details 表

**Timeline 面板约定：**

- TraceQL 带 `resource.service.name="agent-loop-poc"`
- 色块映射：`length` 红、`stop` 蓝、`tool_calls` 黄
- 不 `select` content/reward 等字段，避免多属性泳道
- Turn 明细 Table：`turn` / `tools` / `finish_reason` / `content` / `reward`

---

### 2. `rl_insight/config/services/grafana/dashboards/verl_sync_mode_with_vllm_engine.json`

**改动（1 处查询）：**

```diff
- "{span.state_lane_id!=\"\"}"
+ "{span.state_lane_id != \"\" && resource.service.name != \"agent-loop-poc\"}"
```

**原因：** 该盘也有宽条件 `state_lane_id != ""`，会把 POC 轨迹画进训练 state timeline；与 `quick_start_demo` 同样隔离。

---

### 3. `rl_insight/config/services/tempo/tempo.yaml`

**新增 / 修改：**

```yaml
ingester:
  max_block_duration: 1m
  complete_block_timeout: 2h
  flush_all_on_shutdown: true

query_frontend:
  search:
    query_ingesters_until: 2h
    query_backend_after: 30m
    max_duration: 168h

storage.trace:
  blocklist_poll: 10s   # 新增
```

**原因：**

- 本地 POC 导入后，早期 sample 常落在默认 ingester 搜索窗口外 → 查询为空或半截
- 加快 block 切割 / 延长 `query_ingesters_until` / 缩短 `blocklist_poll`，提高导入后可查性  
- （运行时真实数据目录由 rl-insight 渲染到 `~/.rl-insight/data/tempo/`，模板里的 `/tmp/tempo` 仍会被覆盖）

---

## 四、数据与运行约定（非仓库文件，但属于本项目）

| 项 | 内容 | 原因 |
|----|------|------|
| 源数据 | `/home/youjinlin/timeline.html`（未改文件内容） | POC 输入 |
| Tempo 持久化 | `~/.rl-insight/data/tempo/{traces,wal}` | 清库/重导路径 |
| service 隔离 | `agent-loop-poc` | 与 demo / verl 训练 span 分离 |
| Timeline 标签 | `finish_reason` ∈ {`length`,`stop`,`tool_calls`} | 产品要求只展示截断/结束语义；工具看 Table Tools 列 |

---

## 五、验证清单（改动后）

```bash
# 清库后只导入一次
rl-insight server stop
rm -rf ~/.rl-insight/data/tempo/traces ~/.rl-insight/data/tempo/wal
mkdir -p ~/.rl-insight/data/tempo/traces ~/.rl-insight/data/tempo/wal
rl-insight server start --detach
python examples/agent_loop_trajectory/import_timeline_to_tempo.py --html /home/youjinlin/timeline.html
```

Grafana：打开 **`quick_start_demo`**，时间 **Last 30m**。

- 上行 `state_timeline`：仅有 POC 时应为空（查询已排除 `agent-loop-poc`）  
- 下行 `agent loop轨迹展示`：Sample→Session→轨迹总览→Trajectory→Turn 明细  
- Timeline 图例仅有 `tool_calls` / `length` / `stop`
- Dashboard 列表仅保留 `quick_start_demo` 与 `verl_sync_mode_with_vllm_engine`（本 POC 相关）
