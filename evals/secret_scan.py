"""Fail CI when tracked source contains likely credential values.

This is deliberately a repository scanner, not an environment inspector: no
process environment values are read or printed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


_PATTERNS = (
    re.compile(r"(?i)\b(?:bailian|agentrouter|dashscope|github|aws(?:_access)?|database)[a-z0-9_]*\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/%=-]{12,}"),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@[^\s/]+"),
    re.compile(r"\bsk-(?:ant-)?[a-z0-9_-]{12,}\b", re.IGNORECASE),
)
_PLACEHOLDER = re.compile(r"(?i)(example[._-]|test-only|do-not-print|must-not|placeholder|redacted|\$\{)")


def line_has_credential(line: str) -> bool:
    return not _PLACEHOLDER.search(line) and any(pattern.search(line) for pattern in _PATTERNS)


def scan_tracked_files(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        text=True, capture_output=True, check=True,
    ).stdout.split("\0")
    findings: list[str] = []
    for relative in filter(None, listed):
        path = root / relative
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            if line_has_credential(line):
                findings.append(f"{relative}:{number}")
    return findings


def scan_artifact_roots(roots: list[Path]) -> list[str]:
    """Scan explicit local Artifact roots without reading process environment."""
    findings: list[str] = []
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                if line_has_credential(line):
                    findings.append(f"{path}:{number}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan source and explicit Run Artifacts")
    parser.add_argument("--artifact-root", action="append", type=Path, default=[])
    args = parser.parse_args()
    findings = [
        *scan_tracked_files(Path.cwd()),
        *scan_artifact_roots(args.artifact_root),
    ]
    if findings:
        print("credential-shaped tracked content: " + ", ".join(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
