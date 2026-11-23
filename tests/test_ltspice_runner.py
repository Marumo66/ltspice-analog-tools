import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.ltspice_runner import (
    LTspiceRunner,
    LTspiceRunnerError,
    find_ltspice_executable,
)


class TestLTspiceRunner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.executable = self.root / "fake-ltspice"
        self.executable.write_text("fake executable", encoding="utf-8")
        self.netlist = self.root / "opamp.net"
        self.netlist.write_text("* minimal test netlist\n.end\n", encoding="utf-8")
        self.runner = LTspiceRunner(self.executable, timeout=5)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builds_batch_command_for_netlist(self):
        command = self.runner.build_command(self.netlist)
        self.assertEqual(command[0], str(self.executable.resolve()))
        self.assertEqual(command[1:3], ("-b", "-Run"))
        self.assertEqual(command[3], str(self.netlist.resolve()))

    def test_rejects_schematic_input(self):
        schematic = self.root / "opamp.asc"
        schematic.write_text("Version 4\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"\.net files only"):
            self.runner.build_command(schematic)

    def test_rejects_missing_netlist(self):
        with self.assertRaises(FileNotFoundError):
            self.runner.build_command(self.root / "missing.net")

    @patch("src.ltspice_runner.subprocess.run")
    def test_run_returns_fresh_raw_and_log(self, mocked_run):
        def simulate(command, **kwargs):
            self.netlist.with_suffix(".raw").write_bytes(b"raw result")
            self.netlist.with_suffix(".log").write_text("simulation ok\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "batch done", "")

        mocked_run.side_effect = simulate
        result = self.runner.run_netlist(self.netlist)
        self.assertTrue(result.raw_path.is_file())
        self.assertTrue(result.log_path.is_file())
        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "batch done")
        mocked_run.assert_called_once()
        self.assertEqual(
            Path(mocked_run.call_args.kwargs["cwd"]),
            self.root.resolve(),
        )

    @patch("src.ltspice_runner.subprocess.run")
    def test_rejects_zero_exit_without_output_files(self, mocked_run):
        mocked_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with self.assertRaisesRegex(LTspiceRunnerError, "missing"):
            self.runner.run_netlist(self.netlist)

    @patch("src.ltspice_runner.subprocess.run")
    def test_rejects_stale_output_files(self, mocked_run):
        raw = self.netlist.with_suffix(".raw")
        log = self.netlist.with_suffix(".log")
        raw.write_bytes(b"old raw")
        log.write_text("old log", encoding="utf-8")
        old_raw = raw.stat().st_mtime_ns
        old_log = log.stat().st_mtime_ns

        def no_update(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "", "")

        mocked_run.side_effect = no_update
        with self.assertRaisesRegex(LTspiceRunnerError, "not updated"):
            self.runner.run_netlist(self.netlist)
        self.assertEqual(raw.stat().st_mtime_ns, old_raw)
        self.assertEqual(log.stat().st_mtime_ns, old_log)

    @patch("src.ltspice_runner.subprocess.run")
    def test_nonzero_exit_raises(self, mocked_run):
        mocked_run.return_value = subprocess.CompletedProcess([], 2, "", "bad netlist")
        with self.assertRaisesRegex(LTspiceRunnerError, "non-zero exit code"):
            self.runner.run_netlist(self.netlist)

    @patch("src.ltspice_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("ltspice", 5))
    def test_timeout_raises(self, mocked_run):
        with self.assertRaisesRegex(LTspiceRunnerError, "did not finish"):
            self.runner.run_netlist(self.netlist)

    @patch.dict(os.environ, {"LTSPICE_EXECUTABLE": ""})
    @patch("src.ltspice_runner.platform.system", return_value="Darwin")
    @patch("src.ltspice_runner.shutil.which")
    def test_empty_executable_env_var_allows_discovery_to_fail(
        self, mocked_which, mocked_system
    ):
        mocked_which.return_value = None
        with self.assertRaisesRegex(LTspiceRunnerError, "Could not locate"):
            find_ltspice_executable()

    def test_rejects_non_positive_timeout(self):
        with self.assertRaisesRegex(ValueError, "timeout"):
            LTspiceRunner(self.executable, timeout=0)


if __name__ == "__main__":
    unittest.main()
