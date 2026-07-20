"""Fail CI when tracked source contains likely credential values.

This is deliberately a repository scanner, not an environment inspector: no
process environment values are read or printed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


_PATTERNS = (
    # A double-colon namespace such as AWS::AccountId is public source text,
    # not an assignment carrying a credential value.
    re.compile(r"(?i)\b(?:bailian|agentrouter|dashscope|github|aws(?:_access)?|database)[a-z0-9_]*\s*(?::(?!:)|=)\s*['\"]?[^\s'\"]{8,}"),
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


def scan_artifact_roots(
    roots: list[Path], *, untrusted_json_fields: frozenset[str] = frozenset(),
) -> list[str]:
    """Scan explicit local Artifact roots without reading process environment.

    Selected JSONL fields may be explicitly excluded when they contain a public,
    untrusted benchmark corpus rather than a produced Artifact value.
    """
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
                scanned_line = line
                if untrusted_json_fields and path.suffix == ".jsonl":
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        scanned_line = json.dumps({
                            key: value for key, value in payload.items()
                            if key not in untrusted_json_fields
                        }, ensure_ascii=False)
                if line_has_credential(scanned_line):
                    findings.append(f"{path}:{number}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan source and explicit Run Artifacts")
    parser.add_argument("--artifact-root", action="append", type=Path, default=[])
    parser.add_argument(
        "--untrusted-json-field", action="append", default=[],
        help="Exclude this explicit JSONL field from Artifact scanning.",
    )
    args = parser.parse_args()
    findings = [
        *scan_tracked_files(Path.cwd()),
        *scan_artifact_roots(
            args.artifact_root, untrusted_json_fields=frozenset(args.untrusted_json_field),
        ),
    ]
    if findings:
        print("credential-shaped tracked content: " + ", ".join(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
