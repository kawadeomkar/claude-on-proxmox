"""The CI workflow's gate: `CI result`, the one check branch protection requires, holds every other job to it.

Required alone, it fails open where a list of required job names failed closed: a job added to the workflow and left
out of its `needs`, or one allowed to fail, could fail and the pull request still merge. So the job set is held here,
and the job's own script is run on what GitHub would hand it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
JOBS = WORKFLOW["jobs"]
RESULT = JOBS["result"]
# Not part of the gate: it runs on the weekly schedule against the unpinned image and may fail on purpose.
OUTSIDE = {"canary"}


def test_ci_result_needs_every_job_but_the_canary():
    gate = set(JOBS) - {"result"} - OUTSIDE
    assert RESULT["name"] == "CI result"
    assert set(RESULT["needs"]) == gate, (sorted(RESULT["needs"]), sorted(gate))


def test_ci_result_runs_whatever_its_jobs_did():
    # Without always() a failed job skips it, and a skipped required check passes.
    assert RESULT["if"] == "always()"


def test_no_job_of_the_gate_can_fail_and_still_succeed():
    # continue-on-error makes a failure a success in `needs`, whether set on the job or one of its steps.
    for name in set(JOBS) - OUTSIDE:
        job = JOBS[name]
        assert "continue-on-error" not in job, name
        for step in job.get("steps", []):
            assert "continue-on-error" not in step, (name, step.get("name"))


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed (GitHub's runners have it)")
@pytest.mark.parametrize(
    ("results", "passes"),
    [
        ({}, True),
        ({"dependency-review": "skipped"}, True),
        ({"molecule": "failure"}, False),
        ({"playbooks": "cancelled"}, False),
        ({"lint": "skipped", "molecule": "failure"}, False),
    ],
)
def test_ci_result_passes_only_when_each_job_succeeded_or_was_skipped(results, passes):
    (step,) = RESULT["steps"]
    needs = {name: {"result": results.get(name, "success"), "outputs": {}} for name in RESULT["needs"]}
    # As GitHub runs a step's script on ubuntu-latest: bash -e with pipefail.
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env={"PATH": f"{Path(shutil.which('jq')).parent}:/usr/bin:/bin", "NEEDS": json.dumps(needs)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (run.returncode == 0) is passes, (results, run.stdout, run.stderr)
    for name in RESULT["needs"]:
        assert f"{name}: {results.get(name, 'success')}" in run.stdout
