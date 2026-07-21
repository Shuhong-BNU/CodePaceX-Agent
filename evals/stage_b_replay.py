"""Offline-only replay for Stage B validation traces.

This module never imports or initializes a Provider client.  It replays
sanitized JSONL records into the same deterministic controller used at runtime
and writes a new local summary marked as non-experimental evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codepacex.validation import ValidationController, ValidationProfile, replay_events


def load_trace(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def replay_trace(trace: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    controller = ValidationController(
        ValidationProfile.stage_b(), session_id="offline-replay", state_dir=output_dir,
    )
    summary = replay_events(load_trace(trace), controller)
    payload = {
        **summary,
        "replay_only": True,
        "provider_requests": 0,
        "formal_experiment": False,
        "source_trace": str(trace),
    }
    (output_dir / "validation-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a Stage B trace without a Provider")
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = replay_trace(args.trace, args.output_dir)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
