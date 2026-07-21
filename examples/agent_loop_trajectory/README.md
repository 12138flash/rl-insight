# Agent Loop Trajectory — Grafana POC

用 **Grafana 内置面板**（State Timeline + Table + Rows）在 **`quick_start_demo`** 中展示 `timeline.html` 的 agent loop 轨迹。

## 产物

| 文件 | 作用 |
|------|------|
| `import_timeline_to_tempo.py` | 解析 HTML → OTLP spans → Tempo |
| `FIELD_MAPPING.md` | 字段映射说明 |
| `CHANGES.md` | 本分支改动说明 |
| `../../rl_insight/config/services/grafana/dashboards/quick_start_demo.json` | **交付盘**：含 `state_timeline` + `agent loop轨迹展示` |

配套修改（非本目录）：`tempo.yaml` 搜索窗口；`verl_sync_mode_with_vllm_engine.json` TraceQL 排除 POC。

## 快速运行

### 1. 启动 rl-insight 监控栈

```bash
cd /home/youjinlin/rl-insight
rl-insight server start --detach
```

确认 Tempo OTLP 端口（默认 `4318`）与 Grafana（默认 `3000`）已起来。

### 2. 导入 timeline 数据

```bash
python examples/agent_loop_trajectory/import_timeline_to_tempo.py \
  --html /home/youjinlin/timeline.html
```

可先 dry-run：

```bash
python examples/agent_loop_trajectory/import_timeline_to_tempo.py --dry-run
```

### 3. 打开 Grafana

1. 浏览器访问 `http://127.0.0.1:3000`
2. 进入 **RL-Insight** 文件夹
3. 打开 **`quick_start_demo`**
4. 时间范围选 **Last 1 hour** 及以上（导入默认压缩进约 45 分钟窗口）
5. 展开 **`agent loop轨迹展示`** 行，再依次展开 Sample → Session → Trajectory
6. 同级的 **`state_timeline`** 行已排除 `agent-loop-poc`，不会再混入 POC 轨迹

隔离说明：POC span 的 `resource.service.name=agent-loop-poc`；`state_timeline` 查询为  
`{span.state_lane_id != "" && resource.service.name != "agent-loop-poc"}`。

## 数据写到哪里？

| 步骤 | 位置 |
|------|------|
| 导入命令 | OTLP HTTP → `http://127.0.0.1:4318/v1/traces` |
| 持久化 | `~/.rl-insight/data/tempo/traces/` + `.../wal/` |
| 查询 | Grafana Tempo 数据源 → `http://127.0.0.1:3200` |

默认 `--anchor-now`：把时间平移到当前附近；默认再 **压缩进约 45 分钟窗口**（`--compress-window-seconds 2700`），避免早期 sample 落在 Tempo ingester 默认可搜索窗口之外。

### 重复导入 / 清库

每次导入都会 **追加** span，不会覆盖。重复跑导入会导致 Turn 表出现多套副本。

清库后只导入一次：

```bash
rl-insight server stop
rm -rf ~/.rl-insight/data/tempo/traces ~/.rl-insight/data/tempo/wal
mkdir -p ~/.rl-insight/data/tempo/traces ~/.rl-insight/data/tempo/wal
rl-insight server start --detach
python examples/agent_loop_trajectory/import_timeline_to_tempo.py \
  --html /home/youjinlin/timeline.html
```

注意：真实路径是 `~/.rl-insight/data/tempo/`，不是 `services/data/tempo/`。

每个 **turn** → 一个 Tempo root span：

- `resource.service.name` = `agent-loop-poc`
- `state_lane_id` = `sample={s}/session={n}/traj={t}`（State Timeline 泳道）
- `state_name` = **`length` / `stop` / `tool_calls`**（直接对应 finish_reason），用于 State Timeline 着色
- 工具名在 Table 的 **Tools** 列查看
- 属性：`turn`, `traj`, `tools`, `finish_reason`, `content`, `reward`, …

与 rl-insight 现有 `trace_state()` 的 `monitor.trace_segment=state_interval` 约定对齐。

## 面板说明（`quick_start_demo`）

| 区域 | 内容 |
|------|------|
| **state_timeline** 行 | 原训练 state timeline + metrics（已排除 POC） |
| **agent loop轨迹展示** 行 | Sample → Session → 轨迹总览 + Trajectory → Turn 明细 |

Grafana **Rows** 嵌套，与 `timeline.html` 层级一致：

| 层级 | 内容 |
|------|------|
| Sample | success / turns / sessions 摘要 |
| Session | 轨迹总览（多泳道）+ 各 Trajectory 折叠行 |
| Trajectory | State Timeline + Turn 明细表 |
| Turn | turn、tool、finish_reason、content、reward |

Session 下 **轨迹总览** 直接显示（`hideHeader`，无独立折叠标题）。State Timeline 按 `finish_reason` 着色，不把 content/reward 拆成独立泳道。

## 故障排查

- **无数据**：确认时间范围覆盖导入锚定后的窗口（Last 1h）；Tempo 是否 ready
- **Dashboard 未出现 / 仍见旧盘**：`rl-insight server stop && rl-insight server start --detach`（会 re-stage dashboards）
- **导入失败**：检查 `4318` 是否监听、`opentelemetry-exporter-otlp-proto-http` 是否已安装（`pip install -e .`）
- **图例仍有旧标签（如 step）**：清 Tempo 库后只导入一次
