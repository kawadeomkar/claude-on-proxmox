"""playbooks/init_settings.yml, just init's questions: run against copies of the example files, never the checkout's.

Every run here points the four init_*_file variables into its own temporary
directory, through _init or _init_at_a_terminal alone, and
test_no_run_reaches_the_checkouts_files holds that: the playbook's defaults
are this checkout's own hosts.yml, local.yml, vault.yml and .vault_pass.

A run with stdin /dev/null is just init with no terminal; one at a
pseudo-terminal of the harness's own (_run_at_a_terminal) is a person typing.
The secret every test types or gives is SECRET, and no run may print it or log
it, at -vvv with every module's arguments in its result.

At a terminal, just init offers to set up the Proxmox API access over root
SSH. No run reaches a real ssh: every one has a refusing ssh first on PATH
(_no_real_ssh), which records its call and exits 255, as an unreachable host
does, and the runs that take the offer reach a stand-in host instead - the
local connection, with the stand-in pveum and pvesh first on its PATH
(_stand_in_proxmox). test_no_run_reaches_a_real_ssh holds every run to that.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import (
    REPO,
    _desktop,
    _env,
    _failures,
    _messages,
    _pve_cli,
    _pve_cli_calls,
    _pve_cli_state,
    _run,
    _run_at_a_terminal,
)

PLAYBOOK = REPO / "playbooks" / "init_settings.yml"
INVENTORY = REPO / "playbooks" / "inventory" / "init.yml"
ANSIBLE_VAULT = Path(sys.executable).with_name("ansible-vault")
EXAMPLES = {
    "init_hosts_file": REPO / "inventory" / "hosts.yml.example",
    "init_local_file": REPO / "inventory" / "group_vars" / "all" / "local.yml.example",
    "init_vault_file": REPO / "inventory" / "group_vars" / "all" / "vault.yml.example",
}
FILE_VARS = {*EXAMPLES, "init_vault_pass_file"}
SECRET = "11111111-2222-3333-4444-555555555555"
SECRET_LINE = f'vault_proxmox_api_token_secret: "{SECRET}"'
GITHUB_LINE = 'vault_github_username: "octocat"'
HOST_PROMPT = b"Proxmox host address:"
TOKEN_PROMPT = b"API token ID:"
SECRET_PROMPT = b"API token secret"
GITHUB_PROMPT = b"GitHub username:"
ACCESS_PROMPT = b"Set it up now? y or n"
SSH_STAND_IN = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/ssh-calls.log"
echo "ssh: connect to host pve.lan port 22: Connection refused" >&2
exit 255
"""


def _files(root: Path) -> dict:
    """Copies of the three example files under root, and the vars pointing the playbook at them and a .vault_pass."""
    # In a directory of their own: the harness writes its inventory, hosts.yml, in root.
    checkout = root / "checkout"
    checkout.mkdir()
    files = {}
    for var, example in EXAMPLES.items():
        copy = checkout / example.name.removesuffix(".example")
        copy.write_text(example.read_text())
        files[var] = str(copy)
    files["init_vault_pass_file"] = str(checkout / ".vault_pass")
    return files


def _held(root: Path, files: dict) -> None:
    # Every file the run can write is under the test's own directory.
    assert set(files) == FILE_VARS, files
    for path in files.values():
        assert Path(path).is_relative_to(root) and not Path(path).is_relative_to(REPO), path


def _no_real_ssh(root: Path, env: dict | None = None) -> dict:
    """env with a refusing ssh first on PATH: one that records its call in root/ssh-calls.log and exits 255."""
    stand_in = root / "ssh-stand-in"
    stand_in.mkdir(exist_ok=True)
    (stand_in / "ssh").write_text(SSH_STAND_IN)
    (stand_in / "ssh").chmod(0o755)
    path = f"{stand_in}{os.pathsep}{_desktop(root)}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    return (env or {}) | {"PATH": path}


def _ssh_calls(root: Path) -> str:
    log = root / "ssh-stand-in" / "ssh-calls.log"
    return log.read_text() if log.exists() else ""


def _init(root: Path, files: dict, extra: dict | None = None, *args: str, env: dict | None = None) -> dict:
    """init_settings.yml with stdin /dev/null, as just init runs it with no terminal; the JSON report."""
    _held(root, files)
    extra_vars = json.dumps(files | (extra or {}))
    return _run(root, PLAYBOOK, {}, "-i", str(INVENTORY), "-e", extra_vars, *args, env=_no_real_ssh(root, env))


def _init_at_a_terminal(
    root: Path, files: dict, steps, extra: dict | None = None, env: dict | None = None, **kwargs
) -> tuple[int, str, str]:
    """init_settings.yml at a pseudo-terminal, as just init runs it at one (init_ask), typing steps; rc, output, log."""
    _held(root, files)
    return _run_at_a_terminal(
        root,
        PLAYBOOK,
        {},
        files | {"init_ask": True} | (extra or {}),
        steps=steps,
        args=["-i", str(INVENTORY)],
        env=_no_real_ssh(root, env),
        **kwargs,
    )


def _stand_in_proxmox(root: Path, files: dict, state: dict | None = None, where: str = "host") -> Path:
    """Point hosts.yml's Proxmox host at a stand-in: the local connection, through a Python whose PATH starts with
    the stand-in pveum, pvesh and the rest (fake_pve_cli.py), made with state; returns their directory. The
    settings go on the host, or with where="group" in the proxmox group's vars."""
    host = _pve_cli(root / "pve", {"next_secret": SECRET} | (state or {}))
    python = root / "pve-python"
    python.write_text(f'#!/bin/sh\nPATH="{host}:$PATH"\nexport PATH\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    hosts = Path(files["init_hosts_file"])
    if where == "host":
        text = hosts.read_text().replace(
            "          ansible_user: root\n",
            f"          ansible_user: root\n          ansible_connection: local\n"
            f"          ansible_python_interpreter: {python}\n",
        )
    else:
        text = hosts.read_text().replace(
            "    proxmox:\n",
            "    proxmox:\n      vars:\n        ansible_connection: local\n"
            f"        ansible_python_interpreter: {python}\n",
        )
    assert text != hosts.read_text()
    hosts.write_text(text)
    return host


def _snapshot(files: dict) -> dict:
    """Each file's bytes and mtime, or None where it does not exist."""
    snapshot = {}
    for path in map(Path, files.values()):
        snapshot[path.name] = (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
    return snapshot


def _ansible_vault(files: dict, *args: str, text: str | None = None) -> str:
    """ansible-vault with args, text on its stdin; its stdout. In the harness's environment under the test's
    directory (_files' root), never this shell's: an ANSIBLE_VERBOSITY there puts more than the vault on stdout,
    and an ANSIBLE_LOG_PATH, just test unit's run log among them, gains its lines."""
    root = Path(files["init_vault_file"]).parents[1]
    return subprocess.run(
        [str(ANSIBLE_VAULT), *args],
        cwd=root,
        env=_env(root),
        input=text,
        stdin=None if text is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _decrypt(files: dict) -> str:
    password = files["init_vault_pass_file"]
    return _ansible_vault(
        files, "decrypt", "--vault-password-file", password, "--output", "-", files["init_vault_file"]
    )


def _example(var: str) -> str:
    return EXAMPLES[var].read_text()


def _changed(report: dict) -> int:
    return sum(host["changed"] for host in report["stats"].values())


def _said(report: dict, task: str) -> str:
    return "\n".join(str(message) for message in _messages(report, task).values())


def _mode(path: str) -> int:
    return Path(path).stat().st_mode & 0o777


def _encrypt(files: dict, text: str, password: str = "a-password-of-the-persons-own\n") -> None:
    Path(files["init_vault_pass_file"]).write_text(password)
    Path(files["init_vault_pass_file"]).chmod(0o600)
    _ansible_vault(
        files,
        "encrypt",
        "--vault-password-file",
        files["init_vault_pass_file"],
        "--output",
        files["init_vault_file"],
        "-",
        text=text,
    )


# Typed: every question, in order, each answer and Enter.
EVERY_ANSWER = [
    (HOST_PROMPT, b"pve.lan\n"),
    (TOKEN_PROMPT, b"\n"),
    (GITHUB_PROMPT, b"octocat\n"),
    (SECRET_PROMPT, SECRET.encode() + b"\n"),
]


# ------------------------------------------------------------------ no terminal ---
@pytest.mark.parametrize("asking", [False, True], ids=["init_ask-off", "init_ask-on"])
def test_without_a_terminal_nothing_is_written(tmp_path: Path, asking: bool) -> None:
    # With init_ask on, each pause returns "" at once, as Enter: nothing is
    # kept, and with nothing kept nothing is written - but the vault, which a
    # terminal would have encrypted, stays as it is only without init_ask.
    files = _files(tmp_path)
    before = _snapshot(files)
    report = _init(tmp_path, files, {"init_ask": asking} if asking else None)
    assert report["rc"] == 0, _failures(report)
    after = _snapshot(files)
    if asking:
        assert after["hosts.yml"] == before["hosts.yml"] and after["local.yml"] == before["local.yml"]
        assert Path(files["init_vault_file"]).read_text().startswith("$ANSIBLE_VAULT")
    else:
        assert after == before
        summary = _said(report, "Say what was written, and what is left")
        assert "Nothing is asked without a terminal" in summary
        assert "the Proxmox host address" in summary and "the API token secret" in summary


def test_run_by_hand_at_a_terminal_without_init_ask_asks_nothing(tmp_path: Path) -> None:
    files = _files(tmp_path)
    before = _snapshot(files)
    rc, output, _ = _run_at_a_terminal(
        tmp_path, PLAYBOOK, {}, files, steps=[], args=["-i", str(INVENTORY)], env=_no_real_ssh(tmp_path)
    )
    assert rc == 0, output
    assert "USER INPUT REQUIRED" not in output
    assert _snapshot(files) == before


# ---------------------------------------------------------------------- typed ---
def test_every_answer_typed_writes_the_three_files(tmp_path: Path) -> None:
    files = _files(tmp_path)
    rc, output, log = _init_at_a_terminal(tmp_path, files, EVERY_ANSWER)
    assert rc == 0, output
    hosts = Path(files["init_hosts_file"]).read_text().splitlines()
    example = _example("init_hosts_file").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(example, hosts, strict=True)) if a != b]
    comment = " # IP of your Proxmox host (also used for the API)"
    assert changed == [example.index("          ansible_host: 192.0.2.10" + comment)]
    assert hosts[changed[0]] == '          ansible_host: "pve.lan"' + comment
    # The default token ID writes nothing.
    assert Path(files["init_local_file"]).read_text() == _example("init_local_file")
    vault = Path(files["init_vault_file"]).read_text()
    assert vault.startswith("$ANSIBLE_VAULT;1.1;AES256")
    expected = (
        _example("init_vault_file")
        .replace('vault_proxmox_api_token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"', SECRET_LINE)
        .replace('vault_github_username: "your-github-username"', GITHUB_LINE)
    )
    assert _decrypt(files) == expected
    assert _mode(files["init_vault_file"]) == 0o600
    assert _mode(files["init_vault_pass_file"]) == 0o600
    assert "NOTICE  vault.yml is encrypted" in output
    assert SECRET not in output and SECRET not in log


def test_the_same_answers_again_ask_nothing_and_change_nothing(tmp_path: Path) -> None:
    files = _files(tmp_path)
    rc, output, _ = _init_at_a_terminal(tmp_path, files, EVERY_ANSWER)
    assert rc == 0, output
    before = _snapshot(files)
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [])
    assert rc == 0, output
    assert "USER INPUT REQUIRED" not in output
    assert "changed=0" in output
    assert "Every value a first deploy needs is set." in output
    assert _snapshot(files) == before


def test_another_token_id_writes_its_two_settings_in_place_of_their_examples(tmp_path: Path) -> None:
    files = _files(tmp_path)
    steps = [(HOST_PROMPT, b"\n"), (TOKEN_PROMPT, b"deploy@pam!claude\n")]
    steps += [(GITHUB_PROMPT, b"\n"), (SECRET_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    expected = (
        _example("init_local_file")
        .replace("# proxmox_api_user: ansible@pve\n", 'proxmox_api_user: "deploy@pam"\n')
        .replace("# proxmox_api_token_id: ansible\n", 'proxmox_api_token_id: "claude"\n')
    )
    assert Path(files["init_local_file"]).read_text() == expected


def test_only_the_part_of_a_token_id_that_differs_is_written(tmp_path: Path) -> None:
    files = _files(tmp_path)
    steps = [(HOST_PROMPT, b"\n"), (TOKEN_PROMPT, b"ansible@pve!claude\n")]
    steps += [(GITHUB_PROMPT, b"\n"), (SECRET_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    local = Path(files["init_local_file"]).read_text()
    assert 'proxmox_api_token_id: "claude"\n' in local and "# proxmox_api_user: ansible@pve\n" in local


@pytest.mark.parametrize(
    ("prompt", "wrong", "rule"),
    [
        pytest.param(HOST_PROMPT, "pve.lan; x", "an IP address or a host name", id="host-semicolon"),
        pytest.param(HOST_PROMPT, "1.2.3.4 ProxyCommand x", "an IP address or a host name", id="host-space"),
        pytest.param(HOST_PROMPT, "https://pve:8006", "without https:// or a port", id="host-url"),
        pytest.param(HOST_PROMPT, '"quoted"', "an IP address or a host name", id="host-quoted"),
        pytest.param(HOST_PROMPT, "a#b", "an IP address or a host name", id="host-hash"),
        pytest.param(HOST_PROMPT, "192.0.2.10", "not the example address", id="host-example"),
        pytest.param(TOKEN_PROMPT, "ansible@pve", "user@realm!name", id="token-no-name"),
        pytest.param(SECRET_PROMPT, "11111111-2222", "36-character secret", id="secret-short"),
        pytest.param(GITHUB_PROMPT, "-octocat", "GitHub username", id="github-hyphen"),
    ],
)
def test_a_wrong_answer_is_asked_for_once_more_then_left(tmp_path: Path, prompt: bytes, wrong: str, rule: str) -> None:
    files = _files(tmp_path)
    steps = []
    for asked, _ in EVERY_ANSWER:
        if asked == prompt:
            steps += [(asked, wrong.encode() + b"\n"), (asked, wrong.encode() + b"\n")]
        else:
            steps.append((asked, b"\n"))
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert "That is not" in output and rule in output
    assert "is left as it was" in output
    assert Path(files["init_hosts_file"]).read_text() == _example("init_hosts_file")
    assert Path(files["init_local_file"]).read_text() == _example("init_local_file")


def test_a_wrong_answer_put_right_is_kept(tmp_path: Path) -> None:
    files = _files(tmp_path)
    steps = [(HOST_PROMPT, b"https://pve.lan:8006\n"), (HOST_PROMPT, b"10.0.0.5\n")]
    steps += [(TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"\n"), (SECRET_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert '          ansible_host: "10.0.0.5" #' in Path(files["init_hosts_file"]).read_text()


def test_enter_at_every_question_encrypts_the_vault_and_asks_again_next_time(tmp_path: Path) -> None:
    files = _files(tmp_path)
    enter = [(prompt, b"\n") for prompt, _ in EVERY_ANSWER]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, enter)
    assert rc == 0, output
    assert Path(files["init_hosts_file"]).read_text() == _example("init_hosts_file")
    assert Path(files["init_local_file"]).read_text() == _example("init_local_file")
    assert _decrypt(files) == _example("init_vault_file")
    assert "NOTICE  vault.yml is encrypted" in output and "cannot be opened without it" in output
    # The second run opens the vault with the .vault_pass the first made, and
    # asks again for the three values still missing, the token ID with them.
    rc, output, _ = _init_at_a_terminal(tmp_path, files, enter)
    assert rc == 0, output
    assert "NOTICE  vault.yml is encrypted" not in output


def test_set_values_are_never_asked(tmp_path: Path) -> None:
    files = _files(tmp_path)
    hosts = Path(files["init_hosts_file"])
    hosts.write_text(hosts.read_text().replace("192.0.2.10", "10.0.0.5"))
    local = Path(files["init_local_file"])
    local.write_text(local.read_text() + "proxmox_api_token_id: claude\n")
    text = _example("init_vault_file").replace(
        'vault_proxmox_api_token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"', SECRET_LINE
    )
    _encrypt(files, text.replace('vault_github_username: "your-github-username"', GITHUB_LINE))
    before = _snapshot(files)
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [])
    assert rc == 0, output
    assert "USER INPUT REQUIRED" not in output
    assert _snapshot(files) == before


def test_an_encrypted_vault_with_a_placeholder_is_asked_and_encrypted_again(tmp_path: Path) -> None:
    files = _files(tmp_path)
    _encrypt(files, _example("init_vault_file"))
    password = Path(files["init_vault_pass_file"])
    pass_before = (password.read_bytes(), password.stat().st_mtime_ns, _mode(str(password)))
    steps = [(HOST_PROMPT, b"\n"), (TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [*steps, (SECRET_PROMPT, SECRET.encode() + b"\n")])
    assert rc == 0, output
    assert SECRET_LINE in _decrypt(files)
    # The person's own .vault_pass is used, never rewritten.
    assert (password.read_bytes(), password.stat().st_mtime_ns, _mode(str(password))) == pass_before
    assert "NOTICE  vault.yml is encrypted" not in output


@pytest.mark.parametrize("noisy", [{"ANSIBLE_VERBOSITY": "3"}, {"ANSIBLE_DEBUG": "1"}], ids=["verbose", "debug"])
def test_a_verbose_or_debugging_environment_reads_and_writes_the_vault_as_it_is(tmp_path: Path, noisy: dict) -> None:
    # ansible-vault takes both from the environment, and then prints more than
    # the vault's text on the stdout the vault is read from: just init runs it
    # quiet, so the vault opens, takes the answer and reads back as written.
    files = _files(tmp_path)
    _encrypt(files, _example("init_vault_file"))
    steps = [(HOST_PROMPT, b"\n"), (TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"octocat\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [*steps, (SECRET_PROMPT, SECRET.encode() + b"\n")], env=noisy)
    assert rc == 0, output[-3000:]
    assert _decrypt(files) == _example("init_vault_file").replace(
        'vault_proxmox_api_token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"', SECRET_LINE
    ).replace('vault_github_username: "your-github-username"', GITHUB_LINE)


def test_every_ansible_vault_runs_quiet():
    tasks = [
        task
        for task in _tasks(yaml.safe_load(PLAYBOOK.read_text()))
        if "block" not in task and "init_ansible_vault" in json.dumps(task)
    ]
    assert [task["name"] for task in tasks] == [
        "Open the encrypted vault",
        "Write the vault, encrypted",
        "Open the vault again",
    ]
    for task in tasks:
        assert task["environment"] == "{{ init_ansible_vault_env }}", task["name"]
    play = yaml.safe_load(PLAYBOOK.read_text())[0]["vars"]
    assert play["init_ansible_vault_env"] == {"ANSIBLE_VERBOSITY": "0", "ANSIBLE_DEBUG": "0"}


def test_an_encrypted_vault_with_no_password_is_left_alone(tmp_path: Path) -> None:
    files = _files(tmp_path)
    _encrypt(files, _example("init_vault_file"))
    Path(files["init_vault_pass_file"]).unlink()
    before = _snapshot(files)
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [(HOST_PROMPT, b"\n")])
    assert rc == 0, output
    assert output.count("USER INPUT REQUIRED") == 1
    assert "just vault opens it" in output
    assert _snapshot(files) == before


# ---------------------------------------------------------------------- given ---
def _secret_file(root: Path, text: str = SECRET + "\n", mode: int = 0o600) -> Path:
    path = root / "token-secret"
    path.write_text(text)
    path.chmod(mode)
    return path


def _every_value_given(root: Path) -> dict:
    return {"host": "pve.lan", "github_user": "octocat", "token_secret_file": str(_secret_file(root))}


def test_every_value_given_with_no_terminal_writes_them_and_encrypts(tmp_path: Path) -> None:
    files = _files(tmp_path)
    report = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path)})
    assert report["rc"] == 0, _failures(report)
    assert '"pve.lan"' in Path(files["init_hosts_file"]).read_text()
    vault = _decrypt(files)
    assert SECRET_LINE in vault and GITHUB_LINE in vault
    assert _mode(files["init_vault_pass_file"]) == 0o600
    again = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path)})
    assert again["rc"] == 0, _failures(again)
    assert _changed(again) == 0


def test_a_given_value_that_differs_from_a_set_one_fails_and_writes_nothing(tmp_path: Path) -> None:
    files = _files(tmp_path)
    hosts = Path(files["init_hosts_file"])
    hosts.write_text(hosts.read_text().replace("192.0.2.10", "10.0.0.5"))
    before = _snapshot(files)
    report = _init(tmp_path, files, {"init_given": {"host": "pve.lan"}})
    assert report["rc"] != 0
    failure = "\n".join(_failures(report).values())
    assert "--host pve.lan" in failure and "already sets 10.0.0.5" in failure
    assert _snapshot(files) == before


def test_a_given_token_user_that_differs_from_local_yml_fails(tmp_path: Path) -> None:
    files = _files(tmp_path)
    local = Path(files["init_local_file"])
    local.write_text(local.read_text() + "proxmox_api_user: deploy@pam\n")
    report = _init(tmp_path, files, {"init_given": {"token_user": "other@pve"}})
    assert report["rc"] != 0
    assert "--token-user other@pve" in "\n".join(_failures(report).values())


def test_given_token_parts_are_written_only_where_they_differ_from_the_default(tmp_path: Path) -> None:
    files = _files(tmp_path)
    given = {"token_user": "ansible@pve", "token_name": "claude"}
    report = _init(tmp_path, files, {"init_given": given})
    local = Path(files["init_local_file"]).read_text()
    assert 'proxmox_api_token_id: "claude"\n' in local and "# proxmox_api_user: ansible@pve\n" in local
    # Still missing: the address, the secret, the username - a scripted run that
    # gave values fails, naming them.
    assert report["rc"] != 0
    failure = "\n".join(_failures(report).values())
    assert "the Proxmox host address (--host)" in failure and "--github-user" in failure


def test_some_values_given_at_a_terminal_ask_only_for_the_rest(tmp_path: Path) -> None:
    files = _files(tmp_path)
    given = {"host": "pve.lan", "token_secret_file": str(_secret_file(tmp_path))}
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [(GITHUB_PROMPT, b"octocat\n")], {"init_given": given})
    assert rc == 0, output
    # One question, the username's: the task names hold the others' labels too.
    assert output.count("USER INPUT REQUIRED") == 1
    vault = _decrypt(files)
    assert SECRET_LINE in vault and GITHUB_LINE in vault


@pytest.mark.parametrize(
    ("given", "said"),
    [
        pytest.param({"host": "https://pve.lan"}, "--host (https://pve.lan) is not", id="host"),
        pytest.param({"github_user": "octo--cat"}, "--github-user (octo--cat) is not", id="github"),
        pytest.param(
            {"token_user": "ansible"}, "--token-user and --token-name (ansible!ansible) are not", id="token-user"
        ),
    ],
)
def test_a_given_value_that_breaks_its_rule_fails_without_a_prompt(tmp_path: Path, given: dict, said: str) -> None:
    files = _files(tmp_path)
    before = _snapshot(files)
    report = _init(tmp_path, files, {"init_given": given, "init_ask": True})
    assert report["rc"] != 0
    assert said in "\n".join(_failures(report).values())
    assert _snapshot(files) == before


def test_a_given_secret_that_is_no_secret_fails_without_saying_it(tmp_path: Path) -> None:
    files = _files(tmp_path)
    report = _init(tmp_path, files, {"init_given": {"token_secret_file": str(_secret_file(tmp_path, "not-it\n"))}})
    assert report["rc"] != 0
    failure = "\n".join(_failures(report).values())
    assert "the secret in --token-secret-file" in failure and "36-character secret" in failure
    assert "not-it" not in json.dumps(report)


def test_the_secret_given_in_the_environment_is_written(tmp_path: Path) -> None:
    files = _files(tmp_path)
    given = {"host": "pve.lan", "github_user": "octocat"}
    report = _init(tmp_path, files, {"init_given": given}, env={"CLAUDE_ON_PROXMOX_TOKEN_SECRET": SECRET.upper()})
    assert report["rc"] == 0, _failures(report)
    assert SECRET_LINE in _decrypt(files)


def test_the_secret_given_two_ways_is_refused(tmp_path: Path) -> None:
    files = _files(tmp_path)
    given = {"token_secret_file": str(_secret_file(tmp_path))}
    report = _init(tmp_path, files, {"init_given": given}, env={"CLAUDE_ON_PROXMOX_TOKEN_SECRET": SECRET})
    assert report["rc"] != 0
    assert "give it one way" in "\n".join(_failures(report).values())


def test_a_secret_file_others_can_read_is_said(tmp_path: Path) -> None:
    files = _files(tmp_path)
    given = _every_value_given(tmp_path) | {"token_secret_file": str(_secret_file(tmp_path, mode=0o644))}
    report = _init(tmp_path, files, {"init_given": given})
    assert report["rc"] == 0, _failures(report)
    assert "can be read by others" in _said(report, "Say that others can read the secret's file")


def test_a_vault_value_given_for_a_vault_that_cannot_be_opened_fails(tmp_path: Path) -> None:
    files = _files(tmp_path)
    _encrypt(files, _example("init_vault_file"))
    Path(files["init_vault_pass_file"]).unlink()
    report = _init(tmp_path, files, {"init_given": {"github_user": "octocat"}})
    assert report["rc"] != 0
    assert "Put its password in .vault_pass" in "\n".join(_failures(report).values())


@pytest.mark.parametrize("how", ["file", "environment", "typed"])
def test_the_secret_reaches_neither_the_output_nor_the_log(tmp_path: Path, how: str) -> None:
    files = _files(tmp_path)
    # The log at -vvv, while the report on stdout stays the JSON callback's alone.
    loud = ["-e", '{"ansible_inject_invocation": true}']
    if how == "typed":
        rc, output, log = _run_at_a_terminal(
            tmp_path,
            PLAYBOOK,
            {},
            files | {"init_ask": True, "ansible_inject_invocation": True},
            steps=EVERY_ANSWER,
            args=["-i", str(INVENTORY), "-vvv"],
            env=_no_real_ssh(tmp_path),
        )
        assert rc == 0, output
        seen = output + log
    else:
        given = {"host": "pve.lan", "github_user": "octocat"}
        env = {"ANSIBLE_LOG_PATH": str(tmp_path / "run.log"), "ANSIBLE_LOG_VERBOSITY": "3"}
        if how == "file":
            given["token_secret_file"] = str(_secret_file(tmp_path))
        else:
            env["CLAUDE_ON_PROXMOX_TOKEN_SECRET"] = SECRET
        report = _init(tmp_path, files, {"init_given": given}, *loud, env=env)
        assert report["rc"] == 0, _failures(report)
        seen = json.dumps(report) + (tmp_path / "run.log").read_text()
    assert SECRET_LINE in _decrypt(files)
    assert SECRET not in seen and SECRET.upper() not in seen


# ------------------------------------------------------------- the edge cases ---
def _plaintext_vault(files: dict, secret: bool = True, github: bool = True) -> None:
    text = _example("init_vault_file")
    if secret:
        text = text.replace('vault_proxmox_api_token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"', SECRET_LINE)
    if github:
        text = text.replace('vault_github_username: "your-github-username"', GITHUB_LINE)
    Path(files["init_vault_file"]).write_text(text)


def _password_file(root: Path, name: str = "vault-password") -> Path:
    path = root / name
    path.write_text("a-password-of-the-persons-own\n")
    path.chmod(0o600)
    return path


def _decrypt_with(files: dict, password: Path) -> str:
    return _ansible_vault(
        files, "decrypt", "--vault-password-file", str(password), "--output", "-", files["init_vault_file"]
    )


def test_a_password_file_of_ansibles_own_encrypts_and_no_vault_pass_is_made(tmp_path: Path) -> None:
    files = _files(tmp_path)
    password = _password_file(tmp_path)
    env = {"ANSIBLE_VAULT_PASSWORD_FILE": str(password)}
    report = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path)}, env=env)
    assert report["rc"] == 0, _failures(report)
    assert SECRET_LINE in _decrypt_with(files, password)
    assert not Path(files["init_vault_pass_file"]).exists()
    assert str(password) in _said(report, "Say that the vault is encrypted")


def test_vault_pass_and_a_password_file_of_ansibles_own_together_encrypt(tmp_path: Path) -> None:
    # ansible-vault holds two passwords then, and encrypts only when told which.
    files = _files(tmp_path)
    _password_file(tmp_path / "checkout", ".vault_pass")
    env = {"ANSIBLE_VAULT_PASSWORD_FILE": files["init_vault_pass_file"]}
    report = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path)}, env=env)
    assert report["rc"] == 0, _failures(report)
    assert SECRET_LINE in _decrypt(files)


def test_an_encryption_that_fails_says_why(tmp_path: Path) -> None:
    # ansible-vault cannot write into the vault's directory: what it says is
    # said, where the no_log task alone would only say it was censored.
    files = _files(tmp_path)
    (tmp_path / "password").mkdir()
    files["init_vault_pass_file"] = str(_password_file(tmp_path / "password", ".vault_pass"))
    checkout = Path(files["init_vault_file"]).parent
    checkout.chmod(0o555)
    try:
        given = {"github_user": "octocat", "token_secret_file": str(_secret_file(tmp_path))}
        report = _init(tmp_path, files, {"init_given": given})
    finally:
        checkout.chmod(0o755)
    assert report["rc"] != 0
    failure = "\n".join(_failures(report).values())
    assert "ansible-vault could not encrypt" in failure and "not writable" in failure, failure
    assert SECRET not in json.dumps(report)
    assert Path(files["init_vault_file"]).read_text() == _example("init_vault_file")


def test_a_host_named_without_an_ansible_host_is_set(tmp_path: Path) -> None:
    # defaults.yml reaches the first proxmox host at its name when it has no ansible_host.
    files = _files(tmp_path)
    hosts = Path(files["init_hosts_file"])
    hosts.write_text(
        hosts.read_text().replace("        pve:\n          ansible_host: 192.0.2.10", "        pve.lan:\n         ")
    )
    _plaintext_vault(files)
    report = _init(tmp_path, files, {"init_given": {"host": "PVE.lan", "github_user": "Octocat"}})
    assert report["rc"] == 0, _failures(report)
    summary = _said(report, "Say what was written, and what is left")
    assert "Proxmox host address" not in summary


def test_enter_at_a_terminal_is_later_even_with_values_given(tmp_path: Path) -> None:
    files = _files(tmp_path)
    steps = [(TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"\n"), (SECRET_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps, {"init_given": {"host": "pve.lan"}})
    assert rc == 0, output
    assert "Still missing after the values given" not in output


def test_prompt_no_with_values_given_and_one_missing_fails(tmp_path: Path) -> None:
    # --no-prompt: just init passes no init_ask, even at a terminal.
    files = _files(tmp_path)
    rc, output, _ = _run_at_a_terminal(
        tmp_path,
        PLAYBOOK,
        {},
        files | {"init_given": {"host": "pve.lan"}},
        steps=[],
        args=["-i", str(INVENTORY)],
        env=_no_real_ssh(tmp_path),
    )
    assert rc != 0
    assert "the API token secret (--token-secret-file or CLAUDE_ON_PROXMOX_TOKEN_SECRET)" in output


def test_a_broken_value_given_last_leaves_every_file_as_it_was(tmp_path: Path) -> None:
    files = _files(tmp_path)
    before = _snapshot(files)
    given = {"host": "pve.lan", "token_name": "claude", "github_user": "-octocat"}
    report = _init(tmp_path, files, {"init_given": given})
    assert report["rc"] != 0
    assert "--github-user (-octocat) is not" in "\n".join(_failures(report).values())
    assert _snapshot(files) == before


def test_a_vault_line_the_answer_cannot_replace_is_said_and_left(tmp_path: Path) -> None:
    files = _files(tmp_path)
    vault = Path(files["init_vault_file"])
    vault.write_text(
        vault.read_text().replace(
            'vault_github_username: "your-github-username"', "vault_github_username: |-\n  your-github-username"
        )
    )
    before = _snapshot(files)
    report = _init(tmp_path, files, {"init_given": {"github_user": "octocat"}})
    assert report["rc"] != 0
    assert "cannot take the answer in place" in "\n".join(_failures(report).values())
    assert _snapshot(files) == before


def test_a_vault_left_plaintext_is_said_and_no_deploy_is_suggested(tmp_path: Path) -> None:
    files = _files(tmp_path)
    _plaintext_vault(files)
    report = _init(tmp_path, files, {"init_given": {"host": "pve.lan"}})
    assert report["rc"] == 0, _failures(report)
    summary = _said(report, "Say what was written, and what is left")
    assert "vault.yml is still plaintext" in summary and "just deploy" not in summary
    assert not Path(files["init_vault_file"]).read_text().startswith("$ANSIBLE_VAULT")


def test_the_same_values_in_another_case_are_no_change(tmp_path: Path) -> None:
    files = _files(tmp_path)
    report = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path)})
    assert report["rc"] == 0, _failures(report)
    again = {**_every_value_given(tmp_path), "host": "PVE.LAN", "github_user": "OctoCat"}
    report = _init(tmp_path, files, {"init_given": again})
    assert report["rc"] == 0, _failures(report)
    assert _changed(report) == 0


def test_a_run_that_keeps_the_modules_files_is_refused(tmp_path: Path) -> None:
    files = _files(tmp_path)
    before = _snapshot(files)
    report = _init(
        tmp_path, files, {"init_given": _every_value_given(tmp_path)}, env={"ANSIBLE_KEEP_REMOTE_FILES": "1"}
    )
    assert report["rc"] != 0
    assert "ANSIBLE_KEEP_REMOTE_FILES" in "\n".join(_failures(report).values())
    assert _snapshot(files) == before


# ------------------------------------------------------------ the access offer ---
ACCESS_FILES = (
    REPO / "playbooks" / "tasks" / "init_access.yml",
    REPO / "playbooks" / "tasks" / "init_access_choose.yml",
)
# Every question before the offer, answered.
BEFORE_THE_OFFER = [(HOST_PROMPT, b"pve.lan\n"), (TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"octocat\n")]
CHECKED = yaml.safe_load((REPO / "playbooks" / "init_check.yml").read_text())[0]["vars"]["init_token_privileges"]
ROLE = [*CHECKED["vms"], "VM.GuestAgent.Audit", *CHECKED["storage"], *CHECKED["bridge"]]
SET_UP = {
    "roles": {"AnsibleVM": ROLE},
    "users": {"root@pam": {}, "ansible@pve": {}},
    "acls": [
        {"path": path, "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 1, "type": "user"}
        for path in ("/vms", "/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0")
    ],
    "tokens": {"ansible@pve!ansible": {"privsep": 0}},
}


def _host_writes(host: Path) -> list[str]:
    """The calls on the stand-in Proxmox host that change it."""
    reads = (["pveum", "user", "permissions"],)
    return [
        " ".join(call)
        for call in _pve_cli_calls(host)
        if call[0] == "pveum" and not any(call[: len(read)] == read for read in reads)
    ]


def test_y_sets_up_the_access_and_the_vault_takes_the_tokens_secret(tmp_path: Path) -> None:
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files)
    steps = [*BEFORE_THE_OFFER, (ACCESS_PROMPT, b"y\n")]
    env = {"ANSIBLE_LOG_VERBOSITY": "3"}
    rc, output, log = _init_at_a_terminal(tmp_path, files, steps, {"ansible_inject_invocation": True}, env=env)
    assert rc == 0, output
    # The question showed what the script would run, and it ran that.
    assert "pveum user token add ansible@pve ansible --privsep 0 --comment claude-on-proxmox" in output
    state = _pve_cli_state(host)
    assert state["roles"]["AnsibleVM"] == ROLE and state["tokens"]["ansible@pve!ansible"]["privsep"] == 0
    assert len(_host_writes(host)) == 6
    # The secret it made is the vault's, never asked for, said or logged.
    assert SECRET_PROMPT.decode() not in output
    assert _decrypt(files) == _example("init_vault_file").replace(
        'vault_proxmox_api_token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"', SECRET_LINE
    ).replace('vault_github_username: "your-github-username"', GITHUB_LINE)
    assert SECRET not in output and SECRET not in log
    assert Path(files["init_local_file"]).read_text() == _example("init_local_file")
    assert "the token ansible@pve!ansible, with what it needs" in output
    assert _ssh_calls(tmp_path) == ""


def test_the_proxmox_groups_connection_settings_reach_the_host(tmp_path: Path) -> None:
    # As the template build reaches it: hosts.yml's proxmox vars under the host's own.
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files, where="group")
    rc, output, _ = _init_at_a_terminal(tmp_path, files, [*BEFORE_THE_OFFER, (ACCESS_PROMPT, b"y\n")])
    assert rc == 0, output
    assert "ansible@pve!ansible" in _pve_cli_state(host)["tokens"] and _ssh_calls(tmp_path) == ""
    assert SECRET_LINE in _decrypt(files)


def test_n_leaves_the_host_alone_says_how_and_asks_for_the_secret(tmp_path: Path) -> None:
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files)
    steps = [*BEFORE_THE_OFFER, (ACCESS_PROMPT, b"n\n"), (SECRET_PROMPT, SECRET.encode() + b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert _host_writes(host) == []
    assert "To set up the API access yourself, run tools/proxmox-access.sh on the Proxmox host as root" in output
    assert "ssh root@pve.lan sh -s < tools/proxmox-access.sh" in output
    assert SECRET_LINE in _decrypt(files)


def test_root_ssh_that_fails_says_so_and_asks_for_the_secret(tmp_path: Path) -> None:
    files = _files(tmp_path)
    steps = [*BEFORE_THE_OFFER, (SECRET_PROMPT, SECRET.encode() + b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert "root SSH to pve.lan failed" in output and "ssh-copy-id root@pve.lan" in output
    assert ACCESS_PROMPT.decode() not in output
    assert "pve.lan" in _ssh_calls(tmp_path)
    assert SECRET_LINE in _decrypt(files)


def test_a_host_without_the_default_storage_asks_which_and_writes_it(tmp_path: Path) -> None:
    files = _files(tmp_path)
    storages = [
        {"storage": "local-zfs", "type": "zfspool", "content": "images,rootdir"},
        {"storage": "tank", "type": "nfs", "content": "images"},
    ]
    host = _stand_in_proxmox(tmp_path, files, {"storages": storages})
    steps = [*BEFORE_THE_OFFER, (b"Number, Enter to decide later", b"2\n"), (ACCESS_PROMPT, b"y\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert "1) local-zfs" in output and "2) tank" in output
    assert "pveum acl modify /storage/tank --users ansible@pve --roles AnsibleVM" in output
    assert '\nproxmox_storage: "tank"\n' in Path(files["init_local_file"]).read_text()
    assert any(acl["path"] == "/storage/tank" for acl in _pve_cli_state(host)["acls"])
    assert SECRET_LINE in _decrypt(files)


def test_a_storage_left_undecided_says_how_to_do_it_by_hand(tmp_path: Path) -> None:
    files = _files(tmp_path)
    host = _stand_in_proxmox(
        tmp_path, files, {"storages": [{"storage": "local-zfs", "type": "zfspool", "content": "images"}]}
    )
    steps = [*BEFORE_THE_OFFER, (b"Use local-zfs? y or n", b"\n"), (SECRET_PROMPT, b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert (
        "the storage is not chosen yet (the host has local-zfs, which the script takes as --storage <name>)" in output
    )
    assert ACCESS_PROMPT.decode() not in output and _host_writes(host) == []
    assert "\nproxmox_storage:" not in Path(files["init_local_file"]).read_text()


def test_a_host_set_up_already_asks_only_for_the_secret(tmp_path: Path) -> None:
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files, SET_UP)
    steps = [*BEFORE_THE_OFFER, (SECRET_PROMPT, SECRET.encode() + b"\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    assert rc == 0, output
    assert "The API access is set up on pve.lan already" in output and "token ansible@pve!ansible exists" in output
    assert ACCESS_PROMPT.decode() not in output and _host_writes(host) == []
    assert SECRET_LINE in _decrypt(files)


def test_a_vault_that_cannot_be_written_deletes_the_token_just_made(tmp_path: Path) -> None:
    # As test_an_encryption_that_fails_says_why: the vault's directory is
    # read-only, so its password is elsewhere and the address is set already.
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files)
    hosts = Path(files["init_hosts_file"])
    hosts.write_text(hosts.read_text().replace("192.0.2.10", "pve.lan"))
    (tmp_path / "password").mkdir()
    files["init_vault_pass_file"] = str(_password_file(tmp_path / "password", ".vault_pass"))
    checkout = Path(files["init_vault_file"]).parent
    checkout.chmod(0o555)
    try:
        steps = [(TOKEN_PROMPT, b"\n"), (GITHUB_PROMPT, b"octocat\n"), (ACCESS_PROMPT, b"y\n")]
        rc, output, _ = _init_at_a_terminal(tmp_path, files, steps)
    finally:
        checkout.chmod(0o755)
    assert rc != 0
    assert "ansible-vault could not encrypt" in output
    assert "The token ansible@pve!ansible just init made on pve.lan is deleted again" in output
    assert _pve_cli_state(host)["tokens"] == {}
    assert _host_writes(host)[-1] == "pveum user token delete ansible@pve ansible"
    assert SECRET not in output


def test_a_vault_written_but_not_read_back_keeps_the_token(tmp_path: Path) -> None:
    # The read-back fails after ansible-vault wrote the vault, which then holds the secret: deleting the token
    # would leave a dead secret there, which the next just init takes as set.
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files)
    vault = tmp_path / "ansible-vault"
    vault.write_text(
        f'#!/bin/sh\n[ "$1" != decrypt ] || {{ echo something else; exit 0; }}\nexec "{ANSIBLE_VAULT}" "$@"\n'
    )
    vault.chmod(0o755)
    steps = [*BEFORE_THE_OFFER, (ACCESS_PROMPT, b"y\n")]
    rc, output, _ = _init_at_a_terminal(tmp_path, files, steps, {"init_ansible_vault": str(vault)})
    assert rc != 0
    assert "does not decrypt to what just init wrote into it" in output
    assert "is kept, as the vault was written with its secret" in output
    assert "ansible@pve!ansible" in _pve_cli_state(host)["tokens"]
    assert not [write for write in _host_writes(host) if "delete" in write]
    assert SECRET_LINE in _decrypt(files) and SECRET not in output


def test_nothing_runs_between_the_offer_and_the_vault(tmp_path: Path) -> None:
    # A token the offer made has only its secret, held in memory, until the vault is written: the secret
    # question (skipped then) is the one task between them, and the vault's block deletes the token on failure.
    names = [task.get("name") for task in yaml.safe_load(PLAYBOOK.read_text())[0]["tasks"]]
    offer = names.index("Offer to set up the API access on the Proxmox host")
    assert names[offer + 1 : offer + 3] == ["Ask for the API token's secret", "Write the vault"]


def test_without_a_terminal_the_host_is_never_reached(tmp_path: Path) -> None:
    files = _files(tmp_path)
    host = _stand_in_proxmox(tmp_path, files)
    _init(tmp_path, files, {"init_given": {"host": "pve.lan", "github_user": "octocat"}})
    assert _pve_cli_calls(host) == [] and _ssh_calls(tmp_path) == ""


# --------------------------------------------------------------- Tailscale ---
NOTICE_TASK = "Say that Tailscale is on by default"


def test_the_tailscale_notice_names_the_sign_in_page_on_a_fresh_checkout(tmp_path: Path) -> None:
    files = _files(tmp_path)
    before = _snapshot(files)
    report = _init(tmp_path, files)
    assert report["rc"] == 0, _failures(report)
    notice = _said(report, NOTICE_TASK)
    assert notice.startswith("NOTICE  Tailscale is on by default") and "sign-in page during just deploy" in notice
    assert "tailscale_enabled: false" in notice
    assert _snapshot(files) == before


def test_the_tailscale_notice_names_the_auth_key_the_vault_holds(tmp_path: Path) -> None:
    files = _files(tmp_path)
    vault = Path(files["init_vault_file"])
    vault.write_text(
        vault.read_text().replace('vault_tailscale_auth_key: ""', 'vault_tailscale_auth_key: "tskey-auth-x"')
    )
    report = _init(tmp_path, files)
    assert report["rc"] == 0, _failures(report)
    assert "by vault_tailscale_auth_key." in _said(report, NOTICE_TASK)
    assert "tskey-auth-x" not in json.dumps(report)


@pytest.mark.parametrize(("given", "value"), [("off", "false"), ("on", "true")])
def test_tailscale_given_writes_its_setting_and_ends_the_notice(tmp_path: Path, given: str, value: str) -> None:
    files = _files(tmp_path)
    report = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path) | {"tailscale": given}})
    assert report["rc"] == 0, _failures(report)
    assert Path(files["init_local_file"]).read_text() == _example("init_local_file").replace(
        "# tailscale_enabled: true\n", f"tailscale_enabled: {value}\n"
    )
    assert not _said(report, NOTICE_TASK)
    assert f"tailscale_enabled: {value}" in _said(report, "Say what was written, and what is left")
    again = _init(tmp_path, files, {"init_given": _every_value_given(tmp_path) | {"tailscale": given}})
    assert again["rc"] == 0, _failures(again)
    assert _changed(again) == 0


@pytest.mark.parametrize("setting", ["true", "false"])
def test_the_tailscale_notice_is_gone_once_the_setting_is_there(tmp_path: Path, setting: str) -> None:
    files = _files(tmp_path)
    local = Path(files["init_local_file"])
    local.write_text(local.read_text() + f"tailscale_enabled: {setting}\n")
    report = _init(tmp_path, files)
    assert report["rc"] == 0, _failures(report)
    assert not _said(report, NOTICE_TASK)


def test_tailscale_given_that_differs_from_the_setting_fails(tmp_path: Path) -> None:
    files = _files(tmp_path)
    local = Path(files["init_local_file"])
    local.write_text(local.read_text() + "tailscale_enabled: false\n")
    before = _snapshot(files)
    report = _init(tmp_path, files, {"init_given": {"tailscale": "on"}})
    assert report["rc"] != 0
    assert "--tailscale on, but inventory/group_vars/all/local.yml already sets tailscale_enabled: false" in (
        "\n".join(_failures(report).values())
    )
    assert _snapshot(files) == before


# --------------------------------------------------------------------- static ---
def _tasks(node):
    for item in node if isinstance(node, list) else []:
        if isinstance(item, dict):
            if "tasks" in item:
                yield from _tasks(item["tasks"])
                continue
            yield item
            for key in ("block", "rescue", "always"):
                yield from _tasks(item.get(key))


WRITERS = {"ansible.builtin.lineinfile", "ansible.builtin.command", "ansible.builtin.file", "ansible.builtin.copy"}


def test_every_write_is_at_a_terminal_or_of_a_given_value():
    # A write's condition names init_ask - a terminal - beside a value given;
    # the decryptions are commands too, and read only, and local.yml is written
    # by its include. What runs on the Proxmox host is the next test's.
    writers = []
    for task in _tasks(yaml.safe_load(PLAYBOOK.read_text())):
        module = next((key for key in task if key in WRITERS), None)
        if module is None:
            if task.get("ansible.builtin.include_tasks") != "tasks/init_local_setting.yml":
                continue
        elif "decrypt" in str(task[module]) or "delegate_to" in task:
            continue
        writers.append(task["name"])
        # Its first condition: a terminal, or (the rest of it) a value given.
        assert re.match(r"\(?init_ask \| bool or ", str(task.get("when"))), task["name"]
    assert len(writers) == 5, writers


def test_proxmox_is_changed_only_by_the_script_after_a_y_and_by_deleting_the_token_it_made():
    # The offer is made at a terminal alone. On the host it runs the script, with
    # the script on stdin: --list and --dry-run, which change nothing, and then
    # the script itself only on a y, its output no_log. The one other command
    # there deletes the token it made, when the vault cannot keep its secret.
    playbook = list(_tasks(yaml.safe_load(PLAYBOOK.read_text())))
    offer = next(task for task in playbook if task.get("ansible.builtin.include_tasks") == "tasks/init_access.yml")
    assert " ".join(offer["when"].split()).startswith("init_ask | bool and init_vault_readable and not init_secret_set")
    on_the_host = {}
    for path in (PLAYBOOK, *ACCESS_FILES):
        for task in _tasks(yaml.safe_load(path.read_text())):
            assert "ansible.builtin.script" not in task and "ansible.builtin.raw" not in task, task["name"]
            if "delegate_to" in task:
                on_the_host[task["name"]] = task
    assert sorted(on_the_host) == [
        "Delete the token just made, whose secret the vault could not keep",
        "init_access | Ask the script what it would run",
        "init_access | List the host's storages and bridges, over root SSH",
        "init_access | Set up the access on the Proxmox host",
    ]
    for name in ("init_access | List the host's storages and bridges, over root SSH",
                 "init_access | Ask the script what it would run",
                 "init_access | Set up the access on the Proxmox host"):  # fmt: skip
        command = on_the_host[name]["ansible.builtin.command"]
        assert command["stdin"] == "{{ lookup('ansible.builtin.file', init_access_script) }}", name
        assert str(command["argv"]).startswith(("['/bin/sh', '-s', '--'", "{{ ['/bin/sh', '-s', '--'")), name
    assert on_the_host["init_access | List the host's storages and bridges, over root SSH"]["changed_when"] is False
    plan = on_the_host["init_access | Ask the script what it would run"]
    assert plan["changed_when"] is False and "'--dry-run'" in plan["ansible.builtin.command"]["argv"]
    run = on_the_host["init_access | Set up the access on the Proxmox host"]
    assert run["no_log"] is True
    assert ".proxmox_access | default('') == 'y'" in run["when"]
    delete = on_the_host["Delete the token just made, whose secret the vault could not keep"]
    assert delete["ansible.builtin.command"]["argv"][:4] == ["pveum", "user", "token", "delete"]
    # Only while the vault is unwritten: once written, it holds the secret.
    assert delete["when"] == "init_access_made | default(false) | bool and not init_access_vault_written | bool"
    written = " ".join(yaml.safe_load(PLAYBOOK.read_text())[0]["vars"]["init_access_vault_written"].split())
    assert written == (
        "{{ init_vault_encrypt is defined and init_vault_encrypt is not skipped"
        " and init_vault_encrypt.rc | default(1) == 0 }}"
    )


def test_the_offers_defaults_are_defaults_yml():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    play = yaml.safe_load(PLAYBOOK.read_text())[0]["vars"]
    assert play["init_storage_default"] == defaults["proxmox_storage"]
    assert play["init_bridge_default"] == defaults["proxmox_bridge"]
    assert play["init_token_user_default"] == defaults["proxmox_api_user"]
    assert play["init_token_name_default"] == defaults["proxmox_api_token_id"]


# Names that hold the secret or the vault's text: a task that renders one, in
# anything but its condition, which is never printed, is no_log.
SECRET_NAMES = (
    "init_hidden_answers",
    "init_vault_text",
    "init_vault_new_text",
    "init_vault_with_secret",
    "init_secret_given_value",
    "init_secret_env",
    "init_secret_held",
    "init_vault_read",
    "init_vault_decrypted.stdout",
    "init_vault_reread",
    "init_vault_reread.stdout",
)


def test_every_task_that_renders_the_secret_or_the_vault_is_no_log():
    # An include's vars are handed to its tasks, never printed: tasks/init_ask.yml
    # is no_log wherever the answer is hidden (the test after this one).
    checked = []
    for task in _tasks(yaml.safe_load(PLAYBOOK.read_text())):
        # A block is its tasks, each checked on its own.
        if "ansible.builtin.include_tasks" in task or "block" in task:
            continue
        rendered = json.dumps({key: value for key, value in task.items() if key not in ("when", "name")})
        if any(re.search(rf"\b{re.escape(name)}(?![\w.])", rendered) for name in SECRET_NAMES):
            checked.append(task["name"])
            assert task.get("no_log") is True, task["name"]
    assert len(checked) == 8, checked
    # The offer's: the run whose stdout is the secret, and the secret kept.
    rendering = []
    for task in _tasks(yaml.safe_load(ACCESS_FILES[0].read_text())):
        if "ansible.builtin.include_tasks" in task:
            continue
        rendered = json.dumps({key: value for key, value in task.items() if key not in ("when", "name")})
        if re.search(r"\b(init_hidden_answers|init_access_secret|init_access_run\.stdout)\b", rendered) or (
            task.get("register") == "init_access_run"
        ):
            rendering.append(task["name"])
            assert task.get("no_log") is True, task["name"]
    assert rendering == [
        "init_access | Set up the access on the Proxmox host",
        "init_access | Keep the secret of the token it made",
    ], rendering


def test_the_secret_is_asked_hidden_and_kept_apart():
    tasks = {task["name"]: task for task in _tasks(yaml.safe_load(PLAYBOOK.read_text()))}
    asked = tasks["Ask for the API token's secret"]["vars"]
    assert asked["init_ask_hidden"] is True and asked["init_ask_kind"] == "uuid"
    ask = yaml.safe_load((REPO / "playbooks" / "tasks" / "init_ask.yml").read_text())
    by_name = {task["name"]: task for task in ask}
    for name in ("init_ask | Ask for {{ init_ask_label }}", "init_ask | Ask once more, saying the rule"):
        assert "init_ask_hidden" in by_name[name]["no_log"], name
    assert by_name["init_ask | Keep the hidden answer"]["no_log"] is True
    assert "init_hidden_answers" in json.dumps(by_name["init_ask | Keep the hidden answer"])


def test_no_run_reaches_a_real_ssh():
    # Every run goes through _init or _init_at_a_terminal, which put the
    # refusing ssh first on PATH, or passes _no_real_ssh's environment itself.
    tree = ast.parse(Path(__file__).read_text())
    runners = {"_run", "_run_at_a_terminal"}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) in runners:
                env = next((ast.unparse(k.value) for k in call.keywords if k.arg == "env"), "")
                assert "_no_real_ssh(" in env, function.name
    assert SSH_STAND_IN.rstrip().endswith("exit 255") and "ssh-calls.log" in SSH_STAND_IN


def test_no_run_reaches_the_checkouts_files():
    # Every run of the playbook in this file goes through _init or
    # _init_at_a_terminal, which point all four files into the test's directory;
    # the one other run (no init_ask) passes the same files dict.
    tree = ast.parse(Path(__file__).read_text())
    runners = {"_run", "_run_at_a_terminal"}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) in runners:
                arguments = [ast.unparse(argument) for argument in call.args]
                assert function.name in {"_init", "_init_at_a_terminal"} or any(
                    "files" in argument for argument in arguments[3:4]
                ), function.name


def test_the_inventory_is_the_controller_alone_with_no_group_vars_beside_it():
    inventory = yaml.safe_load(INVENTORY.read_text())
    assert inventory == {
        "all": {
            "hosts": {
                "localhost": {
                    "ansible_connection": "local",
                    "ansible_python_interpreter": "{{ ansible_playbook_python }}",
                    "ansible_pipelining": True,
                }
            }
        }
    }
    for where in (INVENTORY.parent, PLAYBOOK.parent):
        assert not (where / "group_vars").exists() and not (where / "host_vars").exists()
