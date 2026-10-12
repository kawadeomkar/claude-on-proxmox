"""The managed SSH config, written for real into a temporary home.

`playbooks/tasks/ssh_config.yml` is the one thing this project writes on the
controller besides known_hosts: one alias block per VM in a managed file, and
one Include line at the top of the user's own ssh config. A bug there breaks
every `ssh` on the developer's machine, so the task file is run here as it is
run by configure.yml, destroy.yml and ssh_config.yml - against paths under
tmp_path, never ~/.ssh - and the result is resolved with the real ssh client,
the way VS Code's Remote - SSH extension reads it.

The last tests guard the tests: every playbook scenario and the Vagrantfile
must point the managed file into Molecule's ephemeral directory or switch the
feature off, because the `configure` scenario runs the real playbook and its
localhost is the developer's machine.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
TASK_FILE = REPO / "playbooks" / "tasks" / "ssh_config.yml"
SSH = "ssh"

ALPHA = {"name": "alpha", "address": "192.0.2.51", "user": "dev", "workspace": "/home/dev/projects"}
BETA = {"name": "beta", "address": "192.0.2.52", "user": "dev", "workspace": "/home/dev/projects"}


def _paths(tmp_path: Path) -> dict[str, str]:
    ssh_dir = tmp_path / "home" / ".ssh"
    return {
        "claude_ssh_config_file": str(ssh_dir / "claude-on-proxmox.conf"),
        "claude_ssh_user_config": str(ssh_dir / "config"),
    }


def _run(tmp_path: Path, vms: list[dict], state: str = "present", **extra) -> subprocess.CompletedProcess[str]:
    """Run the task file from a one-play wrapper, as every caller does, on localhost."""
    playbook = tmp_path / "ssh_config_test.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Run the ssh_config task file",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [{"name": "Write or remove the aliases", "ansible.builtin.import_tasks": str(TASK_FILE)}],
                }
            ]
        )
    )
    # No project config or inventory: a developer's own inventory would bring
    # in a vault this cannot open, and the defaults under test are passed here.
    (tmp_path / "ansible.cfg").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    # HOME is the temporary one too: the task file recognises the ~/... and
    # ${HOME}/... spellings of the Include by it.
    env.update(ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"), ANSIBLE_NOCOLOR="1", HOME=str(tmp_path / "home"))
    # The project default for the options, from inventory/group_vars, which
    # the wrapper does not load; the task file itself defaults to none.
    extra_vars = {
        "ansible_connection": "local",
        "ansible_python_interpreter": sys.executable,
        "claude_ssh_vms": vms,
        "claude_ssh_state": state,
        "claude_ssh_options": {"ServerAliveInterval": "30"},
        **_paths(tmp_path),
        **extra,
    }
    return subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-e", json.dumps(extra_vars), str(playbook)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _changed(result: subprocess.CompletedProcess[str]) -> int:
    match = re.search(r"localhost\s+: ok=\d+\s+changed=(\d+)", result.stdout)
    assert match, result.stdout + result.stderr
    return int(match.group(1))


def _managed(tmp_path: Path) -> str:
    return Path(_paths(tmp_path)["claude_ssh_config_file"]).read_text()


def _user_config(tmp_path: Path) -> Path:
    return Path(_paths(tmp_path)["claude_ssh_user_config"])


def _blocks(text: str) -> list[str]:
    return re.findall(r"^# BEGIN claude-on-proxmox: (\S+)$", text, flags=re.M)


def _resolve(tmp_path: Path, alias: str) -> dict[str, str]:
    """What the real ssh client makes of the managed file, as VS Code would."""
    result = subprocess.run(
        [SSH, "-G", "-F", _paths(tmp_path)["claude_ssh_config_file"], alias],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)


def test_present_writes_one_block_per_vm_and_the_include_at_the_top(tmp_path: Path) -> None:
    result = _run(tmp_path, [ALPHA, BETA])
    assert result.returncode == 0, result.stdout + result.stderr

    managed = _managed(tmp_path)
    assert _blocks(managed) == ["alpha", "beta"]
    assert "# BEGIN claude-on-proxmox: alpha\nHost alpha\n    HostName 192.0.2.51\n    User dev\n" in managed
    # The block holds what ssh reads, and no state of this project's.
    assert "# vmid" not in managed
    assert "# workspace" not in managed
    assert "ForwardAgent" not in managed
    assert "IdentityFile" not in managed
    assert managed.endswith("# END claude-on-proxmox: beta\n")

    user_config = _user_config(tmp_path)
    assert user_config.read_text().splitlines()[0] == f"Include {_paths(tmp_path)['claude_ssh_config_file']}"
    assert oct(user_config.stat().st_mode & 0o777) == "0o600"
    assert oct(Path(_paths(tmp_path)["claude_ssh_config_file"]).stat().st_mode & 0o777) == "0o600"
    assert oct(user_config.parent.stat().st_mode & 0o777) == "0o700"

    resolved = _resolve(tmp_path, "alpha")
    assert resolved["hostname"] == "192.0.2.51"
    assert resolved["user"] == "dev"
    assert resolved["serveraliveinterval"] == "30"
    assert "alpha: ssh alpha   |   make code VM_NAME=alpha   (opens /home/dev/projects)" in result.stdout


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    assert _run(tmp_path, [ALPHA, BETA]).returncode == 0
    again = _run(tmp_path, [ALPHA, BETA])
    assert again.returncode == 0, again.stdout + again.stderr
    assert _changed(again) == 0


def test_a_new_address_rewrites_one_block_and_keeps_the_include_once(tmp_path: Path) -> None:
    assert _run(tmp_path, [ALPHA, BETA]).returncode == 0
    moved = _run(tmp_path, [{**ALPHA, "address": "192.0.2.77"}])
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert _changed(moved) == 1

    managed = _managed(tmp_path)
    assert _blocks(managed) == ["alpha", "beta"]
    assert _resolve(tmp_path, "alpha")["hostname"] == "192.0.2.77"
    assert _resolve(tmp_path, "beta")["hostname"] == "192.0.2.52"
    assert _user_config(tmp_path).read_text().count("Include ") == 1


def test_absent_removes_only_that_block(tmp_path: Path) -> None:
    assert _run(tmp_path, [ALPHA, BETA]).returncode == 0
    before = _user_config(tmp_path).read_text()
    removed = _run(tmp_path, [{"name": "alpha"}], state="absent")
    assert removed.returncode == 0, removed.stdout + removed.stderr

    assert _blocks(_managed(tmp_path)) == ["beta"]
    assert _resolve(tmp_path, "beta")["hostname"] == "192.0.2.52"
    # The Include stays: removing it would mean editing the user's file on
    # destroy, and including a missing file is harmless.
    assert _user_config(tmp_path).read_text() == before


def test_absent_without_a_managed_file_is_a_no_op(tmp_path: Path) -> None:
    result = _run(tmp_path, [{"name": "alpha"}], state="absent")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _changed(result) == 0
    assert not Path(_paths(tmp_path)["claude_ssh_config_file"]).exists()
    assert not _user_config(tmp_path).exists()


def test_an_alias_the_user_already_defines_is_refused_by_name(tmp_path: Path) -> None:
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Host github.com\n    User git\n\nHost alpha 192.0.2.9\n    User someone\n")
    before = user_config.read_text()

    result = _run(tmp_path, [ALPHA, BETA])
    assert result.returncode != 0
    assert "already has a Host entry named alpha" in result.stdout + result.stderr
    assert "Rename the VM, or rename that entry" in result.stdout + result.stderr
    # Refused before anything was written: neither file touched.
    assert user_config.read_text() == before
    assert not Path(_paths(tmp_path)["claude_ssh_config_file"]).exists()


def test_a_host_line_naming_several_hosts_counts_for_each(tmp_path: Path) -> None:
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Host pve beta 192.0.2.100\n    User root\n")
    result = _run(tmp_path, [ALPHA, BETA])
    assert result.returncode != 0
    assert "named beta" in result.stdout + result.stderr


def test_a_wildcard_host_line_is_not_a_collision(tmp_path: Path) -> None:
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Host *\n    ServerAliveInterval 60\n")
    result = _run(tmp_path, [ALPHA])
    assert result.returncode == 0, result.stdout + result.stderr
    assert user_config.read_text().startswith(f"Include {_paths(tmp_path)['claude_ssh_config_file']}\nHost *\n")


def test_include_off_leaves_the_user_config_alone_and_says_what_to_add(tmp_path: Path) -> None:
    result = _run(tmp_path, [ALPHA], claude_ssh_config_include=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not _user_config(tmp_path).exists()
    assert _blocks(_managed(tmp_path)) == ["alpha"]
    assert f"Put this line at its top: Include {_paths(tmp_path)['claude_ssh_config_file']}" in result.stdout


def test_a_host_domain_replaces_the_address(tmp_path: Path) -> None:
    result = _run(tmp_path, [ALPHA], claude_ssh_host_domain="lan")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _resolve(tmp_path, "alpha")["hostname"] == "alpha.lan"
    assert "192.0.2.51" not in _managed(tmp_path)


def test_identity_file_agent_forwarding_and_options_are_written_when_set(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        [ALPHA],
        claude_ssh_identity_file="~/.ssh/id_ed25519_vms",
        claude_ssh_forward_agent=True,
        claude_ssh_options={"ServerAliveInterval": "15", "ConnectTimeout": "5"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    managed = _managed(tmp_path)
    assert "    IdentityFile ~/.ssh/id_ed25519_vms\n" in managed
    assert "    ForwardAgent yes\n" in managed
    resolved = _resolve(tmp_path, "alpha")
    assert resolved["forwardagent"] == "yes"
    assert resolved["serveraliveinterval"] == "15"
    assert resolved["connecttimeout"] == "5"


def test_without_a_workspace_the_connect_line_says_make_code_and_guesses_no_path(tmp_path: Path) -> None:
    result = _run(tmp_path, [{"name": "solo", "address": "192.0.2.60", "user": "dev"}])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "solo: ssh solo   |   make code VM_NAME=solo" in result.stdout
    assert "/home/dev/projects" not in result.stdout


def test_the_mode_of_an_existing_user_config_is_left_alone(tmp_path: Path) -> None:
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Host github.com\n    User git\n")
    user_config.chmod(0o644)
    result = _run(tmp_path, [ALPHA])
    assert result.returncode == 0, result.stdout + result.stderr
    assert user_config.read_text().startswith("Include ")
    assert oct(user_config.stat().st_mode & 0o777) == "0o644"


@pytest.mark.parametrize(
    "line",
    [
        "Include ~/.ssh/claude-on-proxmox.conf",
        "Include ${HOME}/.ssh/claude-on-proxmox.conf",
        "Include claude-on-proxmox.conf",
        "  include   ~/.ssh/claude-on-proxmox.conf  ",
    ],
)
def test_an_include_already_there_in_another_spelling_is_not_added_again(tmp_path: Path, line: str) -> None:
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    before = f"{line}\n\nHost github.com\n    User git\n"
    user_config.write_text(before)
    result = _run(tmp_path, [ALPHA])
    assert result.returncode == 0, result.stdout + result.stderr
    # Not added again when this machine's ssh reads that spelling (${HOME}/...
    # only from OpenSSH 9.9 on), added above it when it does not; the user's
    # own line is not rewritten either way.
    if _ssh_reads_include(tmp_path, line):
        assert user_config.read_text() == before
    else:
        assert user_config.read_text() == f"Include {_paths(tmp_path)['claude_ssh_config_file']}\n{before}"
    # And either way ssh itself now reaches the managed file through the user's config.
    assert "hostname 192.0.2.51" in _probe(tmp_path, user_config)


def _probe(tmp_path: Path, config: Path) -> str:
    """What the real ssh client makes of the user's config, with the temporary home."""
    return subprocess.run(
        [SSH, "-G", "-F", str(config), "alpha"],
        env={**os.environ, "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _ssh_reads_include(tmp_path: Path, line: str) -> bool:
    """Whether this machine's ssh reaches ~/.ssh/claude-on-proxmox.conf through that Include spelling."""
    home = tmp_path / "probe-home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "claude-on-proxmox.conf").write_text("Host alpha\n    HostName 192.0.2.99\n")
    config = home / ".ssh" / "config"
    config.write_text(f"{line}\n")
    out = subprocess.run(
        [SSH, "-G", "-F", str(config), "alpha"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return "hostname 192.0.2.99" in out


def test_an_include_below_a_host_block_does_not_count(tmp_path: Path) -> None:
    # ssh scopes an Include inside a Host block to that block, so this one
    # reaches the managed file for github.com only; the line is added at the
    # top, and the user's own kept.
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    before = "Host github.com\n    User git\n    Include ~/.ssh/claude-on-proxmox.conf\n"
    user_config.write_text(before)
    result = _run(tmp_path, [ALPHA])
    assert result.returncode == 0, result.stdout + result.stderr
    assert user_config.read_text() == f"Include {_paths(tmp_path)['claude_ssh_config_file']}\n{before}"
    assert "hostname 192.0.2.51" in _probe(tmp_path, user_config)
    # And a second run leaves it at that.
    again = _run(tmp_path, [ALPHA])
    assert again.returncode == 0, again.stdout + again.stderr
    assert "changed=0" in again.stdout
    assert user_config.read_text() == f"Include {_paths(tmp_path)['claude_ssh_config_file']}\n{before}"


def test_a_bad_option_fails_validation_and_leaves_the_file_alone(tmp_path: Path) -> None:
    assert _run(tmp_path, [ALPHA]).returncode == 0
    before = _managed(tmp_path)
    result = _run(tmp_path, [BETA], claude_ssh_options={"NoSuchOption": "1"})
    assert result.returncode != 0
    assert "failed to validate" in result.stdout + result.stderr
    assert _managed(tmp_path) == before


# ---------------------------------------------------------------- guards ---
# These keep `make test` out of the developer's ~/.ssh.

SCENARIO_DIRS = sorted(p for p in (REPO / "molecule").iterdir() if p.is_dir())
EPHEMERAL = "MOLECULE_EPHEMERAL_DIRECTORY"


def _scenario_vars(scenario: Path) -> dict:
    """Every variable a scenario sets itself, skipping the project defaults linked in."""
    merged: dict = {}
    for sub in ("group_vars", "host_vars"):
        for path in sorted((scenario / sub).rglob("*.yml")) if (scenario / sub).exists() else []:
            if path.is_symlink():
                continue
            merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


@pytest.mark.parametrize("scenario", SCENARIO_DIRS, ids=[p.name for p in SCENARIO_DIRS])
def test_every_playbook_scenario_keeps_the_managed_file_out_of_the_home_directory(scenario: Path) -> None:
    found = _scenario_vars(scenario)
    if found.get("claude_ssh_config") is False:
        return
    for key in ("claude_ssh_config_file", "claude_ssh_user_config"):
        assert EPHEMERAL in str(found.get(key, "")), (
            f"{scenario.name}: {key} must point into ${EPHEMERAL}, or claude_ssh_config must be false, "
            "or the scenario writes into the developer's ~/.ssh"
        )
    assert found.get("claude_ssh_config_include") is False, f"{scenario.name}: claude_ssh_config_include must be false"


def test_the_vagrantfile_switches_the_managed_file_off() -> None:
    assert re.search(r"^\s*claude_ssh_config: false,", (REPO / "Vagrantfile").read_text(), flags=re.M)


def test_no_role_scenario_touches_the_task_file() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "roles").glob("*/molecule/**/*.yml")
        if "ssh_config.yml" in path.read_text()
    ]
    assert offenders == []


def test_no_playbook_sets_the_task_files_inputs_as_facts() -> None:
    """A fact outranks the task vars an import passes, so a second import acts on the first list."""
    offenders = []
    for path in [*(REPO / "playbooks").glob("*.yml"), *(REPO / "molecule").glob("*/*.yml")]:
        plays = yaml.safe_load(path.read_text())
        if not isinstance(plays, list):  # molecule.yml and other settings files
            continue
        for play in (play for play in plays if isinstance(play, dict)):
            for task in play.get("tasks", []) + play.get("post_tasks", []):
                facts = task.get("ansible.builtin.set_fact") or task.get("set_fact") or {}
                if isinstance(facts, dict) and {"claude_ssh_vms", "claude_ssh_state"} & facts.keys():
                    offenders.append(f"{path.relative_to(REPO)}: {task.get('name')}")
    assert offenders == []


def test_an_include_ssh_does_not_expand_is_not_taken_for_ours(tmp_path: Path) -> None:
    """ssh expands ${HOME} but not $HOME, so that line includes nothing and ours is added."""
    user_config = _user_config(tmp_path)
    user_config.parent.mkdir(parents=True)
    user_config.write_text("Include $HOME/.ssh/claude-on-proxmox.conf\n")
    result = _run(tmp_path, [ALPHA])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = user_config.read_text().splitlines()
    assert lines[0] == f"Include {_paths(tmp_path)['claude_ssh_config_file']}"
    assert "Include $HOME/.ssh/claude-on-proxmox.conf" in lines
    probe = subprocess.run(
        [SSH, "-G", "-F", str(user_config), "alpha"],
        env={**os.environ, "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "hostname 192.0.2.51" in probe.stdout
