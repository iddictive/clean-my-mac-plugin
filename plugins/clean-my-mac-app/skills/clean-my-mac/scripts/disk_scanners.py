#!/usr/bin/env python3
"""Read-only disk evidence adapters for Clean My Mac."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


HOME = Path.home()
PRIMARY_PROTECTED_PATHS = (
    HOME / ".codex",
    HOME / ".cache/codex-runtimes",
    HOME / "Library/Application Support/Codex",
    HOME / "Library/Caches/Codex",
)
PROTECTED_PATHS = PRIMARY_PROTECTED_PATHS + tuple(
    Path("/System/Volumes/Data") / str(path).lstrip("/") for path in PRIMARY_PROTECTED_PATHS
)
APP_CANDIDATES = {
    "grandperspective": (
        Path("/Applications/GrandPerspective.app"),
        HOME / "Applications/GrandPerspective.app",
    ),
    "spacie": (
        Path("/Applications/Spacie.app"),
        HOME / "Applications/Spacie.app",
    ),
}
MEDIA_EXTENSIONS = {".avi", ".flac", ".gif", ".heic", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".png", ".wav", ".webp"}
ARCHIVE_EXTENSIONS = {".7z", ".bz2", ".dmg", ".gz", ".iso", ".pkg", ".rar", ".tar", ".tgz", ".xz", ".zip"}
DOCUMENT_EXTENSIONS = {".csv", ".doc", ".docx", ".epub", ".key", ".md", ".numbers", ".pages", ".pdf", ".ppt", ".pptx", ".rtf", ".txt", ".xls", ".xlsx"}
DEVELOPER_PARTS = {".git", ".next", "build", "deriveddata", "dist", "node_modules", "target"}


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_protected_path(path: Path) -> bool:
    candidate = Path(os.path.abspath(os.path.expanduser(str(path))))
    for protected in PROTECTED_PATHS:
        owner = Path(os.path.abspath(str(protected)))
        if candidate == owner or owner in candidate.parents:
            return True
    return False


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def _find_binary(name: str) -> str | None:
    if name == "gdu":
        return shutil.which("gdu") or shutil.which("gdu-go")
    return shutil.which(name)


def _version(binary: str) -> str | None:
    try:
        result = _run([binary, "--version"], 5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0][:200] if text else None


def tool_status() -> dict[str, Any]:
    tools: dict[str, Any] = {
        "native": {
            "status": "available",
            "role": "primary",
            "capabilities": [
                "logical-size",
                "allocated-size",
                "hard-link-dedup",
                "same-filesystem",
                "large-files",
                "old-files",
                "duplicate-candidates",
            ],
        }
    }
    for name, role in (("gdu", "independent-verifier"), ("dust", "fast-overview")):
        binary = _find_binary(name)
        tools[name] = {
            "status": "available" if binary else "unavailable",
            "role": role,
            "path": binary,
            "version": _version(binary) if binary else None,
            "installation": "external-user-owned",
        }
    for name, candidates in APP_CANDIDATES.items():
        app = next((path for path in candidates if path.exists()), None)
        tools[name] = {
            "status": "available" if app else "unavailable",
            "role": "semantic-reference",
            "path": str(app) if app else None,
            "invoked": False,
            "reason": "No stable read-only CLI contract is used by this plugin.",
        }
    return {
        "tools": tools,
        "policy": {
            "auto_install": False,
            "gui_launch": False,
            "deletion": False,
            "cloud_materialization": False,
            "protected_paths": [str(path) for path in PROTECTED_PATHS],
        },
    }


def _file_row(path: Path, item_stat: os.stat_result) -> dict[str, Any]:
    allocated = int(getattr(item_stat, "st_blocks", 0)) * 512
    return {
        "path": str(path),
        "logical_bytes": int(item_stat.st_size),
        "allocated_bytes": allocated,
        "modified_at": int(item_stat.st_mtime),
    }


def _smart_category(path: Path) -> str:
    suffix = path.suffix.lower()
    lower_parts = {part.lower() for part in path.parts}
    if suffix in MEDIA_EXTENSIONS:
        return "media"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archives-installers"
    if suffix in DOCUMENT_EXTENSIONS:
        return "documents"
    if lower_parts & DEVELOPER_PARTS:
        return "developer-output"
    if ".app" in lower_parts or any(part.lower().endswith(".app") for part in path.parts):
        return "applications"
    return "other"


def native_scan(root: Path, *, max_entries: int, timeout: int, top: int) -> dict[str, Any]:
    root = _resolved(root)
    if is_protected_path(root):
        raise ValueError(f"Protected Codex path is outside scan scope: {root}")
    root_stat = root.stat()
    root_device = root_stat.st_dev
    started = time.monotonic()
    queue: deque[Path] = deque([root])
    seen_inodes: set[tuple[int, int]] = set()
    logical = 0
    allocated = 0
    entries = 0
    files = 0
    directories = 0
    skipped_symlinks = 0
    skipped_mounts = 0
    skipped_protected: list[str] = []
    errors: list[dict[str, str]] = []
    largest: list[dict[str, Any]] = []
    old: list[dict[str, Any]] = []
    duplicate_sizes: dict[int, list[str]] = defaultdict(list)
    top_level: dict[str, dict[str, int]] = defaultdict(lambda: {"logical_bytes": 0, "allocated_bytes": 0, "entries": 0})
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"logical_bytes": 0, "allocated_bytes": 0, "files": 0})
    complete = True
    stop_reason: str | None = None
    old_before = time.time() - (365 * 24 * 60 * 60)

    while queue:
        if entries >= max_entries:
            complete = False
            stop_reason = "max_entries"
            break
        if time.monotonic() - started >= timeout:
            complete = False
            stop_reason = "timeout"
            break
        current = queue.popleft()
        if current != root and is_protected_path(current):
            skipped_protected.append(str(current))
            continue
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    if entries >= max_entries or time.monotonic() - started >= timeout:
                        queue.appendleft(current)
                        complete = False
                        stop_reason = "max_entries" if entries >= max_entries else "timeout"
                        break
                    path = Path(entry.path)
                    if is_protected_path(path):
                        skipped_protected.append(str(path))
                        continue
                    try:
                        item_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    entries += 1
                    if stat.S_ISLNK(item_stat.st_mode):
                        skipped_symlinks += 1
                        continue
                    if item_stat.st_dev != root_device:
                        skipped_mounts += 1
                        continue
                    inode = (item_stat.st_dev, item_stat.st_ino)
                    if item_stat.st_nlink > 1 and inode in seen_inodes:
                        continue
                    seen_inodes.add(inode)
                    logical += int(item_stat.st_size)
                    item_allocated = int(getattr(item_stat, "st_blocks", 0)) * 512
                    allocated += item_allocated
                    try:
                        owner = path.relative_to(root).parts[0]
                    except (ValueError, IndexError):
                        owner = path.name
                    top_level[owner]["logical_bytes"] += int(item_stat.st_size)
                    top_level[owner]["allocated_bytes"] += item_allocated
                    top_level[owner]["entries"] += 1
                    if stat.S_ISDIR(item_stat.st_mode):
                        directories += 1
                        queue.append(path)
                    elif stat.S_ISREG(item_stat.st_mode):
                        files += 1
                        row = _file_row(path, item_stat)
                        category = _smart_category(path)
                        categories[category]["logical_bytes"] += int(item_stat.st_size)
                        categories[category]["allocated_bytes"] += item_allocated
                        categories[category]["files"] += 1
                        largest.append(row)
                        if item_stat.st_mtime <= old_before:
                            old.append(row)
                        if item_stat.st_size >= 10 * 1024 * 1024:
                            duplicate_sizes[int(item_stat.st_size)].append(str(path))
                if not complete:
                    break
        except OSError as exc:
            errors.append({"path": str(current), "error": f"{type(exc).__name__}: {exc}"})

    largest.sort(key=lambda row: row["allocated_bytes"], reverse=True)
    old.sort(key=lambda row: row["modified_at"])
    duplicate_candidates = [
        {
            "logical_bytes_each": size,
            "paths": paths[:10],
            "candidate_count": len(paths),
            "certainty": "same-logical-size-only",
        }
        for size, paths in duplicate_sizes.items()
        if len(paths) > 1
    ]
    duplicate_candidates.sort(key=lambda row: row["logical_bytes_each"] * row["candidate_count"], reverse=True)
    coverage_complete = complete and not errors
    if errors and stop_reason is None:
        stop_reason = "permission-or-read-errors"
    return {
        "status": "complete" if coverage_complete else "partial",
        "root": str(root),
        "same_filesystem": True,
        "followed_symlinks": False,
        "hard_links_counted_once": True,
        "logical_bytes": logical,
        "allocated_bytes": allocated,
        "allocated_bytes_note": "Per-file allocated blocks; APFS clone-shared blocks may still be counted more than once.",
        "entries": entries,
        "files": files,
        "directories": directories,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stop_reason": stop_reason,
        "skipped": {
            "symlinks": skipped_symlinks,
            "mounts": skipped_mounts,
            "protected_paths": skipped_protected,
        },
        "errors": errors[:200],
        "largest_files": largest[:top],
        "old_files": old[:top],
        "duplicate_candidates": duplicate_candidates[:top],
        "top_level": [
            {"name": name, **values}
            for name, values in sorted(top_level.items(), key=lambda item: item[1]["allocated_bytes"], reverse=True)[:top]
        ],
        "smart_categories": [
            {"category": name, **values}
            for name, values in sorted(categories.items(), key=lambda item: item[1]["allocated_bytes"], reverse=True)
        ],
        "apfs_clone_evidence": {
            "status": "unavailable",
            "exact_reclaimable_bytes": None,
            "reason": "Portable stat accounting cannot prove shared APFS extents without a stable supported clone API.",
        },
    }


def _parse_size_value(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTPE]?I?B)?\s*", value, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    powers = {"B": 0, "KB": 1, "KIB": 1, "MB": 2, "MIB": 2, "GB": 3, "GIB": 3, "TB": 4, "TIB": 4, "PB": 5, "PIB": 5, "EB": 6, "EIB": 6}
    power = powers.get(unit)
    return int(number * (1024**power)) if power is not None else None


def _extract_number(value: Any, keys: tuple[str, ...]) -> int | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = _parse_size_value(value.get(key))
            if candidate is not None:
                return candidate
        for child in value.values():
            found = _extract_number(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list) and len(value) == 1:
        return _extract_number(value[0], keys)
    return None


def _json_adapter(name: str, command: list[str], timeout: int, total_keys: tuple[str, ...]) -> dict[str, Any]:
    binary = _find_binary(name)
    if not binary:
        return {"status": "unavailable", "tool": name, "reason": "binary-not-found"}
    command[0] = binary
    adapter_env = os.environ.copy()
    adapter_env["HOME"] = "/var/empty"
    adapter_env["XDG_CONFIG_HOME"] = "/var/empty"
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=adapter_env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "tool": name, "reason": "timeout"}
    except OSError as exc:
        return {"status": "failed", "tool": name, "reason": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "status": "failed",
            "tool": name,
            "reason": "nonzero-exit",
            "exit_code": result.returncode,
            "stderr": result.stderr.strip()[:1000],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "tool": name, "reason": f"malformed-json: {exc}"}
    total = _extract_number(payload, total_keys)
    return {
        "status": "available" if total is not None else "partial",
        "tool": name,
        "reported_bytes": total,
        "coverage_errors": None,
        "note": "Adapter total normalized read-only; adapter-internal error flags are not promoted to native coverage evidence.",
    }


def gdu_scan(root: Path, timeout: int) -> dict[str, Any]:
    ignored = ",".join(str(path) for path in PROTECTED_PATHS)
    binary = _find_binary("gdu")
    if not binary:
        return {"status": "unavailable", "tool": "gdu", "reason": "binary-not-found"}
    command = [
        binary,
        "-nps",
        "--no-prefix",
        "--no-delete",
        "--no-view-file",
        "--no-spawn-shell",
        "--no-cross",
        "--ignore-dirs",
        ignored,
        str(root),
    ]
    adapter_env = os.environ.copy()
    adapter_env["HOME"] = "/var/empty"
    adapter_env["XDG_CONFIG_HOME"] = "/var/empty"
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=adapter_env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "tool": "gdu", "reason": "timeout"}
    except OSError as exc:
        return {"status": "failed", "tool": "gdu", "reason": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "status": "failed",
            "tool": "gdu",
            "reason": "nonzero-exit",
            "exit_code": result.returncode,
            "stderr": result.stderr.strip()[:1000],
        }
    first_token = result.stdout.strip().split(maxsplit=1)[0] if result.stdout.strip() else ""
    try:
        total = int(first_token)
    except ValueError:
        return {"status": "failed", "tool": "gdu", "reason": "malformed-summary"}
    return {
        "status": "available",
        "tool": "gdu",
        "reported_bytes": total,
        "coverage_errors": result.stderr.strip()[:1000] or None,
        "note": "Bounded non-interactive disk-usage summary; full-tree export is intentionally avoided.",
    }


def dust_scan(root: Path, timeout: int) -> dict[str, Any]:
    names = sorted({path.name for path in PROTECTED_PATHS})
    exclusions = [argument for name in names for argument in ("-X", name)]
    result = _json_adapter(
        "dust",
        ["dust", "-j", "-x", "-o", "b", "-n", "100", "-d", "3", *exclusions, str(root)],
        timeout,
        ("size", "usage", "total", "total_bytes"),
    )
    result["scope_note"] = "Protected basenames are conservatively excluded because dust has no exact-path exclusion contract."
    return result


def volume_evidence(root: Path, timeout: int = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout

    def remaining() -> int:
        return max(0, int(deadline - time.monotonic()))

    evidence: dict[str, Any] = {
        "status": "partial",
        "purgeable_bytes": None,
        "purgeable_note": "No stable exact reclaimable purgeable-byte contract is asserted; APFS shared container free is reported.",
        "local_snapshots": None,
    }
    try:
        diskutil_budget = remaining()
        if diskutil_budget <= 0:
            return {**evidence, "status": "skipped", "reason": "global-time-budget"}
        result = subprocess.run(
            ["diskutil", "info", "-plist", str(root)],
            capture_output=True,
            timeout=max(1, min(diskutil_budget, 15)),
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            payload = plistlib.loads(result.stdout)
            raw_free = payload.get("FreeSpace")
            container_free = payload.get("APFSContainerFree")
            fs_type = payload.get("FilesystemType")
            usable_free = container_free if (fs_type == "apfs" or container_free is not None) and (not raw_free) else raw_free
            if usable_free is None or (usable_free == 0 and fs_type == "apfs"):
                try:
                    st = os.statvfs(str(root))
                    usable_free = st.f_bavail * st.f_frsize
                except OSError:
                    pass
            evidence.update(
                {
                    "status": "available",
                    "filesystem": fs_type,
                    "volume_name": payload.get("VolumeName"),
                    "total_bytes": payload.get("TotalSize"),
                    "free_bytes": usable_free,
                    "volume_free_bytes": raw_free,
                    "apfs_container_free_bytes": container_free,
                }
            )
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException) as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    try:
        snapshot_budget = remaining()
        if snapshot_budget <= 0:
            return evidence
        snapshots = _run(["tmutil", "listlocalsnapshots", str(root)], max(1, min(snapshot_budget, 15)))
        if snapshots.returncode == 0:
            evidence["local_snapshots"] = len(
                [line for line in snapshots.stdout.splitlines() if line.strip().startswith("com.apple.TimeMachine.")]
            )
    except (OSError, subprocess.TimeoutExpired):
        pass
    return evidence


def _disagreements(native: dict[str, Any], adapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = native.get("allocated_bytes")
    rows = []
    if not isinstance(baseline, int) or baseline <= 0:
        return rows
    for adapter in adapters:
        reported = adapter.get("reported_bytes")
        if not isinstance(reported, int):
            continue
        ratio = abs(reported - baseline) / baseline
        if ratio > 0.10:
            note = (
                f"Different accounting semantics or native partial coverage ({native.get('stop_reason', 'partial')}) require review."
                if native.get("status") == "partial"
                else "Different accounting semantics or incomplete coverage require review."
            )
            rows.append(
                {
                    "tool": adapter["tool"],
                    "native_allocated_bytes": baseline,
                    "reported_bytes": reported,
                    "difference_ratio": round(ratio, 4),
                    "material": True,
                    "note": note,
                }
            )
    return rows


def deep_scan(root: Path, *, max_entries: int, timeout: int, top: int) -> dict[str, Any]:
    root = _resolved(root)
    started = time.monotonic()
    deadline = started + timeout

    def remaining() -> int:
        return max(0, int(deadline - time.monotonic()))

    native_budget = max(1, min(timeout, int(timeout * 0.70)))
    native = native_scan(root, max_entries=max_entries, timeout=native_budget, top=top)
    gdu_budget = min(60, max(0, remaining() // 2))
    gdu = gdu_scan(root, gdu_budget) if gdu_budget > 0 else {"status": "skipped", "tool": "gdu", "reason": "global-time-budget"}
    dust_budget = min(60, max(0, remaining() // 2))
    dust = dust_scan(root, dust_budget) if dust_budget > 0 else {"status": "skipped", "tool": "dust", "reason": "global-time-budget"}
    volume_budget = remaining()
    volume = volume_evidence(root, volume_budget) if volume_budget > 0 else {"status": "skipped", "reason": "global-time-budget"}
    active_adapters = [adapter for adapter in (gdu, dust) if adapter.get("status") == "available"]
    return {
        "schema_version": 1,
        "status": "complete" if native["status"] == "complete" else "partial",
        "mode": "read-only",
        "root": str(root),
        "summary": {
            "logical_bytes": native["logical_bytes"],
            "allocated_bytes": native["allocated_bytes"],
            "coverage": native["status"],
            "complete": native["status"] == "complete",
            "stop_reason": native.get("stop_reason"),
            "reclaimable_bytes": None,
            "reclaimable_note": "Exact reclaim requires ownership and APFS clone/snapshot analysis; this scan does not guess it.",
        },
        "native": native,
        "adapters": {"gdu": gdu, "dust": dust},
        "semantic_references": {
            "grandperspective": "Physical versus logical and cloud-placeholder semantics adapted without launching the app.",
            "spacie": "APFS clone, hard-link, duplicate, large-file, old-file, and permission coverage modeled without copying or invoking Spacie.",
        },
        "volume": volume,
        "disagreements": _disagreements(native, active_adapters),
        "coverage": tool_status(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": timeout,
    }
