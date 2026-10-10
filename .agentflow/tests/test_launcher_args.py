"""The launcher's model flags, run in real PowerShell from run-task.ps1 itself (skipped where pwsh is missing).
Regression: a machine default `--model` plus a task `Model:` made Devin exit ("--model cannot be used multiple times")."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


def functions():
    s = (FLOW / "tools" / "run-task.ps1").read_text(encoding="utf-8")
    return s[s.index("function Remove-Overridden"):s.index("function Now {")]


@unittest.skipUnless(PWSH, "PowerShell 7 not installed")
class LauncherArgsTests(unittest.TestCase):
    def run_case(self, tool, model, effort, env=None, codex_args=None):
        script = functions() + f"""
$t = [pscustomobject]@{{ model = {json.dumps(model) if model else '$null'}; effort = {json.dumps(effort) if effort else '$null'} }}
$codexArgs = @({', '.join(json.dumps(a) for a in (codex_args or []))})
if ('{tool}' -eq 'codex') {{   # as the worker does it: filter the machine codex args, then add the task's flags
  $codexArgs = Remove-Overridden $codexArgs $t
  $extra = Get-ModelArgs 'codex' $t
  $x = @($codexArgs) + @($extra)
}} else {{ $x = Get-ModelArgs '{tool}' $t }}
ConvertTo-Json -Compress @($x)
"""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "probe.ps1"
            f.write_text(script, encoding="utf-8")
            e = {k: v for k, v in os.environ.items() if not k.startswith("AGENTFLOW_")}
            e.update(env or {})
            r = subprocess.run([PWSH, "-NoProfile", "-File", str(f)], capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip() or "[]")
        return out if isinstance(out, list) else [out]

    def test_task_model_replaces_machine_default(self):
        args = self.run_case("devin", "swe-2-max", None, {"AGENTFLOW_DEVIN_ARGS": "--model swe-2-high --foo bar"})
        self.assertEqual(args.count("--model"), 1)
        self.assertEqual(args, ["--foo", "bar", "--model", "swe-2-max"])

    def test_machine_default_stays_without_task_model(self):
        args = self.run_case("devin", None, None, {"AGENTFLOW_DEVIN_ARGS": "--model swe-2-high"})
        self.assertEqual(args, ["--model", "swe-2-high"])

    def test_codex_effort_from_task_wins(self):
        args = self.run_case("codex", "gpt-6.1-sol", "medium", codex_args=["-c", "model_reasoning_effort=xhigh", "-c", "x=1"])
        self.assertNotIn("model_reasoning_effort=xhigh", args)
        self.assertEqual(args, ["-c", "x=1", "-m", "gpt-6.1-sol", "-c", "model_reasoning_effort=medium"])

    def test_codex_default_effort_kept_without_task_effort(self):
        args = self.run_case("codex", None, None, codex_args=["-c", "model_reasoning_effort=xhigh"])
        self.assertEqual(args, ["-c", "model_reasoning_effort=xhigh"])

    def test_agy_model_and_effort(self):
        args = self.run_case("agy", "m1", "high", {"AGENTFLOW_AGY_ARGS": "--effort low"})
        self.assertEqual(args, ["--model", "m1", "--effort", "high"])

    def test_claude_model_and_effort(self):
        args = self.run_case("claude", "opus", "xhigh", {"AGENTFLOW_CLAUDE_ARGS": "--effort medium"})
        self.assertEqual(args, ["--model", "opus", "--effort", "xhigh"])


if __name__ == "__main__":
    unittest.main()
