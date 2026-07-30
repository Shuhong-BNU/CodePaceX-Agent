"""Deterministic, zero-provider replay of the preserved Goal 4 task identities.

This does not evaluate patches or resolve tasks.  It binds each frozen task to
its repository and base commit, extracts a compact task-local anchor, and emits
an auditable development-set matrix for the V3.1 activation gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


TASKS = Path("evals/evaluation_v2/full_replay_payloads/tasks.jsonl")
DEFAULT_OUTPUT = Path("evals/evaluation_v2/activation_v31")
_ENTITY = re.compile(r"`([A-Za-z_][A-Za-z0-9_:.-]{2,})`|\b([A-Z][A-Za-z0-9_]{2,}|[a-z_][A-Za-z0-9_]{3,})\b")
_WRAPPERS = frozenset({"Describe", "Expected", "Problem", "Python", "Running", "Version", "What"})


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _entities(problem: str) -> tuple[str, ...]:
    values = [first or second for first, second in _ENTITY.findall(problem)]
    return tuple(dict.fromkeys(value for value in values if value not in _WRAPPERS))[:6]


def build_matrix(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source = root / TASKS
    for line in source.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        entities = _entities(task["problem_statement"])
        repo, commit = task.get("repo", ""), task.get("base_commit", "")
        anchored = bool(repo and commit and entities)
        unknown = "" if anchored else "preserved task metadata lacks repository, base commit, or named entity"
        advice = "Inspect the named issue entities against the bound repository commit before editing."
        row = {
            "task": task["instance_id"], "repository_anchor": f"{repo}@{commit}",
            "anchor_kind": "preserved_task_metadata", "entities": list(entities),
            "anchored": anchored, "advice": advice, "advice_digest": _sha(advice),
            "in_request": "not_applicable_no_transport", "hypothesis": "not_applicable_no_checkout",
            "matrix": "not_applicable_no_checkout", "differential": "not_applicable_no_execution",
            "best_candidate": "not_applicable_no_execution", "unknown_reason": unknown,
        }
        row["event_digest"] = _sha(json.dumps(row, sort_keys=True, separators=(",", ":")))
        rows.append(row)
    return {
        "schema_version": 1,
        "purpose": "P2 development-set identity anchoring only; not a new evaluation",
        "provider_requests": 0, "usage": 0, "charge_cny": "0", "secret_read": False,
        "source": str(TASKS), "source_sha256": _sha(source.read_text(encoding="utf-8")),
        "task_count": len(rows), "repository_anchor_count": sum(row["anchored"] for row in rows),
        "source_definition_anchor_count": 0,
        "source_definition_anchor_note": "No task repository checkout or historical raw trace is present in this replay.",
        "rows": rows,
    }


def write_replay(root: Path, output: Path) -> dict[str, Any]:
    matrix = build_matrix(root)
    destination = root / output
    destination.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(matrix, indent=2, sort_keys=True) + "\n"
    (destination / "activation-matrix.json").write_text(encoded, encoding="utf-8")
    lines = [
        "# V3.1 zero-provider activation replay",
        "",
        "This is a deterministic development-set identity replay. It does not run a Provider, check out task repositories, execute tests, or calculate a resolved rate.",
        "",
        f"- Provider requests / usage / charge: `0 / 0 / CNY 0`",
        f"- Secret read: `false`",
        f"- Preserved repository-commit anchors: `{matrix['repository_anchor_count']}/{matrix['task_count']}`",
        "- Source-definition anchors: `0`; see the explicit no-checkout limitation in the JSON artifact.",
        "",
        "| Task | Repository anchor | Entities | Unknown reason |",
        "| --- | --- | --- | --- |",
    ]
    for row in matrix["rows"]:
        lines.append(f"| {row['task']} | {row['repository_anchor']} | {', '.join(row['entities']) or '-'} | {row['unknown_reason'] or '-'} |")
    (destination / "ACTIVATION_REPLAY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return matrix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    matrix = write_replay(args.root.resolve(), args.output)
    print(json.dumps({"task_count": matrix["task_count"], "repository_anchor_count": matrix["repository_anchor_count"], "provider_requests": 0}))


if __name__ == "__main__":
    main()
