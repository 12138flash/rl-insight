#!/usr/bin/env python3
"""Import agent-loop trajectory turns from timeline.html into Tempo via OTLP/HTTP.

Each turn becomes one root span compatible with rl-insight ``trace_state`` /
Grafana State Timeline panels (``state_lane_id``, ``state_name``).

Example::

    python examples/agent_loop_trajectory/import_timeline_to_tempo.py \\
        --html /home/youjinlin/timeline.html \\
        --endpoint http://127.0.0.1:4318/v1/traces
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

SERVICE_NAME_VALUE = "agent-loop-poc"

def state_name_for_turn(turn: dict[str, Any]) -> str:
    """Timeline label = turn finish_reason (length / stop / tool_calls)."""
    reason = str(turn.get("finish_reason") or "").strip()
    return reason or "unknown"


def parse_timeline_html(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"var DATA = (\{.*?\});\nvar activeSessions", html, re.S)
    if not match:
        raise ValueError(f"Could not find DATA object in {path}")
    return json.loads(match.group(1))


def lane_id(sample: str | int, session: int, traj: int) -> str:
    return f"sample={sample}/session={session}/traj={traj}"


def build_spans(data: dict[str, Any], *, time_offset_s: float = 0.0) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    samples = data.get("samples", {})
    overview = data.get("overview", {})

    for sample_key in sorted(samples, key=lambda x: int(x)):
        sample_overview = overview.get(sample_key, {})
        for session_obj in samples[sample_key]:
            session_idx = int(session_obj["session"])
            session_id = str(session_obj.get("session_id", ""))
            turns = session_obj.get("turns", [])
            rewards = session_obj.get("traj_rewards", {})

            by_traj: dict[int, list[dict[str, Any]]] = {}
            for turn in turns:
                by_traj.setdefault(int(turn["traj"]), []).append(turn)

            for traj_idx, traj_turns in sorted(by_traj.items()):
                traj_turns.sort(key=lambda t: int(t["turn"]))
                reward = rewards.get(str(traj_idx), rewards.get(traj_idx))

                for i, turn in enumerate(traj_turns):
                    start_s = float(turn["ts"]) + time_offset_s
                    if i + 1 < len(traj_turns):
                        end_s = float(traj_turns[i + 1]["ts"]) + time_offset_s
                    else:
                        end_s = start_s + 1.0
                    if end_s <= start_s:
                        end_s = start_s + 0.25

                    tools = turn.get("tools") or []
                    content = str(turn.get("content") or "")
                    spans.append(
                        {
                            "name": state_name_for_turn(turn),
                            "start_time_ns": int(start_s * 1_000_000_000),
                            "end_time_ns": int(end_s * 1_000_000_000),
                            "attributes": {
                                "monitor.trace_segment": "state_interval",
                                "state_name": state_name_for_turn(turn),
                                "state_lane_id": lane_id(sample_key, session_idx, traj_idx),
                                "sample": str(sample_key),
                                "session": str(session_idx),
                                "session_id": session_id,
                                "traj": str(traj_idx),
                                "turn": str(turn["turn"]),
                                "type": str(turn.get("type", "")),
                                "tools": json.dumps(tools, ensure_ascii=False),
                                "finish_reason": str(turn.get("finish_reason") or ""),
                                "content": content[:500],
                                "reward": "" if reward is None else str(reward),
                                "sample_success_count": str(
                                    sample_overview.get("success_count", "")
                                ),
                                "sample_total_trajs": str(
                                    sample_overview.get("total_trajs", "")
                                ),
                            },
                        }
                    )
    return spans


def anchor_offset_seconds(spans: list[dict[str, Any]], *, lag_s: float = 60.0) -> float:
    """Shift span times so the latest turn ends ``lag_s`` before now."""
    if not spans:
        return 0.0
    max_end_s = max(item["end_time_ns"] for item in spans) / 1_000_000_000
    return (time.time() - lag_s) - max_end_s


def compress_span_times(
    spans: list[dict[str, Any]], *, window_s: float, lag_s: float = 60.0
) -> list[dict[str, Any]]:
    """Fit all spans into ``[now - lag - window, now - lag]``.

    Tempo's default search prefers the ingester for recent data. The raw
    timeline spans ~50 minutes, so early samples fall outside the live
    searchable window before blocks flush to backend storage. Compressing
    keeps every trajectory queryable right after import.
    """
    if not spans or window_s <= 0:
        return spans
    starts = [item["start_time_ns"] for item in spans]
    ends = [item["end_time_ns"] for item in spans]
    min_start = min(starts)
    max_end = max(ends)
    orig_span = max_end - min_start
    if orig_span <= 0:
        return spans

    target_end_ns = int((time.time() - lag_s) * 1_000_000_000)
    target_start_ns = target_end_ns - int(window_s * 1_000_000_000)
    scale = (target_end_ns - target_start_ns) / orig_span

    out: list[dict[str, Any]] = []
    for item in spans:
        start = target_start_ns + int((item["start_time_ns"] - min_start) * scale)
        end = target_start_ns + int((item["end_time_ns"] - min_start) * scale)
        if end <= start:
            end = start + 250_000_000  # 0.25s
        cloned = dict(item)
        cloned["start_time_ns"] = start
        cloned["end_time_ns"] = end
        out.append(cloned)
    return out


def wait_for_otlp(endpoint: str, *, timeout_s: float = 60.0) -> None:
    """Block until the OTLP/HTTP endpoint accepts connections.

    Importing immediately after ``rl-insight server start`` can drop the first
    batches with connection-refused errors, which preferentially loses early
    samples/sessions (e.g. sample=0/session=0) and leaves Grafana panels empty.
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(endpoint, timeout=2)
            return
        except urllib.error.HTTPError:
            # Server responded => listener is up.
            return
        except Exception as exc:  # noqa: BLE001 - connection race during startup
            last_err = exc
            time.sleep(0.5)
    raise RuntimeError(f"OTLP endpoint not ready after {timeout_s:.0f}s: {endpoint} ({last_err})")


def export_spans(endpoint: str, spans: list[dict[str, Any]], batch_size: int) -> None:
    """Export spans synchronously so early batches are not dropped under load."""
    wait_for_otlp(endpoint)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: SERVICE_NAME_VALUE})
    )
    # SimpleSpanProcessor exports on span.end — more reliable than Batch for one-shot import.
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agent-loop-trajectory-poc")

    for i in range(0, len(spans), batch_size):
        chunk = spans[i : i + batch_size]
        for item in chunk:
            span = tracer.start_span(
                item["name"],
                start_time=item["start_time_ns"],
                attributes=item["attributes"],
            )
            span.end(end_time=item["end_time_ns"])
        # Brief pause between chunks so Tempo WAL/ingest can keep up.
        time.sleep(0.15)
        print(f"  exported {min(i + batch_size, len(spans))}/{len(spans)}")

    provider.force_flush(timeout_millis=30_000)
    provider.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html",
        type=Path,
        default=Path("/home/youjinlin/timeline.html"),
        help="Path to timeline.html (default: /home/youjinlin/timeline.html)",
    )
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:4318/v1/traces",
        help="OTLP/HTTP traces endpoint (default: Tempo on localhost:4318)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Spans per export chunk / progress print (default: 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print summary without sending spans",
    )
    parser.add_argument(
        "--anchor-now",
        action="store_true",
        default=True,
        help="Shift timestamps so data ends ~60s ago (default: enabled)",
    )
    parser.add_argument(
        "--no-anchor-now",
        action="store_false",
        dest="anchor_now",
        help="Keep original timeline.html unix timestamps",
    )
    parser.add_argument(
        "--anchor-lag-seconds",
        type=float,
        default=60.0,
        help="When anchoring, end the dataset this many seconds before now",
    )
    parser.add_argument(
        "--compress-window-seconds",
        type=float,
        default=2700.0,
        help=(
            "After anchoring, compress all spans into this many seconds "
            "(default: 2700 = 45m). Keeps early samples inside Tempo's "
            "live search window. Use 0 to disable compression."
        ),
    )
    args = parser.parse_args(argv)

    if not args.html.exists():
        print(f"ERROR: HTML file not found: {args.html}", file=sys.stderr)
        return 1

    data = parse_timeline_html(args.html)
    raw_spans = build_spans(data)
    if not raw_spans:
        print("ERROR: no spans built from input data", file=sys.stderr)
        return 1

    offset_s = 0.0
    if args.anchor_now:
        offset_s = anchor_offset_seconds(raw_spans, lag_s=args.anchor_lag_seconds)
    spans = build_spans(data, time_offset_s=offset_s)
    if args.compress_window_seconds and args.compress_window_seconds > 0:
        spans = compress_span_times(
            spans,
            window_s=args.compress_window_seconds,
            lag_s=args.anchor_lag_seconds if args.anchor_now else 60.0,
        )

    samples = len(data.get("samples", {}))
    sessions = sum(len(v) for v in data.get("samples", {}).values())
    print(f"Parsed {samples} samples, {sessions} sessions, {len(spans)} turn spans")
    if args.anchor_now:
        print(f"Anchored timestamps to now (offset {offset_s:+.1f}s)")
    if args.compress_window_seconds and args.compress_window_seconds > 0:
        print(
            f"Compressed into last {args.compress_window_seconds:.0f}s window "
            f"(ends {args.anchor_lag_seconds:.0f}s before now)"
        )

    if args.dry_run:
        preview = spans[:3]
        for item in preview:
            attrs = item["attributes"]
            print(
                f"  {attrs['state_lane_id']} turn={attrs['turn']} "
                f"name={item['name']} reward={attrs['reward']}"
            )
        print("Dry run complete — no spans exported.")
        return 0

    try:
        export_spans(args.endpoint, spans, args.batch_size)
    except Exception as exc:  # noqa: BLE001 - CLI should surface exporter errors
        print(f"ERROR: failed to export spans: {exc}", file=sys.stderr)
        return 1

    print(f"Exported {len(spans)} spans to {args.endpoint}")
    print(
        "Data is stored in Tempo (OTLP :4318, "
        "blocks under ~/.rl-insight/data/tempo/)."
    )
    print(
        "Open Grafana dashboard 'quick_start_demo' with time range "
        "'Last 1 hour' (or refresh if already on that range)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
