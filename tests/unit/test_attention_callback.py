"""The stdout callback just runs use: the default's output, with the waits set apart and NEXT STEPS at the end.

callback_plugins/claude_on_proxmox.py subclasses the default callback. Run for
real here, against small playbooks on this machine's localhost, with the
result settings the repository's ansible.cfg gives: untagged output must stay
the default's byte for byte, and no hook may fail - a subclass that lost its
options' DOCUMENTATION fails every hook while Ansible only warns, and every
task's result disappears. Statically: ansible.cfg wires it, the tags sit on the
tasks that wait in the browser, every prompt is drawn by banner, and every
report task records what it leaves to do.
"""

from __future__ import annotations

import configparser
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import _env

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
PLUGINS = REPO / "callback_plugins"

_spec = importlib.util.spec_from_file_location("claude_on_proxmox_callback", PLUGINS / "claude_on_proxmox.py")
_callback = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_callback)

RULE = "#" * 80

# The tasks a run waits on in the browser: drawn as a block, or one waiting line.
WAITS = {
    "browser_login | Say the page and what happens next",
    "browser_login | Wait for your browser to come back",
    "device_flow | Say the code and the page",
    "device_flow | Wait for the approval",
    "web_login | Say where to sign in",
    "web_login | Wait for the page to be approved",
}
# Every report that says what is left to do after the run, each followed by the
# task that records it for NEXT STEPS (docs/architecture/custom-code.md §7.5).
REPORTS = {
    "roles/claude_code/tasks/login.yml": ["login | Report the login state and what to do about it"],
    "roles/claude_code/tasks/browser_login.yml": [
        "browser_login | Say that no code was entered",
        "browser_login | Say that the browser did not come back",
    ],
    "roles/claude_code/tasks/remote_control.yml": [
        "remote_control | Report where to reach the session",
        "remote_control | Say exactly what to run",
    ],
    "roles/github_projects/tasks/auth.yml": [
        "auth | Say that the token only lists",
        "auth | Say that the token only lists beside a login of your own",
        "auth | Say how to sign gh in",
    ],
    "roles/github_projects/tasks/login.yml": ["login | Report the login state and what to do about it"],
    "roles/github_projects/tasks/clone.yml": ["clone | Say that an organisation gives no git identity"],
    "roles/tailscale/tasks/login.yml": ["login | Say why there is nothing to sign in"],
    "roles/tailscale/tasks/report.yml": ["report | Say how this VM is on Tailscale"],
    "playbooks/code.yml": ["Say how to open each VM", "Say what was opened", "Say how to open the VM later"],
    "playbooks/provision.yml": ["Show each VM and the address it was given"],
    "playbooks/destroy.yml": [
        "Say what each deleted VM was released from, and what is left of it",
        "Say which aliases were kept, and why",
    ],
}

EVERY_KIND = """
- name: Every kind of result
  hosts: localhost
  gather_facts: false
  tasks:
    - name: A message
      ansible.builtin.debug:
        msg: plain
    - name: A change
      ansible.builtin.command: "true"
      changed_when: true
    - name: A skip
      ansible.builtin.debug:
        msg: never
      when: false
    - name: A failure, ignored
      ansible.builtin.command: "false"
      ignore_errors: true
    - name: A loop
      ansible.builtin.debug:
        msg: "{{ item }}"
      loop: [one, two]
    - name: A register
      ansible.builtin.command: echo out
      register: said
      changed_when: false
    - name: The register, printed
      ansible.builtin.debug:
        var: said.stdout_lines
    - name: An untagged retry
      ansible.builtin.command: "true"
      register: tries
      until: tries.attempts | default(0) > 1
      retries: 3
      delay: 0
      changed_when: false
"""


def _config(callback: str, extra_roles: Path | None = None) -> str:
    """The run's ansible.cfg after harness.ANSIBLE_CFG: the result format and callback, as ansible.cfg has them."""
    return f"callback_result_format = yaml\nstdout_callback = {callback}\ncallback_plugins = {PLUGINS}\n" + (
        f"roles_path = {extra_roles}\n" if extra_roles else ""
    )


def _play(root: Path, playbook: str, callback: str = "claude_on_proxmox", roles: Path | None = None, **env) -> tuple:
    """Run playbook text with callback; stdout, stderr and the run log."""
    (root / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    path = root / "play.yml"
    path.write_text(playbook)
    log = root / "run.log"
    log.unlink(missing_ok=True)
    settings = {"ANSIBLE_LOG_PATH": str(log), **env}
    environment = _env(root, config=_config(callback, roles), **settings)
    if roles:
        environment["ANSIBLE_ROLES_PATH"] = str(roles)
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), str(path)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,
        check=False,
    )
    assert "Callback dispatch" not in result.stdout + result.stderr, result.stdout + result.stderr
    return result.stdout, result.stderr, log.read_text() if log.exists() else ""


def _masked(output: str) -> str:
    """A run's output with what changes from one run to the next - a command's times - masked."""
    return re.sub(r"(?m)^(\s*(?:start|end|delta): ).*$", r"\1...", output)


def test_untagged_output_is_the_default_callbacks_byte_for_byte(tmp_path):
    # One directory for both: a failure's output names the playbook's path.
    ours, _, _ = _play(tmp_path, EVERY_KIND)
    default, _, _ = _play(tmp_path, EVERY_KIND, "default")
    assert "msg: plain" in ours and "FAILED - RETRYING" in ours and "...ignoring" in ours
    assert _masked(ours) == _masked(default)


def test_no_next_steps_when_nothing_was_recorded(tmp_path):
    out, _, _ = _play(tmp_path, EVERY_KIND)
    assert "NEXT STEPS" not in out


def test_a_browser_message_is_a_block_named_for_its_sign_in(tmp_path):
    roles = tmp_path / "roles"
    tasks = roles / "tailscale" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(
        "- name: web_login | Say where to sign in\n"
        "  ansible.builtin.debug:\n"
        "    msg: 'localhost: sign in to Tailscale at https://login.tailscale.com/a/x'\n"
        "  tags: [attention_browser]\n"
    )
    out, _, log = _play(
        tmp_path, "- name: Sign in\n  hosts: localhost\n  gather_facts: false\n  roles: [tailscale]\n", roles=roles
    )
    block = (
        f"{RULE}\n##  ACTION REQUIRED IN YOUR BROWSER  -  localhost  -  Tailscale sign-in\n{RULE}\n"
        "localhost: sign in to Tailscale at https://login.tailscale.com/a/x\n"
    )
    assert block in out, out
    assert "ok: [localhost] =>" not in out
    assert "TASK [tailscale : web_login | Say where to sign in]" in out
    assert block in log


def test_a_browser_wait_says_once_that_it_waits_and_an_untagged_one_retries_aloud(tmp_path):
    out, _, _ = _play(
        tmp_path,
        """
- name: Waits
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Wait for the page to be approved
      ansible.builtin.command: "true"
      register: approved
      until: approved.attempts | default(0) > 3
      retries: 5
      delay: 1
      changed_when: false
      tags: [attention_browser]
    - name: Untagged
      ansible.builtin.command: "true"
      register: untagged
      until: untagged.attempts | default(0) > 1
      retries: 3
      delay: 0
      changed_when: false
""",
    )
    assert out.count("Waiting for you in the browser for localhost (about 4 seconds left; Ctrl-C ends the run)") == 1
    assert "FAILED - RETRYING: [localhost]: Wait for the page to be approved" not in out
    assert "FAILED - RETRYING: [localhost]: Untagged (2 retries left)." in out


def test_next_steps_after_the_recap_by_vm_then_matter_each_line_once(tmp_path):
    out, _, log = _play(
        tmp_path,
        """
- name: Records
  hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          github_projects_next: "{{ {'beta': {'github': 'beta: run just login beta --only github'}} }}"
    - ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'beta': {'claude': 'beta: run just login beta --only claude'}} }}"
    - ansible.builtin.set_stats:
        data:
          tailscale_next: "{{ {'alpha': {'tailscale': 'alpha: Tailscale is down: sudo tailscale up.'}} }}"
    - ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'alpha': {'claude': 'alpha: not signed in. Run just login alpha'}} }}"
    - name: Signed in later in the run
      ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'alpha': {'claude': ''}} }}"
    - name: The same line for two matters
      ansible.builtin.set_stats:
        data:
          code_next: "{{ {'beta': {'vscode': 'beta: run just login beta --only github'}} }}"
    - name: Under a name nobody reads
      ansible.builtin.set_stats:
        data:
          someone_elses_next: "{{ {'alpha': {'claude': 'not this'}} }}"
""",
    )
    tail = out[out.index("PLAY RECAP") :]
    assert tail.index("localhost") < tail.index("NEXT STEPS")
    assert (
        f"{RULE}\n##  NEXT STEPS\n{RULE}\n"
        "alpha: Tailscale is down: sudo tailscale up.\n"
        "beta: run just login beta --only claude\n"
        "beta: run just login beta --only github\n"
    ) in tail, tail
    assert "not this" not in out and "just login alpha --only claude\n" not in tail
    assert "##  NEXT STEPS" in log


def test_colour_on_the_screen_never_in_the_log(tmp_path):
    out, _, log = _play(
        tmp_path,
        """
- name: Records
  hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          claude_vm_next: "{{ {'alpha': {'vm': 'alpha is stopped: just deploy alpha'}} }}"
""",
        ANSIBLE_NOCOLOR="0",
        ANSIBLE_FORCE_COLOR="1",
    )
    assert re.search(r"\x1b\[[0-9;]*m##  NEXT STEPS", out), out
    assert "##  NEXT STEPS" in log and "\x1b" not in log


@pytest.mark.parametrize(
    ("records", "lines"),
    [
        ({}, []),
        ({"claude_code_next": {"a": {"claude": ""}}}, []),
        (
            {
                "claude_vm_next": {"b": {"destroy": "b left"}, "a": {"vm": "a stopped"}},
                "tailscale_next": {"a": {"tailscale": "a ts"}},
                "claude_code_next": {"a": {"remote_control": "a rc", "claude": "a claude"}},
            },
            ["a stopped", "a claude", "a rc", "a ts", "b left"],
        ),
        # Not a record: ignored, never a failure at the end of the run.
        ({"github_projects_next": {"a": "flat", "b": {"github": 3}}}, []),
        ({"code_next": {"a": {"something_new": "new", "vscode": "open"}}}, ["open", "new"]),
    ],
)
def test_next_steps_reads_the_five_records(records, lines):
    assert _callback.next_steps(records) == lines


def test_ansible_cfg_wires_the_callback_and_nothing_else_does():
    config = configparser.ConfigParser()
    config.read(REPO / "ansible.cfg")
    assert config["defaults"]["stdout_callback"] == "claude_on_proxmox"
    assert (REPO / config["defaults"]["callback_plugins"] / "claude_on_proxmox.py").is_file()
    assert _callback.CallbackModule.CALLBACK_NAME == "claude_on_proxmox"


def _task_lists(node):
    """Every list of tasks in a parsed task file or playbook, blocks and plays included."""
    if isinstance(node, list):
        if all(isinstance(item, dict) for item in node):
            yield node
        for item in node:
            yield from _task_lists(item)
    elif isinstance(node, dict):
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if isinstance(node.get(key), list):
                yield from _task_lists(node[key])


def _files():
    return sorted(
        [
            *REPO.glob("roles/*/tasks/*.yml"),
            *REPO.glob("playbooks/*.yml"),
            *REPO.glob("playbooks/tasks/*.yml"),
            REPO / "deploy.yml",
        ]
    )


def _tasks(path: Path):
    for tasks in _task_lists(yaml.safe_load(path.read_text())):
        yield from tasks


def test_the_browser_waits_and_only_they_carry_the_tag_and_none_loops():
    tagged = {}
    for path in _files():
        for task in _tasks(path):
            if _callback.WAITING_TAG in (task.get("tags") or []):
                tagged[task["name"]] = task
    assert set(tagged) == WAITS
    assert not [name for name, task in tagged.items() if "loop" in task or any(k.startswith("with_") for k in task)]


def test_every_prompt_is_drawn_by_banner():
    prompts = []
    for path in _files():
        parsed = yaml.safe_load(path.read_text())
        for play in parsed if isinstance(parsed, list) else []:
            if isinstance(play, dict):
                prompts += [entry["prompt"] for entry in play.get("vars_prompt") or []]
        prompts += [task["ansible.builtin.pause"]["prompt"] for task in _tasks(path) if "ansible.builtin.pause" in task]
    assert len(prompts) == 7
    assert all("| banner(" in prompt for prompt in prompts), prompts


def test_every_report_is_followed_by_its_record_under_one_of_the_five_names():
    followed, borrowed = {}, []
    for relative, names in REPORTS.items():
        for tasks in _task_lists(yaml.safe_load((REPO / relative).read_text())):
            for index, task in enumerate(tasks):
                if task.get("name") in names:
                    after = tasks[index + 1] if index + 1 < len(tasks) else {}
                    followed[(relative, task["name"])] = "ansible.builtin.set_stats" in after
                    # A report's task vars are its own: a record whose condition named
                    # one failed every just destroy at its end.
                    condition = (
                        " ".join(map(str, after.get("when") or []))
                        if isinstance(after.get("when"), list)
                        else str(after.get("when") or "")
                    )
                    borrowed += [
                        (relative, name) for name in task.get("vars") or {} if re.search(rf"\b{name}\b", condition)
                    ]
    assert borrowed == []
    assert followed == {(relative, name): True for relative, names in REPORTS.items() for name in names}
    stats = [
        task["ansible.builtin.set_stats"]
        for path in _files()
        for task in _tasks(path)
        if "ansible.builtin.set_stats" in task
    ]
    assert len(stats) == sum(len(names) for names in REPORTS.values()) == 18
    assert all(len(stat["data"]) == 1 and next(iter(stat["data"])) in _callback.NEXT_STATS for stat in stats), stats
