# 上游数据规范 vs 本分支实现对照

> 对照对象：上游数据侧「三类存储」表 + Tempo span 属性表。  
> 本分支依据：[`write/mapper.py`](write/mapper.py)、[`SPAN_PROTOCOL.md`](SPAN_PROTOCOL.md)、SampleRecord / FileSampleRecord。  
> 更新日期：2026-07-24（已去掉 Tempo/Turn details 的 `reward`；Rebuild 增加时间窗过滤）。

---

## 1. 三类存储位置（总表）

| 数据类别（上游） | 上游存储 | 本分支 | 是否一致 |
|------------------|----------|--------|----------|
| Step 明细、展示摘要、trajectory 分组身份 | **Tempo span** | 经 `TempoSpanMapper` 写入 Tempo | **一致**（字段子集见 §2） |
| Reward、耗时、token 数、成功率等数值摘要 | **Prometheus metric** | **未实现** Agent Loop → Prom；轨迹 `reward_score` 仅留 SampleRecord；成功率 / turns 由 Rebuild 数 span 写行标题 | **不一致**（无 Prom；也不再把 reward 打进 Tempo） |
| 完整消息、全文 thought/response、tool args/observation、原始 token/logprob/mask | **Trajectory / File Store** | 留在 `SampleRecord` / `FileSampleRecord`（磁盘或内存），**不进 Tempo** | **一致**（方向一致） |

### 不一致说明（存储层）

1. **Prometheus 整类缺失**  
   上游把 reward / 耗时 / token 数 / 成功率定为 Prom。本分支没有对应 metric 导出；Grafana Agent Loop 看板只查 Tempo。

2. **`reward` 不进 Tempo、也不在 Turn details**  
   - 上游：数值摘要 → Prometheus  
   - 本分支：无 per-turn reward；轨迹级 `reward_score` 仅留 SampleRecord / 文件，**不写 span attribute、不上详情表**  
   - 与「File Store 保留完整源」一致；与上游「Prom 承载 reward」仍不一致（本分支尚未做 Prom）

3. **耗时 / token 数 / 成功率**  
   - 上游：Prom  
   - 本分支：无 Prom；耗时为 **合成时钟**（每 step 默认 1s + compress）；`prompt_len` 等 **不进 Tempo**；成功率在 Rebuild 时用 `finish_reason == "stop"` 统计，写在行标题里。

---

## 2. Tempo span 属性：类型与有无

上游表中属性类型全部为 **string**。本分支 OTLP 写出时 attributes 也均为 **string**（索引字段 `str(int)`，`tools` 为 JSON 数组字符串）。

| 属性（上游） | 上游类型 | 本分支 | 类型 | 一致？ | 备注 |
|--------------|----------|--------|------|--------|------|
| `run_id` | string | 有 | string | **一致** | 本分支：`export-{unix}-{uuid8}`（mapper 侧生成） |
| `state_lane_id` | string | 有 | string | **一致** | 本分支：`run={}/sample={}/session={}/traj={}` |
| `sample` | string | 有 | string | **一致** | `str(sample_index)` |
| `session` | string | 有 | string | **一致** | |
| `traj` | string | 有 | string | **一致** | |
| `turn` | string | 有 | string | **一致** | `str(step.step_idx)` |
| `uid` | string | 有 | string | **一致** | |
| `monitor.trace_segment` | string | 有 | string | **一致** | 固定 `"state_interval"` |
| `monitor.trace_source` | string | **无** | — | **缺** | 上游固定 `"trajectory"`；本分支未写 |
| `state_name` | string | 有 | string | **一致** | = finish_reason / span name |
| `finish_reason` | string | 有 | string | **一致** | |
| `type` | string | 有 | string | **一致** | `"tool"` / `"llm"` |
| `tools` | string | 有 | string | **一致** | 工具 **name** 的 JSON 数组字符串 |
| `content` | string | 有 | string | **基本一致** | 上游：thought/response **摘要**；本分支：`thought or response` **截断 500 字符** |
| `trajectory.timing_source` | string | **无** | — | **缺** | 上游：`receive_time` / `execution_time`；本分支用合成时间，无此 attribute |

### 本分支有、上游 Tempo 表未列的 attribute

无（已去掉曾写入 Tempo 的 `reward`）。span `name` 与 `finish_reason` 同值，上游表未单独列 name。

---

## 3. File / Trajectory Store（完整原文）

| 上游归入 File Store 的内容 | 本分支 | 一致？ |
|----------------------------|--------|--------|
| 完整 messages | `TrajectoryRecord.messages`，不进 Tempo | **一致** |
| 完整 thought / response | Step 字段保留；Tempo 仅 `content[:500]` | **一致** |
| tool arguments / observation | `ToolResult.action` / `observation` 等，不进 Tempo | **一致** |
| 原始 token / logprob / mask | `prompt_ids` / `response_ids` / `response_mask` / `response_logprobs`，不进 Tempo | **一致** |

实现载体：`samples/sample.py`（内存）或 `samples/file_sample.py`（文件）。可视化路径 **不读** 这些全文，只读 Tempo。

---

## 4. 差异汇总

### 与上游相同

- Step 级展示 / 分组身份进 **Tempo**，属性多为 string。  
- 完整正文与 token 级数据留在 **SampleRecord / 文件**，不进 Tempo。  
- 已对齐的 Tempo 字段：`run_id`、`state_lane_id`、`sample`/`session`/`traj`/`turn`、`uid`、`monitor.trace_segment`、`state_name`、`finish_reason`、`type`、`tools`、`content`（摘要形态）。

### 与上游不同

| # | 点 | 上游 | 本分支 |
|---|----|------|--------|
| 1 | 数值摘要存储 | Prometheus | **无 Prom 导出**；看板不查 Prom |
| 2 | `reward` | Prom metric | 不进 Tempo；仅 SampleRecord 轨迹字段；Turn details 不展示 |
| 3 | 耗时 / token 数 / 成功率 | Prom | 无 Prom；耗时合成；成功率 Rebuild 时计算 |
| 4 | `monitor.trace_source` | Tempo，固定 `trajectory` | **未写** |
| 5 | `trajectory.timing_source` | Tempo，`receive_time` / `execution_time` | **未写**；时间为合成 + compress |
| 6 | `content` 策略 | 摘要（规范未写死长度） | 明确 **`[:500]`** |

### Rebuild 行为（本分支额外约定，上游表未写）

- 只把 **与 Grafana 时间窗重叠** 的 span/run 写入 runtime 树；窗外 run 不展示。  
- 详情表 **不展示** reward（与上表「reward 不进 Tempo」一致）。

---

