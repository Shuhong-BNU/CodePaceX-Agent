from __future__ import annotations

import json
from pathlib import Path

from evals.evaluation_v2.v3_activation_replay import build_matrix, write_replay


ROOT = Path(__file__).resolve().parents[1]


def test_preserved_goal4_activation_replay_is_deterministic_and_zero_provider(tmp_path: Path) -> None:
    first = build_matrix(ROOT)
    second = build_matrix(ROOT)
    assert first == second
    assert first["task_count"] == first["repository_anchor_count"] == 20
    assert first["provider_requests"] == first["usage"] == 0
    assert first["charge_cny"] == "0" and first["secret_read"] is False
    assert all(row["anchor_kind"] == "preserved_task_metadata" for row in first["rows"])
    written = write_replay(ROOT, tmp_path / "replay")
    assert written == first
    assert json.loads((tmp_path / "replay" / "activation-matrix.json").read_text(encoding="utf-8")) == first
