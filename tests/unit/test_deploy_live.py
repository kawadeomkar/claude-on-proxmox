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
import re
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from harness import _at_a_pseudo_terminal, _end, _env, _fail_if_the_desktop_was_reached, _in_raw_mode
from vt import VT

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
SYNC_ON = b"\x1b[?2026h"
FOOTER = re.compile(r"^. \d+:\d\d · stage \d of \d")
HEADER = re.compile(r"^claude-on-proxmox  deploy .* · started \d\d:\d\d")
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
  roles: [proxmox_vm]
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
    - name: Ask what this run's commands see of the live switch
      ansible.builtin.shell: echo "[${CLAUDE_ON_PROXMOX_LIVE-}]"
      register: inherited
      changed_when: false
      check_mode: false
    - name: Say what they saw
      ansible.builtin.debug:
        msg: "The live switch, as this run's commands see it: {{ inherited.stdout }}"
    - name: Say what was opened
      ansible.builtin.debug:
        msg: VS Code is opening /home/dev/projects on alpha.
    - name: Record what is left to do
      ansible.builtin.set_stats:
        data:
          claude_vm_next: "{{ {'alpha': {'vm': 'alpha: just login alpha --only github'}} }}"
""",
    "roles/proxmox_vm/tasks/main.yml": """
- name: present | Clone from template
  ansible.builtin.command: sleep 0.4
- name: present | Record the VM
  ansible.builtin.set_fact:
    proxmox_vm_exists: false
    proxmox_vm_resolved_id: 104
    proxmox_vm_address: 192.0.2.61
""",
    "roles/common/tasks/main.yml": """
- name: A fact the VM could set, any task's, naming another address
  ansible.builtin.set_fact:
    proxmox_vm_address: "\\e]0;forged\\a192.0.2.99"
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
- name: Say what is left to do
  ansible.builtin.debug:
    msg: >-
      Remote Control is configured on alpha but not started: no Claude Code login is stored for dev on it.
      just deploy signs it in next, in this run. A sign-in made any other way starts it by itself too, and
      just deploy alpha --only configure then confirms it.
- name: Keep what is left to do for the end of the run
  ansible.builtin.set_stats:
    data:
      claude_code_next: "{{ {inventory_hostname: {'remote_control': '', 'claude': ''}} }}"
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
- name: device_flow | Wait for the approval
  ansible.builtin.command: sleep 2.5
  changed_when: false
  when: device_wait | default(false) | bool
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
- name: remote_control | Report where to reach the session
  ansible.builtin.debug:
    msg: "alpha: Remote Control is live for alpha."
- name: remote_control | Keep what is left to do for the end of the run
  ansible.builtin.set_stats:
    data:
      claude_code_next: "{{ {'alpha': {'remote_control': ''}} }}"
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

    def assert_drawn_and_gone(self, visible_only=False):
        """A region was drawn, never wrapped, wrote nothing unknown, swallowed nothing, and left nothing of itself:
        no footer and no header, on the screen or above it (visible_only: on the screen, after a resize, which
        pushes the screen into the history by design)."""
        assert SYNC_ON in self.raw, self.text
        assert self.screen.unknown == set() and self.screen.pending == b"", (self.screen.unknown, self.screen.pending)
        lines = self.screen.lines if visible_only else self.screen.transcript()
        left = [line for line in lines if FOOTER.match(line) or HEADER.match(line)]
        assert left == [], "\n".join(self.screen.transcript())
        if not visible_only:
            assert self.screen.frame_wraps == 0


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


def _seconds(said):
    """A duration as the summary says it - 0.6 s, 33 s, 3:58 - in seconds."""
    if ":" in said:
        minutes, seconds = said.split(":")
        return int(minutes) * 60 + int(seconds)
    return float(said.removesuffix(" s"))


def _deploy(
    root, *args, title="deploy alpha", cols=100, rows=30, keys=None, interrupt=None, at=None, env=None, timeout=120
):
    """deploy.yml at a pseudo-terminal of cols and rows: keys typed once PROMPT has the terminal, a Ctrl-C once the
    title says interrupt, and at: (words of the title, bytes typed or a new (cols, rows)) done once the title says
    them."""
    _fresh(root)
    screen, state = VT(cols, rows), {"typed": None, "interrupt": interrupt, "at": at}

    def act(chunk, output, master, process):
        screen.feed(chunk)
        if keys is not None and PROMPT.encode() in output and _in_raw_mode(master):
            # Typed again while pause has not taken them: keys in the moment before it flushed are lost.
            if state["typed"] is None or (time.monotonic() - state["typed"] > 2 and b"the code was" not in output):
                os.write(master, keys)
                state["typed"] = time.monotonic()
        title = screen.titles[-1] if screen.titles else ""
        if state["interrupt"] and state["interrupt"] in title:
            state["interrupt"] = None
            os.write(master, b"\x03")
        if state["at"] and state["at"][0] in title:
            then, state["at"] = state["at"][1], None
            if isinstance(then, bytes):
                os.write(master, then)
            else:
                # The terminal's new size, which sends the run SIGWINCH; the screen keeps its lines, as xterm does.
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", then[1], then[0], 0, 0))
                screen.resize(*then)

    rc, raw = _at_a_pseudo_terminal(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), *args, str(root / "deploy.yml")],
        root,
        _environment(root, title, env),
        size=(cols, rows),
        act=act,
        timeout=timeout,
        poll=0.1,
    )
    _fail_if_the_desktop_was_reached(root)
    log = root / "run.log"
    return Ran(rc, raw, log.read_text() if log.exists() else "", screen)


def _piped(root, *args, title="deploy alpha", env=None, playbook="deploy.yml", timeout=120):
    _fresh(root)
    process = subprocess.Popen(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), *args, str(root / playbook)],
        cwd=root,
        env=_environment(root, title, env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        process_group=0,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException:
        # The run's whole group: its workers, and what they started.
        _end(process)
        raise
    _fail_if_the_desktop_was_reached(root)
    log = root / "run.log"
    return Ran(process.returncode, output, log.read_text() if log.exists() else "")


def _logged(log: str) -> list[str]:
    """A run log's lines without what changes from run to run: the time and process each starts with, every number
    (profile_tasks' times, a command's), and profile_tasks' recap, which it sorts by time. Every line of the recap,
    however long its task took: one of ten seconds or more reads 00.00s once its digits are zeroed, and a task that
    took 10 s in one run and 3 in the other - a retried task on a loaded machine - kept its line in one log alone."""
    lines = []
    for line in log.splitlines():
        line = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ p=\d+ u=\S+ n=ansible \w+\| ", "", line)
        line = re.sub(r"\d", "0", line)
        if not re.search(r" -{3,} 0+\.00s$", line):
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
    # Enter pressed three times while configure runs: with no echo, the cursor stays where the region left it.
    at = ("configure", b"\r\r\r")
    ran = _deploy(stand_in, "-e", "ask_code=true device_wait=true", keys=b"abc123\r", at=at)
    assert ran.rc == 0, ran.text
    screen = ran.transcript
    # The region: drawn, never wrapped, nothing written it does not mean, and erased at the end.
    ran.assert_drawn_and_gone()
    # The run's plan, whole; what this run's commands inherit, without the switch; a fact the VM set, unused.
    assert "  The template is already built. Then provisioning and configuring alpha." in screen
    assert "The live switch, as this run's commands see it: []" in screen
    assert b"forged" not in ran.raw and "192.0.2.99" not in screen
    # Above it, for good: the reports, the browser's block, the prompt whole and its answer taken.
    assert "  alpha is up at 192.0.2.61" in screen and "All items completed" not in screen
    assert "##  ACTION REQUIRED IN YOUR BROWSER  -  localhost  -  Claude Code sign-in" in screen
    assert f"{PROMPT}:" in screen and "  alpha: the code was abc123" in screen
    # A report is whole, however long: wrapped under its VM's name, never cut.
    first, *rest = screen[screen.index("  alpha  Remote Control is configured") :].splitlines()
    report = [first]
    for line in rest:
        if not line.startswith(" " * 9):
            break
        report.append(line)
    said = " ".join(" ".join(report).split())
    assert len(report) > 1 and said.endswith("just deploy alpha --only configure then confirms it."), report
    assert "…" not in said and all(len(line) < 100 for line in report)
    # What the stream would have shown is not on the screen: the log has it.
    assert "TASK [" not in screen and "PLAY RECAP" not in screen and "ok: [" not in screen
    # The summary, then NEXT STEPS.
    assert re.search(r"^claude-on-proxmox  deploy alpha · done in \d+ s · \d\d:\d\d$", screen, re.M), screen
    stages = re.findall(r"^ ✓ (\S+(?: \S+)?) {2,}", screen[screen.index("STAGES") :], re.M)
    assert stages == ["template", "provision", "configure", "sign in", "VS Code"], screen
    assert screen.index("STAGES") < screen.index("##  NEXT STEPS") and "alpha: just login alpha --only github" in screen
    assert "\nALPHA  192.0.2.61 · VM 104 · new\n" in screen
    # configure's "not started", which the sign-in's report on Remote Control made old, is said above only.
    alpha = screen.split("\nALPHA  ", 1)[1].split("\n\n", 1)[0]
    assert " Remote Control is live for alpha." in alpha and "not started" not in alpha, alpha
    # The tab says where the run is, a wait for the person and the end ring once each.
    titles = ran.screen.titles
    assert titles[0] == "deploy alpha" and titles[-1] == "deploy alpha · done"
    assert "deploy alpha · waiting for you" in titles and any(t.startswith("deploy alpha · configure ") for t in titles)
    # Claude's retried wait, the wait that never retries, and the end; both waits' time is yours.
    assert ran.screen.bells == 3
    yours = re.search(r"^ ✓ sign in .*Claude \S+(?: s)? \((\S+(?: s)?) yours\)$", screen, re.M)
    assert yours and _seconds(yours.group(1)) >= 2.5, screen
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
    ran.assert_drawn_and_gone()
    screen = ran.transcript
    assert "-welcome\n+welcome to alpha" in screen, screen
    assert "WOULD CHANGE" in screen and re.search(r"^ ✓ configure .* 1 +common", screen, re.M), screen
    assert (stand_in / "motd").read_text() == "welcome\n"


@pytest.mark.parametrize(("cols", "rows"), [(50, 12), (200, 60)])
def test_any_size_of_terminal_takes_the_region_unwrapped(stand_in, cols, rows):
    ran = _deploy(stand_in, cols=cols, rows=rows)
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone()


def test_a_resize_mid_run_leaves_no_region_on_the_screen(stand_in):
    ran = _deploy(stand_in, at=("configure", (60, 16)))
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone(visible_only=True)
    assert any(line.startswith("claude-on-proxmox  deploy alpha · done") for line in ran.screen.transcript())


def test_ctrl_c_ends_the_run_with_the_region_gone_and_the_tab_saying_so(stand_in):
    ran = _deploy(stand_in, interrupt="waiting for you")
    assert ran.rc != 0
    assert ran.screen.titles[-1] == "deploy alpha · stopped", ran.screen.titles
    ran.assert_drawn_and_gone()
    assert "User interrupted execution" in ran.transcript


def test_without_a_terminal_each_step_is_a_line(stand_in):
    ran = _piped(stand_in)
    assert ran.rc == 0, ran.text
    assert "\x1b" not in ran.text, ran.text
    lines = ran.text.splitlines()
    assert re.search(r"^✓ configure / common +9 tasks · 2 changed +\d", ran.text, re.M), ran.text
    assert re.search(r"^✓ configure +10 tasks · 2 changed", ran.text, re.M), ran.text
    assert (
        re.search(r"^✓ sign in / Claude +9 tasks · 1 changed", ran.text, re.M)
        and len(re.findall(r"^✓ sign in", ran.text, re.M)) == 1
    )
    assert "Waiting for you in the browser for localhost" in ran.text
    assert "TASK [" not in ran.text and "PLAY RECAP" not in ran.text
    assert lines.index("STAGES          TIME    TASKS  CHANGED") > lines.index("  alpha is up at 192.0.2.61")


FAILURES = """
- name: Four ways to fail, one VM each
  hosts: all
  gather_facts: false
  tasks:
    - name: A block whose rescue fails
      when: inventory_hostname == 'h1'
      block:
        - name: Fail inside the block
          ansible.builtin.fail:
            msg: the block failed and was rescued
      rescue:
        - name: Fail inside the rescue
          ansible.builtin.fail:
            msg: the rescue failed too
    - name: Run each item
      ansible.builtin.shell: "echo {{ item }}-says-no >&2; test {{ item }} != b"
      loop: [a, b]
      when: inventory_hostname == 'h2'
    - name: Crash
      crash: {}
      when: inventory_hostname == 'h3'
    - name: Thirty lines, then fail
      ansible.builtin.shell: "for n in $(seq 1 30); do echo line-$n >&2; done; exit 1"
      when: inventory_hostname == 'h4'
"""


def test_a_failure_says_what_failed_whole_wherever_it_is(tmp_path):
    (tmp_path / "failures.yml").write_text(FAILURES)
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "crash.py").write_text(
        "#!/usr/bin/python\nimport sys\nsys.stderr.write('module went bang\\n')\nsys.exit(1)\n"
    )
    python = f"ansible_connection=local ansible_python_interpreter={sys.executable}"
    (tmp_path / "hosts").write_text("".join(f"h{n} {python}\n" for n in range(1, 5)))
    ran = _piped(tmp_path, playbook="failures.yml")
    assert ran.rc == 2, ran.text
    text = ran.text
    # A failure in a rescue is the run's: only what the rescue took is not.
    assert "✗ run on h1: Fail inside the rescue" in text and "the rescue failed too" in text
    assert "the block failed and was rescued" not in text
    # A loop's failure is its items'.
    assert "✗ run on h2: Run each item" in text and "b-says-no" in text
    # A module that crashed says why.
    assert "✗ run on h3: Crash" in text and "module went bang" in text
    # The end of a long output, where the error is.
    assert "line-30" in text and "line-10\n" not in text
    assert re.search(r"^\d+ tasks · nothing changed · 4 failed", text, re.M), text


RESCUED = """
- name: A failure under each way in, each block's rescue taking it
  hosts: localhost
  gather_facts: false
  tasks:
    - name: An imported file
      block:
        - name: Import the file
          ansible.builtin.import_tasks: fails.yml
      rescue:
        - name: Take the imported file's failure
          ansible.builtin.debug:
            msg: took the import's
    - name: An imported role
      block:
        - name: Import the role
          ansible.builtin.import_role:
            name: fails
      rescue:
        - name: Take the imported role's failure
          ansible.builtin.debug:
            msg: took the role's
    - name: An included file
      block:
        - name: Include the file
          ansible.builtin.include_tasks: fails.yml
      rescue:
        - name: Take the included file's failure
          ansible.builtin.debug:
            msg: took the include's
    - name: An included role, in a block in a block
      block:
        - name: The inner block, with no rescue of its own
          block:
            - name: Include the role
              ansible.builtin.include_role:
                name: fails
      rescue:
        - name: Take the included role's failure
          ansible.builtin.debug:
            msg: took the included role's
"""


def _failing_file_and_role(root):
    (root / "fails.yml").write_text(
        "- name: fails | Fail in the file\n  ansible.builtin.fail:\n    msg: file-says-no\n"
    )
    (root / "roles" / "fails" / "tasks").mkdir(parents=True)
    (root / "roles" / "fails" / "tasks" / "main.yml").write_text(
        "- name: Fail in the role\n  ansible.builtin.fail:\n    msg: role-says-no\n"
    )
    (root / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")


def test_a_failure_a_rescue_takes_through_an_import_or_include_is_the_blocks(tmp_path):
    # A static import puts its file's block under a TaskInclude that no block lists: the rescue still takes it.
    _failing_file_and_role(tmp_path)
    (tmp_path / "rescued.yml").write_text(RESCUED)
    ran = _piped(tmp_path, playbook="rescued.yml")
    assert ran.rc == 0, ran.text
    assert "✗" not in ran.text and "says-no" not in ran.text, ran.text
    assert re.search(r"^\d+ tasks · nothing changed · 0 failed", ran.text, re.M), ran.text


def test_a_rescue_the_tags_leave_out_takes_nothing(tmp_path):
    # --tags runs the block's tagged import and filters its untagged rescue out: Ansible fails the host, and so does
    # the screen. (A tag on the block itself would reach the rescue too.)
    _failing_file_and_role(tmp_path)
    (tmp_path / "tagged.yml").write_text(
        "- name: A block whose import is tagged and whose rescue is not\n  hosts: localhost\n  gather_facts: false\n"
        "  tasks:\n    - name: The block\n      block:\n        - name: Import the file\n"
        "          ansible.builtin.import_tasks: fails.yml\n          tags: [picked]\n      rescue:\n"
        "        - name: Never run\n          ansible.builtin.debug:\n            msg: never\n"
    )
    ran = _piped(tmp_path, "--tags", "picked", playbook="tagged.yml")
    assert ran.rc == 2, ran.text
    assert "✗ run on localhost: fails | Fail in the file" in ran.text and "file-says-no" in ran.text, ran.text


@pytest.mark.parametrize(
    ("args", "env"),
    [(["-v"], {}), ([], {"CLAUDE_ON_PROXMOX_LIVE": ""}), ([], {"ANSIBLE_LOG_PATH": ""})],
    ids=["verbose", "no-variable", "no-log"],
)
def test_verbose_without_the_variable_or_without_a_log_the_run_is_streamed(stand_in, args, env):
    # No log, as LOG_DIR= makes it: the stream it would have held must reach the screen.
    ran = _piped(stand_in, *args, env=env)
    assert ran.rc == 0, ran.text
    assert "TASK [common : Write the motd]" in ran.text and "PLAY RECAP" in ran.text
    assert "STAGES" not in ran.text


def test_no_other_test_or_scenario_draws_a_run():
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    mentions = sorted(
        name
        for name in tracked.splitlines()
        if not name.endswith(".md")
        and (REPO / name).is_file()
        and "CLAUDE_ON_PROXMOX_LIVE" in (REPO / name).read_text(errors="replace")
    )
    # The callback that reads it and the model it feeds, the recipe that sets it, and the tests: test_justfile.py
    # reads the title the recipe exports, from an ansible-playbook stand-in that runs nothing. No scenario, tool or
    # config.
    assert mentions == [
        "callback_plugins/claude_on_proxmox.py",
        "filter_plugins/run_view.py",
        "justfile",
        "tests/unit/test_deploy_live.py",
        "tests/unit/test_justfile.py",
    ], mentions
    # In the justfile, deploy's recipe alone.
    lines = (REPO / "justfile").read_text().splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith("deploy "))
    end = next(n for n in range(start + 1, len(lines)) if lines[n] and not lines[n].startswith(" "))
    assert all(start < n < end for n, line in enumerate(lines) if "CLAUDE_ON_PROXMOX_LIVE" in line)
