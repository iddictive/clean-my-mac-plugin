#!/usr/bin/env python3
"""macOS disk cleanup helper for the clean-my-mac Codex skill."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from disk_scanners import deep_scan, is_protected_path, tool_status


HOME = Path.home()


@dataclass(frozen=True)
class Target:
    label: str
    path: Path
    category: str
    default_clean: bool
    note: str


TARGETS = [
    Target("Yarn cache", HOME / "Library/Caches/Yarn", "cache", True, "regeneratable package cache"),
    Target("Playwright browsers", HOME / "Library/Caches/ms-playwright", "cache", True, "regeneratable test browsers"),
    Target("Homebrew downloads", HOME / "Library/Caches/Homebrew", "cache", True, "regeneratable brew downloads"),
    Target("pip cache", HOME / "Library/Caches/pip", "cache", True, "regeneratable Python cache"),
    Target("pnpm cache", HOME / "Library/Caches/pnpm", "cache", True, "regeneratable package cache"),
    Target("node-gyp cache", HOME / "Library/Caches/node-gyp", "cache", True, "regeneratable build cache"),
    Target("CocoaPods cache", HOME / "Library/Caches/CocoaPods", "cache", True, "regeneratable iOS dependency cache"),
    Target("SwiftPM cache", HOME / "Library/Caches/org.swift.swiftpm", "cache", True, "regeneratable SwiftPM cache"),
    Target("npm cache", HOME / ".npm", "cache", True, "regeneratable npm cache"),
    Target("Hugging Face cache", HOME / ".cache/huggingface", "model-cache", True, "models can be re-downloaded"),
    Target("Whisper cache", HOME / ".cache/whisper", "model-cache", True, "models can be re-downloaded"),
    Target("Codex runtime cache", HOME / ".cache/codex-runtimes", "codex-protected", False, "protected Codex runtime data"),
    Target("Puppeteer cache", HOME / ".cache/puppeteer", "cache", True, "regeneratable browser cache"),
    Target("RantBar cache", HOME / "Library/Caches/rantbar", "rantbar-cache", True, "regeneratable RantBar cache"),
    Target("RantBar bundle cache", HOME / "Library/Caches/com.rantsports.rantbar", "rantbar-cache", True, "regeneratable RantBar bundle cache"),
    Target("Xcode DerivedData", HOME / "Library/Developer/Xcode/DerivedData", "xcode", False, "safe but can slow next build"),
    Target("iOS Simulator devices", HOME / "Library/Developer/CoreSimulator/Devices", "simulators", False, "removes simulator state"),
    Target("Docker Desktop data", HOME / "Library/Containers/com.docker.docker", "docker", False, "removes local Docker state"),
    Target("User Trash", HOME / ".Trash", "trash", False, "deleted files; may need sudo for root-owned leftovers"),
    Target("RantBar app state", HOME / "Library/Application Support/RantBar", "rantbar-state", False, "stateful app data; remove only after app reset decision"),
    Target("Codex relocation backups", HOME / ".codex/workspace-relocation-backups", "codex-protected", False, "protected Codex local history"),
    Target("iCloud Drive Trash", HOME / "Library/Mobile Documents/.Trash", "icloud-trash", False, "diagnostic only; do not rm directly"),
    Target("Photos library originals", HOME / "Pictures/Photos Library.photoslibrary/originals", "photos-originals", False, "diagnostic only; never delete directly"),
]


NEVER_DELETE_CATEGORIES = {"icloud-trash", "photos-originals", "codex-protected"}
MANUAL_INCLUDE_CHOICES = ["xcode", "simulators", "docker", "trash", "rantbar-state"]


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def du_bytes(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        result = run(["du", "-sk", str(path)], timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        kib = int(result.stdout.split()[0])
    except (IndexError, ValueError):
        return None
    return kib * 1024


def human_size(num: int | None) -> str:
    if num is None:
        return "-"
    value = float(num)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def disk_free() -> str:
    result = run(["df", "-h", "/System/Volumes/Data"], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()


def target_rows() -> list[dict[str, object]]:
    rows = []
    for target in TARGETS:
        skip_size = target.category in NEVER_DELETE_CATEGORIES
        size = None if skip_size else du_bytes(target.path)
        rows.append(
            {
                "label": target.label,
                "path": str(target.path),
                "category": target.category,
                "default_clean": target.default_clean,
                "size_bytes": size,
                "size": human_size(size),
                "note": target.note,
                "exists": target.path.exists(),
                "size_skipped": skip_size,
            }
        )
    rows.sort(key=lambda row: row["size_bytes"] or 0, reverse=True)
    return rows


def classify_app_support(name: str) -> tuple[str, str]:
    lower = name.lower()
    if lower.startswith("icloud-reset-backup") or lower.startswith("icloud-local-state-reset"):
        return "repair-backup", "Review retention; delete only after confirming the backup is obsolete."
    if lower in {"fileprovider", "clouddocs"}:
        return "sync-state", "Do not terminal-delete while File Provider or iCloud is active."
    if lower in {"steam", "minecraft", "modrinthapp"}:
        return "game-state", "Library or game state; use the app's own cleanup/uninstall flow."
    if lower in {"rantbar"}:
        return "app-state", "RantBar state; clean caches first, reset state only after explicit app decision."
    if lower in {"whisperkiller"}:
        return "app-output", "App output/state; identify generated artifacts before deleting."
    if lower in {"cursor", "figma", "notion", "slack", "linear", "google", "pachca", "comet"}:
        return "app-state", "App state/cache mix; prefer in-app cleanup or targeted cache paths."
    return "app-support", "Stateful by default; inspect producer and retention before deletion."


def app_support_rows(limit: int) -> list[dict[str, object]]:
    root = HOME / "Library/Application Support"
    rows = []
    if not root.exists():
        return rows
    for path in root.iterdir():
        size = du_bytes(path)
        if not size:
            continue
        category, guidance = classify_app_support(path.name)
        rows.append(
            {
                "label": path.name,
                "path": str(path),
                "category": category,
                "size_bytes": size,
                "size": human_size(size),
                "guidance": guidance,
            }
        )
    rows.sort(key=lambda row: row["size_bytes"] or 0, reverse=True)
    return rows[:limit]


def selected_targets(includes: Iterable[str]) -> list[Target]:
    include_set = set(includes)
    allowed = []
    for target in TARGETS:
        if target.category in NEVER_DELETE_CATEGORIES or is_protected_path(target.path):
            continue
        if target.default_clean or target.category in include_set:
            allowed.append(target)
    return allowed


def remove_path(path: Path) -> tuple[bool, str]:
    if is_protected_path(path):
        raise ValueError(f"Protected Codex path cannot be removed: {path}")
    if not path.exists():
        return True, "missing"
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True, "removed"
    shutil.rmtree(path)
    return True, "removed"


def print_scan(json_mode: bool) -> None:
    rows = target_rows()
    if json_mode:
        print(json.dumps({"disk": disk_free(), "targets": rows}, indent=2))
        return
    print(disk_free())
    print()
    for row in rows:
        if not row["exists"] and row["size_bytes"] is None:
            continue
        marker = "default" if row["default_clean"] else "manual"
        print(f"{row['size']:>10}  {marker:7}  {row['category']:15}  {row['label']}")
        print(f"            {row['path']}")
        print(f"            {row['note']}")


def print_plan(includes: Iterable[str], json_mode: bool) -> None:
    targets = selected_targets(includes)
    rows = []
    total = 0
    for target in targets:
        size = du_bytes(target.path)
        if size:
            total += size
        rows.append(
            {
                "label": target.label,
                "path": str(target.path),
                "category": target.category,
                "size_bytes": size,
                "size": human_size(size),
                "note": target.note,
            }
        )
    if json_mode:
        print(json.dumps({"estimated_reclaim": total, "targets": rows}, indent=2))
        return
    print(f"Estimated reclaim: {human_size(total)}")
    for row in rows:
        print(f"{row['size']:>10}  {row['category']:15}  {row['label']}")
        print(f"            {row['path']}")


def print_app_support_audit(limit: int, json_mode: bool) -> None:
    rows = app_support_rows(limit)
    total = sum(row["size_bytes"] or 0 for row in rows)
    if json_mode:
        print(json.dumps({"total_listed": total, "targets": rows}, indent=2))
        return
    print(f"Top Application Support entries: {human_size(total)} listed")
    for row in rows:
        print(f"{row['size']:>10}  {row['category']:15}  {row['label']}")
        print(f"            {row['path']}")
        print(f"            {row['guidance']}")


def clean(includes: Iterable[str], yes: bool, json_mode: bool) -> int:
    if not yes:
        print("Refusing to delete without --yes. Run plan first, then clean --yes.", file=sys.stderr)
        return 2

    targets = selected_targets(includes)
    before = disk_free()
    results = []
    for target in targets:
        size = du_bytes(target.path)
        try:
            ok, message = remove_path(target.path)
        except Exception as exc:  # noqa: BLE001 - surface cleanup failure verbatim
            ok, message = False, f"{type(exc).__name__}: {exc}"
        results.append(
            {
                "label": target.label,
                "path": str(target.path),
                "category": target.category,
                "size_before": human_size(size),
                "ok": ok,
                "message": message,
            }
        )
    after = disk_free()
    if json_mode:
        print(json.dumps({"before": before, "after": after, "results": results}, indent=2))
        return 0 if all(item["ok"] for item in results) else 1
    print("Before:")
    print(before)
    print("\nResults:")
    for item in results:
        status = "ok" if item["ok"] else "failed"
        print(f"{status:6} {item['size_before']:>10}  {item['category']:15}  {item['label']} - {item['message']}")
    print("\nAfter:")
    print(after)
    return 0 if all(item["ok"] for item in results) else 1


def cloud_status() -> int:
    print("Processes:")
    proc = run(
        [
            "sh",
            "-c",
            "ps -axo pid,%cpu,%mem,etime,comm,args | awk 'NR==1 || /\\/bird($| )|\\/cloudd($| )|\\/fileproviderd($| )|Finder.app|Google Drive.app/' | head -40",
        ],
        timeout=10,
    )
    print(proc.stdout.strip())
    print("\niCloud status:")
    try:
        brctl = run(["sh", "-c", "brctl status 2>/tmp/clean-my-mac-brctl.err | sed -n '1,120p'"], timeout=20)
        print(brctl.stdout.strip() or "(no brctl output)")
    except subprocess.TimeoutExpired:
        print("(brctl timed out after 20 seconds; cloud status remains partial)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan and clean safe macOS disk junk.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="show disk usage candidates")
    scan_p.add_argument("--json", action="store_true")

    deep_p = sub.add_parser("deep-scan", help="run a read-only physical/logical disk audit")
    deep_p.add_argument("--path", type=Path, default=Path("/System/Volumes/Data"))
    deep_p.add_argument("--max-entries", type=int, default=1_000_000)
    deep_p.add_argument("--timeout", type=int, default=300)
    deep_p.add_argument("--top", type=int, default=30)
    deep_p.add_argument("--json", action="store_true")

    status_p = sub.add_parser("tool-status", help="show deep-scan adapter availability")
    status_p.add_argument("--json", action="store_true")

    for command in ("codex-scan", "codex-plan", "codex-clean"):
        artifact_p = sub.add_parser(command, help="inspect or clean selected regeneratable Codex task artifacts")
        artifact_p.add_argument("--root", action="append", default=None)
        artifact_p.add_argument("--timeout", type=int, default=60)
        artifact_p.add_argument("--max-entries", type=int, default=500_000)
        artifact_p.add_argument("--json", action="store_true")
        if command != "codex-scan":
            artifact_p.add_argument("--id", action="append", default=[])
        if command == "codex-clean":
            artifact_p.add_argument("--yes", action="store_true")

    plan_p = sub.add_parser("plan", help="show what clean would remove")
    plan_p.add_argument("--include", action="append", default=[], choices=MANUAL_INCLUDE_CHOICES)
    plan_p.add_argument("--json", action="store_true")

    clean_p = sub.add_parser("clean", help="remove selected targets")
    clean_p.add_argument("--include", action="append", default=[], choices=MANUAL_INCLUDE_CHOICES)
    clean_p.add_argument("--yes", action="store_true")
    clean_p.add_argument("--json", action="store_true")

    sub.add_parser("cloud-status", help="show Finder/iCloud/File Provider status")

    app_support_p = sub.add_parser("app-support-audit", help="show largest Application Support owners")
    app_support_p.add_argument("--limit", type=int, default=20)
    app_support_p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command.startswith("codex-"):
        from codex_artifacts import clean_codex_artifacts, plan_codex_artifacts, scan_codex_artifacts

        kwargs = {"roots": args.root, "timeout": args.timeout, "max_entries": args.max_entries}
        try:
            if args.command == "codex-scan":
                result = scan_codex_artifacts(**kwargs)
            elif args.command == "codex-plan":
                result = plan_codex_artifacts(args.id, **kwargs)
            else:
                result = clean_codex_artifacts(args.id, yes=args.yes, **kwargs)
        except (ValueError, OSError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        failed = result.get("error") or result.get("status") in {"rejected", "failed"}
        if args.command == "codex-clean" and result.get("status") != "cleaned":
            failed = True
        return 1 if failed or any(not row.get("ok", False) for row in result.get("results", [])) else 0
    if args.command == "scan":
        print_scan(args.json)
        return 0
    if args.command == "deep-scan":
        result = deep_scan(
            args.path,
            max_entries=max(1, min(args.max_entries, 5_000_000)),
            timeout=max(1, min(args.timeout, 900)),
            top=max(1, min(args.top, 100)),
        )
        print(json.dumps(result, indent=2) if args.json else json.dumps(result, indent=2))
        return 0
    if args.command == "tool-status":
        result = tool_status()
        print(json.dumps(result, indent=2) if args.json else json.dumps(result, indent=2))
        return 0
    if args.command == "plan":
        print_plan(args.include, args.json)
        return 0
    if args.command == "clean":
        return clean(args.include, args.yes, args.json)
    if args.command == "cloud-status":
        return cloud_status()
    if args.command == "app-support-audit":
        print_app_support_audit(max(1, args.limit), args.json)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
