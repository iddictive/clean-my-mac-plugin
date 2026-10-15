from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/clean-my-mac/scripts/disk_scanners.py"
SPEC = importlib.util.spec_from_file_location("disk_scanners", SCRIPT)
disk_scanners = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(disk_scanners)


class NativeScannerTests(unittest.TestCase):
    def test_sparse_hardlink_and_symlink_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular.bin"
            regular.write_bytes(b"x" * 4096)
            sparse = root / "sparse.bin"
            with sparse.open("wb") as handle:
                handle.seek(16 * 1024 * 1024)
                handle.write(b"x")
            os.link(regular, root / "regular-hardlink.bin")
            os.symlink(regular, root / "regular-symlink.bin")

            result = disk_scanners.native_scan(root, max_entries=100, timeout=10, top=10)

            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["logical_bytes"] > result["allocated_bytes"])
            self.assertEqual(result["skipped"]["symlinks"], 1)
            self.assertTrue(result["hard_links_counted_once"])
            self.assertTrue(result["same_filesystem"])
            self.assertTrue(result["top_level"])
            self.assertTrue(result["smart_categories"])
            self.assertEqual(result["apfs_clone_evidence"]["status"], "unavailable")

    def test_codex_paths_are_protected(self) -> None:
        protected = Path.home() / ".codex/archived_sessions"
        self.assertTrue(disk_scanners.is_protected_path(protected))
        data_alias = Path("/System/Volumes/Data") / str(protected).lstrip("/")
        self.assertTrue(disk_scanners.is_protected_path(data_alias))
        with self.assertRaisesRegex(ValueError, "Protected Codex path"):
            disk_scanners.native_scan(protected, max_entries=10, timeout=1, top=1)

    def test_missing_adapters_are_nonfatal(self) -> None:
        original_path = os.environ.get("PATH")
        try:
            os.environ["PATH"] = "/nonexistent"
            self.assertEqual(disk_scanners.gdu_scan(Path("/tmp"), 1)["status"], "unavailable")
            self.assertEqual(disk_scanners.dust_scan(Path("/tmp"), 1)["status"], "unavailable")
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path

    def test_adapter_commands_are_read_only_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            argument_log = bin_dir / "arguments.log"
            for name, payload in (("gdu", "2048 /tmp"), ("dust", '{"size": "2050B"}')):
                script = bin_dir / name
                script.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' \"$*\" >> {argument_log!s}\n"
                    f"printf '%s\\n' '{payload}'\n",
                    encoding="utf-8",
                )
                script.chmod(script.stat().st_mode | stat.S_IXUSR)
            original_path = os.environ.get("PATH")
            try:
                os.environ["PATH"] = str(bin_dir)
                gdu = disk_scanners.gdu_scan(Path("/tmp"), 5)
                dust = disk_scanners.dust_scan(Path("/tmp"), 5)
            finally:
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path

            self.assertEqual(gdu["reported_bytes"], 2048)
            self.assertEqual(dust["reported_bytes"], 2050)
            arguments = argument_log.read_text(encoding="utf-8")
            self.assertIn("--no-delete", arguments)
            self.assertIn("--no-view-file", arguments)
            self.assertIn("--no-spawn-shell", arguments)
            self.assertIn("--no-cross", arguments)
            self.assertIn("-nps --no-prefix", arguments)
            self.assertIn("-j -x", arguments)
            self.assertIn("-o b", arguments)
            self.assertIn("-n 100 -d 3", arguments)
            self.assertIn(".codex", arguments)

    def test_gdu_go_fallback_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            script = bin_dir / "gdu-go"
            script.write_text("#!/bin/sh\nprintf '4096 /tmp\n'\n", encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            original_path = os.environ.get("PATH")
            try:
                os.environ["PATH"] = str(bin_dir)
                self.assertIsNone(disk_scanners.shutil.which("gdu"))
                self.assertIsNotNone(disk_scanners._find_binary("gdu"))
                gdu = disk_scanners.gdu_scan(Path("/tmp"), 5)
                self.assertEqual(gdu["status"], "available")
                self.assertEqual(gdu["reported_bytes"], 4096)
            finally:
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path

    def test_apfs_container_free_reporting_oracle(self) -> None:
        evidence = disk_scanners.volume_evidence(Path("/System/Volumes/Data"), timeout=10)
        if evidence.get("status") == "available" and evidence.get("filesystem") == "apfs":
            self.assertIsNotNone(evidence.get("free_bytes"))
            self.assertGreater(evidence["free_bytes"], 0)
            self.assertEqual(evidence.get("volume_free_bytes"), 0)
            self.assertEqual(evidence["free_bytes"], evidence.get("apfs_container_free_bytes"))

    def test_partial_coverage_stop_reason_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(5):
                (root / f"file_{i}.txt").write_text("hello", encoding="utf-8")
            native = disk_scanners.native_scan(root, max_entries=2, timeout=10, top=5)
            self.assertEqual(native["status"], "partial")
            self.assertEqual(native["stop_reason"], "max_entries")
            scan = disk_scanners.deep_scan(root, max_entries=2, timeout=10, top=5)
            self.assertEqual(scan["status"], "partial")
            self.assertEqual(scan["summary"]["coverage"], "partial")
            self.assertEqual(scan["summary"]["stop_reason"], "max_entries")


if __name__ == "__main__":
    unittest.main()
