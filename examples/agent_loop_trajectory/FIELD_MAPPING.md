# Agent Loop Trajectory Field Mapping

本文说明 `timeline.html` 中的原始数据，如何被导入脚本转换为 Tempo span，以及当前 Grafana POC 实际依赖哪些字段。

## 1. 结论先说

- **没有修改 `timeline.html` 原文件内容**
- **有修改导入后的 Tempo 表示形式**
- 每个 `turn` 被映射成一个 **Tempo root span**
- 为了让 Grafana 用 `now-1h` 这类时间窗查到数据，导入时默认会做一次 **时间重锚（anchor-now）**

## 2. 原始数据位置

来源文件：

- `/home/youjinlin/timeline.html`

脚本会从 HTML 里的这段 JS 变量读取数据：

- `var DATA = {...};`

其核心结构大致是：

```text
DATA
  overview[sample_id]
  samples[sample_id][session_index]
    session_id
    turns[]
    traj_rewards
    turn_count
    traj_count
```

## 3. 原始字段 -> Tempo span 字段

导入脚本：

- `examples/agent_loop_trajectory/import_timeline_to_tempo.py`

映射规则如下。

### 3.1 一条 turn 变成一条 span

`timeline.html` 中每个 `turn`：

- `turn`
- `traj`
- `type`
- `tools`
- `finish_reason`
- `content`
- `ts`

会被转换成一个 Tempo root span。

### 3.2 Span 基本字段

| HTML / 派生值 | Tempo span 字段 | 说明 |
|---|---|---|
| `state_name_for_turn(turn)` | `span.name` | 作为 span 名称 |
| `turn.ts` | `start_time_ns` | 起始时间，秒转纳秒 |
| `next_turn.ts` 或 `start+1s` | `end_time_ns` | 结束时间 |
| 常量 | `resource.service.name=agent-loop-poc` | 用于区分这批 POC 数据 |

### 3.3 Span attributes

| 来源 | Tempo attribute | 说明 |
|---|---|---|
| 常量 | `monitor.trace_segment=state_interval` | 对齐 rl-insight `trace_state()` 语义 |
| `state_name_for_turn(turn)` | `state_name` | 给 State Timeline 用 |
| `sample/session/traj` 组合 | `state_lane_id` | 轨道 ID，格式见下 |
| `sample_key` | `sample` | sample 编号，字符串 |
| `session_obj["session"]` | `session` | session 编号，字符串 |
| `session_obj["session_id"]` | `session_id` | 原始 session id |
| `turn["traj"]` | `traj` | trajectory 编号 |
| `turn["turn"]` | `turn` | turn 编号 |
| `turn["type"]` | `type` | `tool` / `llm` |
| `turn["tools"]` | `tools` | JSON 字符串，如 `["Bash"]` |
| `turn["finish_reason"]` | `finish_reason` | 如 `tool_calls` / `length` |
| `turn["content"]` | `content` | 截断到前 500 字符 |
| `traj_rewards[traj]` | `reward` | 当前 traj 的 reward |
| `overview[sample].success_count` | `sample_success_count` | sample 级摘要 |
| `overview[sample].total_trajs` | `sample_total_trajs` | sample 级摘要 |

## 4. 派生规则

### 4.1 `state_lane_id`

格式固定为：

```text
sample=<sample>/session=<session>/traj=<traj>
```

例子：

```text
sample=7/session=3/traj=2
```

它的作用是把同一个 trajectory 的 turn 聚合到同一条泳道里。

### 4.2 `state_name`

`state_name` 直接用于 State Timeline 色块标签：

- `finish_reason == "length"` → `length`（截断，红）
- `finish_reason == "stop"` → `stop`（正常结束，蓝）
- `finish_reason == "tool_calls"` → `tool_calls`（中间工具步，黄）

工具名仍在 attribute `tools` / Table 的 **Tools** 列中查看。

## 5. 时间字段到底改了什么

这是这次 POC 最重要的“变形”。

### 5.1 原始时间

`timeline.html` 自带的 `ts` 是原始 Unix 秒时间戳。  
这批数据原本大致落在：

- `2026-07-01 18:11:57 UTC`
- 到 `2026-07-01 19:00:38 UTC`

### 5.2 导入后的时间重锚

脚本后来默认启用了：

- `--anchor-now`

效果是：

- 先按原始时间构造 spans
- 再整体计算一个 `time_offset_s`
- 让**最新一条 span 结束在“当前时间前约 60 秒”**

所以：

- span 的**相对顺序**没变
- span 的**间隔关系**没变
- 但它们的**绝对时间**会整体平移到“现在附近”

这样 Grafana 用：

- `now-1h`
- `now-30m`

就能查到数据。

## 6. 哪些字段是“保留下来但未必被面板用到”的

Tempo 里现在保留的 attribute 比 dashboard 实际使用的多，尤其包括：

- `content`
- `finish_reason`
- `reward`
- `turn`
- `traj`
- `type`
- `tools`
- `session_id`
- `sample_success_count`
- `sample_total_trajs`

也就是说，POC 当前图表没有把这些字段全部可视化出来，但它们已经在 Tempo span 里。

## 7. 当前 dashboard 实际依赖哪些字段

当前 POC dashboard 想稳定运行，主要依赖：

- `resource.service.name`
- `state_lane_id`
- `traceName` / `state_name`
- `time`
- `duration`

其中：

- `resource.service.name=agent-loop-poc` 用于隔离这批 POC 数据
- `state_lane_id` 用于划分泳道
- `time` / `duration` 用于画时间段

## 8. 和原始 HTML 展示相比，丢失了什么

并不是完全等价搬运。

当前导入方式与 HTML 相比，主要差异有：

1. HTML 的“交互细节”没有原样复制  
   比如 hover 卡片、pin detail、session tab 切换逻辑，不是 Tempo 原生数据结构的一部分。

2. 颜色不是直接从 HTML 样式继承  
   现在颜色更多依赖 Grafana 的 field mappings / thresholds。

3. 绝对时间被重锚了  
   这对 Grafana 查询友好，但已经不再是原始 7 月 1 日的 wall-clock 时间。

4. `tools` 被序列化成字符串  
   当前写法是 JSON 字符串，而不是数组字段。

## 9. 如果要更“原汁原味”地贴近 HTML

后续可以继续做这些增强：

1. 保留原始时间和锚定时间两套导入模式
2. 单独导出 `tool_name` 字段，而不是只存 `tools` JSON
3. 增加 `session_index`、`turn_index_in_traj` 等辅助字段
4. 为 `finish_reason=length` 单独映射特殊颜色或 annotation
5. 增加更稳定的过滤字段，避免和 `quick_start_demo` 共用 Tempo 时串盘

## 10. 一句话总结

这次 POC 的真实数据变换方式是：

```text
timeline.html 的每个 turn
  -> 导入脚本转成一个 Tempo span
  -> 增加 state_lane_id / state_name 等可视化字段
  -> 默认整体平移到当前时间附近
  -> Grafana 再基于这些 spans 画 State Timeline / Table / Pie
```
