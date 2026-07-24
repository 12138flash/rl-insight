# Agent Loop → Tempo Span Protocol（现状）

回应 [#129](https://github.com/verl-project/rl-insight/pull/129) 评审：写清哪些进 Tempo、哪些不进、看板读哪些。不改运行行为以外的约定说明。

约定：一步 step → 一条 root span；`service.name = "agent-loop-poc"`。  
**无 per-turn reward**：轨迹级 `reward_score` 留在 SampleRecord，不进 Tempo、不上 Turn details。

---

## 进 Tempo

| Attribute | 说明 |
|-----------|------|
| `run_id` | `export-{unix}-{uuid8}` |
| `state_lane_id` | `run={}/sample={}/session={}/traj={}` |
| `sample` / `session` / `traj` / `turn` / `uid` | 索引与 uid，string |
| `type` | 有 tool_results → `"tool"`，否则 `"llm"` |
| `tools` | `json.dumps` 工具 **name** 列表 |
| `content` | `thought or response`，**最多 500 字符** |
| `monitor.trace_segment` | 固定 `"state_interval"` |
| `state_name` / `finish_reason` | 与 span `name` 一致 |
| span `name` | = finish_reason；空则 `"unknown"` |
| 时间 | 合成（默认每 step 1s）；flush 前可 `compress_span_times` 压到约 30min 窗 |

---

## 不进 Tempo

留在 SampleRecord / 文件存储，**故意不写进 span**：

- `thought` / `response` 全文（只经 `content[:500]`）
- `done`；`exit_reason` 仅间接进 `finish_reason`
- tool：`action` / `observation` / `status` / `execution_time` / `tool_call_id`（name 只进 `tools`）
- token：`prompt_ids` / `response_ids` / `response_mask` / `response_logprobs`
- **`reward_score` / `reward_extra_info`（轨迹级，非 turn 字段；不进 Tempo）**
- `messages` / `routed_experts` / `multi_modal_data` / traj `execution_time`
- `prompt_len` / `response_len` / `seq_len` / `num_turns`（turns 由 Rebuild 数 spans）
- `tag` / `extra_fields` 全文（末 step 可能用到 `tag.finish_reason`）
- Session / Sample tag 字段

---

## 看板读哪些

- 过滤：`state_lane_id`、`run_id`、`resource.service.name`
- details 列：`turn`, `state_name`, `type`, `tools`, `finish_reason`, `content`

## Rebuild 时间窗（展示干净）

`rebuild_from_tempo` 在建树前会：

1. 按 Grafana `from`/`to` 拉 span，并可 `complete_stamped_runs` 补全同 `run_id`
2. **`filter_to_window`**：丢掉时间戳与窗口无交集的 span；若某 run 无剩余 span 则整 run 不进树
3. Run 列表按结束时间 **新→旧** 排序

因此不会再为「search 误带回、但面板查不到」的旧 run 画空壳行。

---

## 对评审的回应

评审要求：数据协议要钉死，别 silently drop token / tool 等字段。

- SampleRecord 仍是完整源；mapper **显式选择**进 Tempo 的子集（上表），其余字段**明确不进**，不是漏写。
- 当前看板只需要 details 那几列；token 数组、tool action/observation、轨迹 reward 等体积大或非 turn 合同，故不进 Tempo。
- 以后若要进（全文、分字段、tool 正文、token、Prom 侧 reward 等），先改本协议再改 mapper / 面板。

实现：`agent_loop.write.mapper.samples_to_span_dicts`：

```python
attrs: dict[str, Any] = {
    "run_id": run_id,
    "state_lane_id": lane_id(run_id, si, sess_i, ti),
    "sample": str(si),
    "uid": uid,
    "session": str(sess_i),
    "traj": str(ti),
    "turn": str(step.step_idx),
    "type": _step_type(step),
    "tools": json.dumps(_tool_names(step), ensure_ascii=False),
    "content": (step.thought or step.response or "")[:500],
    "monitor.trace_segment": "state_interval",
    "state_name": name,
    "finish_reason": name,
}
```
