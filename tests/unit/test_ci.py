"""The CI workflow's gate, and the releases: each workflow's own script run on what GitHub would hand it.

`CI result`, the one check branch protection requires, holds every other job to it. Required alone, it fails open
where a list of required job names failed closed: a job added to the workflow and left out of its `needs`, or one
allowed to fail, could fail and the pull request still merge. So the job set is held here.

A release is an annotated SemVer tag on main (release.yml), its notes the pull requests merged since the last one,
grouped by the label labels.yml gives each from its title's tag (.github/release.yml). Their scripts run here
against a stand-in gh, which records what it was asked, and a scratch repository, never GitHub.
"""

from __future__ import annotations

import json
import os
import re
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


# ----------------------------------------------------------------------- pull requests' labels, and releases ---

LABELS = yaml.safe_load((REPO / ".github" / "workflows" / "labels.yml").read_text())
RELEASE = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
NOTES = yaml.safe_load((REPO / ".github" / "release.yml").read_text())
NEEDS_JQ = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed (GitHub's runners have it)")


def _title_tags() -> list[str]:
    """The tags CONTRIBUTING.md asks a pull request's title to start with, in its order."""
    text = (REPO / "CONTRIBUTING.md").read_text()
    paragraph = text[text.index("**PR titles**") :].split("\n\n", 1)[0]
    return list(dict.fromkeys(re.findall(r"`\[(\w+)\]", paragraph)))


def _step(workflow: dict, job: str) -> str:
    (step,) = [step for step in workflow["jobs"][job]["steps"] if "run" in step]
    return step["run"]


def _stand_in_gh(tmp_path: Path) -> Path:
    """A gh that records each call, a line of its arguments, and does nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_CALLS"\n')
    (bin_dir / "gh").chmod(0o755)
    return bin_dir


def _run(script: str, tmp_path: Path, cwd: Path, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """script as GitHub runs a step's on ubuntu-latest, bash -e with pipefail, and the calls it made of gh."""
    calls = tmp_path / "gh-calls"
    calls.unlink(missing_ok=True)
    if not (tmp_path / "bin").exists():
        _stand_in_gh(tmp_path)
    path = [str(tmp_path / "bin")]
    path += [str(Path(found).parent) for found in (shutil.which("jq"), shutil.which("git")) if found]
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env={"PATH": ":".join([*path, "/usr/bin", "/bin"]), "GH_CALLS": str(calls), "HOME": str(tmp_path), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    return run, calls.read_text().splitlines() if calls.exists() else []


def _label(tmp_path: Path, title: str, labels=(), author: str = "octocat"):
    env = {"REPO": "o/r", "NUMBER": "7", "TITLE": title, "AUTHOR": author, "LABELS": json.dumps(list(labels))}
    return _run(_step(LABELS, "label"), tmp_path, tmp_path, **env)


def test_the_labels_are_written_by_the_base_branchs_workflow_with_nothing_of_the_pull_requests():
    # pull_request_target hands a token that writes: nothing of the pull request may be checked out, and its title
    # reaches the script as a variable, never inside it.
    assert set(LABELS["on"]) == {"pull_request_target"}
    assert LABELS["permissions"] == {"pull-requests": "write"}
    (step,) = LABELS["jobs"]["label"]["steps"]
    assert "uses" not in step and "${{" not in step["run"]
    assert step["env"]["TITLE"] == "${{ github.event.pull_request.title }}"


@NEEDS_JQ
def test_every_title_tag_is_a_label_and_every_label_a_section_of_the_notes(tmp_path):
    tags = _title_tags()
    assert tags == ["feat", "fix", "cleanup", "docs", "test", "ci", "build"], tags
    sections = [category["labels"] for category in NOTES["changelog"]["categories"]]
    assert sections[-1] == ["*"], "the last section takes every pull request no other one does"
    for tag in tags:
        run, calls = _label(tmp_path, f"[{tag}] A change")
        assert run.returncode == 0, run.stderr
        (call,) = calls
        label = call.rsplit("labels[]=", 1)[1]
        assert any(label in labels for labels in sections[:-1]), (tag, label)


@NEEDS_JQ
@pytest.mark.parametrize(
    ("title", "labels", "calls"),
    [
        ("[feat] Draw just test live", [], ["POST repos/o/r/issues/7/labels -f labels[]=enhancement"]),
        # A stacked pull request's title: its first kind of change.
        ("[1/2] [VSCodeRemote] [feat] open each VM", [], ["POST repos/o/r/issues/7/labels -f labels[]=enhancement"]),
        # Retagged: the old tag's label goes, and a label of another kind stays.
        (
            "[fix] Hold Ctrl-C off",
            ["enhancement", "python"],
            ["DELETE repos/o/r/issues/7/labels/enhancement", "POST repos/o/r/issues/7/labels -f labels[]=bug"],
        ),
        ("[docs] Show just status", ["documentation"], []),
        ("[ci] Shard the unit tests", [], ["POST repos/o/r/issues/7/labels -f labels[]=ci"]),
    ],
)
def test_a_pull_request_is_labelled_as_its_titles_tag_says(tmp_path, title, labels, calls):
    run, made = _label(tmp_path, title, labels)
    assert run.returncode == 0, run.stderr
    assert made == [f"api --method {call}" for call in calls]


@NEEDS_JQ
def test_a_title_without_a_tag_is_warned_of_and_its_old_label_taken_off(tmp_path):
    run, made = _label(tmp_path, "Draw just test live", ["enhancement"])
    assert run.returncode == 0, run.stderr
    assert made == ["api --method DELETE repos/o/r/issues/7/labels/enhancement"]
    assert "::warning::The title has none of [feat]" in run.stdout


@NEEDS_JQ
def test_dependabots_pull_requests_are_left_as_it_labels_them(tmp_path):
    run, made = _label(tmp_path, "build(deps): Bump ruff from 0.16.10 to 0.17.0", ["dependencies"], "dependabot[bot]")
    assert run.returncode == 0 and made == [] and run.stdout == "", (run.stdout, run.stderr)


@NEEDS_JQ
def test_a_title_is_never_run(tmp_path):
    title = '[feat] $(touch ran-1) `touch ran-2` "; touch ran-3; "'
    run, made = _label(tmp_path, title)
    assert run.returncode == 0, run.stderr
    assert made == ["api --method POST repos/o/r/issues/7/labels -f labels[]=enhancement"]
    assert not list(tmp_path.glob("ran-*"))


def _git(cwd: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def tagged(tmp_path_factory) -> Path:
    """An origin with main, a branch off it, and a tag of each kind; and a clone of it as actions/checkout leaves
    one: every branch and tag fetched, each tag lightweight, since it fetches a pushed tag by its commit."""
    root = tmp_path_factory.mktemp("release")
    origin, work, checkout = root / "origin.git", root / "work", root / "checkout"
    _git(root, "init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    _git(root, "clone", "--quiet", str(origin), str(work))
    _git(work, "commit", "--quiet", "--allow-empty", "-m", "one")
    _git(work, "push", "--quiet", "origin", "HEAD:main")
    _git(work, "tag", "-a", "v0.1.0", "-m", "First release\n\nEvery VM in one command.")
    _git(work, "tag", "-a", "v1.0.0-rc.1", "-m", "The first candidate")
    _git(work, "tag", "-a", "v1.2", "-m", "Two parts")
    _git(work, "tag", "v0.2.0")
    _git(work, "checkout", "--quiet", "-b", "side")
    _git(work, "commit", "--quiet", "--allow-empty", "-m", "off main")
    _git(work, "push", "--quiet", "origin", "side")
    _git(work, "tag", "-a", "v0.3.0", "-m", "Off main")
    _git(work, "push", "--quiet", "origin", "--tags")
    _git(root, "clone", "--quiet", str(origin), str(checkout))
    for tag in _git(checkout, "tag", "--list").split():
        _git(checkout, "tag", "--force", tag, f"{tag}^{{commit}}")
    return checkout


def _release(tmp_path: Path, checkout: Path, tag: str):
    runner = tmp_path / "runner"
    runner.mkdir()
    run, calls = _run(_step(RELEASE, "release"), tmp_path, checkout, TAG=tag, RUNNER_TEMP=str(runner))
    _git(checkout, "tag", "--force", tag, f"{tag}^{{commit}}")  # as the next run's checkout finds it
    return run, calls, runner


def test_an_annotated_semver_tag_on_main_is_released_its_message_first(tmp_path, tagged):
    run, calls, runner = _release(tmp_path, tagged, "v0.1.0")
    assert run.returncode == 0, (run.stdout, run.stderr)
    notes = runner / "notes.md"
    assert calls == [f"release create v0.1.0 --verify-tag --title v0.1.0 --notes-file {notes} --generate-notes"]
    assert notes.read_text().startswith("First release\n\nEvery VM in one command.")


def test_a_prerelease_is_released_as_one(tmp_path, tagged):
    run, calls, _ = _release(tmp_path, tagged, "v1.0.0-rc.1")
    assert run.returncode == 0, (run.stdout, run.stderr)
    (call,) = calls
    assert call.startswith("release create v1.0.0-rc.1 ") and call.endswith(" --generate-notes --prerelease")


@pytest.mark.parametrize(
    ("tag", "said"),
    [
        ("v0.2.0", "is a lightweight tag"),
        ("v0.3.0", "is on a commit main does not have"),
        ("v1.2", "is not vMAJOR.MINOR.PATCH"),
    ],
)
def test_a_tag_that_is_no_release_is_refused_before_anything_is_published(tmp_path, tagged, tag, said):
    run, calls, _ = _release(tmp_path, tagged, tag)
    assert run.returncode == 1 and calls == [], (run.stdout, run.stderr, calls)
    assert f"::error::{tag} {said}" in run.stdout
