"""Selected, reproducible task artifacts; never Codex runtime or whole tasks.

Hugging Face local_dir metadata is read directly: the native metadata reader
creates lock directories and can delete invalid metadata, unsuitable for a scan.
No model client is imported and no network operation is performed.
"""
from __future__ import annotations

import hashlib
import base64
import binascii
import errno
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time

from disk_scanners import is_protected_path


_PROTECTED = {".git", ".codex", "outputs", "original", "originals", "input", "inputs", "source", "sources", "training", "sessions", "archived_sessions", "credentials", "codex-runtimes"}
_GENERATED = {"node_modules", ".next", ".next-build", "dist", "build", "target", ".venv", "venv", "local-venv"}
_HEX = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_LIMIT_OUTPUT = 16 * 1024 * 1024


class _Bound(Exception):
    pass


class _Budget:
    def __init__(self, timeout, max_entries):
        self.deadline = time.monotonic() + min(max(float(timeout), 0.01), 120)
        self.limit = min(max(int(max_entries), 1), 1000000)
        self.entries = 0

    def check(self, count=0):
        self.entries += count
        if time.monotonic() >= self.deadline or self.entries > self.limit:
            raise _Bound("deadline or entry limit reached")


def _run(args, budget, child_pids=None):
    """Bound output and deadline; kill the command's entire process group."""
    budget.check()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdout=output, stderr=errors, start_new_session=True)
        if child_pids is not None:
            child_pids.add(proc.pid)
        try:
            while proc.poll() is None:
                budget.check()
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > _LIMIT_OUTPUT:
                    raise _Bound("command output limit reached")
                time.sleep(0.01)
            output.seek(0)
            errors.seek(0)
            data, error = output.read(_LIMIT_OUTPUT + 1), errors.read(_LIMIT_OUTPUT + 1)
            if len(data) + len(error) > _LIMIT_OUTPUT:
                raise _Bound("command output limit reached")
            return proc.returncode, data.decode("utf-8", "surrogateescape"), error.decode("utf-8", "replace")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def _inside(path, parent):
    return path == parent or parent in path.parents


def _no_links(path):
    return all(not part.is_symlink() for part in (path, *path.parents))


def _roots(roots):
    if roots is None:
        configured = os.environ.get("CLEAN_MY_MAC_CODEX_ROOTS")
        roots = configured.split(os.pathsep) if configured else [Path.home() / "05_Repo/Codex_Default", Path.home() / "Documents/Codex"]
    if isinstance(roots, (str, Path)):
        roots = [roots]
    result = []
    for raw in roots:
        path = Path(os.path.abspath(Path(raw).expanduser()))
        # Explicit roots are supported for configured workspace locations/tests,
        # but a system/home root or runtime tree is never an admissible scope.
        if path == Path.home() or len(path.parts) < 4 or is_protected_path(path) or any(p in _PROTECTED for p in path.parts):
            raise ValueError("root must be a bounded Codex task workspace, outside runtime/source/output trees")
        if not _no_links(path):
            raise ValueError("symlink workspace roots are not allowed")
        if not any(_inside(path, prior) for prior in result):
            result = [prior for prior in result if not _inside(prior, path)] + [path]
    return result


def _activity(budget):
    """Ignore only this scanner and its descendants, never other task writers."""
    code, out, err = _run(["ps", "-axo", "pid=,ppid="], budget)
    if code or err:
        return {"known": False, "reason": "process inventory unavailable", "paths": []}
    parents = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(f.isdecimal() for f in fields):
            return {"known": False, "reason": "process inventory incomplete", "paths": []}
        parents[int(fields[0])] = int(fields[1])
    own = {os.getpid()}
    while True:
        descendants = {pid for pid, parent in parents.items() if parent in own}
        if descendants <= own:
            break
        own.update(descendants)
    code, out, err = _run(["lsof", "-nP", "-a", "-u", str(os.getuid()), "-F", "pfn"], budget, child_pids=own)
    if code or err or not out.strip():
        return {"known": False, "reason": "open-file inventory unavailable or incomplete", "paths": []}
    paths, pid, fd = [], None, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid, fd = int(line[1:]), None
        elif line.startswith("f"):
            fd = line[1:]
        elif line.startswith("n/") and pid not in own:
            paths.append((Path(line[1:]), fd == "cwd"))
    return {"known": True, "reason": "", "paths": paths}


def _repo(path, boundary):
    for parent in (path.parent, *path.parent.parents):
        if not _inside(parent, boundary):
            break
        if (parent / ".git").exists():
            return parent
    return None


def _finder_store(fd):
    """Recognize bounded regular Finder metadata without following any link."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or not 36 <= info.st_size <= 1024 * 1024:
        return False
    if getattr(info, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0):
        return False
    header = os.pread(fd, 36, 0)
    if len(header) != 36:
        return False
    version, magic, offset, length, repeated_offset = struct.unpack(">I4sIII", header[:20])
    return (version == 1 and magic == b"Bud1" and offset == repeated_offset
            and offset >= 32 and length >= 32 and length & (length - 1) == 0
            and offset + length + 4 <= info.st_size)


def _finder_path(path):
    if path.name != ".DS_Store" or not _no_links(path):
        return False
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        return _finder_store(fd)
    finally:
        os.close(fd)


def _tree(path, budget, directories=None):
    """Stat every entry without following links or crossing volumes."""
    device = path.lstat().st_dev
    digest = hashlib.sha256()
    records, logical, allocated, blocked, links = [], 0, 0, set(), False
    pending = [path]
    while pending:
        current = pending.pop()
        budget.check(1)
        info = current.lstat()
        relative = current.relative_to(path)
        if info.st_dev != device or (current != path and os.path.ismount(current)):
            blocked.add("nested mount")
            continue
        if getattr(info, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0):
            blocked.add("cloud placeholder in artifact tree")
            continue
        finder = stat.S_ISREG(info.st_mode) and current.name == ".DS_Store" and _finder_path(current)
        # Directory timestamps/size also change when Finder creates or removes
        # its metadata. Identity and permissions stay bound; every meaningful
        # child still contributes its path, inode, size and modification state.
        if not finder:
            identity = [str(relative), info.st_mode, info.st_dev, info.st_ino]
            if not stat.S_ISDIR(info.st_mode):
                identity += [info.st_size, info.st_mtime_ns, info.st_ctime_ns]
            digest.update(json.dumps(identity, separators=(",", ":")).encode())
        if stat.S_ISLNK(info.st_mode):
            links = True
            digest.update(os.fsencode(os.readlink(current)))
            continue
        if stat.S_ISDIR(info.st_mode):
            if directories is not None:
                directories[str(relative)] = info
            if current != path and current.name in {".git", "outputs", "originals", "training"}:
                blocked.add("protected content in artifact tree")
            with os.scandir(current) as entries:
                children = []
                for entry in entries:
                    budget.check()
                    children.append(Path(entry.path))
                    if len(children) > budget.limit - budget.entries:
                        raise _Bound("entry limit reached")
            pending.extend(sorted(children, reverse=True))
        elif stat.S_ISREG(info.st_mode):
            records.append((relative, info))
            logical += info.st_size
            allocated += getattr(info, "st_blocks", 0) * 512
        else:
            blocked.add("special file in artifact tree")
    return digest.hexdigest(), records, logical, allocated, sorted(blocked), links


def _small(path, limit=1024 * 1024):
    if not _no_links(path) or not path.is_file() or path.stat().st_size > limit:
        return ""
    if getattr(path.stat(), "st_flags", 0) & getattr(stat, "SF_DATALESS", 0):
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.read(limit + 1)


def _hf(path, records, repo):
    prefix = Path(".cache/huggingface/download")
    revisions, payloads = set(), []
    for relative, info in records:
        if relative.name == ".DS_Store" and _finder_path(path / relative):
            continue
        if _inside(relative, Path(".cache")):
            if relative == Path(".cache/huggingface/CACHEDIR.TAG") and _small(path / relative, 4096).startswith("Signature: 8a477f597d28d172789f06886806bc55"):
                continue
            if relative.parent == Path(".cache/huggingface/trees") and re.fullmatch(r"[0-9a-f]{40}\.json", relative.name):
                try:
                    tree = json.loads(_small(path / relative))
                except ValueError:
                    return {}, "invalid Hugging Face tree metadata"
                if tree.get("format_version") == 1 and isinstance(tree.get("files"), dict):
                    continue
            if relative == Path(".cache/huggingface/.gitignore") or (_inside(relative, prefix) and relative.suffix in {".metadata", ".lock"}):
                continue
            return {}, "unknown/incomplete Hugging Face cache content"
        metadata = path / prefix / (str(relative) + ".metadata")
        fields = _small(metadata, 4096).splitlines()
        if len(fields) != 3 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]) or not _HEX.fullmatch(fields[1]):
            return {}, "file lacks immutable download metadata: " + str(relative)
        try:
            timestamp = float(fields[2])
        except ValueError:
            return {}, "invalid download timestamp"
        if not math.isfinite(timestamp) or info.st_mtime - 1 > timestamp:
            return {}, "downloaded file modified after download"
        revisions.add(fields[0])
        payloads.append(str(relative))
    if not payloads or len(revisions) != 1:
        return {}, "missing payload or mixed download revisions"
    revision = next(iter(revisions))
    # Metadata alone omits the repository identity. Retained task documentation
    # must bind this bundle's basename and exact commit to a recovery source.
    evidence = []
    for doc in [repo / "work/README.md", repo / "README.md"]:
        for line in _small(doc).splitlines():
            match = re.search(r"([A-Za-z0-9_.-]+/" + re.escape(path.name) + r")(?![\w.-])", line)
            if match and revision in line:
                evidence.append({"repository": match[1], "revision": revision, "evidence_path": str(doc)})
    if not evidence:
        return {}, "repository and exact revision recovery evidence missing"
    return {"kind": "huggingface-local-dir", **evidence[0], "downloaded_files": len(payloads)}, ""


def _classification(path, records, repo):
    if (path / ".cache/huggingface/download").is_dir():
        return "codex-model-download", *_hf(path, records, repo)
    parent = path.parent
    if path.name in {"node_modules", ".next", ".next-build", "dist", "build"}:
        locks = [parent / n for n in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")]
        lock = next((p for p in locks if _small(p)), None)
        package = _small(parent / "package.json")
        if package and lock:
            try:
                manifest = json.loads(package)
            except ValueError:
                manifest = {}
            if path.name == "node_modules" or manifest.get("scripts", {}).get("build"):
                return "codex-build", {"kind": "node", "manifest": str(parent / "package.json"), "lockfile": str(lock)}, ""
    if path.name == "target" and _small(parent / "Cargo.toml") and _small(parent / "Cargo.lock"):
        return "codex-build", {"kind": "cargo", "manifest": str(parent / "Cargo.toml"), "lockfile": str(parent / "Cargo.lock")}, ""
    if (path / "pyvenv.cfg").is_file():
        for base in [parent, repo]:
            for name in ("requirements.txt", "requirements.lock", "uv.lock", "poetry.lock", "Pipfile.lock"):
                lock = base / name
                if _small(lock):
                    return "codex-python-env", {"kind": "python-venv", "requirements": str(lock)}, ""
    return "codex-review", {}, "no complete reconstruction evidence; retained"


def _token(path, fingerprint):
    payload = json.dumps({"path": str(path), "fingerprint": fingerprint}, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    body = "codex-artifact-v1." + encoded
    return body + "." + hashlib.sha256(body.encode()).hexdigest()


def _token_path(token):
    # The checksum detects corruption, not authority. Every decoded path must
    # independently pass scope, artifact ownership and current-tree validation.
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError
        prefix, encoded, checksum = token.split(".")
        if prefix != "codex-artifact-v1" or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError
        body = prefix + "." + encoded
        if hashlib.sha256(body.encode()).hexdigest() != checksum:
            raise ValueError
        data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        if set(data) != {"path", "fingerprint"} or not isinstance(data["path"], str) or "\0" in data["path"]:
            raise ValueError
        path = Path(data["path"])
        if not path.is_absolute() or str(path) != os.path.abspath(path) or not re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"]):
            raise ValueError
        if _token(path, data["fingerprint"]) != token:
            raise ValueError
        return path
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise ValueError("Malformed, unknown or stale artifact ID; rescan before selection") from None


def _selected_root(path, roots):
    root = next((root for root in roots if path != root and _inside(path, root)), None)
    if root is None or is_protected_path(path) or any(part in _PROTECTED for part in path.parts):
        raise ValueError("Artifact ID is outside task roots or references protected content")
    if not _no_links(path):
        raise ValueError("symlink artifact root or ancestor refused")
    device = root.lstat().st_dev
    current = path
    while current != root:
        info = current.lstat()
        if info.st_dev != device or os.path.ismount(current):
            raise ValueError("nested mount in selected artifact path")
        if getattr(info, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0):
            raise ValueError("cloud placeholder in selected artifact path")
        current = current.parent
    if not path.is_dir() or (path / ".git").exists():
        raise ValueError("Artifact ID is stale or references a whole task/repository")
    if not (path.name in _GENERATED or (path / "pyvenv.cfg").is_file() or (path / ".cache/huggingface/download").is_dir()):
        raise ValueError("Artifact ID is stale or does not identify a supported artifact")
    return root


def _candidate(path, root, budget, activity, directories=None):
    fingerprint, records, logical, allocated, reasons, links = _tree(path, budget, directories)
    repo = _repo(path, root)
    category, provenance = "codex-review", {}
    if repo is None:
        reasons.append("no owning Git workspace")
    else:
        category, provenance, classification_reason = _classification(path, records, repo)
        if classification_reason:
            reasons.append(classification_reason)
        code, tracked, error = _run(["git", "-C", str(repo), "ls-files", "-z", "--", str(path.relative_to(repo))], budget)
        if code or error or tracked:
            reasons.append("tracked files or unknown Git inventory")
        code, _, _ = _run(["git", "-C", str(repo), "check-ignore", "-q", "--", str(path.relative_to(repo))], budget)
        if code:
            reasons.append("artifact is not Git-ignored")
    # stdlib's descriptor-based rmtree unlinks child symlinks without following
    # them. Only proven generated environments/builds may use that facility;
    # downloaded model bundles and unknown content remain strict.
    if links and (category not in {"codex-build", "codex-python-env"} or not provenance or not shutil.rmtree.avoids_symlink_attacks):
        reasons.append("symlink in artifact tree")
    if not activity["known"]:
        reasons.append(activity["reason"])
    elif any(_inside(opened, path) or (cwd and repo and _inside(opened, repo)) for opened, cwd in activity["paths"]):
        reasons.append("workspace or artifact is actively used")
    reasons = list(dict.fromkeys(reasons))
    size = f"{logical / (1024 ** 3):.2f} GiB" if logical >= 1024 ** 3 else f"{logical / 1024:.1f} KiB"
    return {"id": _token(path, fingerprint), "label": path.name, "path": str(path), "category": category,
            "size_bytes": logical, "size": size, "logical_bytes": logical, "allocated_bytes": allocated,
            "eligible": not reasons, "blocked_reason": "; ".join(reasons) or None, "blocked_reasons": reasons,
            "note": "Re-download/rebuild required after removal; allocated bytes are not exact APFS reclaimable space.",
            "provenance": provenance}


def _scan(roots, budget):
    resolved = _roots(roots)
    report = {"status": "ok", "roots": [str(p) for p in resolved], "targets": [],
              "coverage": {"complete": True, "entries": 0, "activity_known": False, "errors": [], "skipped": []}}
    try:
        activity = _activity(budget)
        report["coverage"]["activity_known"] = activity["known"]
        if not activity["known"]:
            report["coverage"]["errors"].append(activity["reason"])
        for root in resolved:
            if not root.is_dir():
                report["coverage"]["skipped"].append({"path": str(root), "reason": "missing root"})
                continue
            device, pending = root.stat().st_dev, [root]
            while pending:
                path = pending.pop()
                budget.check(1)
                if not _no_links(path) or path.lstat().st_dev != device or (path != root and os.path.ismount(path)):
                    report["coverage"]["skipped"].append({"path": str(path), "reason": "symlink or nested mount"})
                    continue
                if path != root and (path.name in _PROTECTED or is_protected_path(path)):
                    continue
                if path != root and (path.name in _GENERATED or (path / "pyvenv.cfg").is_file() or (path / ".cache/huggingface/download").is_dir()):
                    report["targets"].append(_candidate(path, root, budget, activity))
                    continue
                with os.scandir(path) as entries:
                    for entry in entries:
                        budget.check(1)
                        if entry.is_dir(follow_symlinks=False) and entry.name not in _PROTECTED:
                            pending.append(Path(entry.path))
                        if len(pending) > budget.limit - budget.entries:
                            raise _Bound("entry limit reached")
    except (OSError, ValueError, _Bound) as error:
        report["coverage"]["complete"] = False
        report["coverage"]["errors"].append(str(error))
    report["coverage"]["entries"] = budget.entries
    if not report["coverage"]["complete"] or not report["coverage"]["activity_known"]:
        report["status"] = "partial"
        for row in report["targets"]:
            row["eligible"], row["blocked_reason"] = False, "scan/activity coverage incomplete"
    report["targets"].sort(key=lambda row: row["size_bytes"], reverse=True)
    return report


def scan_codex_artifacts(roots=None, timeout=60, max_entries=500000):
    return _scan(roots, _Budget(timeout, max_entries))


def _plan(ids, roots, budget):
    requested = list(ids) if not isinstance(ids, str) else [ids]
    if not requested:
        raise ValueError("Explicit non-empty artifact IDs required")
    resolved = _roots(roots)
    selected = [(_token_path(token), token) for token in requested]
    selected = list(dict.fromkeys(selected))
    report = {"status": "ok", "roots": [str(p) for p in resolved], "targets": [], "rejected_ids": [],
              "coverage": {"complete": True, "entries": 0, "activity_known": False, "errors": [], "skipped": [], "scope": "selected-artifacts"}}
    try:
        activity = _activity(budget)
        report["coverage"]["activity_known"] = activity["known"]
        for path, token in selected:
            budget.check()
            root = _selected_root(path, resolved)
            row = _candidate(path, root, budget, activity)
            if row["id"] != token:
                raise ValueError("Unknown or stale artifact ID; rescan before selection")
            report["targets"].append(row)
        if not activity["known"]:
            report["coverage"]["errors"].append(activity["reason"])
    except OSError as error:
        raise ValueError("Unknown or stale artifact ID: " + str(error)) from None
    except _Bound as error:
        report["coverage"]["complete"] = False
        report["coverage"]["errors"].append(str(error))
    report["coverage"]["entries"] = budget.entries
    if not report["coverage"]["complete"] or not report["coverage"]["activity_known"]:
        report["status"] = "partial"
        for row in report["targets"]:
            row["eligible"], row["blocked_reason"] = False, "selected tree/activity coverage incomplete"
    if any(not row["eligible"] for row in report["targets"]):
        report["status"] = "rejected"
    return report


def plan_codex_artifacts(ids, roots=None, timeout=60, max_entries=500000):
    return _plan(ids, roots, _Budget(timeout, max_entries))


def _finder_onexc(path, parent_fd, directories, budget):
    """One metadata-only ENOTEMPTY retry, beneath the original pinned parent."""
    retried = set()

    def onexc(function, failed, error):
        if function is not os.rmdir or not isinstance(error, OSError) or error.errno != errno.ENOTEMPTY:
            raise error
        relative = Path(failed)
        if relative.is_absolute() or not relative.parts or relative.parts[0] != path.name or any(p in {".", ".."} for p in relative.parts):
            raise error
        if relative in retried:
            raise error
        budget.check()
        retried.add(relative)
        opened = []
        directory_fd = parent_fd
        try:
            # onexc receives a relative path, not shutil's internal dirfd.
            # Reopen each component with NOFOLLOW and bind it to the validated
            # pre-delete directory identities; never resolve a replacement tree.
            for index, component in enumerate(relative.parts):
                snapshot = directories.get(str(Path(*relative.parts[1:index + 1])))
                if snapshot is None or os.path.ismount(path.parent / Path(*relative.parts[:index + 1])):
                    raise error
                child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                opened.append(child_fd)
                current = os.fstat(child_fd)
                if (not os.path.samestat(snapshot, current) or current.st_mode != snapshot.st_mode
                        or getattr(current, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0)):
                    raise error
                directory_fd = child_fd
            with os.scandir(directory_fd) as entries:
                first = next(entries, None)
                if first is None or first.name != ".DS_Store" or next(entries, None) is not None:
                    raise error
            metadata_fd = os.open(".DS_Store", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                if not _finder_store(metadata_fd):
                    raise error
                checked = os.fstat(metadata_fd)
                named = os.stat(".DS_Store", dir_fd=directory_fd, follow_symlinks=False)
                if not os.path.samestat(checked, named):
                    raise error
                os.unlink(".DS_Store", dir_fd=directory_fd)
            finally:
                os.close(metadata_fd)
            # Retry exactly this rmdir once. Any newly arriving data, metadata,
            # or a replacement path propagates its failure; no recursive retry.
            container_fd = opened[-2] if len(opened) > 1 else parent_fd
            named = os.stat(relative.name, dir_fd=container_fd, follow_symlinks=False)
            if not os.path.samestat(named, os.fstat(directory_fd)):
                raise error
            os.rmdir(relative.name, dir_fd=container_fd)
        finally:
            for fd in reversed(opened):
                os.close(fd)

    return onexc


def clean_codex_artifacts(ids, yes=False, roots=None, timeout=60, max_entries=500000):
    if yes is not True:
        raise ValueError("Explicit yes=True confirmation required")
    budget = _Budget(timeout, max_entries)
    report = _plan(ids, roots, budget)
    report["results"] = []
    if report["status"] != "ok":
        report["results"] = [{"id": row["id"], "path": row["path"], "ok": False, "message": row["blocked_reason"]} for row in report["targets"]]
        return report
    if not shutil.rmtree.avoids_symlink_attacks:
        report["status"] = "rejected"
        report["coverage"]["errors"].append("runtime lacks symlink-safe removal")
        return report
    # The selected set is validated above; revalidate each selected tree and live
    # activity immediately before removal without rediscovering unrelated tasks.
    for row in report["targets"]:
        path = Path(row["path"])
        try:
            budget.check()
            if not _no_links(path):
                raise ValueError("symlink traversal refused")
            # Parent FD pins the selected directory's location during removal.
            parent_fd = os.open(path.parent, os.O_RDONLY)
            try:
                directories = {}
                current = _candidate(path, _selected_root(path, _roots(roots)), budget, _activity(budget), directories)
                if current["id"] != row["id"] or not current["eligible"]:
                    raise ValueError("artifact changed or became active")
                shutil.rmtree(path.name, dir_fd=parent_fd, onexc=_finder_onexc(path, parent_fd, directories, budget))
            finally:
                os.close(parent_fd)
            report["results"].append({"id": row["id"], "path": str(path), "status": "removed", "ok": True, "message": "removed"})
        except (OSError, ValueError, _Bound) as error:
            report["status"] = "failed"
            report["results"].append({"id": row["id"], "path": str(path), "status": "retained", "ok": False, "message": str(error)})
            break
    if report["status"] == "ok":
        report["status"] = "cleaned"
    return report
