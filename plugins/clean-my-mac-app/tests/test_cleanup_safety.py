from __future__ import annotations

import sys
import os
import subprocess
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).parents[1] / "skills/clean-my-mac/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import clean_my_mac  # noqa: E402
import codex_artifacts  # noqa: E402

FINDER_STORE = struct.pack(">I4sIII", 1, b"Bud1", 4096, 2048, 4096) + bytes(6148 - 20)


class CleanupSafetyTests(unittest.TestCase):
    def test_codex_targets_never_enter_cleanup_selection(self) -> None:
        selected = clean_my_mac.selected_targets([])
        self.assertFalse(any(clean_my_mac.is_protected_path(target.path) for target in selected))
        self.assertFalse(any(target.category == "codex-protected" for target in selected))

    def test_remove_path_refuses_codex_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "Protected Codex path"):
            clean_my_mac.remove_path(Path.home() / ".codex/archived_sessions")

    def test_selected_artifact_retention_matrix(self) -> None:
        # One deletion invariant: only selected, current, reproducible and idle
        # ignored artifacts can disappear; task source and deliverables survive.
        scenarios = ("download", "tracked", "source", "outputs", "unique-checkpoint", "modified-download", "symlink", "active", "unknown-activity", "stale", "node", "python", "node-link", "python-link", "cargo-link", "unknown-link", "finder-update", "finder-payload-change", "finder-invalid-change", "finder-rmdir-root", "finder-rmdir-nested", "finder-rmdir-file", "finder-rmdir-invalid", "finder-rmdir-symlink", "finder-rmdir-mount")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary).resolve() / "task"
                repo.mkdir()
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                (repo / ".gitignore").write_text("work/*\noutputs/\nsource/\n")
                (repo / "work").mkdir()
                (repo / "outputs").mkdir()
                original = repo / "outputs/original.png"
                original.write_bytes(b"original")
                source = repo / "work/source.py"
                source.write_text("print('retain')\n")
                bundle = repo / "work/weights/model"
                if scenario in {"source", "outputs"}:
                    bundle = repo / scenario / "model"
                bundle.mkdir(parents=True)
                payload = bundle / "weights.safetensors"
                payload.write_bytes(b"downloaded immutable weights")
                metadata = bundle / ".cache/huggingface/download/weights.safetensors.metadata"
                metadata.parent.mkdir(parents=True)
                metadata.write_text("a" * 40 + "\n" + "b" * 64 + "\n" + str(time.time() + 1) + "\n")
                cache = bundle / ".cache/huggingface"
                (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
                (cache / "trees").mkdir()
                (cache / "trees" / ("a" * 40 + ".json")).write_text('{"format_version":1,"files":{}}')
                (bundle / ".DS_Store").write_bytes(FINDER_STORE)
                (repo / "work/README.md").write_text("Download owner/model at " + "a" * 40 + "\n")
                if scenario == "tracked":
                    subprocess.run(["git", "-C", str(repo), "add", "-f", str(payload)], check=True)
                elif scenario == "unique-checkpoint":
                    (bundle / "trained.ckpt").write_bytes(b"unique training state")
                elif scenario == "modified-download":
                    future = time.time() + 10
                    os.utime(payload, (future, future))
                elif scenario == "symlink":
                    (bundle / "original-link").symlink_to(original)
                elif scenario in {"node", "python", "node-link", "python-link", "cargo-link", "unknown-link"} or scenario.startswith("finder-"):
                    kind = "node" if scenario.startswith("finder-") else scenario.split("-")[0]
                    bundle = repo / "work" / {"node": "node_modules", "python": ".venv", "cargo": "target", "unknown": "node_modules"}[kind]
                    bundle.mkdir()
                    (bundle / "installed.bin").write_bytes(b"generated")
                    if kind == "node":
                        (repo / "work/package.json").write_text('{"dependencies":{"a":"1.0.0"}}')
                        (repo / "work/package-lock.json").write_text('{"lockfileVersion":3}')
                    elif scenario.startswith("cargo"):
                        (repo / "work/Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
                        (repo / "work/Cargo.lock").write_text("version = 3\n")
                    elif scenario.startswith("python"):
                        (bundle / "pyvenv.cfg").write_text("home=/usr/bin\n")
                        (repo / "work/requirements.txt").write_text("example==1.0\n")
                    if scenario.endswith("-link"):
                        (bundle / "external-source").symlink_to(source)
                        (bundle / "external-originals").symlink_to(repo / "outputs", target_is_directory=True)
                        (bundle / "dangling-link").symlink_to(repo / "absent")
                    if scenario.startswith("finder-"):
                        (bundle / "cache").mkdir()
                        (bundle / "cache/generated.js").write_text("generated")
                activity = {"known": scenario != "unknown-activity", "reason": "activity unknown", "paths": [(repo, True)] if scenario == "active" else []}
                with patch.object(codex_artifacts, "_activity", return_value=activity):
                    scan = codex_artifacts.scan_codex_artifacts([repo])
                    targets = [row for row in scan["targets"] if row["path"] == str(bundle)]
                    if scenario in {"source", "outputs"}:
                        self.assertEqual(targets, [])
                        self.assertTrue(payload.exists())
                        continue
                    self.assertEqual(len(targets), 1)
                    row = targets[0]
                    allowed = scenario in {"download", "stale", "node", "python", "node-link", "python-link", "cargo-link"} or scenario.startswith("finder-")
                    self.assertEqual(row["eligible"], allowed, row)
                    self.assertGreater(row["logical_bytes"], 0)
                    self.assertIsInstance(row["allocated_bytes"], int)
                    if scenario in {"stale", "finder-payload-change", "finder-invalid-change"}:
                        changed = payload if scenario == "stale" else bundle / ("new-source.py" if scenario == "finder-payload-change" else ".DS_Store")
                        changed.write_bytes(b"changed meaningful content")
                        with self.assertRaisesRegex(ValueError, "stale"):
                            codex_artifacts.clean_codex_artifacts([row["id"]], yes=True, roots=[repo])
                        self.assertTrue(bundle.exists())
                    elif scenario.startswith("finder-rmdir-"):
                        native_rmdir, native_ismount = os.rmdir, os.path.ismount
                        target = bundle / "cache" if scenario == "finder-rmdir-nested" else bundle
                        injected = []

                        def arriving_file(name, *, dir_fd=None):
                            if str(name) == target.name and not injected:
                                injected.append(True)
                                late = target / ("new-source.py" if scenario == "finder-rmdir-file" else ".DS_Store")
                                if scenario == "finder-rmdir-symlink":
                                    late.symlink_to(source)
                                else:
                                    late.write_bytes(b"unique source" if scenario in {"finder-rmdir-file", "finder-rmdir-invalid"} else FINDER_STORE)
                            return native_rmdir(name, dir_fd=dir_fd)

                        def mounted(path):
                            return bool(scenario == "finder-rmdir-mount" and injected and Path(path) == target) or native_ismount(path)

                        with patch.object(os, "rmdir", side_effect=arriving_file), patch.object(os.path, "ismount", side_effect=mounted):
                            result = codex_artifacts.clean_codex_artifacts([row["id"]], yes=True, roots=[repo])
                        removed = scenario in {"finder-rmdir-root", "finder-rmdir-nested"}
                        self.assertTrue(injected)
                        self.assertEqual(bundle.exists(), not removed, result)
                        self.assertEqual(result["status"], "cleaned" if removed else "failed", result)
                        if not removed:
                            late = target / ("new-source.py" if scenario == "finder-rmdir-file" else ".DS_Store")
                            self.assertTrue(late.exists())
                    else:
                        if scenario == "finder-update":
                            (bundle / ".DS_Store").write_bytes(FINDER_STORE)
                            (bundle / "cache/.DS_Store").write_bytes(FINDER_STORE)
                            plan = codex_artifacts.plan_codex_artifacts([row["id"]], roots=[repo])
                            self.assertEqual(plan["status"], "ok", plan)
                        result = codex_artifacts.clean_codex_artifacts([row["id"]], yes=True, roots=[repo])
                        self.assertEqual(bundle.exists(), not allowed, result)
                        self.assertEqual(result["status"], "cleaned" if allowed else "rejected", result)
                    self.assertEqual(original.read_bytes(), b"original")
                    self.assertTrue(source.exists())

    def test_artifact_selection_and_coverage_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "task"
            root.mkdir()
            (root / "work").mkdir()
            for ids, yes, message in (([], True, "non-empty"), (["unknown"], True, "stale"), (["unknown"], False, "yes=True")):
                with self.subTest(ids=ids, yes=yes), self.assertRaisesRegex(ValueError, message):
                    codex_artifacts.clean_codex_artifacts(ids, yes=yes, roots=[root])
            with patch.object(codex_artifacts, "_activity", return_value={"known": True, "reason": "", "paths": []}):
                report = codex_artifacts.scan_codex_artifacts([root], max_entries=1)
            self.assertEqual(report["status"], "partial")
            self.assertFalse(report["coverage"]["complete"])
            alias = root.parent / "alias"
            alias.symlink_to(root)
            with self.assertRaisesRegex(ValueError, "symlink"):
                codex_artifacts.scan_codex_artifacts([alias])
            with self.assertRaises(ValueError):
                codex_artifacts.scan_codex_artifacts([Path.home() / ".codex"])
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("work/*\n")
            (root / "work/package.json").write_text('{"dependencies":{"a":"1.0.0"}}')
            (root / "work/package-lock.json").write_text('{"lockfileVersion":3}')
            modules = root / "work/node_modules"
            modules.mkdir()
            (modules / "installed.js").write_text("generated")
            idle = {"known": True, "reason": "", "paths": []}
            with patch.object(codex_artifacts, "_activity", return_value=idle):
                row = codex_artifacts.scan_codex_artifacts([root])["targets"][0]
                unrelated = root / "work/unrelated-large-tree"
                unrelated.mkdir()
                for number in range(64):
                    (unrelated / str(number)).write_text("unrelated")
                scandir = os.scandir

                def selected_only(path):
                    self.assertFalse(Path(path) == unrelated or unrelated in Path(path).parents)
                    return scandir(path)

                with patch.object(codex_artifacts.os, "scandir", side_effect=selected_only):
                    plan = codex_artifacts.plan_codex_artifacts([row["id"]], roots=[root], max_entries=10)
                self.assertEqual(plan["status"], "ok", plan)
                self.assertEqual(plan["coverage"]["scope"], "selected-artifacts")
                self.assertLess(plan["coverage"]["entries"], 10)
                fake_digest = "0" * 64
                tokens = {
                    "checksum": row["id"][:-1] + ("0" if row["id"][-1] != "0" else "1"),
                    "protected": codex_artifacts._token(root / "outputs/node_modules", fake_digest),
                    "outside": codex_artifacts._token(root.parent / "outside/node_modules", fake_digest),
                    "whole-task": codex_artifacts._token(root, fake_digest),
                    "symlink-ancestor": codex_artifacts._token(root / "link/node_modules", fake_digest),
                }
                (root / "link").symlink_to(root / "work", target_is_directory=True)
                for label, token in tokens.items():
                    with self.subTest(token=label), self.assertRaises(ValueError):
                        codex_artifacts.plan_codex_artifacts([token], roots=[root])
                self.assertTrue((modules / "installed.js").exists())


if __name__ == "__main__":
    unittest.main()
