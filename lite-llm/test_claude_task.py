"""Offline tests for prompt isolation and dispatch failure behavior."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import claude_task


class ClaudeTaskTests(unittest.TestCase):
    def test_prompt_and_model_remain_arguments(self):
        prompt = '--settings example.json & harmless; punctuation\nnext line'
        command = claude_task.task_command("claude.exe", prompt, "--example")
        self.assertEqual(command[-2:], ["--", prompt])
        self.assertIn("--model=--example", command)

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            claude_task.task_command("claude.exe", "  ")

    def test_missing_settings_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                claude_task.task_command("claude.exe", "task", settings=Path(folder) / "missing.json")

    @patch("claude_task.subprocess.run")
    @patch("claude_task.shutil.which", return_value="C:/tools/claude.exe")
    def test_dry_run_does_not_launch(self, which, run):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(claude_task.main(["--cwd", folder, "--prompt", "task", "--dry-run"]), 0)
        run.assert_not_called()

    @patch("claude_task.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 60))
    @patch("claude_task.shutil.which", return_value="C:/tools/claude.exe")
    def test_ambiguous_timeout_is_not_retried(self, which, run):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(claude_task.main(["--cwd", folder, "--prompt", "task"]), 2)
        self.assertEqual(run.call_count, 1)
        self.assertIs(run.call_args.kwargs["shell"], False)

    @patch("claude_task.subprocess.run", return_value=subprocess.CompletedProcess([], 7))
    @patch("claude_task.shutil.which", return_value="C:/tools/claude.exe")
    def test_launcher_failure_propagates(self, which, run):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(claude_task.main(["--cwd", folder, "--prompt", "task"]), 7)


if __name__ == "__main__":
    unittest.main()
