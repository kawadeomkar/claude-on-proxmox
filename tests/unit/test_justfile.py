"""The justfile's guards must keep working, or a regression ships green.

Every recipe is a bash script with no dependencies, which refuses a run
started in another checkout, checks its own arguments, then what it needs,
and only then does its work (justfile, header). Nothing but `just` itself
exercises that, so these tests run the real `just` - the one on PATH, at least
the version the justfile pins - against stand-ins: BIN points at a directory of
tools that only record their arguments, VAULT_FILE at a stub that only looks
encrypted, LOG_DIR into tmp_path. Nothing could reach a real ansible-playbook
even if a guard regressed.

- just holds each argument to its [arg] pattern before anything runs, so a
  quote, a space, a shell metacharacter or a newline never reaches a script;
  the script checks what goes into JSON again (tools/check names, project).
- The names reach Ansible as one JSON extra-var. `-e vm_name=...` splits on
  whitespace and once silently provisioned half a fleet.
- The rules - --tags beside names, --check with --only, a stage as a VM name
  - are the first lines of each script, said before any check or tool, and
  nothing can switch them off: there is no dependency for JUST_NO_DEPS to skip.

On macOS the recipes run under /bin/bash, 3.2, so what passes here passes for
anyone without a newer bash; CI's Ubuntu runs them under bash 5.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import pty
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
JUSTFILE = REPO / "justfile"
JUST = shutil.which("just")
VAULT_HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"
# The recipes that run a playbook, and the one each runs without options.
PLAYBOOK_RECIPES = {
    "deploy": "deploy.yml",
    "code": "playbooks/code.yml",
    "login": "playbooks/login.yml",
    "list": "playbooks/list.yml",
    "destroy": "playbooks/destroy.yml",
}
NAMES_PATTERN = "[A-Za-z0-9][A-Za-z0-9._,-]*"
NOT_MATCHED = "does not match pattern"


def _pinned() -> str:
    return re.search(r"(?m)^just_pin := '([0-9.]+)'$", JUSTFILE.read_text()).group(1)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


if JUST is None:
    pytest.fail(f"no just on PATH: install just {_pinned()} or later (docs/getting-started/requirements.md)")
_found = subprocess.run([JUST, "--version"], capture_output=True, text=True, check=True).stdout.split()[1]
if _version(_found) < _version(_pinned()):
    pytest.fail(f"just {_found} is older than the {_pinned()} the justfile takes: install a newer one")


def _bash_dir() -> str | None:
    """On macOS, a directory whose bash is /bin/bash, 3.2, put first on every run's PATH."""
    if sys.platform != "darwin" or not Path("/bin/bash").exists():
        return None
    global _BASH_DIR
    if "_BASH_DIR" not in globals():
        _BASH_DIR = tempfile.mkdtemp(prefix="just-bash-")
        os.symlink("/bin/bash", os.path.join(_BASH_DIR, "bash"))
        atexit.register(shutil.rmtree, _BASH_DIR, ignore_errors=True)
    return _BASH_DIR


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    # A clean environment: a developer's own VM_NAME, TAGS or JUST_NO_DEPS never
    # reaches a run, and the locale is fixed. extra adds to it.
    path = os.environ["PATH"]
    if _bash_dir():
        path = f"{_bash_dir()}{os.pathsep}{path}"
    return {"PATH": path, "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"} | (extra or {})


def _merged(settings) -> list[str]:
    """NAME=value settings, a later one for the same name replacing an earlier one."""
    merged: dict[str, str] = {}
    for setting in settings:
        merged[setting.split("=", 1)[0]] = setting
    return list(merged.values())


def _just(*args: str, settings=(), env=None, stdin=subprocess.DEVNULL, cwd=REPO) -> subprocess.CompletedProcess[str]:
    """just with its settings (NAME=value, before the recipe) and then the recipe's arguments."""
    return subprocess.run(
        [JUST, *_merged(settings), *args],
        cwd=cwd,
        env=_env(env),
        stdin=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _stand_in(bin_dir: Path, name: str, body: str = "", rc: int = 0) -> Path:
    """A tool in bin_dir that appends its arguments, one per line and then --end--, to <tmp>/<name>.args, and its
    name to <tmp>/order; then runs body and exits rc. Returns the record."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    record = bin_dir.parent / f"{name}.args"
    order = bin_dir.parent / "order"
    tool = bin_dir / name
    tool.write_text(
        "#!/bin/sh\n"
        f'{{ for arg in "$@"; do printf "%s\\n" "$arg"; done; echo --end--; }} >> "{record}"\n'
        f'echo "{name}" >> "{order}"\n'
        f"{body}\n"
        f"exit {rc}\n"
    )
    tool.chmod(0o755)
    return record


def _runs(tmp_path: Path, name: str) -> list[list[str]]:
    """Each run of a stand-in, its arguments as a list."""
    record = tmp_path / f"{name}.args"
    text = record.read_text() if record.exists() else ""
    return [run.splitlines() for run in text.split("--end--\n")[:-1]]


def _vault(tmp_path: Path, content: str = VAULT_HEADER) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault.yml"
    vault.write_text(content)
    return vault


def _safe(tmp_path: Path, galaxy_rc: int = 0) -> list[str]:
    """A run nothing can harm: tools with no ansible-playbook among them, a vault stub, logs in tmp_path."""
    _stand_in(tmp_path / "bin", "ansible-galaxy", rc=galaxy_rc)
    return [f"BIN={tmp_path / 'bin'}", f"VAULT_FILE={_vault(tmp_path)}", f"LOG_DIR={tmp_path / 'logs'}"]


def _recording(tmp_path: Path, playbook_body: str = "") -> list[str]:
    """As _safe, with an ansible-playbook that records its arguments (_runs(tmp_path, "ansible-playbook"))."""
    _stand_in(tmp_path / "bin", "ansible-playbook", playbook_body)
    return _safe(tmp_path)


def _playbook(tmp_path: Path, *args: str, env=None, stdin=subprocess.DEVNULL) -> list[str]:
    """The arguments of the one ansible-playbook run of a recipe that succeeds."""
    result = _just(*args, settings=_recording(tmp_path), env=env, stdin=stdin)
    assert result.returncode == 0, _output(result)
    runs = _runs(tmp_path, "ansible-playbook")
    assert len(runs) == 1, runs
    return runs[0]


def _refused(tmp_path: Path, *args: str, settings=(), env=None) -> str:
    """The output of a run that is refused before any ansible-playbook."""
    result = _just(*args, settings=[*_recording(tmp_path), *settings], env=env)
    assert result.returncode != 0, _output(result)
    assert _runs(tmp_path, "ansible-playbook") == [], _runs(tmp_path, "ansible-playbook")
    return _output(result)


def _json() -> dict:
    return json.loads(subprocess.run([JUST, "--json"], cwd=REPO, capture_output=True, text=True, check=True).stdout)


def _public() -> dict[str, dict]:
    return {name: recipe for name, recipe in _json()["recipes"].items() if not recipe["private"]}


def _evaluate(name: str) -> str:
    return subprocess.run([JUST, "--evaluate", name], cwd=REPO, capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- the justfile itself ----


def test_the_pinned_version_is_one_version() -> None:
    # set minimum-version can only be a literal, so the pin is written three times.
    text = JUSTFILE.read_text()
    minimum = re.search(r"(?m)^set minimum-version := '([0-9.]+)'$", text).group(1)
    action = yaml.safe_load((REPO / ".github" / "actions" / "setup" / "action.yml").read_text())
    ci = next(
        str(step["env"]["JUST_VERSION"]) for step in action["runs"]["steps"] if "JUST_VERSION" in step.get("env", {})
    )
    assert minimum == _pinned() == ci, (minimum, _pinned(), ci)


def test_no_recipe_has_a_dependency() -> None:
    # JUST_NO_DEPS or --no-deps would skip one: every check is a line of the script.
    assert {name: recipe["dependencies"] for name, recipe in _json()["recipes"].items() if recipe["dependencies"]} == {}


def test_no_assignment_starts_a_program() -> None:
    # just evaluates what a recipe uses before any of its lines runs, so a backtick or a shell() would start
    # before a refusal - and a Python would, before env-check could say it is no virtualenv's.
    text = JUSTFILE.read_text()
    assert "`" not in re.sub(r"(?m)#.*$", "", text).replace("``", "")
    assert not re.search(r"\bshell\(", text)


def test_nothing_unstable_and_no_dotenv() -> None:
    settings = _json()["settings"]
    assert not settings["unstable"]
    # A .env in the checkout is the developer's own.
    assert not settings["dotenv_load"]


def test_every_public_recipe_says_what_it_does_and_every_option_what_it_is() -> None:
    for name, recipe in _public().items():
        # A comment above a recipe becomes its doc by its last line only, so the doc is an attribute.
        assert any("doc" in attribute for attribute in recipe["attributes"]), name
        assert name == "help" or any("group" in attribute for attribute in recipe["attributes"]), name
        for parameter in recipe["parameters"]:
            assert parameter["help"], (name, parameter["name"])


def test_every_recipe_whose_playbook_can_prompt_continues_past_a_ctrl_c_it_survives() -> None:
    # Ansible's pause takes Ctrl-C then C to go on, or A to end: without [continue], just ends the run for
    # the Ctrl-C itself (test_without_continue_just_ends_a_run_the_playbook_survived).
    continuing = {
        name for name, recipe in _public().items() if any("continue" in attribute for attribute in recipe["attributes"])
    }
    assert continuing == {"init", "deploy", "code", "login", "destroy"}


def test_every_parameter_but_a_flag_has_a_literal_pattern() -> None:
    # A pattern that named an assignment could be loosened by overriding it on the command line.
    for name, recipe in _public().items():
        for parameter in recipe["parameters"]:
            if parameter["value"] is None and (name, parameter["name"]) != ("init", "EXTRA"):
                assert parameter["pattern"], (name, parameter["name"])
    assert not re.search(r"pattern=[^'\"]", JUSTFILE.read_text())


def test_every_vm_recipe_holds_its_names_to_one_pattern() -> None:
    recipes = _public()
    for name in PLAYBOOK_RECIPES:
        # code opens one VM, so its word is named in the singular.
        names = next(p for p in recipes[name]["parameters"] if p["name"] == ("NAME" if name == "code" else "NAMES"))
        assert names["pattern"] == [NAMES_PATTERN] and names["kind"] == "star", (name, names)


def test_no_parameter_shares_its_name_with_an_assignment() -> None:
    # Inside the recipe, just lets the parameter hide the assignment, silently.
    data = _json()
    assignments = set(data["assignments"])
    assert [
        (name, p["name"])
        for name, recipe in data["recipes"].items()
        for p in recipe["parameters"]
        if p["name"] in assignments
    ] == []


def test_self_passes_on_every_setting() -> None:
    # A recipe that runs another (test lints, init installs) runs it through self, which would otherwise drop the
    # command line's BIN= and the rest.
    settings = {name for name in _json()["assignments"] if name.isupper()} | {"stamp"}
    passed = set(re.findall(r" ([A-Za-z_]+)=", _evaluate("self")))
    assert passed == settings, (passed, settings)


def test_the_justfile_is_formatted_as_just_formats_it() -> None:
    if _found != _pinned():
        pytest.skip(f"just {_found} may format otherwise than the pinned {_pinned()}, which CI runs this with")
    result = subprocess.run([JUST, "--fmt", "--check"], cwd=REPO, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------- the checks ----


def test_the_vault_check_refuses_a_missing_vault_naming_just_init(tmp_path: Path) -> None:
    output = _refused(tmp_path, "list", settings=[f"VAULT_FILE={tmp_path / 'none.yml'}"])
    assert f"{tmp_path / 'none.yml'} missing. Run: just init" in output
    assert "not encrypted" not in output


def test_the_vault_check_refuses_a_plaintext_vault(tmp_path: Path) -> None:
    plain = tmp_path / "plain.yml"
    plain.write_text("vault_proxmox_api_token_secret: not-a-real-secret\n")
    output = _refused(tmp_path, "list", settings=[f"VAULT_FILE={plain}"])
    assert f"{plain} is not encrypted. Run: just vault" in output


def test_the_collections_check_asks_galaxy_offline_whether_the_pins_are_installed(tmp_path: Path) -> None:
    _playbook(tmp_path, "list")
    assert _runs(tmp_path, "ansible-galaxy") == [["collection", "install", "-r", "requirements.yml", "--offline"]]


@pytest.mark.parametrize("args", [["deploy"], ["code", "alpha"], ["login"], ["list"], ["destroy"], ["test", "unit"]])
def test_every_recipe_that_runs_ansible_stops_at_collections_that_are_not_the_pins(tmp_path: Path, args) -> None:
    settings = _safe(tmp_path, galaxy_rc=1)
    _stand_in(tmp_path / "bin", "ansible-playbook")
    _stand_in(tmp_path / "bin", "pytest")
    result = _just(*args, settings=settings)
    assert result.returncode != 0
    assert "are not the ones requirements.yml pins. Run: just install" in _output(result)
    assert _runs(tmp_path, "ansible-playbook") == [] and _runs(tmp_path, "pytest") == []


def test_a_recipe_without_the_tools_says_just_install(tmp_path: Path) -> None:
    result = _just("list", settings=[f"BIN={tmp_path / 'empty'}", f"VAULT_FILE={_vault(tmp_path)}"])
    assert result.returncode != 0
    assert f"no ansible-galaxy in [{tmp_path / 'empty'}]" in _output(result)
    assert "Run: just install" in _output(result)


def test_the_checks_hold_with_dependencies_switched_off(tmp_path: Path) -> None:
    output = _refused(tmp_path, "deploy", "--tags", "claude_code", env={"JUST_NO_DEPS": "true"})
    assert "--tags on its own configures the VMs named beside it" in output


# ---------------------------------------------------------------- where just was started ----


def test_a_run_from_a_subdirectory_of_the_checkout_runs(tmp_path: Path) -> None:
    result = _just("list", settings=_recording(tmp_path), cwd=REPO / "roles")
    assert result.returncode == 0, _output(result)


def test_a_run_from_another_checkout_is_refused(tmp_path: Path) -> None:
    # As a make-era worktree under .claude/worktrees/ would be: just finds this justfile above it.
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    result = _just("--justfile", str(JUSTFILE), *_recording(tmp_path), "list", cwd=other)
    assert result.returncode != 0
    assert f"in the checkout {other.resolve()}" in result.stderr or f"in the checkout {other}" in result.stderr
    assert _runs(tmp_path, "ansible-playbook") == []


def test_a_run_from_the_checkout_spelt_in_another_case_runs(tmp_path: Path) -> None:
    # A shell keeps a directory as it was typed (cd /users/...), which macOS's file system takes in any
    # case, while git spells the checkout as the disk does: the shell's pwd against git's refused this.
    variant = REPO.with_name(REPO.name.swapcase())
    if variant == REPO or not variant.exists() or not variant.samefile(REPO):
        pytest.skip("this file system tells the checkout's name from its other case")
    result = _just("list", settings=_recording(tmp_path), env={"PWD": str(variant)}, cwd=variant)
    assert result.returncode == 0, _output(result)


def test_a_copy_with_no_git_of_its_own_inside_another_work_tree_runs(tmp_path: Path) -> None:
    # A release archive unpacked in a home directory that is a git repository: git takes the copy for that
    # tree's, from where just was started and from the justfile's directory alike.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_env())
    copy = tmp_path / "claude-on-proxmox"
    copy.mkdir()
    result = subprocess.run(
        [str(REPO / "tools" / "check"), "checkout", str(copy)],
        cwd=copy,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------- VM names ----


NAME_WORDS = [
    (["alpha"], "alpha"),
    (["alpha", "beta"], "alpha,beta"),
    (["alpha,beta"], "alpha,beta"),
    (["a.b-c_d"], "a.b-c_d"),
]


@pytest.mark.parametrize(
    ("recipe", "words", "names"),
    # code opens one VM.
    [
        (recipe, words, names)
        for recipe in PLAYBOOK_RECIPES
        for words, names in NAME_WORDS
        if recipe != "code" or "," not in names
    ],
)
def test_every_vm_recipe_takes_the_names_after_it_as_one_json_extra_var(
    tmp_path: Path, recipe: str, words, names
) -> None:
    run = _playbook(tmp_path, recipe, *words)
    assert run[run.index(f'{{"vm_name": "{names}"}}') - 1] == "-e", run
    assert run[-1] == PLAYBOOK_RECIPES[recipe], run


@pytest.mark.parametrize("recipe", [name for name in PLAYBOOK_RECIPES if name != "code"])
def test_a_vm_recipe_without_names_passes_no_vm_name_at_all(tmp_path: Path, recipe: str) -> None:
    assert not any("vm_name" in arg for arg in _playbook(tmp_path, recipe))


@pytest.mark.parametrize(
    "word",
    [
        pytest.param("a b", id="space"),
        pytest.param("a'b", id="single-quote"),
        pytest.param('a"b', id="double-quote"),
        pytest.param("a;b", id="semicolon"),
        pytest.param("$(x)", id="command-substitution"),
        pytest.param("{{x}}", id="interpolation"),
        pytest.param("a`b", id="backtick"),
        pytest.param("a\tb", id="tab"),
        pytest.param("alpha\nbeta", id="newline"),
        pytest.param("ålpha", id="non-ascii"),
        pytest.param("a%", id="percent"),
        pytest.param("a:b", id="colon"),
        pytest.param("-x", id="an-option"),
        pytest.param(".hidden", id="from-a-dot"),
    ],
)
@pytest.mark.parametrize("recipe", PLAYBOOK_RECIPES)
def test_a_name_outside_its_characters_is_refused_before_anything_runs(tmp_path: Path, recipe: str, word: str) -> None:
    output = _refused(tmp_path, recipe, word)
    # A recipe with options of its own takes a word that starts with - as one.
    assert NOT_MATCHED in output or (word.startswith("-") and "does not have option" in output), output


def test_a_name_is_never_run_as_code(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    _refused(tmp_path, "deploy", f"$(touch {ran})")
    _refused(tmp_path, "deploy", f"`touch {ran}`")
    assert not ran.exists()


def test_the_names_check_holds_the_json_whatever_reaches_it() -> None:
    # The script's own check, behind the pattern: tools/check names.
    for name in ('a"b', "a b", "a\\b", "", "-x", ",a"):
        result = subprocess.run(
            [str(REPO / "tools" / "check"), "names", name], capture_output=True, text=True, check=False
        )
        assert result.returncode != 0 and "VM names start with a letter or a digit" in result.stderr, name
    assert subprocess.run([str(REPO / "tools" / "check"), "names", "alpha", "b-2.c_d,e"], check=False).returncode == 0


# ---------------------------------------------------------------- a stage where the names go ----


def _answered(tmp_path: Path, answer: str, *args: str) -> subprocess.CompletedProcess[str]:
    """A recipe run at a terminal of its own (a pseudo-terminal), with answer typed there before it asks."""
    primary, secondary = pty.openpty()
    try:
        os.write(primary, answer.encode())
        return _just(*args, settings=_recording(tmp_path), stdin=secondary)
    finally:
        os.close(primary)
        os.close(secondary)


@pytest.mark.parametrize(
    "words",
    [["configure"], ["alpha", "template"], ["provision"], ["ssh-config"], ["alpha,check"], ["check", "provision"]],
)
def test_deploy_without_a_terminal_takes_a_stage_word_for_neither(tmp_path: Path, words: list[str]) -> None:
    # just deploy configure would otherwise create a VM named configure, and nobody is there to ask.
    output = _refused(tmp_path, "deploy", *words)
    assert "there is no terminal to ask which was meant" in output
    assert "or a VM so named beside one, as just deploy --only provision" in output


def test_deploy_asks_and_takes_a_stage_word_for_a_vm_name_when_that_was_meant(tmp_path: Path) -> None:
    result = _answered(tmp_path, "v\n", "deploy", "alpha", "configure")
    assert result.returncode == 0, _output(result)
    assert "configure is a stage of just deploy and also a name a VM could have. Did you mean" in result.stderr
    assert "s  the stage: just deploy --only configure alpha" in result.stderr
    assert "v  a VM named configure, created if there is none: just deploy alpha,configure" in result.stderr
    (run,) = _runs(tmp_path, "ansible-playbook")
    assert run[-1] == "deploy.yml" and '{"vm_name": "alpha,configure"}' in run, run


@pytest.mark.parametrize(
    ("words", "playbook", "names"),
    [
        (["configure"], "playbooks/configure.yml", None),
        (["alpha", "configure"], "playbooks/configure.yml", "alpha"),
        (["provision"], "playbooks/provision.yml", None),
        (["alpha,ssh-config"], "playbooks/ssh_config.yml", "alpha"),
    ],
)
def test_deploy_runs_the_stage_when_that_was_meant(tmp_path: Path, words: list[str], playbook: str, names) -> None:
    result = _answered(tmp_path, "s\n", "deploy", *words)
    assert result.returncode == 0, _output(result)
    (run,) = _runs(tmp_path, "ansible-playbook")
    assert run[-1] == playbook, run
    if names:
        assert json.dumps({"vm_name": names}) in run, run
    else:
        assert not [word for word in run if "vm_name" in word], run


def test_check_meant_as_the_stage_is_the_dry_run(tmp_path: Path) -> None:
    result = _answered(tmp_path, "s\n", "deploy", "check")
    assert result.returncode == 0, _output(result)
    assert "s  the stage: just deploy --check" in result.stderr
    (run,) = _runs(tmp_path, "ansible-playbook")
    assert run[-1] == "playbooks/configure.yml" and "--check" in run and "--diff" in run, run


@pytest.mark.parametrize("answer", ["\n", "x\n", ""])
def test_deploy_runs_nothing_unless_the_question_is_answered(tmp_path: Path, answer: str) -> None:
    # Enter, anything else, or the terminal closing: nothing runs, and no VM is created.
    primary, secondary = pty.openpty()
    try:
        if answer:
            os.write(primary, answer.encode())
        else:
            os.write(primary, b"\x04")  # Ctrl-D at the start of a line: end of input.
        result = _just("deploy", "configure", settings=_recording(tmp_path), stdin=secondary)
    finally:
        os.close(primary)
        os.close(secondary)
    assert result.returncode != 0, _output(result)
    assert "Nothing was run." in result.stderr
    assert _runs(tmp_path, "ansible-playbook") == []


def test_two_stage_words_are_vm_names_or_nothing(tmp_path: Path) -> None:
    stage = _answered(tmp_path / "stage", "s\n", "deploy", "configure", "check")
    assert stage.returncode != 0 and "One stage runs at a time, given with --only: nothing was run." in stage.stderr
    assert "  s  " not in stage.stderr and _runs(tmp_path / "stage", "ansible-playbook") == []
    vms = _answered(tmp_path / "vms", "v\n", "deploy", "configure", "check")
    assert vms.returncode == 0, _output(vms)
    (run,) = _runs(tmp_path / "vms", "ansible-playbook")
    assert run[-1] == "deploy.yml" and '{"vm_name": "configure,check"}' in run, run


def test_the_stage_meant_still_meets_the_rules(tmp_path: Path) -> None:
    # As if typed: --start starts nothing under --only configure.
    result = _answered(tmp_path, "s\n", "deploy", "configure", "--start")
    assert result.returncode != 0 and "--start starts a VM the run would leave stopped" in result.stderr
    assert _runs(tmp_path, "ansible-playbook") == []


def test_a_stage_word_beside_a_stage_is_a_vm_name_without_a_question(tmp_path: Path) -> None:
    provision = _playbook(tmp_path / "provision", "deploy", "--only", "provision", "configure")
    assert provision[-1] == "playbooks/provision.yml" and '{"vm_name": "configure"}' in provision
    check = _playbook(tmp_path / "check", "deploy", "--check", "check")
    assert check[-1] == "playbooks/configure.yml" and '{"vm_name": "check"}' in check and "--check" in check


@pytest.mark.parametrize(
    ("words", "names"), [(["test"], "test"), (["alpha", "lint"], "alpha,lint"), (["docs,code"], "docs,code")]
)
def test_deploy_takes_a_vm_named_like_a_recipe(tmp_path: Path, words: list[str], names: str) -> None:
    # deploy takes every word after it as a VM name and just runs no second recipe, so test is a VM.
    run = _playbook(tmp_path, "deploy", *words)
    assert run[-1] == "deploy.yml" and json.dumps({"vm_name": names}) in run, run


def test_check_refused_as_a_name_points_at_the_dry_run_option(tmp_path: Path) -> None:
    output = _refused(tmp_path, "deploy", "check")
    assert "give a stage as just deploy --check," in output and "--only check" not in output


def test_code_opens_exactly_one_vm(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "code", "alpha")
    assert '{"vm_name": "alpha"}' in run and run[-1] == "playbooks/code.yml"
    assert "code opens one VM: just code <name>" in _refused(tmp_path / "none", "code")
    assert "code opens one VM at a time, not [alpha beta]" in _refused(tmp_path / "two", "code", "alpha", "beta")
    assert "code opens one VM at a time, not [alpha list]" in _refused(tmp_path / "list", "code", "alpha", "list")
    assert "code opens one VM at a time" in _refused(tmp_path / "comma", "code", "alpha,beta")


# ---------------------------------------------------------------- deploy's stages, and login's sign-ins ----

DEPLOY_STAGES = {
    "template": "playbooks/template.yml",
    "provision": "playbooks/provision.yml",
    "configure": "playbooks/configure.yml",
    "ssh-config": "playbooks/ssh_config.yml",
}
SIGN_INS = {
    "claude": "playbooks/claude_login.yml",
    "github": "playbooks/github_login.yml",
    "tailscale": "playbooks/tailscale_login.yml",
}


@pytest.mark.parametrize(("stage", "playbook"), DEPLOY_STAGES.items())
def test_each_stage_of_deploy_runs_its_playbook(tmp_path: Path, stage: str, playbook: str) -> None:
    names = [] if stage == "template" else ["alpha", "beta"]
    run = _playbook(tmp_path, "deploy", "--only", stage, *names)
    assert run[-1] == playbook, run
    assert ('{"vm_name": "alpha,beta"}' in run) == (stage != "template"), run
    # Only the whole deploy runs sign-ins, so only it turns them off without a terminal.
    assert not any("login_desktop" in arg for arg in run), run
    assert "--check" not in run


def test_check_is_a_dry_run_of_configuration(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "deploy", "alpha", "--check")
    assert run[-3:] == ["--check", "--diff", "playbooks/configure.yml"], run
    assert '{"vm_name": "alpha"}' in run


def test_deploy_without_options_runs_deploy_yml_with_the_no_terminal_switch(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "deploy", "alpha")
    assert run[-1] == "deploy.yml", run
    assert '{"login_desktop":false,"deploy_sign_in":false}' in run


@pytest.mark.parametrize(
    ("args", "title"),
    [
        (["deploy"], "deploy"),
        (["deploy", "alpha", "beta"], "deploy alpha beta"),
        (["deploy", "alpha", "--check"], "deploy --check alpha"),
        (["deploy", "--only", "configure"], "deploy --only configure"),
        (["deploy", "alpha", "--tags", "claude_code,tailscale"], "deploy --tags claude_code,tailscale alpha"),
        (["deploy", "alpha", "--start"], "deploy --start alpha"),
        (["login", "alpha"], "unset"),
        (["list"], "unset"),
    ],
)
def test_deploy_and_only_deploy_is_drawn_live_under_what_was_run(tmp_path: Path, args: list[str], title: str) -> None:
    # The live output's switch and its title (callback_plugins/claude_on_proxmox.py), as the playbook sees it.
    seen = tmp_path / "live"
    body = f'printf "%s\\n" "${{CLAUDE_ON_PROXMOX_LIVE-unset}}" > "{seen}"'
    result = _just(*args, settings=_recording(tmp_path, body))
    assert result.returncode == 0, _output(result)
    assert seen.read_text() == f"{title}\n"


def test_an_option_may_come_before_or_after_the_names(tmp_path: Path) -> None:
    after = _playbook(tmp_path / "after", "deploy", "alpha", "--tags", "claude_code")
    before = _playbook(tmp_path / "before", "deploy", "--tags=claude_code", "alpha")
    short = _playbook(tmp_path / "short", "deploy", "-t", "claude_code", "alpha")
    assert after == before == short


@pytest.mark.parametrize(("only", "playbook"), [("", "playbooks/login.yml"), *SIGN_INS.items()])
def test_each_sign_in_runs_its_playbook_and_none_all_three(tmp_path: Path, only: str, playbook: str) -> None:
    run = _playbook(tmp_path, "login", "alpha", "beta", *(["--only", only] if only else []))
    assert run[-1] == playbook, run
    assert '{"vm_name": "alpha,beta"}' in run
    assert '{"login_desktop":false,"deploy_sign_in":false}' in run


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(["deploy", "--only", "bogus"], NOT_MATCHED, id="unknown-stage"),
        pytest.param(["deploy", "--only", "check"], NOT_MATCHED, id="check-is-a-flag"),
        pytest.param(["login", "--only", "configure"], NOT_MATCHED, id="another-recipe-s"),
        pytest.param(["deploy", "--type", "configure"], "does not have option `--type`", id="make-s-type"),
        pytest.param(["deploy", "--tags", "a", "--tags", "b"], "cannot be passed more than once", id="twice"),
        pytest.param(["test", "lint"], NOT_MATCHED, id="lint-is-its-own"),
        # list takes no options, so an option is a word to it, and no name starts with -.
        pytest.param(["list", "--only", "x"], NOT_MATCHED, id="list"),
        pytest.param(["destroy", "--project", "repo"], NOT_MATCHED, id="destroy"),
        pytest.param(["deploy", "alpha", "TAGS=claude_code"], NOT_MATCHED, id="make-s-spelling"),
    ],
)
def test_an_option_is_checked_before_anything_runs(tmp_path: Path, args: list[str], error: str) -> None:
    assert error in _refused(tmp_path, *args)


def test_a_setting_just_does_not_have_is_refused(tmp_path: Path) -> None:
    result = _just("TYPE=configure", "deploy", settings=_recording(tmp_path))
    assert result.returncode != 0 and "variable `TYPE` overridden on the command line but not present" in result.stderr


def test_template_takes_no_names(tmp_path: Path) -> None:
    assert "builds the template, which takes no VM names" in _refused(tmp_path, "deploy", "alpha", "--only", "template")


@pytest.mark.parametrize("stage", ["template", "provision", "ssh-config"])
def test_check_with_a_stage_is_refused(tmp_path: Path, stage: str) -> None:
    assert "--check is a dry run of configuration" in _refused(tmp_path, "deploy", "--check", "--only", stage)


# ---------------------------------------------------------------- --tags ----


def test_tags_beside_names_means_only_configure(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "deploy", "alpha", "--tags", "claude_code,tailscale")
    assert run[-1] == "playbooks/configure.yml", run
    assert run[run.index("--tags") + 1] == "claude_code,tailscale"
    assert "--check" not in run


def test_tags_without_names_is_refused_and_the_fleet_takes_only_configure(tmp_path: Path) -> None:
    # A bare just deploy means the default VM, configuration without names every VM: an API key pushed with
    # just deploy --tags claude_code would otherwise reach them all.
    output = _refused(tmp_path / "refused", "deploy", "--tags", "claude_code")
    assert "--tags on its own configures the VMs named beside it: just deploy <name>... --tags claude_code" in output
    assert "just deploy --only configure --tags claude_code" in output
    fleet = _playbook(tmp_path / "fleet", "deploy", "--only", "configure", "--tags", "claude_code")
    assert not any("vm_name" in arg for arg in fleet) and "claude_code" in fleet


def test_tags_with_check_are_a_dry_run_of_those_roles(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "deploy", "--check", "--tags", "github_projects")
    assert run[-3:] == ["--check", "--diff", "playbooks/configure.yml"], run
    assert run[run.index("--tags") + 1] == "github_projects"


@pytest.mark.parametrize("stage", ["template", "provision", "ssh-config"])
def test_tags_with_a_stage_that_has_none_are_refused(tmp_path: Path, stage: str) -> None:
    output = _refused(tmp_path, "deploy", "--only", stage, "--tags", "common")
    assert f"--tags narrows configuration, which --only {stage} does not run" in output


@pytest.mark.parametrize("tags", ["claude", "common,always", "common,,tailscale", ","])
def test_tags_may_name_only_the_roles(tmp_path: Path, tags: str) -> None:
    output = _refused(tmp_path, "deploy", "alpha", "--tags", tags)
    assert "--tags may name only" in output or "separated by single commas" in output


@pytest.mark.parametrize("tags", ["claude_code;rm -rf x", "$(touch x)", "--skip-tags=common", "Common"])
def test_tags_outside_their_characters_are_refused_by_the_pattern(tmp_path: Path, tags: str) -> None:
    assert NOT_MATCHED in _refused(tmp_path, "deploy", "alpha", f"--tags={tags}")


def test_the_tags_are_configure_s_own() -> None:
    configure = (REPO / "playbooks" / "configure.yml").read_text()
    used = set(re.findall(r"tags: \[(\w+)\]", configure)) - {"always"}
    assert used == set(_evaluate("configure_roles").split())


# ---------------------------------------------------------------- --start ----


@pytest.mark.parametrize(
    ("args", "playbook"), [([], "deploy.yml"), (["--only", "provision"], "playbooks/provision.yml")]
)
def test_start_starts_a_vm_the_run_would_leave_stopped(tmp_path: Path, args: list[str], playbook: str) -> None:
    run = _playbook(tmp_path, "deploy", "gamma", "--start", *args)
    assert run[run.index('{"proxmox_vm_start_existing": true}') - 1] == "-e" and run[-1] == playbook, run


@pytest.mark.parametrize(
    "args",
    [["--only", "configure"], ["--check"], ["--tags", "common"], ["--only", "template"], ["--only", "ssh-config"]],
)
def test_start_with_a_run_that_starts_nothing_is_refused(tmp_path: Path, args: list[str]) -> None:
    names = [] if "template" in args else ["gamma"]
    assert "--start starts a VM the run would leave stopped" in _refused(tmp_path, "deploy", *names, "--start", *args)


# ---------------------------------------------------------------- help ----


def test_just_lists_every_recipe_and_help_names_each_option(tmp_path: Path) -> None:
    listed = _just()
    assert listed.returncode == 0, _output(listed)
    for name in _public():
        assert re.search(rf"(?m)^\s+{re.escape(name)}\b", listed.stdout), name
    assert "Without names, deploy and destroy act on claude-on-proxmox-default" in listed.stdout
    for name, recipe in _public().items():
        usage = _just("help", name)
        assert usage.returncode == 0, _output(usage)
        for parameter in recipe["parameters"]:
            if parameter["long"]:
                assert f"--{parameter['long']}" in usage.stdout, (name, parameter["long"])


def test_every_word_a_recipe_takes_is_a_star_parameter() -> None:
    # One with a default lists as help RECIPE='', make's spelling, which just refuses after the recipe, and
    # leaves the word after it to run as a second recipe: just help deploy test ran every test.
    for name, recipe in _public().items():
        for parameter in recipe["parameters"]:
            if not parameter["long"] and not parameter["short"]:
                assert parameter["kind"] == "star", (name, parameter["name"])
    listed = [line.split("#")[0] for line in _just().stdout.splitlines() if line.startswith("    ")]
    assert listed and not [line for line in listed if "=" in line], listed


# Each recipe whose word is named in the singular, and a word it takes.
ONE_WORD = {"help": "deploy", "code": "alpha", "docs": "build", "vagrant": "destroy", "clean": "logs"}


def test_a_word_named_in_the_singular_is_one_word() -> None:
    assert ONE_WORD.keys() == {
        name
        for name, recipe in _public().items()
        for parameter in recipe["parameters"]
        if parameter["kind"] == "star" and not parameter["name"].endswith("S") and parameter["name"] != "EXTRA"
    }


@pytest.mark.parametrize("second", ["same", "logs"])
@pytest.mark.parametrize("recipe", sorted(ONE_WORD))
def test_a_second_word_is_refused_and_never_runs_as_a_recipe(tmp_path: Path, recipe: str, second: str) -> None:
    # logs only reads: had it run as a second recipe, it would say there are none.
    stand_ins = tmp_path / "stand-ins"
    _stand_in(stand_ins, "vagrant")
    _stand_in(tmp_path / "bin", "zensical")
    word = ONE_WORD[recipe]
    result = _just(
        recipe,
        word,
        word if second == "same" else second,
        settings=_recording(tmp_path),
        env={"PATH": f"{stand_ins}:{_env()['PATH']}"},
    )
    output = _output(result)
    assert result.returncode != 0, output
    assert " one " in output or NOT_MATCHED in output, output
    assert "No run logs in" not in output and not (tmp_path / "order").exists(), output


# ---------------------------------------------------------------- run logs ----


def _logged(tmp_path: Path) -> list[str]:
    _stand_in(tmp_path / "bin", "ansible-playbook", 'echo "stub ANSIBLE_LOG_PATH=[$ANSIBLE_LOG_PATH]"')
    return _safe(tmp_path)


def _stub_log_path(output: str) -> str:
    match = re.search(r"stub ANSIBLE_LOG_PATH=\[(.*)\]", output)
    assert match, output
    return match.group(1)


@pytest.mark.parametrize(
    ("args", "name"),
    [
        (["list", "alpha"], "list"),
        (["deploy", "alpha"], "deploy"),
        (["deploy", "alpha", "--only", "configure"], "deploy-configure"),
        (["deploy", "alpha", "--tags", "claude_code"], "deploy-configure"),
        (["deploy", "alpha", "--check"], "deploy-check"),
        (["deploy", "--only", "template"], "deploy-template"),
        (["login", "alpha"], "login"),
        (["login", "alpha", "--only", "github"], "login-github"),
        (["destroy", "alpha"], "destroy"),
        (["code", "alpha"], "code"),
    ],
)
def test_a_recipe_logs_its_run_to_its_own_file(tmp_path: Path, args: list[str], name: str) -> None:
    result = _just(*args, settings=_logged(tmp_path))
    output = _output(result)
    assert result.returncode == 0, output
    path = _stub_log_path(output)
    assert re.fullmatch(rf"{re.escape(str(tmp_path / 'logs'))}/\d{{8}}-\d{{6}}-{re.escape(name)}\.log", path), path
    assert f"Logging this run to {path}" in output
    # Never the VM name: nothing a person typed becomes a file's name.
    assert "alpha" not in Path(path).name
    assert oct((tmp_path / "logs").stat().st_mode & 0o777) == "0o700"


def test_an_empty_log_dir_turns_logging_off(tmp_path: Path) -> None:
    result = _just("list", settings=[*_logged(tmp_path), "LOG_DIR="])
    assert result.returncode == 0, _output(result)
    assert _stub_log_path(_output(result)) == ""
    assert "Logging this run" not in _output(result)


def test_an_ansible_log_path_of_your_own_is_kept(tmp_path: Path) -> None:
    mine = tmp_path / "elsewhere" / "ansible.log"
    result = _just("list", settings=_logged(tmp_path), env={"ANSIBLE_LOG_PATH": str(mine)})
    assert result.returncode == 0, _output(result)
    assert _stub_log_path(_output(result)) == str(mine)


def test_a_log_dir_exported_or_given_is_the_one_used(tmp_path: Path) -> None:
    exported = _just(
        "list",
        settings=[s for s in _logged(tmp_path) if not s.startswith("LOG_DIR=")],
        env={"LOG_DIR": str(tmp_path / "exported")},
    )
    assert _stub_log_path(_output(exported)).startswith(str(tmp_path / "exported") + "/")


def test_old_run_logs_are_pruned_and_nothing_else_is(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    old, recent, notes = logs / "20260101-000000-deploy.log", logs / "20261001-000000-list.log", logs / "notes.log"
    for path in (old, recent, notes):
        path.write_text("x")
    month_ago = time.time() - 30 * 86400
    for path in (old, notes):
        os.utime(path, (month_ago, month_ago))
    result = _just("list", settings=_logged(tmp_path))
    assert result.returncode == 0, _output(result)
    assert not old.exists() and recent.exists()
    # Not named the way run-logged names its logs, so not its to delete.
    assert notes.exists()


def test_a_retention_that_is_not_a_number_prunes_nothing(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "20260101-000000-deploy.log"
    old.write_text("x")
    month_ago = time.time() - 30 * 86400
    os.utime(old, (month_ago, month_ago))
    result = _just("list", settings=[*_logged(tmp_path), "LOG_RETENTION_DAYS=14;rm"])
    assert result.returncode == 0, _output(result)
    assert "LOG_RETENTION_DAYS must be a number of days" in _output(result)
    assert old.exists()


def test_logs_lists_and_clean_logs_deletes_only_run_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    assert "No run logs in" in _just("logs", settings=[f"LOG_DIR={logs}"]).stdout
    logs.mkdir()
    run_log, notes = logs / "20261007-203015-deploy.log", logs / "notes.txt"
    run_log.write_text("x")
    notes.write_text("x")
    assert _just("logs", settings=[f"LOG_DIR={logs}"]).stdout.strip() == str(run_log)
    assert _just("logs", env={"LOG_DIR": str(logs)}).stdout.strip() == str(run_log)
    cleaned = _just("clean", "logs", settings=[f"LOG_DIR={logs}"])
    assert cleaned.returncode == 0, _output(cleaned)
    assert "Deleted 1 run log(s)" in cleaned.stdout
    assert not run_log.exists() and notes.exists()


def test_clean_never_touches_the_python_environment() -> None:
    body = json.dumps(_json()["recipes"]["clean"]["body"])
    assert ".venv" not in body and "PYTHON" not in body and "bin" not in body


# ---------------------------------------------------------------- --project ----


@pytest.mark.parametrize(
    "value", ["discord-music-bot", "ParkBnb", "djangoTest", "omkar_kv", "site.github.io", "x", "a" * 100]
)
def test_code_passes_a_project_as_its_own_json_extra_var(tmp_path: Path, value: str) -> None:
    run = _playbook(tmp_path, "code", "alpha", "--project", value)
    assert run[run.index('{"vm_name": "alpha"}') + 1 : run.index('{"vm_name": "alpha"}') + 3] == [
        "-e",
        f'{{"vm_project": "{value}"}}',
    ], run


def test_code_without_a_project_passes_none(tmp_path: Path) -> None:
    assert not any("vm_project" in arg for arg in _playbook(tmp_path, "code", "alpha"))


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param("../etc", NOT_MATCHED, id="parent-path"),
        pytest.param("org/repo", NOT_MATCHED, id="slash"),
        pytest.param("/etc", NOT_MATCHED, id="absolute"),
        pytest.param(".", "--project names one project folder, not [.]", id="dot"),
        pytest.param("..", "--project names one project folder, not [..]", id="dot-dot"),
        pytest.param("a b", NOT_MATCHED, id="space"),
        pytest.param('a"b', NOT_MATCHED, id="double-quote"),
        pytest.param("a,b", NOT_MATCHED, id="comma"),
        pytest.param("repo\nother", NOT_MATCHED, id="newline"),
        pytest.param("répo", NOT_MATCHED, id="non-ascii"),
        pytest.param("a" * 101, NOT_MATCHED, id="too-long"),
    ],
)
def test_code_refuses_a_bad_project_before_the_playbook(tmp_path: Path, value: str, error: str) -> None:
    assert error in _refused(tmp_path, "code", "alpha", "--project", value)


@pytest.mark.parametrize("recipe", ["deploy", "login", "list", "destroy"])
def test_only_code_takes_a_project(tmp_path: Path, recipe: str) -> None:
    output = _refused(tmp_path, recipe, "alpha", "--project", "repo")
    assert "does not have option `--project`" in output or NOT_MATCHED in output


# ---------------------------------------------------------------- --session ----

# One Claude Code session ID, lowercase hex in groups of 8-4-4-4-12, as Claude
# Code names a transcript. Only just code takes it.
SESSION_ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
SESSION_ERROR = "--session is a Claude Code session ID"


def test_code_passes_a_session_as_its_own_json_extra_var(tmp_path: Path) -> None:
    run = _playbook(tmp_path, "code", "alpha", "--project", "repo", "--session", SESSION_ID)
    assert run[run.index('{"vm_name": "alpha"}') + 1 : run.index('{"vm_name": "alpha"}') + 5] == [
        "-e",
        '{"vm_project": "repo"}',
        "-e",
        f'{{"vm_session": "{SESSION_ID}"}}',
    ], run


def test_code_without_a_session_passes_none(tmp_path: Path) -> None:
    assert not any("vm_session" in arg for arg in _playbook(tmp_path, "code", "alpha", "--project", "repo"))


BAD_SESSIONS = [
    pytest.param(SESSION_ID.upper(), id="uppercase"),
    pytest.param(SESSION_ID + "0", id="too-long"),
    pytest.param(SESSION_ID[:-1], id="too-short"),
    pytest.param(SESSION_ID.replace("-", ""), id="no-dashes"),
    pytest.param("../" + SESSION_ID, id="path"),
    pytest.param(SESSION_ID + "' -e 'x=1", id="quote"),
    pytest.param(SESSION_ID + '"}', id="json"),
    pytest.param("$(x)", id="command-substitution"),
    pytest.param(SESSION_ID + "\nother", id="newline"),
    pytest.param("-" * 36, id="dashes"),
]


@pytest.mark.parametrize("value", BAD_SESSIONS)
def test_code_refuses_a_bad_session_before_the_playbook(tmp_path: Path, value: str) -> None:
    assert NOT_MATCHED in _refused(tmp_path, "code", "alpha", "--session", value)


@pytest.mark.parametrize("value", BAD_SESSIONS)
def test_the_session_check_holds_the_json_whatever_reaches_it(value: str) -> None:
    # The script's own check, behind the pattern: tools/check session.
    result = subprocess.run(
        [str(REPO / "tools" / "check"), "session", value], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0 and SESSION_ERROR in result.stderr, value


@pytest.mark.parametrize("value", [SESSION_ID, ""])
def test_the_session_check_takes_a_session_id_or_none(value: str) -> None:
    assert subprocess.run([str(REPO / "tools" / "check"), "session", value], check=False).returncode == 0


def test_a_project_or_session_is_never_run_as_code(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    for option in ("--project", "--session"):
        _refused(tmp_path, "code", "alpha", option, f"$(touch {ran})")
        _refused(tmp_path, "code", "alpha", option, f"`touch {ran}`")
    assert not ran.exists()


@pytest.mark.parametrize("recipe", ["deploy", "login", "list", "destroy"])
def test_only_code_takes_a_session(tmp_path: Path, recipe: str) -> None:
    output = _refused(tmp_path, recipe, "alpha", "--session", SESSION_ID)
    assert "does not have option `--session`" in output or NOT_MATCHED in output


# ---------------------------------------------------------------- a terminal, or none ----

OFF = '{"login_desktop":false,"deploy_sign_in":false}'
SIGNING_IN = [["deploy", "alpha"], ["login", "alpha"], *(["login", "alpha", "--only", only] for only in SIGN_INS)]


@pytest.mark.parametrize("args", SIGNING_IN)
def test_a_run_with_no_terminal_turns_the_desktop_steps_and_deploy_s_sign_ins_off(tmp_path: Path, args) -> None:
    run = _playbook(tmp_path, *args)
    assert run[run.index(OFF) - 1] == "-e", run


@pytest.mark.parametrize("args", SIGNING_IN)
def test_a_run_at_a_terminal_leaves_the_desktop_steps_on(tmp_path: Path, args) -> None:
    primary, secondary = pty.openpty()
    try:
        run = _playbook(tmp_path, *args, stdin=secondary)
    finally:
        os.close(primary)
        os.close(secondary)
    assert OFF not in run, run


def _interrupted(started: Path, *command: str) -> tuple[int, str]:
    """Run just at a pseudo-terminal of its own, Ctrl-C its process group once started exists, and return its
    exit code and output."""
    primary, secondary = pty.openpty()
    try:
        process = subprocess.Popen(
            [JUST, *command],
            cwd=REPO,
            env=_env(),
            stdin=secondary,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGINT)
        output, _ = process.communicate(timeout=30)
    finally:
        os.close(primary)
        os.close(secondary)
    return process.returncode, output


@pytest.mark.parametrize("continues", [True, False])
def test_ctrl_c_that_the_playbook_survives_ends_the_run_well(tmp_path: Path, continues: bool) -> None:
    # Ansible's pause, Ctrl-C then C: the playbook catches the signal and finishes. just waits for it and, with
    # [continue], exits as it does; the script's bash survives the signal too, which Ubuntu's dash would not.
    started = tmp_path / "started"
    body = f'trap "echo caught" INT; touch "{started}"; sleep 3; echo finished'
    settings = _recording(tmp_path, playbook_body=body if continues else f'touch "{started}"; sleep 3; exit 1')
    returncode, output = _interrupted(started, *settings, "deploy", "alpha")
    if continues:
        assert returncode == 0, output
        assert "caught" in output and "finished" in output
    else:
        assert returncode != 0, output


def test_without_continue_just_ends_a_run_the_playbook_survived(tmp_path: Path) -> None:
    # The control for the test above: [continue] is what makes it pass. Without it just exits 130 for the
    # Ctrl-C though the playbook caught it and finished, as Ansible's pause, Ctrl-C then C, does. Should a
    # newer just stop doing so, this fails, and [continue] can go.
    started = tmp_path / "started"
    (tmp_path / "justfile").write_text(
        "set script-interpreter := ['bash', '-eu']\n"
        "\n"
        "[script]\n"
        "run:\n"
        f'    exec bash -c \'trap "echo caught" INT; touch "{started}"; sleep 3; echo finished\'\n'
    )
    returncode, output = _interrupted(started, "--justfile", str(tmp_path / "justfile"), "run")
    assert "caught" in output and "finished" in output, output
    assert returncode == 130, output


# ---------------------------------------------------------------- vault ----


@pytest.mark.parametrize(
    ("content", "action"), [("vault_proxmox_api_token_secret: x\n", "encrypt"), (VAULT_HEADER, "edit")]
)
def test_vault_encrypts_a_plaintext_vault_and_edits_an_encrypted_one(tmp_path: Path, content: str, action: str) -> None:
    _stand_in(tmp_path / "bin", "ansible-vault")
    vault = _vault(tmp_path, content)
    result = _just(
        "vault", settings=[f"BIN={tmp_path / 'bin'}", f"VAULT_FILE={vault}", f"VAULT_PASS={tmp_path / 'none'}"]
    )
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, "ansible-vault") == [[action, str(vault)]]


def test_vault_opens_with_vault_pass_where_there_is_one(tmp_path: Path) -> None:
    _stand_in(tmp_path / "bin", "ansible-vault")
    (tmp_path / ".vault_pass").write_text("x\n")
    vault = _vault(tmp_path)
    result = _just(
        "vault", settings=[f"BIN={tmp_path / 'bin'}", f"VAULT_FILE={vault}", f"VAULT_PASS={tmp_path / '.vault_pass'}"]
    )
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, "ansible-vault") == [
        ["edit", "--vault-password-file", str(tmp_path / ".vault_pass"), str(vault)]
    ]


def test_vault_names_just_init_when_the_vault_is_missing(tmp_path: Path) -> None:
    result = _just("vault", settings=[f"BIN={tmp_path}", f"VAULT_FILE={tmp_path / 'none.yml'}"])
    assert result.returncode != 0
    assert "missing. Run: just init" in _output(result)


# ---------------------------------------------------------------- install ----
# The locked packages into PYTHON's own environment, then the collections;
# --lock the lock alone. The project keeps no environment of its own.
ENVIRONMENT = sysconfig.get_path("scripts")


def _uv(tmp_path: Path) -> Path:
    """A uv that records its arguments and the environment it was given to sync."""
    _stand_in(
        tmp_path / "uv-bin", "uv", f'echo "UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT" >> "{tmp_path / "uv-env"}"'
    )
    return tmp_path / "uv-bin" / "uv"


def test_install_syncs_the_lock_into_python_s_own_environment(tmp_path: Path) -> None:
    uv = _uv(tmp_path)
    _stand_in(tmp_path / "bin", "ansible-galaxy")
    result = _just("install", settings=[f"PYTHON={sys.executable}", f"UV={uv}", f"BIN={tmp_path / 'bin'}"])
    assert result.returncode == 0, _output(result)
    # --inexact: the environment is the user's, and nothing in it the lock does not list is removed.
    assert _runs(tmp_path, "uv")[0][:5] == ["sync", "--locked", "--inexact", "--python", sys.executable]
    assert (tmp_path / "uv-env").read_text().strip() == f"UV_PROJECT_ENVIRONMENT={sys.prefix}"
    assert _runs(tmp_path, "ansible-galaxy") == [["collection", "install", "-r", "requirements.yml"]]


def test_install_lock_only_writes_the_lock(tmp_path: Path) -> None:
    # Not even an environment: uv from PATH writes the lock.
    uv = _uv(tmp_path)
    result = _just("install", "--lock", settings=["PYTHON=/nonexistent", f"UV={uv}"])
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, "uv") == [["lock"]]


def _not_a_virtualenv(tmp_path: Path) -> Path:
    python = tmp_path / "python3"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    return python


def test_install_refuses_a_python_that_is_no_virtualenv(tmp_path: Path) -> None:
    python = _not_a_virtualenv(tmp_path)
    uv = _uv(tmp_path)
    result = _just("install", settings=[f"PYTHON={python}", f"UV={uv}"])
    assert result.returncode != 0
    assert f"error: [{python}] is not the Python of a virtualenv" in _output(result)
    assert _runs(tmp_path, "uv") == []


def test_install_refuses_the_base_python_of_the_tests_virtualenv() -> None:
    base = Path(sys.base_prefix) / "bin" / "python3"
    if sys.prefix == sys.base_prefix or not base.exists():
        pytest.skip("the tests do not run in a virtualenv with a base python3 beside it")
    refused = _just("install", settings=[f"PYTHON={base}", "UV=false"])
    assert refused.returncode != 0
    assert "is not the Python of a virtualenv" in _output(refused)


@pytest.mark.parametrize("args", [["list"], ["lint", "yaml"], ["docs"], ["vault"], ["test", "unit"], ["code", "alpha"]])
def test_every_recipe_that_runs_a_tool_refuses_a_python_that_is_no_virtualenv(tmp_path: Path, args) -> None:
    # Its scripts directory would be /usr/local/bin or the like, where a system-wide Ansible lives.
    python = _not_a_virtualenv(tmp_path)
    result = _just(
        *args, settings=[f"PYTHON={python}", f"VAULT_FILE={_vault(tmp_path)}", f"LOG_DIR={tmp_path / 'logs'}"]
    )
    assert result.returncode != 0
    assert f"error: [{python}] is not the Python of a virtualenv, and this project runs its tools only from" in _output(
        result
    )


def test_recipes_run_the_tools_of_python_s_environment(tmp_path: Path) -> None:
    # A Python whose environment's scripts are stand-ins: what env-run --bin asks it is where the tools come from.
    _stand_in(tmp_path / "env" / "bin", "ansible-playbook")
    _stand_in(tmp_path / "env" / "bin", "ansible-galaxy")
    python = tmp_path / "python3"
    python.write_text(f'#!/bin/sh\necho "{tmp_path / "env" / "bin"}"\n')
    python.chmod(0o755)
    result = _just(
        "list", settings=[f"PYTHON={python}", f"VAULT_FILE={_vault(tmp_path)}", f"LOG_DIR={tmp_path / 'logs'}"]
    )
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path / "env", "ansible-playbook")[0][-1] == "playbooks/list.yml"


def test_a_bin_in_the_environment_is_not_the_tools(tmp_path: Path) -> None:
    _stand_in(tmp_path / "bin", "ansible-playbook")
    _stand_in(tmp_path / "bin", "ansible-galaxy")
    python = _not_a_virtualenv(tmp_path)
    result = _just(
        "list", settings=[f"PYTHON={python}", f"VAULT_FILE={_vault(tmp_path)}"], env={"BIN": str(tmp_path / "bin")}
    )
    assert result.returncode != 0 and _runs(tmp_path, "ansible-playbook") == []


def test_help_and_a_refusal_start_no_python(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    python = tmp_path / "python3"
    python.write_text(f'#!/bin/sh\ntouch "{ran}"\n')
    python.chmod(0o755)
    assert _just(settings=[f"PYTHON={python}"]).returncode == 0
    assert _just("help", "deploy", settings=[f"PYTHON={python}"]).returncode == 0
    assert _just("deploy", "--only", "bogus", settings=[f"PYTHON={python}"]).returncode != 0
    assert _just("deploy", "--tags", "claude_code", settings=[f"PYTHON={python}"]).returncode != 0
    assert _just("deploy", "configure", settings=[f"PYTHON={python}"]).returncode != 0
    assert not ran.exists()


# uv writes the path an environment was synced by into the first line of each
# command it installs, and make venv synced through the checkout's .venv link,
# so every command named the link and broke when it went; a sync that installs
# nothing rewrites none. just install reinstalls each locked package with such a
# command (tools/stale_scripts.py), and only those: pip's own, from making the
# environment, would be reinstalled on every run and never change.
STALE_SCRIPTS = REPO / "tools" / "stale_scripts.py"


def _stale_scripts():
    spec = importlib.util.spec_from_file_location("stale_scripts", STALE_SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installed(site: Path, scripts: Path, package: str, commands: dict[str, str]) -> None:
    """A package as an installer leaves it: its dist-info in site, and its commands in scripts, each starting with
    the first line given, listed in its RECORD."""
    info = site / f"{package}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package}\nVersion: 1.0\n")
    scripts.mkdir(parents=True, exist_ok=True)
    record = [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    for name, first in commands.items():
        (scripts / name).write_text(f"{first}\nimport sys\n")
        record.append(f"{os.path.relpath(scripts / name, site)},,")
    (info / "RECORD").write_text("\n".join(record) + "\n")


def _sh(python: str) -> str:
    """The first lines uv gives a command whose Python's path is too long for one, or has a space in it."""
    return "#!/bin/sh\n'''exec' " + python + ' "$0" "$@"\n' + "' '''"


def test_stale_scripts_names_the_locked_packages_whose_commands_start_another_python(tmp_path: Path) -> None:
    env = tmp_path / "env"
    scripts, site = env / "bin", env / "lib" / "site-packages"
    # The checkout's old .venv: a link to the environment, so its Python is the same one.
    link = tmp_path / "checkout" / ".venv"
    link.parent.mkdir()
    link.symlink_to(env)
    scripts.mkdir(parents=True)
    (scripts / "python").write_text("")
    assert (link / "bin" / "python").resolve() == (scripts / "python").resolve()
    far = tmp_path / ("long" * 30) / "bin" / "python"

    _installed(site, scripts, "by-the-link", {"by-the-link": f"#!{link}/bin/python"})
    _installed(site, scripts, "too-long", {"too-long": _sh(f"'{far}'")})
    _installed(site, scripts, "current", {"current": f"#!{scripts}/python", "current-sh": _sh(f"'{scripts}/python'")})
    _installed(site, scripts, "by-env", {"by-env": "#!/usr/bin/env python3"})
    # uv's relocatable form: the Python beside the command, wherever the environment is.
    beside = '"$(dirname -- "$(realpath -- "$0")")"/' + "'python'"
    _installed(site, scripts, "relocatable", {"relocatable": _sh(beside)})
    _installed(site, scripts, "unlocked", {"unlocked": f"#!{link}/bin/python"})
    locked = {"by-the-link", "too-long", "current", "by-env", "relocatable"}

    assert _stale_scripts().stale(scripts, [str(site)], locked) == {
        "by-the-link": [("by-the-link", f"{link}/bin/python")],
        "too-long": [("too-long", str(far))],
    }


def test_stale_scripts_looks_at_what_uv_lock_pins_by_uv_s_spelling() -> None:
    module = _stale_scripts()
    names = module.locked(REPO / "uv.lock")
    assert {"ansible-core", "pytest", "markdown", "ruamel-yaml"} <= names
    assert "pip" not in names
    assert [module.normalized(name) for name in ("Markdown", "ruamel.yaml", "charset_normalizer")] == [
        "markdown",
        "ruamel-yaml",
        "charset-normalizer",
    ]


def _environment_naming_its_link(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A real virtualenv whose pytest - a package uv.lock pins - starts its Python by a link to the environment, as
    make venv left them; returns its python, that command, and the link."""
    env = tmp_path / "env"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(env)], check=True)
    python = env / "bin" / "python"
    paths = json.loads(
        subprocess.run(
            [python, "-c", "import json, sysconfig; print(json.dumps(sysconfig.get_paths()))"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    link = tmp_path / "checkout" / ".venv"
    link.parent.mkdir()
    link.symlink_to(env)
    _installed(Path(paths["purelib"]), Path(paths["scripts"]), "pytest", {"pytest": f"#!{link}/bin/python"})
    return python, Path(paths["scripts"]) / "pytest", link


def _fixing_uv(tmp_path: Path, fixes: bool) -> tuple[Path, Path]:
    """A uv that records each run's arguments, a line per run; with fixes, a reinstall rewrites the command's first
    line as uv's does."""
    record = tmp_path / "uv-runs"
    uv = tmp_path / "uv"
    fix = 'sed -i.orig "1s|.*|#!$UV_PROJECT_ENVIRONMENT/bin/python|" "$UV_PROJECT_ENVIRONMENT/bin/pytest"'
    reinstall = f'case "$*" in *--reinstall-package*) {fix};; esac\n' if fixes else ""
    uv.write_text(f'#!/bin/sh\necho "$*" >> {record}\n{reinstall}')
    uv.chmod(0o755)
    return uv, record


def test_install_reinstalls_a_package_whose_commands_start_another_python(tmp_path: Path) -> None:
    python, command, link = _environment_naming_its_link(tmp_path)
    uv, record = _fixing_uv(tmp_path, fixes=True)
    _stand_in(tmp_path / "galaxy-bin", "ansible-galaxy")
    settings = [f"PYTHON={python}", f"UV={uv}", f"BIN={tmp_path / 'galaxy-bin'}"]
    result = _just("install", settings=settings)
    assert result.returncode == 0, _output(result)
    assert f"1 command in [{command.parent}] starts a Python outside it" in result.stderr
    assert f"  {link}/bin/python: pytest" in result.stderr
    first_run = record.read_text().splitlines()
    assert len(first_run) == 1, first_run
    assert first_run[0].endswith(f"--python {python} --reinstall-package pytest"), first_run
    assert command.read_text().startswith(f"#!{python.parent}/python\n")
    # Fixed, so the next just install reinstalls nothing.
    again = _just("install", settings=settings)
    assert again.returncode == 0, _output(again)
    assert "--reinstall-package" not in record.read_text().splitlines()[-1]
    assert "Python outside" not in again.stderr


def test_install_fails_when_the_reinstall_left_a_command_starting_another_python(tmp_path: Path) -> None:
    # Said, rather than reinstalled again on every just install.
    python, command, _ = _environment_naming_its_link(tmp_path)
    uv, _ = _fixing_uv(tmp_path, fixes=False)
    _stand_in(tmp_path / "galaxy-bin", "ansible-galaxy")
    result = _just("install", settings=[f"PYTHON={python}", f"UV={uv}", f"BIN={tmp_path / 'galaxy-bin'}"])
    assert result.returncode != 0
    assert (
        f"error: after the reinstall, 1 command in [{command.parent}] still starts a Python outside it" in result.stderr
    )
    assert _runs(tmp_path, "ansible-galaxy") == []


# ---------------------------------------------------------------- init ----
# init runs for real here, every tool a stand-in: uv (through just install),
# pre-commit, ansible-galaxy and ansible-playbook, which runs twice - the
# questions, then the checks. INIT_FILES, VAULT_FILE and VAULT_PASS are files in
# tmp_path, never the checkout's.
ASK = '{"init_ask":true}'
SECRET = "11111111-2222-3333-4444-555555555555"


def _init_settings(tmp_path: Path, settings_body: str = "") -> list[str]:
    """Stand-ins for what just init runs, and the settings that point it at them and at files in tmp_path."""
    bin_dir = tmp_path / "bin"
    # As Ansible does, the first run of the log's path makes the file.
    _stand_in(
        bin_dir, "ansible-playbook", f'[ -z "${{ANSIBLE_LOG_PATH:-}}" ] || touch "$ANSIBLE_LOG_PATH"\n{settings_body}'
    )
    _stand_in(bin_dir, "pre-commit")
    _stand_in(bin_dir, "ansible-galaxy")
    _stand_in(bin_dir, "uv")
    copies = []
    for name in ("hosts.yml", "local.yml", "vault.yml"):
        (tmp_path / f"{name}.example").write_text(f"# {name}\n")
        copies.append(str(tmp_path / name))
    settings = [f"BIN={bin_dir}", f"UV={bin_dir / 'uv'}", f"INIT_FILES={' '.join(copies)}"]
    settings += [
        f"VAULT_FILE={tmp_path / 'vault.yml'}",
        f"VAULT_PASS={tmp_path / '.vault_pass'}",
        f"LOG_DIR={tmp_path / 'logs'}",
    ]
    return settings


def _init(tmp_path: Path, *args: str, stdin=subprocess.DEVNULL, env=None, settings_body: str = "", cwd=REPO):
    settings = _init_settings(tmp_path, settings_body)
    result = _just("init", *args, settings=settings, env=env, stdin=stdin, cwd=cwd)
    return result, _runs(tmp_path, "ansible-playbook")


def _at_a_terminal(tmp_path: Path, *args: str, env=None):
    primary, secondary = pty.openpty()
    try:
        return _init(tmp_path, *args, stdin=secondary, env=env)
    finally:
        os.close(primary)
        os.close(secondary)


def test_ctrl_c_that_init_s_questions_survive_goes_on_to_the_checks(tmp_path: Path) -> None:
    # init's first playbook is not its last command, so it is not exec'd: the script's bash waits through the
    # Ctrl-C the questions catch, and runs the checks after them.
    started = tmp_path / "started"
    body = f'case "$*" in *init_settings.yml) trap "echo caught" INT; touch "{started}"; sleep 3; echo finished ;; esac'
    settings = _init_settings(tmp_path, settings_body=body)
    returncode, output = _interrupted(started, *settings, "init", "--no-prompt")
    assert returncode == 0, output
    assert "caught" in output and "finished" in output
    assert [run[-1] for run in _runs(tmp_path, "ansible-playbook")] == [
        "playbooks/init_settings.yml",
        "playbooks/init_check.yml",
    ]


def test_init_installs_first_then_the_hook_the_config_and_the_questions(tmp_path: Path) -> None:
    result, runs = _init(tmp_path)
    assert result.returncode == 0, _output(result)
    order = (tmp_path / "order").read_text().split()
    # The galaxy's install, then the hook, then the questions (and the copies before them).
    assert order.index("uv") < order.index("pre-commit") < order.index("ansible-playbook"), order
    assert "Created" in result.stdout and (tmp_path / "hosts.yml").exists()
    assert runs[0][:2] == ["-i", "playbooks/inventory/init.yml"] and runs[0][-1] == "playbooks/init_settings.yml"


def test_init_checks_proxmox_after_its_questions(tmp_path: Path) -> None:
    result, runs = _init(tmp_path)
    assert result.returncode == 0, _output(result)
    assert [run[-1] for run in runs] == ["playbooks/init_settings.yml", "playbooks/init_check.yml"]
    # The checks load the project's inventory: no -i of init's own.
    assert "-i" not in runs[1]


def test_init_opens_the_vault_for_its_checks_with_vault_pass(tmp_path: Path) -> None:
    (tmp_path / ".vault_pass").write_text("x\n")
    result, runs = _init(tmp_path)
    assert result.returncode == 0, _output(result)
    assert runs[1][runs[1].index("--vault-password-file") + 1] == str(tmp_path / ".vault_pass")


def test_init_finds_the_vault_pass_its_questions_just_made(tmp_path: Path) -> None:
    # The questions encrypt the vault and write .vault_pass in the same run: the checks must be given it.
    vault, password = tmp_path / "vault.yml", tmp_path / ".vault_pass"
    header = "'$ANSIBLE_VAULT;1.1;AES256'"
    made = f'case "$*" in *init_settings.yml*) echo {header} > "{vault}"; echo x > "{password}";; esac'
    result, runs = _init(tmp_path, settings_body=made)
    assert result.returncode == 0, _output(result)
    assert len(runs) == 2, runs
    assert runs[1][runs[1].index("--vault-password-file") + 1] == str(tmp_path / ".vault_pass")


def test_init_checks_with_a_password_file_of_ansibles_own(tmp_path: Path) -> None:
    (tmp_path / "vault.yml").write_text(VAULT_HEADER)
    result, runs = _init(tmp_path, env={"ANSIBLE_VAULT_PASSWORD_FILE": str(tmp_path / "password")})
    assert result.returncode == 0, _output(result)
    assert runs[1][-1] == "playbooks/init_check.yml" and "--vault-password-file" not in runs[1]


def test_init_skips_its_checks_when_nothing_opens_the_vault(tmp_path: Path) -> None:
    (tmp_path / "vault.yml").write_text(VAULT_HEADER)
    result, runs = _init(tmp_path)
    assert result.returncode == 0, _output(result)
    assert [run[-1] for run in runs] == ["playbooks/init_settings.yml"]
    assert "Proxmox is not checked" in result.stdout


def test_init_asks_only_at_a_terminal(tmp_path: Path) -> None:
    result, runs = _init(tmp_path)
    assert result.returncode == 0, _output(result)
    assert ASK not in runs[0], runs
    result, runs = _at_a_terminal(tmp_path)
    assert result.returncode == 0, _output(result)
    settings, checks = runs[2], runs[3]
    assert settings[settings.index(ASK) - 1] == "-e", settings
    # The checks ask too, for a storage or a bridge.
    assert checks[checks.index(ASK) - 1] == "-e", checks
    # Copied once, said once.
    assert "Created" not in result.stdout


def test_no_prompt_asks_nothing_even_at_a_terminal(tmp_path: Path) -> None:
    result, runs = _at_a_terminal(tmp_path, "--no-prompt")
    assert result.returncode == 0, _output(result)
    assert all(ASK not in run for run in runs), runs


def test_init_logs_its_run_once(tmp_path: Path) -> None:
    result, _ = _init(tmp_path)
    said = re.findall(r"Logging this run to .*/logs/\d{8}-\d{6}-init\.log", result.stdout)
    assert len(said) == 1, result.stdout


GIVEN = [
    "--host", "pve.lan", "--token-user", "deploy@pam", "--token-name", "claude", "--github-user", "octo-cat",
    "--tailscale", "off", "--storage", "local-zfs", "--bridge", "vmbr1",
]  # fmt: skip


def test_the_values_given_reach_both_playbooks_as_one_json_extra_var(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, *GIVEN, "--token-secret-file", "~/.config/pve-token")
    assert result.returncode == 0, _output(result)
    passed = [[json.loads(arg) for arg in run if arg.startswith('{"init_given"')] for run in runs]
    # The checks, which settle the storage and the bridge, are given the same.
    assert (
        passed[0]
        == passed[1]
        == [
            {
                "init_given": {
                    "host": "pve.lan",
                    "token_user": "deploy@pam",
                    "token_name": "claude",
                    "token_secret_file": "~/.config/pve-token",
                    "github_user": "octo-cat",
                    "storage": "local-zfs",
                    "bridge": "vmbr1",
                    "tailscale": "off",
                }
            }
        ]
    )


def test_a_relative_secret_file_is_where_just_was_started(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, "--token-secret-file", "pve-token", cwd=REPO / "roles")
    assert result.returncode == 0, _output(result)
    given = json.loads(next(arg for arg in runs[0] if arg.startswith('{"init_given"')))
    assert given["init_given"]["token_secret_file"] == f"{REPO / 'roles'}/pve-token"


def test_no_value_given_passes_no_init_given(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, "--host", "")
    assert result.returncode == 0, _output(result)
    assert not any("init_given" in arg for run in runs for arg in run), runs


def test_an_exported_value_is_not_given(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, env={"HOST": "exported.lan", "GITHUB_USER": "exported"})
    assert result.returncode == 0, _output(result)
    assert not any("exported" in arg for run in runs for arg in run), runs


@pytest.mark.parametrize(
    "option", ["host", "token-user", "token-name", "token-secret-file", "github-user", "storage", "bridge"]
)
@pytest.mark.parametrize("value", ["a'b", 'a"b', "a b", "$(x)", "a`b", "a;b", "a\nb", "ålpha"])
def test_a_given_value_outside_its_characters_is_refused_before_anything_runs(
    tmp_path: Path, option: str, value: str
) -> None:
    result, runs = _init(tmp_path, f"--{option}", value)
    assert result.returncode != 0
    assert NOT_MATCHED in result.stderr
    assert runs == [] and not (tmp_path / "order").exists()


def test_tailscale_is_on_or_off(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, "--tailscale", "yes")
    assert result.returncode != 0 and NOT_MATCHED in result.stderr and runs == []


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([f"TOKEN_SECRET={SECRET}"], id="make-s-spelling"),
        pytest.param([f"CLAUDE_ON_PROXMOX_TOKEN_SECRET={SECRET}"], id="the-variable"),
        pytest.param(["--host", "pve.lan", SECRET], id="a-bare-word"),
        pytest.param(["--token-secret", SECRET], id="an-option"),
        pytest.param([f"--token-secret={SECRET}"], id="an-option-with-its-value"),
    ],
)
def test_the_token_secret_is_refused_on_the_command_line_and_never_said(tmp_path: Path, args: list[str]) -> None:
    result, runs = _init(tmp_path, *args)
    assert result.returncode != 0
    assert "11111111-2222" not in _output(result)
    assert runs == [] and not (tmp_path / "order").exists()


def test_the_token_secret_as_a_setting_is_refused_and_never_said(tmp_path: Path) -> None:
    result = _just(f"CLAUDE_ON_PROXMOX_TOKEN_SECRET={SECRET}", "init", settings=_safe(tmp_path))
    assert result.returncode != 0 and "11111111-2222" not in _output(result)


def test_a_word_after_init_s_options_is_refused_naming_the_two_ways_in(tmp_path: Path) -> None:
    result, _ = _init(tmp_path, f"TOKEN_SECRET={SECRET}")
    assert "just init takes only its options" in result.stderr
    assert "--token-secret-file" in result.stderr and "CLAUDE_ON_PROXMOX_TOKEN_SECRET" in result.stderr


def test_the_secret_in_the_environment_reaches_no_argument(tmp_path: Path) -> None:
    result, runs = _init(tmp_path, "--host", "pve.lan", env={"CLAUDE_ON_PROXMOX_TOKEN_SECRET": SECRET})
    assert result.returncode == 0, _output(result)
    assert SECRET not in "\n".join(arg for run in runs for arg in run) + _output(result)


# ---------------------------------------------------------------- exported names ----


@pytest.mark.parametrize(
    ("args", "env", "never"),
    [
        (["deploy", "alpha"], {"TYPE": "check"}, "--check"),
        (["deploy", "alpha"], {"TAGS": "claude_code"}, "--tags"),
        (["deploy", "alpha"], {"STAGE": "configure", "ROLES": "common", "tags": "common"}, "--tags"),
        (["deploy", "alpha"], {"VM_NAME": "beta"}, '{"vm_name": "beta"}'),
    ],
)
def test_an_exported_name_changes_nothing(tmp_path: Path, args, env, never) -> None:
    run = _playbook(tmp_path, *args, env=env)
    assert run[-1] == "deploy.yml" and never not in run, run


# ---------------------------------------------------------------- tools/env-run ----
ENV_RUN = REPO / "tools" / "env-run"


def _env_run(*args: str, python: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "PYTHON": python}
    return subprocess.run([str(ENV_RUN), *args], env=env, capture_output=True, text=True, check=False, timeout=60)


def test_env_run_runs_a_tool_of_python_s_environment() -> None:
    result = _env_run("python", "-c", "import sys; print(sys.prefix)", python=sys.executable)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.prefix


def test_env_run_bin_prints_the_environment_s_scripts_directory() -> None:
    result = _env_run("--bin", python=sys.executable)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ENVIRONMENT


@pytest.mark.parametrize("args", [["yamllint", "--version"], ["--bin"]])
def test_env_run_refuses_a_python_that_is_no_virtualenv(tmp_path: Path, args: list[str]) -> None:
    python = _not_a_virtualenv(tmp_path)
    result = _env_run(*args, python=str(python))
    assert result.returncode != 0 and result.stdout == ""
    assert f"error: [{python}] is not the Python of a virtualenv" in result.stderr


def test_env_run_names_a_tool_the_environment_lacks() -> None:
    result = _env_run("no-such-tool", python=sys.executable)
    assert result.returncode != 0
    assert f"error: no no-such-tool in [{ENVIRONMENT}]. Run: just install" in result.stderr


def test_every_local_hook_runs_through_env_run() -> None:
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    local = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    assert local
    assert all(hook["entry"].startswith("tools/env-run ") for hook in local), [hook["entry"] for hook in local]
    assert os.access(ENV_RUN, os.X_OK)


@pytest.mark.parametrize("helper", ["env-run", "check", "run-logged"])
def test_the_helpers_are_executable(helper: str) -> None:
    assert os.access(REPO / "tools" / helper, os.X_OK)


# ---------------------------------------------------------------- vagrant, lint, test, docs ----


def test_the_vagrantfile_never_falls_back_to_path_s_ansible_playbook() -> None:
    text = (REPO / "Vagrantfile").read_text()
    assert 'ENV.fetch("ANSIBLE_PLAYBOOK") do' in text
    assert "sys.prefix != sys.base_prefix" in text
    assert 'ENV.fetch("ANSIBLE_PLAYBOOK", "ansible-playbook")' not in text


@pytest.mark.parametrize(("args", "command"), [([], ["up", "--provision"]), (["destroy"], ["destroy", "-f"])])
def test_vagrant_brings_the_vm_up_with_the_environment_s_ansible_playbook_or_destroys_it(
    tmp_path: Path, args, command
) -> None:
    stand_ins = tmp_path / "stand-ins"
    _stand_in(stand_ins, "vagrant", f'echo "$ANSIBLE_PLAYBOOK|${{ANSIBLE_LOG_PATH:-}}" > "{tmp_path / "seen"}"')
    result = _just(
        "vagrant",
        *args,
        settings=[f"BIN={tmp_path / 'env' / 'bin'}", f"LOG_DIR={tmp_path / 'logs'}"],
        env={"PATH": f"{stand_ins}:{_env()['PATH']}"},
    )
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, "vagrant") == [command]
    playbook, log = (tmp_path / "seen").read_text().strip().split("|")
    if not args:
        assert playbook == f"{tmp_path / 'env' / 'bin'}/ansible-playbook"
        assert re.search(r"/logs/\d{8}-\d{6}-vagrant\.log$", log), log
    else:
        assert log == ""


LINTERS = {
    "lock": ("uv", ["lock", "--check"]),
    "yaml": ("yamllint", ["--strict", "."]),
    "ansible": ("ansible-lint", []),
    "python": ("ruff", ["format", "--check", "."]),
    "syntax": ("ansible-playbook", ["--syntax-check", "deploy.yml"]),
    "hooks": ("pre-commit", ["run", "--all-files"]),
}


def _linters(tmp_path: Path) -> list[str]:
    for tool in ("yamllint", "ansible-lint", "ruff", "ansible-playbook", "pre-commit"):
        _stand_in(tmp_path / "bin", tool, f'[ "{tool}" != pre-commit ] || printf "%s" "$SKIP" > "{tmp_path / "skip"}"')
    _stand_in(tmp_path / "uv-bin", "uv")
    return [f"BIN={tmp_path / 'bin'}", f"UV={tmp_path / 'uv-bin' / 'uv'}"]


@pytest.mark.parametrize("part", ["", *LINTERS])
def test_lint_runs_every_linter_or_the_one_named(tmp_path: Path, part: str) -> None:
    result = _just("lint", *([part] if part else []), settings=_linters(tmp_path))
    assert result.returncode == 0, _output(result)
    for name, (tool, args) in LINTERS.items():
        ran = any(run[: len(args)] == args for run in _runs(tmp_path, tool))
        assert ran == (not part or name == part), (name, _runs(tmp_path, tool))


def test_lint_takes_several_parts(tmp_path: Path) -> None:
    result = _just("lint", "yaml", "python", settings=_linters(tmp_path))
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, "yamllint") and _runs(tmp_path, "ruff") and not _runs(tmp_path, "ansible-lint")


# Every call a lint part makes, by its tool and its first argument: ruff is called twice.
LINT_CALLS = [
    ("lock", "uv", "lock"),
    ("yaml", "yamllint", "--strict"),
    ("ansible", "ansible-lint", ""),
    ("python", "ruff", "check"),
    ("python", "ruff", "format"),
    ("syntax", "ansible-playbook", "--syntax-check"),
    ("hooks", "pre-commit", "run"),
]


@pytest.mark.parametrize("named", [True, False], ids=["named", "every-part"])
@pytest.mark.parametrize(("part", "tool", "first"), LINT_CALLS, ids=[f"{t}-{f}".strip("-") for _, t, f in LINT_CALLS])
def test_a_linter_that_fails_fails_lint(tmp_path: Path, part: str, tool: str, first: str, named: bool) -> None:
    # bash -e ends nothing at a command left of && or ||: ruff check failing
    # there once left just lint, just test and CI green.
    settings = _linters(tmp_path)
    fails = f'[ "${{1:-}}" != "{first}" ] || exit 1' if first else "exit 1"
    _stand_in(tmp_path / ("uv-bin" if tool == "uv" else "bin"), tool, fails)
    result = _just("lint", *([part] if named else []), settings=settings)
    assert result.returncode != 0, _output(result)
    assert any(run[:1] == ([first] if first else []) for run in _runs(tmp_path, tool)), _runs(tmp_path, tool)


@pytest.mark.parametrize(
    ("given", "env", "skip"),
    [
        ([], {}, "yamllint,ansible-lint,ruff-check,ruff-format,gitleaks"),
        (["--all-hooks"], {}, ""),
        ([], {"SKIP": "gitleaks"}, "yamllint,ansible-lint,ruff-check,ruff-format,gitleaks"),
    ],
)
def test_lint_s_hooks_skip_what_lint_runs_itself(tmp_path: Path, given, env, skip) -> None:
    # A SKIP exported in the shell, for the hooks of another project, is not lint's.
    result = _just("lint", "hooks", *given, settings=_linters(tmp_path), env=env)
    assert result.returncode == 0, _output(result)
    assert (tmp_path / "skip").read_text() == skip


def _testing(tmp_path: Path) -> list[str]:
    settings = _linters(tmp_path)
    _stand_in(tmp_path / "bin", "ansible-galaxy")
    _stand_in(tmp_path / "bin", "pytest")
    _stand_in(tmp_path / "bin", "molecule", f'pwd >> "{tmp_path / "molecule-dirs"}"')
    return [*settings, f"LOG_DIR={tmp_path / 'logs'}"]


def _molecule(tmp_path: Path) -> dict[str, list]:
    dirs = (tmp_path / "molecule-dirs").read_text().split() if (tmp_path / "molecule-dirs").exists() else []
    runs = _runs(tmp_path, "molecule")
    return {
        "role": [Path(d).name for d, run in zip(dirs, runs, strict=True) if run == ["test"]],
        "integration": [run for run in runs if run == ["test", "-s", "configure"]],
        "provision": [run for run in runs if run == ["test", "-s", "provision"]],
    }


SCENARIO_ROLES = [
    "common",
    "vscode_server",
    "dev_tools",
    "claude_code",
    "github_projects",
    "tailscale",
    "proxmox_template",
    "proxmox_vm",
]


@pytest.mark.parametrize(
    ("args", "runs"),
    [
        ([], ["lint", "unit", "role", "integration", "provision"]),
        (["unit"], ["unit"]),
        (["molecule"], ["role", "integration", "provision"]),
        (["role"], ["role"]),
        (["integration"], ["integration"]),
        (["provision"], ["provision"]),
        (["unit", "provision"], ["unit", "provision"]),
        (["--role", "common"], ["role"]),
    ],
)
def test_test_runs_lint_and_every_test_or_the_parts_named(tmp_path: Path, args: list[str], runs: list[str]) -> None:
    result = _just("test", *args, settings=_testing(tmp_path))
    assert result.returncode == 0, _output(result)
    # Lint runs through self, with this run's BIN: its stand-in yamllint ran, never the real one.
    assert bool(_runs(tmp_path, "yamllint")) == ("lint" in runs)
    assert bool(_runs(tmp_path, "pytest")) == ("unit" in runs)
    molecule = _molecule(tmp_path)
    for part in ("role", "integration", "provision"):
        assert bool(molecule[part]) == (part in runs), (part, molecule)


@pytest.mark.parametrize(
    ("args", "tool"),
    [
        ([], "yamllint"),
        (["unit"], "pytest"),
        (["role"], "molecule"),
        (["integration"], "molecule"),
        (["provision"], "molecule"),
    ],
    ids=["lint", "unit", "role", "integration", "provision"],
)
def test_a_part_that_fails_fails_test(tmp_path: Path, args: list[str], tool: str) -> None:
    settings = _testing(tmp_path)
    _stand_in(tmp_path / "bin", tool, rc=1)
    result = _just("test", *args, settings=settings)
    assert result.returncode != 0, _output(result)
    # The first failure ends the run: nothing after it starts.
    assert len(_runs(tmp_path, tool)) == 1, _runs(tmp_path, tool)


def test_every_role_scenario_runs_one_after_another(tmp_path: Path) -> None:
    result = _just("test", "role", settings=_testing(tmp_path))
    assert result.returncode == 0, _output(result)
    assert _molecule(tmp_path)["role"] == SCENARIO_ROLES == _evaluate("scenario_roles").split()


@pytest.mark.parametrize(
    ("roles", "expect"), [("common", ["common"]), ("common,claude_code", ["common", "claude_code"])]
)
def test_role_narrows_the_role_scenarios(tmp_path: Path, roles: str, expect: list[str]) -> None:
    result = _just("test", "--role", roles, settings=_testing(tmp_path))
    assert result.returncode == 0, _output(result)
    assert _molecule(tmp_path)["role"] == expect


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["--role", "bogus"], "--role may name only common, vscode_server"),
        (["--role", "../x"], NOT_MATCHED),
        (
            ["--role", "common", "unit"],
            "--role narrows the role scenarios, which unit does not run: just test --role common",
        ),
    ],
)
def test_role_is_checked_before_anything_runs(tmp_path: Path, args: list[str], error: str) -> None:
    result = _just("test", *args, settings=_testing(tmp_path))
    assert result.returncode != 0
    assert error in _output(result)
    assert not (tmp_path / "order").exists()


@pytest.mark.parametrize(
    ("args", "tool", "expect"),
    [
        ([], "python", ["tools/docs.py"]),
        (["serve"], "zensical", ["serve", "-a", "127.0.0.1:8000"]),
        (["build"], "zensical", ["build", "--strict", "--clean"]),
    ],
)
def test_docs_regenerates_the_pages_or_serves_or_builds_the_site(tmp_path: Path, args, tool, expect) -> None:
    _stand_in(tmp_path / "bin", tool)
    result = _just("docs", *args, settings=[f"BIN={tmp_path / 'bin'}"])
    assert result.returncode == 0, _output(result)
    assert _runs(tmp_path, tool) == [expect]
