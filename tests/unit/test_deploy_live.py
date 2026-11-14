"""just deploy's live output, run: the stdout callback at a pseudo-terminal of the test's own, read back by vt.py.

A stand-in deploy - deploy.yml importing playbooks named as the real ones are, so the stages are the real ones -
runs on this machine's localhost with callback_plugins/agent_warehouse.py and AGENT_WAREHOUSE_LIVE as the
justfile sets it. What a person sees: the region drawn and never wrapped, gone at the end, the reports, the browser
block, a prompt and a diff above it for good, the summary and NEXT STEPS after; the title and the bells. What the
log keeps: everything the stream would have had, in order, and no colour. Without a terminal, a line per step; with
-v, or without the variable, the stream as before. Nothing here is the developer's terminal: each run has a session
and a pseudo-terminal of its own (harness._take_the_terminal), or no terminal at all. No other test sets the
variable, so no other run is drawn (docs/architecture/custom-code.md §7.5).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import live_kit as kit
import pytest
from harness import repo_files
from test_attention_callback import _vm_module

from claude_vm.ansible import engine
from claude_vm.run import recipes

REPO = Path(__file__).resolve().parents[2]
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
- name: Open the VM in the editor
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
- name: Open a VM in the editor
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Ask what this run's commands see of the live switch
      ansible.builtin.shell: echo "[${AGENT_WAREHOUSE_LIVE-}]"
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
          claude_vm_next: "{{ {'alpha': {'vm': long}} }}"
          code_next: "{{ {'alpha, beta': {'editor': fleet}} }}"
      vars:
        long: >-
          alpha: just login alpha --only github signs GitHub in on alpha, which this run left for later: a line
          longer than any terminal here, never wrapped by its words
        fleet: |-
          VS Code opens one VM at a time, so this deploy for 2 VMs opened none. To open one:
          just code alpha
          just code beta
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


# NEXT STEPS as the stand-in records it: a line longer than the terminal, and a fleet's VS Code lines, each whole.
NEXT_STEPS = [
    "alpha: just login alpha --only github signs GitHub in on alpha, which this run left for later: a line longer than"
    " any terminal here, never wrapped by its words",
    "VS Code opens one VM at a time, so this deploy for 2 VMs opened none. To open one:",
    "just code alpha",
    "just code beta",
]


@pytest.fixture
def stand_in(tmp_path):
    for name, text in PLAYBOOKS.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    python = f"ansible_connection=local ansible_python_interpreter={sys.executable}"
    (tmp_path / "hosts").write_text(f"localhost {python}\nalpha {python} motd={tmp_path / 'motd'}\n")
    return tmp_path


def _environment(root, title, env):
    return kit.environment(root, title, ANSIBLE_ROLES_PATH=str(root / "roles"), **(env or {}))


def _fresh(root):
    """Each run as the first: no log, and the motd it writes as it was."""
    (root / "run.log").unlink(missing_ok=True)
    (root / "motd").write_text("welcome\n")


def _deploy(
    root, *args, title="deploy alpha", cols=100, rows=30, keys=None, interrupt=None, at=None, env=None, timeout=120
):
    """deploy.yml at a pseudo-terminal of cols and rows: keys typed once PROMPT has the terminal, a Ctrl-C once the
    title says interrupt, and at: (words of the title, bytes typed or a new (cols, rows)) done once the title says
    them."""
    _fresh(root)
    return kit.at_a_terminal(
        kit.playbook(root / "hosts", root / "deploy.yml", *args),
        root,
        _environment(root, title, env),
        cols=cols,
        rows=rows,
        prompts={PROMPT: keys} if keys is not None else None,
        interrupt=interrupt,
        at=at,
        timeout=timeout,
    )


def _piped(root, *args, title="deploy alpha", env=None, playbook="deploy.yml", timeout=120):
    _fresh(root)
    argv = kit.playbook(root / "hosts", root / playbook, *args)
    return kit.piped(argv, root, _environment(root, title, env), timeout=timeout)


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
    assert re.search(r"^agent-warehouse  deploy alpha · done in \d+ s · \d\d:\d\d$", screen, re.M), screen
    stages = re.findall(r"^ ✓ (\S+(?: \S+)?) {2,}", screen[screen.index("STAGES") :], re.M)
    assert stages == ["template", "provision", "configure", "sign in", "editor"], screen
    assert screen.index("STAGES") < screen.index("##  NEXT STEPS") and "alpha: just login alpha --only github" in screen
    # Each line whole, as the stream prints it: the terminal wraps a long one, and a fleet's lines stay their own.
    assert kit.next_steps(ran.text) == kit.next_steps(ran.log) == NEXT_STEPS, ran.text
    assert "\nALPHA  192.0.2.61 · VM 104 · new\n" in screen
    # configure's "not started", which the sign-in's report on Remote Control made old, is said above only.
    alpha = ran.table("\nALPHA  ")
    assert " Remote Control is live for alpha." in alpha and "not started" not in alpha, alpha
    # The tab says where the run is, a wait for the person and the end ring once each.
    titles = ran.screen.titles
    assert titles[0] == "deploy alpha" and titles[-1] == "deploy alpha · done"
    assert "deploy alpha · waiting for you" in titles and any(t.startswith("deploy alpha · configure ") for t in titles)
    # Claude's retried wait, the wait that never retries, and the end; both waits' time is yours.
    assert ran.screen.bells == 3
    yours = re.search(r"^ ✓ sign in .*Claude \S+(?: s)? \((\S+(?: s)?) yours\)$", screen, re.M)
    assert yours and kit.seconds(yours.group(1)) >= 2.5, screen
    # The log: no colour, the stream's every task, and the run's own words.
    assert "\x1b" not in ran.log
    for said in ("TASK [common : Write the motd]", "PLAY RECAP", "##  NEXT STEPS", "STAGES", "alpha is up at"):
        assert said in ran.log, said


def test_the_log_is_the_streams_with_the_runs_own_words_added(stand_in):
    drawn = _deploy(stand_in)
    streamed = _deploy(stand_in, title="")
    assert drawn.rc == streamed.rc == 0
    assert kit.SYNC_ON not in streamed.raw and "PLAY RECAP" in streamed.transcript
    assert kit.in_order(kit.logged(streamed.log), kit.logged(drawn.log)) == []
    added = [line for line in kit.logged(drawn.log) if line not in kit.logged(streamed.log)]
    assert any(line.startswith("STAGES") for line in added), added
    # NEXT STEPS is the stream's, line for line, drawn or piped.
    piped = _piped(stand_in)
    assert kit.next_steps(streamed.text) == kit.next_steps(drawn.text) == kit.next_steps(piped.text) == NEXT_STEPS


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
    assert any(line.startswith("agent-warehouse  deploy alpha · done") for line in ran.screen.transcript())


def test_ctrl_c_ends_the_run_with_the_region_gone_and_the_tab_saying_so(stand_in):
    # ansible-core now and then hangs on a Ctrl-C, a known bug (ansible/ansible#87812): CI's run sat in the browser
    # wait, between its tries, for two minutes after it. A run that hangs is run once more; any other failure is not.
    # The second run goes once the ansible-core this project pins has the fix.
    try:
        ran = _deploy(stand_in, interrupt="waiting for you", timeout=60)
    except pytest.fail.Exception as hung:
        if "did not finish within" not in str(hung):
            raise
        ran = _deploy(stand_in, interrupt="waiting for you", timeout=60)
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
    assert "✗ run · h1: Fail inside the rescue" in text and "the rescue failed too" in text
    assert "the block failed and was rescued" not in text
    # A loop's failure is its items'.
    assert "✗ run · h2: Run each item" in text and "b-says-no" in text
    # A module that crashed says why.
    assert "✗ run · h3: Crash" in text and "module went bang" in text
    # The end of a long output, where the error is.
    assert "line-30" in text and "line-10\n" not in text
    assert re.search(r"^\d+ tasks · nothing changed · 4 failed \(4 VMs\)", text, re.M), text


# A module as a VM's root can make any module there answer (test_attention_callback.VM_MODULE): warnings and a
# failure's message carrying a clipboard write (OSC 52), a title, a screen erase and an 8-bit CSI, which ansible-core
# prints as they are, on stderr - which the region lets through to the screen - and in the log.
VM_SAYS = """
- name: A VM's modules
  hosts: alpha
  gather_facts: false
  tasks:
    - name: Says what it likes
      vm_said: {}
    - name: Fails with what it likes
      vm_said:
        fail: true
"""


def test_what_a_vm_s_module_says_is_drawn_plain_on_the_screen_and_in_the_log(stand_in):
    (stand_in / "vm_says.yml").write_text(VM_SAYS)
    _vm_module(stand_in)
    _fresh(stand_in)
    ran = kit.at_a_terminal(
        kit.playbook(stand_in / "hosts", stand_in / "vm_says.yml"),
        stand_in,
        _environment(stand_in, "deploy alpha", None),
    )
    assert ran.rc == 2, ran.text
    ran.assert_drawn_and_gone()
    for said in (b"\x1b]52;", b"retitled", b"\x1b[2J", "\x9b".encode()):
        assert said not in ran.raw, (said, ran.text)
    assert "[WARNING]: warned" in ran.transcript and "failed" in ran.transcript, ran.transcript
    # The log has the failure's dump too, where YAML writes each sequence escaped (\e, \a), as the stream does.
    assert not re.search("[\x00-\x08\x0b-\x1f\x7f-\x9f]", ran.log), ran.log
    assert "[WARNING]: warned" in ran.log, ran.log


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
    # The task without its file's prefix, on this machine, which is no VM of the run's.
    assert "✗ run · this machine: Fail in the file" in ran.text and "file-says-no" in ran.text, ran.text


@pytest.mark.parametrize(
    ("args", "env"),
    [(["-v"], {}), ([], {"AGENT_WAREHOUSE_LIVE": ""}), ([], {"ANSIBLE_LOG_PATH": ""})],
    ids=["verbose", "no-variable", "no-log"],
)
def test_verbose_without_the_variable_or_without_a_log_the_run_is_streamed(stand_in, args, env):
    # No log, as LOG_DIR= makes it: the stream it would have held must reach the screen.
    ran = _piped(stand_in, *args, env=env)
    assert ran.rc == 0, ran.text
    assert "TASK [common : Write the motd]" in ran.text and "PLAY RECAP" in ran.text
    assert "STAGES" not in ran.text


def test_no_other_test_or_scenario_draws_a_run():
    mentions = sorted(
        str(path.relative_to(REPO))
        for path in repo_files()
        if path.suffix != ".md" and "AGENT_WAREHOUSE_LIVE" in path.read_text(errors="replace")
    )
    # The callback that hands the run to the engine, the engines that read it - a playbook's, a command's and the
    # tool that decides whether a command is drawn, and pytest's plugin, whose file it names - and the declarations
    # its title names, the recipes that set it, and the tests, each by name: the live kit sets it for the live tests,
    # this one, the live tests that draw a playbook through the engine and the command engine's name it, and
    # test_justfile.py and test_hosts.py read the title and the host the recipe exports, from an ansible-playbook
    # stand-in that runs nothing. No scenario or config, and no other test - test_status_live.py's run is drawn by a
    # callback of its own.
    assert mentions == [
        "callback_plugins/agent_warehouse.py",
        "justfile",
        "lib/claude_vm/ansible/engine.py",
        "lib/claude_vm/ansible/parts.py",
        "lib/claude_vm/run/recipes.py",
        "tests/unit/live_kit.py",
        "tests/unit/live_pytest.py",
        "tests/unit/test_deploy_live.py",
        "tests/unit/test_hosts.py",
        "tests/unit/test_justfile.py",
        "tests/unit/test_live_commands.py",
        "tests/unit/test_login_live.py",
        "tests/unit/test_test_live.py",
        "tools/live",
    ], mentions
    # In the justfile, only the recipes a declaration draws (lib/claude_vm/run/recipes.py), each its own title.
    lines = (REPO / "justfile").read_text().splitlines()
    recipe, naming = None, set()
    for line in lines:
        if line and not line.startswith((" ", "#", "[")) and ":" in line and not line.startswith(("set ", "alias ")):
            recipe = line.split()[0].rstrip(":")
        if "AGENT_WAREHOUSE_LIVE" in line:
            naming.add(recipe)
    assert naming <= set(recipes.RECIPES), naming
    assert {"deploy", "login", "destroy", "code", "lint", "init", "test"} <= naming, naming


RAISED = """
- name: Configure Claude development VMs
  hosts: alpha
  gather_facts: false
  tasks:
    - name: Install Tailscale and keep it joined
      block:
        - name: Run the tailscale role
          ansible.builtin.import_role:
            name: tailscale
      rescue:
        - name: Remember that Tailscale failed
          ansible.builtin.set_fact:
            claude_vm_configure_failures:
              - "alpha: Tailscale failed. Retry it with: just deploy alpha --tags tailscale"
    - name: Say what was written
      ansible.builtin.debug:
        msg:
          - "Wrote:"
          - "  inventory/hosts.yml        ansible_host"
          - "  local.yml                  proxmox_storage"
    - name: Keep what is left to do
      ansible.builtin.set_stats:
        data:
          claude_vm_next:
            alpha:
              vm: >-
                alpha: a line far longer than any banner is wide, which names a command to copy whole,
                just deploy alpha --tags tailscale, and goes on past eighty columns, never wrapped by its words
- name: Fail each VM whose configuration failed past a rescue
  hosts: alpha
  gather_facts: false
  tasks:
    - name: Fail where Tailscale failed
      ansible.builtin.fail:
        msg: "{{ claude_vm_configure_failures | join(' ') }}"
"""


def test_a_failure_raised_again_is_drawn_under_its_cause_with_its_own_words(tmp_path):
    (tmp_path / "playbooks").mkdir()
    (tmp_path / "playbooks" / "configure.yml").write_text(RAISED)
    (tmp_path / "roles" / "tailscale" / "tasks").mkdir(parents=True)
    (tmp_path / "roles" / "tailscale" / "tasks" / "main.yml").write_text(
        "- name: Join the tailnet\n  ansible.builtin.fail:\n    msg: tailscale up timed out\n"
    )
    python = f"ansible_connection=local ansible_python_interpreter={sys.executable}"
    (tmp_path / "hosts").write_text(f"alpha {python}\n")
    ran = _piped(
        tmp_path,
        "-e",
        '{"vm_name": "alpha"}',
        title="deploy --only configure alpha",
        playbook="playbooks/configure.yml",
    )
    assert ran.rc == 2, ran.text
    text = ran.text
    # The cause, by its step and VM, and the raise's own words; never the raise's task as if it were the failure.
    assert "✗ tailscale · alpha: Join the tailnet" in text and "tailscale up timed out" in text, text
    assert "alpha: Tailscale failed. Retry it with: just deploy alpha --tags tailscale" in text
    assert "Fail where Tailscale failed" not in ran.above_summary("deploy")
    assert re.search(r"^agent-warehouse  deploy --only configure alpha · failed after \d+ s at tailscale", text, re.M)
    # A report of several lines keeps them, and their indent.
    wrote = text.split("alpha  Wrote:\n", 1)[1].splitlines()[:2]
    assert wrote == [
        " " * 11 + "inventory/hosts.yml        ansible_host",
        " " * 11 + "local.yml" + " " * 18 + "proxmox_storage",
    ]
    # The failure is its cause's step's: tailscale's row is the failed one.
    assert re.search(r"^ ✗ tailscale ", text, re.M) and re.search(r"^ ✓ configure ", text, re.M), text
    # NEXT STEPS whole, past the block's width: the command is copied from one line.
    assert kit.next_steps(text) == [
        "alpha: a line far longer than any banner is wide, which names a command to copy whole, just deploy alpha"
        " --tags tailscale, and goes on past eighty columns, never wrapped by its words"
    ], text


def test_the_vms_a_run_was_given_are_read_from_its_extra_vars():
    assert engine.vm_names(['{"vm_name": "alpha,beta"}', "x=1"]) == ["alpha", "beta"]
    assert engine.vm_names(["vm_name=gamma"]) == ["gamma"]
    assert engine.vm_names(['{"vm_name": "alpha\\nProxyCommand x,-bad"}']) == []
    assert engine.vm_names([]) == []


def test_a_name_the_run_s_facts_hold_is_a_vm_s_only_whole():
    # refs.NAME, matched in full: a name with a space, a ; or a line break is no VM's, and a line break would break
    # the region's count of the lines it drew.
    names = ["alpha", "alpha beta", "al;pha", "alpha\nx", {"name": "beta"}, {"name": "beta\n"}]
    assert engine.Engine._names(names) == ["alpha", "beta"]


def test_a_tasks_file_is_the_one_the_import_nearest_the_play_names(tmp_path):
    # A file another file imports stays its importer's: destroy's release_pause.yml is release.yml's, Claude's.
    (tmp_path / "outer.yml").write_text(
        "- name: outer | Say outer\n  ansible.builtin.debug:\n    msg: outer\n"
        "- name: outer | Import the inner file\n  ansible.builtin.import_tasks: inner.yml\n"
    )
    (tmp_path / "inner.yml").write_text("- name: inner | Say inner\n  ansible.builtin.debug:\n    msg: inner\n")
    (tmp_path / "play.yml").write_text(
        "- name: A play\n  hosts: localhost\n  gather_facts: false\n  tasks:\n"
        "    - name: Its own\n      ansible.builtin.debug:\n        msg: own\n"
        "    - name: Import the outer file\n      ansible.builtin.import_tasks: outer.yml\n"
    )
    script = f"""
import json, sys
sys.path.insert(0, {str(REPO / "lib")!r})
from ansible import context
from ansible.module_utils.common.collections import ImmutableDict
from ansible.plugins.loader import init_plugin_loader
init_plugin_loader()
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader
from ansible.playbook import Playbook
from ansible.vars.manager import VariableManager
from claude_vm.ansible import engine
context.CLIARGS = ImmutableDict(tags=[], skip_tags=[])
loader = DataLoader()
inventory = InventoryManager(loader, sources="localhost,")
variables = VariableManager(loader=loader, inventory=inventory)
play = Playbook.load({str(tmp_path / "play.yml")!r}, variables, loader).get_plays()[0]
tasks = []
def walk(block):
    for item in block.block:
        walk(item) if hasattr(item, "block") else tasks.append(item)
for block in play.compile():
    walk(block)
print(json.dumps({{engine.drawn_name(t): engine.task_file(t) for t in tasks if not t.implicit}}))
"""
    ran = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=tmp_path, check=True)
    files = json.loads(ran.stdout.strip().splitlines()[-1])
    outer = str(tmp_path / "outer.yml")
    assert files == {"Its own": str(tmp_path / "play.yml"), "Say outer": outer, "Say inner": outer}, files
