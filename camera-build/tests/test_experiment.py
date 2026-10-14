import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codegen_experiment as probe

ROOT = Path(__file__).resolve().parents[1]


class ExperimentPlanTests(unittest.TestCase):
    def test_manual_exploratory_workflow_has_no_kernel_or_mmrm_build(self):
        workflow = yaml.load(
            (
                ROOT.parent / ".github/workflows/camera-codegen-experiment.yml"
            ).read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(
            workflow["permissions"], {"contents": "read", "actions": "read"}
        )
        self.assertEqual(len(workflow["jobs"]), 1)
        job = workflow["jobs"]["camera"]
        self.assertEqual(job["env"]["CAMERA_EXPLORATORY"], "true")
        self.assertEqual(job["env"]["CAMERA_SUSFS_REF"], probe.SUSFS_SHA)
        self.assertNotIn("GITHUB_TOKEN", job["env"])
        for step in job["steps"]:
            if "uses" in step:
                self.assertRegex(
                    step["uses"], r"^actions/(checkout|upload-artifact)@[0-9a-f]{40}$"
                )
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
                self.assertNotEqual(step["run"], "python3 camera-build/ci.py kernel")
                self.assertNotEqual(step["run"], "python3 camera-build/ci.py mmrm")
                self.assertNotEqual(step["run"], "python3 camera-build/ci.py patched")
        uploads = [
            step
            for step in job["steps"]
            if step.get("with", {}).get("name") == "camera-codegen-experiment"
        ]
        self.assertEqual(len(uploads), 1)
        self.assertIn("always()", uploads[0]["if"])
        self.assertFalse(
            any(
                step.get("with", {}).get("name") == "patched-camera"
                for step in job["steps"]
            )
        )

    def test_full_probe_retains_cfi_protections(self):
        for flag in [
            "-flto",
            "-fsanitize=cfi",
            "-fsanitize-cfi-cross-dso",
            "-fno-sanitize-cfi-canonical-jump-tables",
            "-ftrap-function=__ubsan_handle_cfi_check_fail_abort",
        ]:
            self.assertIn(flag, probe.FULL_FLAGS.split())
        self.assertNotIn("-fsanitize-recover=cfi", probe.FULL_FLAGS)

    def test_stages_are_local_dry_runs_only_without_runner(self):
        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        for stage in probe.STAGES:
            result = subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "codegen_experiment.py"),
                    stage,
                    "--dry-run",
                ],
                text=True,
                env=env,
            )
            plan = json.loads(result)
            self.assertFalse(plan["kernel_build"])
            self.assertFalse(plan["mmrm_build"])
            self.assertFalse(plan["gate_can_open"])
            self.assertFalse(plan["network_or_compilation"])
        result = subprocess.run(
            [sys.executable, str(ROOT / "codegen_experiment.py"), "full"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only on GitHub Actions", result.stderr)


if __name__ == "__main__":
    unittest.main()
