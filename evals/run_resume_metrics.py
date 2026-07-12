"""Create a versioned report from measured resume-metric samples.

The input is deliberately a JSON artifact produced by a real runner. This
command never invents samples or substitutes synthetic values for live usage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark import RunManifest, RunRecorder, current_git_commit, reduction_percent, summarize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Measured metric samples as JSON")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--runs-dir", default="evals/.runs/resume-metrics")
    args = parser.parse_args()
    samples = json.loads(Path(args.input).read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    recorder = RunRecorder(
        Path(args.runs_dir),
        RunManifest(kind="resume-metrics", model=args.model, provider=args.provider, git_commit=current_git_commit(root)),
    )
    result: dict[str, object] = {}
    for name, values in samples.items():
        if not isinstance(values, list) or not all(isinstance(item, (int, float)) for item in values):
            raise ValueError(f"{name} must be a numeric sample list")
        result[name] = summarize([float(item) for item in values])
    eager = samples.get("mcp_eager_input_tokens")
    deferred = samples.get("mcp_deferred_input_tokens")
    if isinstance(eager, list) and isinstance(deferred, list) and eager and deferred:
        result["mcp_reduction_percent"] = reduction_percent(sum(eager) / len(eager), sum(deferred) / len(deferred))
    recorder.write_json("usage.json", samples)
    recorder.finalize(result)
    print(recorder.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
