from __future__ import annotations

import re
from pathlib import Path


DOCS = (
    Path("evals/STAGE_C_CHARTER.md"),
    Path("evals/STAGE_C_RUNBOOK.md"),
    Path("evals/STAGE_C_FREEZE_REPORT.md"),
    Path("evals/README.md"),
    Path("evals/README.en.md"),
)


def test_stage_c_local_markdown_links_resolve() -> None:
    for document in DOCS:
        for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", document.read_text(encoding="utf-8")):
            if "://" not in target:
                assert (document.parent / target).exists(), f"{document}: {target}"
