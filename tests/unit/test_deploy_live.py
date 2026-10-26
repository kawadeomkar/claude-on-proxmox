"""just deploy's live output, run: the stdout callback at a pseudo-terminal of the test's own, read back by vt.py.

A stand-in deploy - deploy.yml importing playbooks named as the real ones are, so the stages are the real ones -
runs on this machine's localhost with callback_plugins/claude_on_proxmox.py and CLAUDE_ON_PROXMOX_LIVE as the
justfile sets it. What a person sees: the region drawn and never wrapped, gone at the end, the reports, the browser
block, a prompt and a diff above it for good, the summary and NEXT STEPS after; the title and the bells. What the
log keeps: everything the stream would have had, in order, and no colour. Without a terminal, a line per step; with
-v, or without the variable, the stream as before. Nothing here is the developer's terminal: each run has a session
and a pseudo-terminal of its own (harness._take_the_terminal), or no terminal at all. No other test sets the
variable, so no other run is drawn (docs/architecture/custom-code.md §7.5).
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from harness import _end, _env, _fail_if_the_desktop_was_reached, _in_raw_mode, _take_the_terminal
from vt import VT

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
SYNC_ON = b"\x1b[?2026h"
FOOTER = re.compile(r"^. \d+:\d\d · stage \d of \d")
PROMPT = "alpha: paste the code here, or press Enter to skip"

PLAYBOOKS = {
    "deploy.yml": """
- name: Check whether the cloud-init template already exists
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Report what this run will do
      ansible.builtin.debug:
        msg: The template is already built. Then provisioning and configuring alpha.
- name: Build the cloud-init VM template
  ansible.builtin.import_playbook: playbooks/template.yml
- name: Provision VMs on Proxmox
  ansible.builtin.import_playbook: playbooks/provision.yml
- name: Configure VMs
  ansible.builtin.import_playbook: playbooks/configure.yml
- name: Sign Claude Code in
  ansible.builtin.import_playbook: playbooks/claude_login.yml
- name: Open the VM in VS Code
  ansible.builtin.import_playbook: playbooks/code.yml
""",
    "playbooks/template.yml": """
- name: Build the cloud-init VM template
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Check for the template
      ansible.builtin.command: "true"
      changed_when: false
""",
    "playbooks/provision.yml": """
- name: Create the VMs on Proxmox
  hosts: alpha
  gather_facts: false
  tasks:
    - name: present | Clone from template
      ansible.builtin.command: sleep 0.4
    - name: Record the VM
      ansible.builtin.set_fact:
        proxmox_vm_exists: false
        proxmox_vm_resolved_id: 104
        proxmox_vm_address: 192.0.2.61
- name: Publish and report the provisioned VMs
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Show each VM and the address it was given
      ansible.builtin.debug:
        msg: "{{ item }} is up at 192.0.2.61"
      loop: [alpha]
""",
    "playbooks/discover.yml": """
- name: Discover VMs on Proxmox
  hosts: localhost
  gather_facts: false
  tasks:
    - name: cluster_vms | List every VM in the cluster
      ansible.builtin.debug:
        msg: never
      when: false
""",
    "playbooks/configure.yml": """
- name: Discover VMs on Proxmox
  ansible.builtin.import_playbook: discover.yml
- name: Configure Claude development VMs
  hosts: alpha
  gather_facts: false
  roles: [common]
""",
    "playbooks/claude_login.yml": """
- name: Discover VMs on Proxmox
  ansible.builtin.import_playbook: discover.yml
- name: Sign Claude Code in, or say how each VM is signed in
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Sign in
      ansible.builtin.include_role:
        name: claude_code
        tasks_from: browser_login
""",
    "playbooks/code.yml": """
- name: Open a VM in VS Code
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Say what was opened
      ansible.builtin.debug:
        msg: VS Code is opening /home/dev/projects on alpha.
    - name: Record what is left to do
      ansible.builtin.set_stats:
        data:
          claude_vm_next: "{{ {'alpha': {'vm': 'alpha: just login alpha --only github'}} }}"
""",
    "roles/common/tasks/main.yml": """
- name: Refresh apt cache
  ansible.builtin.command: "true"
- name: Set the time zone
  ansible.builtin.command: "true"
  when: false
- name: Write the motd
  ansible.builtin.copy:
    dest: "{{ motd }}"
    content: "welcome to alpha\\n"
    mode: "0644"
- name: Install the tools
  ansible.builtin.include_tasks: tools.yml
""",
    "roles/common/tasks/tools.yml": """
- name: tools | Install Node.js
  ansible.builtin.command: "true"
  changed_when: false
- name: tools | Install uv
  ansible.builtin.command: "true"
  changed_when: false
""",
    "roles/claude_code/tasks/browser_login.yml": """
- name: browser_login | Say the page and what happens next
  ansible.builtin.debug:
    msg: "alpha: sign Claude Code in at https://claude.ai/oauth/authorize?code=true"
  tags: [attention_browser]
- name: browser_login | Wait for your browser to come back
  ansible.builtin.command: "true"
  register: claude_waited
  until: claude_waited.attempts | default(0) > 2
  retries: 30
  delay: 1
  changed_when: true
  tags: [attention_browser]
- name: browser_login | Ask for the code
  ansible.builtin.pause:
    prompt: "alpha: paste the code here, or press Enter to skip"
  register: claude_answer
  when: ask_code | default(false) | bool
- name: browser_login | Say the code
  ansible.builtin.debug:
    msg: "alpha: the code was {{ claude_answer.user_input }}"
  when: ask_code | default(false) | bool
""",
}
CONFIG = f"""callback_result_format = yaml
stdout_callback = claude_on_proxmox
callback_plugins = {REPO / "callback_plugins"}
callbacks_enabled = ansible.posix.profile_tasks
"""


class Ran:
    def __init__(self, rc, raw, log, screen=None):
        self.rc, self.raw, self.log, self.screen = rc, raw, log, screen
        self.text = raw.decode(errors="replace")

    @property
    def transcript(self):
        return "\n".join(line.rstrip() for line in self.screen.transcript())


@pytest.fixture
def stand_in(tmp_path):
    for name, text in PLAYBOOKS.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    python = f"ansible_connection=local ansible_python_interpreter={sys.executable}"
    (tmp_path / "hosts").write_text(f"localhost {python}\nalpha {python} motd={tmp_path / 'motd'}\n")
    return tmp_path


def _environment(root, title, env):
    settings = {
        "ANSIBLE_LOG_PATH": str(root / "run.log"),
        "ANSIBLE_ROLES_PATH": str(root / "roles"),
        "ANSIBLE_NOCOLOR": "0",
        "TERM": "xterm-256color",
        "PYTHONUTF8": "1",
        **({"CLAUDE_ON_PROXMOX_LIVE": title} if title else {}),
        **(env or {}),
    }
    return _env(root, config=CONFIG, **settings)


def _fresh(root):
    """Each run as the first: no log, and the motd it writes as it was."""
    (root / "run.log").unlink(missing_ok=True)
    (root / "motd").write_text("welcome\n")


def _deploy(root, *args, title="deploy alpha", cols=100, rows=30, keys=None, interrupt=None, env=None, timeout=120):
    """deploy.yml at a pseudo-terminal of cols and rows: keys typed once PROMPT has the terminal, or a Ctrl-C once the
    title says interrupt."""
    _fresh(root)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    process = subprocess.Popen(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), *args, str(root / "deploy.yml")],
        cwd=root,
        env=_environment(root, title, env),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=_take_the_terminal,  # noqa: PLW1509 - nothing else runs threads here
    )
    os.close(slave)
    screen, raw, deadline, typed = VT(cols, rows), b"", time.monotonic() + timeout, None
    try:
        while True:
            if time.monotonic() > deadline:
                pytest.fail(f"the run did not finish within {timeout} seconds:\n" + raw.decode(errors="replace"))
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                raw += chunk
                screen.feed(chunk)
            elif process.poll() is not None:
                break
            if keys is not None and PROMPT.encode() in raw and _in_raw_mode(master):
                # Typed again while pause has not taken them: keys in the moment before it flushed are lost.
                if typed is None or (time.monotonic() - typed > 2 and b"the code was" not in raw):
                    os.write(master, keys)
                    typed = time.monotonic()
            if interrupt and screen.titles and interrupt in screen.titles[-1]:
                interrupt = None
                os.write(master, b"\x03")
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            _end(process)
        os.close(master)
    _fail_if_the_desktop_was_reached(root)
    log = root / "run.log"
    return Ran(process.returncode, raw, log.read_text() if log.exists() else "", screen)


def _piped(root, *args, title="deploy alpha", env=None):
    _fresh(root)
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), *args, str(root / "deploy.yml")],
        cwd=root,
        env=_environment(root, title, env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        start_new_session=True,
        check=False,
    )
    _fail_if_the_desktop_was_reached(root)
    log = root / "run.log"
    return Ran(result.returncode, result.stdout + result.stderr, log.read_text() if log.exists() else "")


def _logged(log: str) -> list[str]:
    """A run log's lines without what changes from run to run: the time and process each starts with, every number
    (profile_tasks' times, a command's), and profile_tasks' recap, which it sorts by time."""
    lines = []
    for line in log.splitlines():
        line = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ p=\d+ u=\S+ n=ansible \w+\| ", "", line)
        line = re.sub(r"\d", "0", line)
        if not re.search(r" -{3,} 0\.00s$", line):
            lines.append(line)
    return lines


def _in_order(part: list[str], whole: list[str]) -> list[str]:
    """What of part is not in whole in the same order: [] when part is whole's with lines added."""
    position = 0
    for index, line in enumerate(part):
        try:
            position = whole.index(line, position) + 1
        except ValueError:
            return part[index:]
    return []


def test_a_deploy_at_a_terminal_is_drawn_in_place_and_leaves_its_words_and_the_summary(stand_in):
    ran = _deploy(stand_in, "-e", "ask_code=true", keys=b"abc123\r")
    assert ran.rc == 0, ran.text
    screen = ran.transcript
    # The region: drawn, never wrapped, nothing written it does not mean, and erased at the end.
    assert SYNC_ON in ran.raw
    assert ran.screen.frame_wraps == 0 and ran.screen.unknown == set(), ran.screen.unknown
    assert not [line for line in screen.splitlines() if FOOTER.match(line)], screen
    # Above it, for good: the reports, the browser's block, the prompt whole and its answer taken.
    assert "  alpha is up at 192.0.2.61" in screen and "All items completed" not in screen
    assert "##  ACTION REQUIRED IN YOUR BROWSER  -  localhost  -  Claude Code sign-in" in screen
    assert f"{PROMPT}:" in screen and "  alpha: the code was abc123" in screen
    # What the stream would have shown is not on the screen: the log has it.
    assert "TASK [" not in screen and "PLAY RECAP" not in screen and "ok: [" not in screen
    # The summary, then NEXT STEPS.
    assert re.search(r"^claude-on-proxmox  deploy alpha · done in \d+ s · \d\d:\d\d$", screen, re.M), screen
    stages = re.findall(r"^ ✓ (\S+(?: \S+)?) {2,}", screen[screen.index("STAGES") :], re.M)
    assert stages == ["template", "provision", "configure", "sign in", "VS Code"], screen
    assert screen.index("STAGES") < screen.index("##  NEXT STEPS") and "alpha: just login alpha --only github" in screen
    assert "\nALPHA  192.0.2.61 · VM 104 · new\n" in screen
    # The tab says where the run is, a wait for the person and the end ring once each.
    titles = ran.screen.titles
    assert titles[0] == "deploy alpha" and titles[-1] == "deploy alpha · done"
    assert "deploy alpha · waiting for you" in titles and any(t.startswith("deploy alpha · configure ") for t in titles)
    assert ran.screen.bells == 2
    # The log: no colour, the stream's every task, and the run's own words.
    assert "\x1b" not in ran.log
    for said in ("TASK [common : Write the motd]", "PLAY RECAP", "##  NEXT STEPS", "STAGES", "alpha is up at"):
        assert said in ran.log, said


def test_the_log_is_the_streams_with_the_runs_own_words_added(stand_in):
    drawn = _deploy(stand_in)
    streamed = _deploy(stand_in, title="")
    assert drawn.rc == streamed.rc == 0
    assert SYNC_ON not in streamed.raw and "PLAY RECAP" in streamed.transcript
    assert _in_order(_logged(streamed.log), _logged(drawn.log)) == []
    added = [line for line in _logged(drawn.log) if line not in _logged(streamed.log)]
    assert any(line.startswith("STAGES") for line in added), added


def test_a_dry_run_is_drawn_with_its_diffs_above(stand_in):
    ran = _deploy(stand_in, "--check", "--diff", title="deploy --check alpha")
    assert ran.rc == 0, ran.text
    assert ran.screen.frame_wraps == 0
    screen = ran.transcript
    assert "-welcome\n+welcome to alpha" in screen, screen
    assert "WOULD CHANGE" in screen and re.search(r"^ ✓ configure .* 1 +common", screen, re.M), screen
    assert (stand_in / "motd").read_text() == "welcome\n"


@pytest.mark.parametrize(("cols", "rows"), [(50, 12), (200, 60)])
def test_any_size_of_terminal_takes_the_region_unwrapped(stand_in, cols, rows):
    ran = _deploy(stand_in, cols=cols, rows=rows)
    assert ran.rc == 0, ran.text
    assert ran.screen.frame_wraps == 0 and ran.screen.unknown == set()
    assert not [line for line in ran.transcript.splitlines() if FOOTER.match(line)]


def test_ctrl_c_ends_the_run_with_the_region_gone_and_the_tab_saying_so(stand_in):
    ran = _deploy(stand_in, interrupt="waiting for you")
    assert ran.rc != 0
    assert ran.screen.titles[-1] == "deploy alpha · stopped", ran.screen.titles
    assert not [line for line in ran.transcript.splitlines() if FOOTER.match(line)], ran.transcript
    assert "User interrupted execution" in ran.transcript


def test_without_a_terminal_each_step_is_a_line(stand_in):
    ran = _piped(stand_in)
    assert ran.rc == 0, ran.text
    assert "\x1b" not in ran.text, ran.text
    lines = ran.text.splitlines()
    assert re.search(r"^✓ configure / common +6 tasks · 2 changed +\d", ran.text, re.M), ran.text
    assert re.search(r"^✓ configure +7 tasks · 2 changed", ran.text, re.M), ran.text
    assert (
        re.search(r"^✓ sign in / Claude +6 tasks · 1 changed", ran.text, re.M)
        and len(re.findall(r"^✓ sign in", ran.text, re.M)) == 1
    )
    assert "Waiting for you in the browser for localhost" in ran.text
    assert "TASK [" not in ran.text and "PLAY RECAP" not in ran.text
    assert lines.index("STAGES          TIME    TASKS  CHANGED") > lines.index("  alpha is up at 192.0.2.61")


@pytest.mark.parametrize("args", [["-v"], []], ids=["verbose", "no-variable"])
def test_verbose_or_without_the_variable_the_run_is_streamed(stand_in, args):
    ran = _piped(stand_in, *args, title="deploy alpha" if args else "")
    assert ran.rc == 0, ran.text
    assert "TASK [common : Write the motd]" in ran.text and "PLAY RECAP" in ran.text
    assert "STAGES" not in ran.text


def test_no_other_test_or_scenario_draws_a_run():
    mentions = [
        str(path.relative_to(REPO))
        for root in ("tests", ".github")
        for path in (REPO / root).rglob("*")
        if path.is_file()
        and path.suffix in (".py", ".yml", ".yaml", ".sh", ".cfg", ".ini")
        and "CLAUDE_ON_PROXMOX_LIVE" in path.read_text(errors="replace")
    ]
    # test_justfile.py reads the title the recipe exports, from an ansible-playbook stand-in that runs nothing.
    assert sorted(mentions) == ["tests/unit/test_deploy_live.py", "tests/unit/test_justfile.py"], mentions
    justfile = (REPO / "justfile").read_text()
    assert justfile.count("export CLAUDE_ON_PROXMOX_LIVE") == 1
