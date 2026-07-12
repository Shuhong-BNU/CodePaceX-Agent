"""Safe, atomic installation of directory-based Skills from GitHub."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_FILE_SIZE = 1 << 20
MAX_TOTAL_SIZE = 8 << 20
MAX_FILE_COUNT = 64
MAX_DEPTH = 4
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REF = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SkillSource:
    owner: str
    repo: str
    ref: str
    path: str
    name: str
    original_url: str


@dataclass
class InstallReport:
    name: str
    target_dir: str
    file_count: int
    total_bytes: int
    sha256: str


def parse_skill_url(raw: str) -> SkillSource:
    url = urlparse(raw.strip())
    if url.scheme != "https":
        raise ValueError("only HTTPS Skill URLs are supported")
    parts = [part for part in url.path.split("/") if part]
    host = url.hostname or ""
    if host == "github.com" and len(parts) >= 5 and parts[2] == "tree":
        owner, repo, _, ref = parts[:4]
        path = "/".join(parts[4:])
        if parts[4] != "skills":
            raise ValueError("ambiguous tree URL; use /tree/<ref>/skills/<name>")
    elif host == "raw.githubusercontent.com" and len(parts) >= 5:
        owner, repo, ref = parts[:3]
        path = "/".join(parts[3:-1])
        if parts[-1] != "SKILL.md" or parts[3] != "skills":
            raise ValueError("ambiguous raw URL; use /<ref>/skills/<name>/SKILL.md")
    else:
        raise ValueError("use a github.com tree URL or raw.githubusercontent.com SKILL.md URL")
    if not _REF.fullmatch(ref):
        raise ValueError("ref must not contain slashes; use an unambiguous ref")
    name = Path(path).name
    if not _NAME.fullmatch(name):
        raise ValueError("skill name must contain only lowercase letters, digits, _ or -")
    return SkillSource(owner, repo, ref, path, name, raw)


async def install_skill(
    source: SkillSource,
    *,
    root: Path | None = None,
    overwrite: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InstallReport:
    root = root or (Path.home() / ".codepacex" / "skills")
    root.mkdir(parents=True, exist_ok=True)
    target = root / source.name
    if target.exists() and not overwrite:
        raise FileExistsError(f"skill '{source.name}' is already installed")
    staging = Path(tempfile.mkdtemp(prefix=f".install-{source.name}-", dir=root))
    digest = hashlib.sha256()
    count = 0
    total = 0

    async def fetch_tree(client: httpx.AsyncClient, path: str, dest: Path, depth: int) -> None:
        nonlocal count, total
        if depth > MAX_DEPTH:
            raise ValueError("skill directory is too deep")
        endpoint = f"https://api.github.com/repos/{source.owner}/{source.repo}/contents/{path}?ref={source.ref}"
        response = await client.get(endpoint, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        entries = response.json()
        entries = entries if isinstance(entries, list) else [entries]
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("unsafe file name from source")
            child = dest / name
            if entry.get("type") == "dir":
                child.mkdir()
                await fetch_tree(client, entry["path"], child, depth + 1)
                continue
            if entry.get("type") != "file":
                continue
            if count >= MAX_FILE_COUNT or int(entry.get("size") or 0) > MAX_FILE_SIZE:
                raise ValueError("skill exceeds file safety limits")
            download_url = str(entry.get("download_url") or "")
            parsed_download = urlparse(download_url)
            if parsed_download.scheme != "https" or parsed_download.hostname != "raw.githubusercontent.com":
                raise ValueError("unsafe Skill download URL")
            blob = await client.get(download_url)
            blob.raise_for_status()
            data = blob.content
            if len(data) > MAX_FILE_SIZE or total + len(data) > MAX_TOTAL_SIZE:
                raise ValueError("skill exceeds size safety limits")
            child.write_bytes(data)
            digest.update(data)
            count += 1
            total += len(data)

    backup = root / f".{source.name}.backup"
    replaced = False
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=transport) as client:
            await fetch_tree(client, source.path, staging, 0)
        if not ((staging / "SKILL.md").is_file() or (staging / "skill.yaml").is_file()):
            raise ValueError("downloaded directory has no SKILL.md or skill.yaml")
        (staging / ".source.json").write_text(json.dumps({
            "url": source.original_url,
            "ref": source.ref,
            "installed_at": int(time.time()),
            "sha256": digest.hexdigest(),
        }, indent=2), encoding="utf-8")
        if target.exists():
            if backup.exists():
                raise RuntimeError(f"stale backup blocks safe replacement: {backup}")
            target.replace(backup)
            replaced = True
        staging.replace(target)
        if replaced:
            shutil.rmtree(backup, ignore_errors=True)
        return InstallReport(source.name, str(target), count, total, digest.hexdigest())
    except Exception as exc:
        if replaced and backup.exists() and not target.exists():
            try:
                backup.replace(target)
            except Exception as restore_exc:
                raise RuntimeError(f"{exc}; failed to restore previous Skill: {restore_exc}") from exc
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
