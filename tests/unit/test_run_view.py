"""A live run, as drawn: filter_plugins/run_view.py, fed events with the time given.

A Run is what the callback keeps of a run - the stages deploy.yml's imports make, each step's tasks - and every
frame, line and summary is worked out from it alone, so each is checked here without Ansible: what a running, a
waiting, a failed and a finished run look like, that no line is wider than the terminal at any width, that a frame
folds to the height, and that the stage table names every playbook deploy imports
(docs/architecture/custom-code.md §7.5). The region that draws it is test_live_region.py's, the lines' helpers
test_screen.py's, and test_deploy_live.py runs the whole of it at a pseudo-terminal.
"""

from __future__ import annotations

import importlib.util
import re
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"test_{name}", REPO / "filter_plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


view = _load("run_view")

STARTED = time.mktime((2026, 10, 25, 9, 30, 0, 0, 0, -1))
LOG = ".logs/20261025-093000-deploy.log"
SGR = re.compile(r"\x1b\[[0-9;]*m")
# The plays of a deploy of one VM, as the callback hands them over: (ID, file:line, [(task ID, role)...]).
PLAYS = [
    ("plan", "/r/deploy.yml:39", [("plan-say", None)]),
    ("template", "/r/playbooks/template.yml:4", [("t1", "proxmox_template"), ("t2", "proxmox_template")]),
    ("register", "/r/playbooks/provision.yml:21", [("p0", None)]),
    ("create", "/r/playbooks/provision.yml:137", [("p1", "proxmox_vm"), ("p2", "proxmox_vm"), ("p3", "proxmox_vm")]),
    ("publish", "/r/playbooks/provision.yml:144", [("p4", None)]),
    ("discover", "/r/playbooks/discover.yml:3", [("d1", None)]),
    (
        "configure",
        "/r/playbooks/configure.yml:20",
        [("c0", None), *[(f"c{n}", "common") for n in range(1, 8)], *[(f"v{n}", "dev_tools") for n in range(1, 4)]],
    ),
    ("discover-login", "/r/playbooks/discover.yml:3", [("d2", None)]),
    ("login", "/r/playbooks/claude_login.yml:51", [("l1", "claude_code"), ("l2", "claude_code"), ("l3", None)]),
    ("code", "/r/playbooks/code.yml:61", [("o1", None), ("o2", None)]),
    ("raise", "/r/deploy.yml:225", [("r1", None)]),
]


def _plain(lines):
    return [SGR.sub("", line) for line in lines]


class Script:
    """A run fed events as Ansible sends them, one task at a time, the clock moved by each."""

    def __init__(self, check=False, plays=PLAYS, title="deploy alpha"):
        self.run = view.Run(title, 0.0, check=check, log=LOG, started_at=STARTED)
        self.run.plays(plays)
        self.now = 0.0

    def task(self, play, key, name, role=None, took=1.0, outcome="ok", hosts=("alpha",)):
        """A task that started, ran on hosts and ended: outcome None leaves it running."""
        self.run.play_start(play)
        self.run.task(key, name, role, self.now)
        for host in hosts:
            self.run.host_start(key, host, self.now)
        self.now += took
        if outcome is not None:
            for host in hosts:
                self.run.result(key, host, outcome, self.now)
        return self

    def to_configure(self):
        """The template there already, alpha created, and configure under way in common."""
        self.task("plan", "plan-say", "Report what this run will do", took=0.2)
        self.run.note("localhost", "Template is already built. Then provisioning and configuring alpha.", plan=True)
        self.task("template", "t1", "Check for the template", "proxmox_template", took=0.4)
        self.task("template", "t2", "Build the template", "proxmox_template", took=0.1, outcome="skipped")
        self.task("register", "p0", "Register the VMs to provision", hosts=("localhost",), took=0.3)
        self.task("create", "p1", "present | Clone from template", "proxmox_vm", took=12.0, outcome="changed")
        for name, value in (("proxmox_vm_exists", False), ("proxmox_vm_resolved_id", 104)):
            self.run.fact("alpha", name, value)
        self.run.fact("alpha", "proxmox_vm_address", "192.0.2.61")
        self.task("create", "p2", "present | Start the VM", "proxmox_vm", took=3.0, outcome="changed")
        self.task("create", "p3", "present | Wait for the guest agent", "proxmox_vm", took=45.0)
        self.task("publish", "p4", "Show each VM and the address it was given", hosts=("localhost",), took=0.1)
        self.run.note("localhost", "alpha is up at 192.0.2.61")
        self.task("discover", "d1", "cluster_vms | List every VM in the cluster", took=0.1, outcome="skipped")
        self.task("configure", "c0", "Wait for cloud-init to finish", took=20.0)
        self.task("configure", "c1", "Refresh apt cache", "common", took=4.0, outcome="changed")
        self.task("configure", "c2", "Install baseline packages", "common", took=30.0, outcome="changed")
        self.task("configure", "c3", "Set the time zone", "common", took=0.5, outcome="skipped")
        self.task("configure", "c4", "Harden sshd", "common", took=0.6)
        self.task("configure", "c5", "Mask each unit", "common", took=0.7, outcome="changed")
        self.task("configure", "c6", "Write the motd", "common", took=0, outcome=None)
        return self


def test_a_play_takes_its_stage_from_its_file_and_an_unnamed_one_from_a_neighbour():
    files = [
        "/r/deploy.yml:39",
        "/r/playbooks/template.yml:4",
        "/r/playbooks/discover.yml:3",
        "/r/playbooks/configure.yml:20",
        "/r/playbooks/discover.yml:3",
        "/r/playbooks/github_login.yml:66",
        "/r/deploy.yml:225",
    ]
    assert view.stages_of(files) == [
        ("template", None),
        ("template", None),
        ("configure", None),
        ("configure", None),
        ("sign in", "GitHub"),
        ("sign in", "GitHub"),
        ("sign in", "GitHub"),
    ]
    assert view.stages_of(["/elsewhere/site.yml:1"]) == [("run", None)]
    assert view.stages_of([]) == []


def _play_files(path: Path) -> list[Path]:
    """The file of each play a playbook runs, its imports followed, in order."""
    files = []
    for entry in yaml.safe_load(path.read_text()):
        imported = entry.get("ansible.builtin.import_playbook")
        files += _play_files(path.parent / imported) if imported else [path]
    return files


def test_the_stage_table_names_every_playbook_deploy_runs_in_the_order_it_runs_them():
    files = _play_files(REPO / "deploy.yml")
    stages = view.stages_of([str(f) for f in files])
    order = [stage for index, (stage, _) in enumerate(stages) if index == 0 or stages[index - 1][0] != stage]
    assert order == ["template", "provision", "configure", "sign in", "VS Code"]
    sign_ins = [
        step for index, (stage, step) in enumerate(stages) if stage == "sign in" and step != stages[index - 1][1]
    ]
    assert sign_ins == ["Claude", "GitHub", "Tailscale"]
    named = {f.name for f in files} - {"deploy.yml", "discover.yml"}
    assert named <= set(view.STAGE_FILES), named - set(view.STAGE_FILES)
    # What just deploy --only runs, a playbook of its own: playbooks/<the stage, - as _>.yml.
    recipe = (REPO / "justfile").read_text()
    only = re.search(r"\[arg\('STAGE', long='only', pattern='\(([a-z|-]+)\)\?'", recipe).group(1).split("|")
    for stage in only:
        name = f"{stage.replace('-', '_')}.yml"
        assert (REPO / "playbooks" / name).is_file() and name in view.STAGE_FILES, name


def test_count_tasks_counts_what_ansible_will_start(tmp_path):
    (tmp_path / "main.yml").write_text(
        """
- name: One
  ansible.builtin.debug: {}
- block:
    - {name: Two, ansible.builtin.debug: {}}
    - {name: Three, ansible.builtin.debug: {}}
  rescue:
    - {name: Four, ansible.builtin.debug: {}}
  always:
    - {name: Five, ansible.builtin.debug: {}}
- ansible.builtin.import_tasks: imported.yml
- ansible.builtin.include_tasks: included.yml
- ansible.builtin.import_tasks: "{{ which }}.yml"
- not a task
"""
    )
    (tmp_path / "imported.yml").write_text("- {name: Six}\n- block:\n    - {name: Seven}\n")
    (tmp_path / "included.yml").write_text("- {name: Never counted from here}\n")
    (tmp_path / "loop.yml").write_text("- ansible.builtin.import_tasks: loop.yml\n- {name: Once}\n")
    (tmp_path / "broken.yml").write_text("- name: [unclosed\n")
    assert view.count_tasks(str(tmp_path / "main.yml")) == 1 + 4 + 2 + 1 + 1
    assert view.count_tasks(str(tmp_path / "missing.yml")) == 0
    assert view.count_tasks(str(tmp_path / "broken.yml")) == 0
    # An import of itself ends: at a depth no real file reaches.
    assert view.count_tasks(str(tmp_path / "loop.yml")) == 9


def test_an_included_roles_tasks_are_found_in_the_first_directory_that_has_them(tmp_path):
    for directory in ("first", "second"):
        (tmp_path / directory / "claude_code" / "tasks").mkdir(parents=True)
    (tmp_path / "second" / "claude_code" / "tasks" / "browser_login.yml").write_text("[]\n")
    (tmp_path / "second" / "claude_code" / "tasks" / "main.yml").write_text("[]\n")
    search = [str(tmp_path / "first"), str(tmp_path / "second")]
    found = view.role_tasks_file("claude_code", "browser_login", search)
    assert found == str(tmp_path / "second" / "claude_code" / "tasks" / "browser_login.yml")
    assert view.role_tasks_file("claude_code", None, search).endswith("second/claude_code/tasks/main.yml")
    assert view.role_tasks_file("nobody", None, search) == ""


def test_a_running_frame_shows_the_stages_the_running_steps_tasks_and_where_the_run_is():
    script = Script().to_configure()
    lines = script.run.frame(script.now + 2.5, 100, 40, "⠋", color=False)
    started = time.strftime("%H:%M", time.localtime(STARTED))
    assert lines[0] == f"claude-on-proxmox  deploy alpha · started {started}"
    # The plan is printed whole above the region, and not cut into it.
    assert lines[1] == "" and "Template is already built" not in "\n".join(lines)
    text = "\n".join(lines)
    # Done stages whole, the template stage's skipped build no failure; provision says the VM.
    assert re.search(r"^ ✓ template     █{20}    3/3 .* no changes +0\.7 s$", text, re.M), text
    assert re.search(r"^ ✓ provision    █{20}    5/5    2 changed .* VM 104 · 192\.0\.2\.61 +1:00$", text, re.M)
    # The running stage: its own tasks first, then each step; the running step's last five tasks, the newest
    # with its timer, a skipped one dim and said, the others with their time.
    assert re.search(r"^ ⠋ configure    █+░+    8/12   3 changed +0:58$", text, re.M), text
    assert re.search(r"^     ⠋ common +█+░+ +6/7 +3 changed +0:38$", text, re.M), text
    window = [line.strip() for line in lines if line.startswith(" " * 9)]
    assert [line.split("  ")[0] for line in window] == [
        "● Install baseline packages",
        "– Set the time zone",
        "✓ Harden sshd",
        "● Mask each unit",
        "⠋ Write the motd",
    ]
    assert window[1].endswith("skipped") and window[-1].endswith("0:02")
    assert re.search(r"^     ○ dev_tools +3 tasks$", text, re.M), text
    assert re.search(r"^ ○ sign in      4 tasks$", text, re.M) and re.search(r"^ ○ VS Code      3 tasks$", text, re.M)
    assert lines[-2] == ""
    assert lines[-1] == f"⠋ 1:59 · stage 3 of 5 · {LOG}"
    # 16 tasks reached of 27: the ended stages' 3 and 5, configure's 12, the sign-in's 4 and VS Code's 3.
    assert script.run.title_text(script.now) == "deploy alpha · configure 59%"


def test_colour_is_on_the_screen_only_when_asked_for():
    script = Script().to_configure()
    coloured = script.run.frame(script.now, 100, 40, "⠋", color=True)
    plain = script.run.frame(script.now, 100, 40, "⠋", color=False)
    assert any("\x1b[" in line for line in coloured)
    assert not any("\x1b" in line for line in plain)
    assert _plain(coloured) == plain


@pytest.mark.parametrize("cols", [30, 40, 51, 52, 63, 64, 79, 80, 100, 200])
@pytest.mark.parametrize("rows", [6, 10, 14, 24, 50])
def test_no_line_of_a_frame_is_wider_than_the_terminal_and_a_frame_folds_to_its_height(cols, rows):
    script = Script(
        plays=[*PLAYS, ("extra", "/r/playbooks/configure.yml:20", [(f"x{n}", f"role{n}") for n in range(8)])]
    )
    script.to_configure()
    lines = _plain(script.run.frame(script.now, cols, rows, "⠋", color=True))
    assert all(len(line) <= cols - 1 for line in lines), [line for line in lines if len(line) > cols - 1]
    if rows >= 14:
        assert len(lines) <= rows - 1, "\n".join(lines)


def test_a_folded_frame_says_what_is_to_come_on_one_line():
    script = Script().to_configure()
    lines = script.run.frame(script.now, 100, 14, "⠋", color=False)
    assert "     ○ then dev_tools" in lines
    assert " ○ then sign in, VS Code" in lines


def test_a_wait_for_the_browser_is_said_on_its_stage_in_the_footer_and_the_title():
    script = Script().to_configure()
    script.run.result("c6", "alpha", "ok", script.now)
    for key in ("v1", "v2", "v3"):
        script.task("configure", key, f"Install {key}", "dev_tools")
    script.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    script.task("login", "l1", "browser_login | Say the page", "claude_code", took=0.1)
    script.task("login", "l2", "browser_login | Wait for your browser", "claude_code", outcome=None, took=0)
    assert script.run.retry("l2", "alpha", script.now, 600) is True
    assert script.run.retry("l2", "alpha", script.now + 5, 595) is False, "a second retry rings no bell"
    lines = script.run.frame(script.now + 65, 120, 40, "⠋", color=False)
    text = "\n".join(lines)
    assert " ⏸ sign in      Claude: waiting for you in the browser · alpha · 1:05 of up to 10:00" in lines, text
    assert re.search(r"^     ⏸ Claude ", text, re.M), text
    assert lines[-1].startswith("⏸ ") and " · waiting for you · " in lines[-1]
    assert script.run.title_text(script.now + 65) == "deploy alpha · waiting for you"
    script.now += 70
    script.run.result("l2", "alpha", "changed", script.now)
    script.task("login", "l3", "Report", took=0.1)
    summary = "\n".join(script.run.summary(script.now + 1, 100, color=False))
    assert re.search(r"^ ✓ sign in +1:1\d +4 +1 +Claude 1:1\d \(1:10 yours\)$", summary, re.M), summary
    assert "1:10 of it yours" in summary
    assert "Wait for your browser" not in summary.split("SLOWEST", 1)[1], "a wait is no slow task"


def test_a_failed_task_marks_its_step_and_stage_and_the_summary_says_where_the_run_stopped():
    script = Script().to_configure()
    script.run.result("c6", "alpha", "failed", script.now)
    lines = script.run.frame(script.now, 100, 40, "⠋", color=False)
    assert any(line.startswith(" ✗ configure") for line in lines)
    assert any(line.startswith("     ✗ common") for line in lines)
    assert any(line.strip().startswith("✗ Write the motd") for line in lines)
    script.run.end(script.now)
    summary = script.run.summary(script.now, 100, color=False, failed_hosts=["alpha"])
    assert summary[0].startswith("claude-on-proxmox  deploy alpha · stopped after 1 min 57 s on alpha · ")
    assert any(line.startswith(" ✗ configure") for line in summary)
    assert summary[-1].startswith("16 tasks · 5 changed · 1 failed · log ")


def test_a_dry_run_says_so_and_counts_what_would_change():
    script = Script(check=True, title="deploy --check alpha").to_configure()
    lines = script.run.frame(script.now, 120, 40, "⠋", color=False)
    assert lines[0].endswith(" · a dry run: nothing is changed")
    assert any("3 would change" in line for line in lines)
    script.run.end(script.now)
    summary = script.run.summary(script.now, 100, color=False)
    assert summary[2].startswith("STAGES          TIME    TASKS  WOULD CHANGE")
    assert "5 would change" in summary[-1]


def test_a_task_on_several_vms_says_each_and_as_counts_when_they_do_not_fit():
    script = Script()
    script.task("create", "p1", "present | Clone from template", "proxmox_vm", outcome=None, hosts=("alpha", "beta"))
    script.run.result("p1", "beta", "changed", script.now + 3)
    lines = script.run.frame(script.now + 4, 100, 40, "⠋", color=False)
    clone = next(line for line in lines if "Clone from template" in line)
    assert clone.endswith("alpha ⠋ 0:05 · beta ✓ 4.0 s"), clone
    hosts = tuple(f"vm{n}" for n in range(9))
    script.task("create", "p2", "present | Start the VM", "proxmox_vm", outcome=None, hosts=hosts)
    script.run.result("p2", "vm3", "failed", script.now + 2)
    start = next(line for line in script.run.frame(script.now + 4, 100, 40, "⠋", color=False) if "Start the VM" in line)
    assert start.endswith("1/9 VMs 1 failed ⠋ 0:05"), start


def test_output_that_is_not_a_terminal_gets_a_line_as_each_step_and_stage_ends_once():
    script = Script()
    script.task("plan", "plan-say", "Report what this run will do", took=0.2)
    script.task("template", "t1", "Check for the template", "proxmox_template", took=0.4)
    assert script.run.ended_lines(script.now) == []
    script.task("register", "p0", "Register the VMs", hosts=("localhost",), took=0.3)
    ended = script.run.ended_lines(script.now)
    assert ended == [
        f"✓ {'template / proxmox_template':<28} {'1 task':<36} 0.4 s",
        f"✓ {'template':<28} {'2 tasks':<36} 0.6 s",
    ]
    assert script.run.ended_lines(script.now) == []
    script.task("create", "p1", "present | Clone", "proxmox_vm", outcome="changed")
    script.task("create", "p2", "present | Start", "proxmox_vm", outcome="failed")
    script.task("publish", "p4", "Show each VM", hosts=("localhost",))
    assert script.run.ended_lines(script.now) == [
        f"✗ {'provision / proxmox_vm':<28} {'2 tasks · 1 changed · 1 failed':<36} 2.0 s"
    ]


def test_a_stage_that_is_one_step_is_said_once_by_the_steps_name():
    script = Script()
    script.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    script.task("login", "l1", "browser_login | Say the page", "claude_code")
    script.task("code", "o1", "Say what was opened", hosts=("localhost",))
    assert script.run.ended_lines(script.now) == [f"✓ {'sign in / Claude':<28} {'2 tasks':<36} 2.0 s"]


def test_the_summary_has_the_stages_each_vms_words_the_slowest_tasks_and_the_totals():
    script = Script().to_configure()
    script.run.result("c6", "alpha", "ok", script.now)
    script.run.note("alpha", "alpha: Claude Code 2.1.308")
    script.run.note("localhost", "alpha: signed in as you@example.com")
    script.run.note("localhost", "Opening VS Code")
    script.run.end(script.now)
    summary = script.run.summary(script.now, 100, color=False)
    text = "\n".join(summary)
    assert summary[0].startswith("claude-on-proxmox  deploy alpha · done in 1 min 57 s · ")
    assert summary[2:5] == [
        "STAGES          TIME    TASKS  CHANGED",
        " ✓ template    0.7 s        3        —   proxmox_template 0.5 s",
        " ✓ provision    1:00        5        2   VM 104 · 192.0.2.61 · proxmox_vm 1:00",
    ]
    assert re.search(r"^ ✓ configure    56 s +8 +3   common 36 s$", text, re.M), text
    assert "\nALPHA  192.0.2.61 · VM 104 · new\n alpha is up at 192.0.2.61\n Claude Code 2.1.308\n" in text
    assert " signed in as you@example.com\n" in text
    assert "\nTHIS MACHINE\n Opening VS Code\n" in text
    assert re.search(r"^SLOWEST  present \| Wait for the guest agent 45 s · Install baseline packages 30 s", text, re.M)
    assert summary[-1] == f"16 tasks · 5 changed · 0 failed · log {LOG}"
    assert all(len(line) <= 99 for line in summary)


def test_a_steps_time_is_its_own_tasks_and_a_handler_at_the_end_of_the_play_adds_only_its_own():
    script = Script()
    script.task("configure", "c1", "Harden sshd", "common", took=2.0, outcome="changed")
    script.task("configure", "v1", "Install the editor", "dev_tools", took=50.0)
    script.task("configure", "c9", "Restart ssh", "common", took=1.0, outcome="changed")
    script.run.end(script.now)
    summary = "\n".join(script.run.summary(script.now, 100, color=False))
    assert re.search(r"^ ✓ configure    53 s .* common 3\.0 s · dev_tools 50 s$", summary, re.M), summary


def test_a_tagged_task_that_runs_on_is_a_wait_once_and_a_quick_one_never():
    script = Script()
    script.run.play_start("login")
    script.run.task("l1", "device_flow | Wait for the approval", "github_projects", 0.0, waits=True)
    assert script.run.settle(1.9) is False and script.run.current.wait is None
    assert script.run.settle(2.0) is True, "the bell"
    assert script.run.settle(9.0) is False, "once"
    assert script.run.title_text(9.0) == "deploy alpha · waiting for you"
    lines = script.run.frame(65.0, 120, 40, "⠋", color=False)
    assert " ⏸ sign in      Claude: waiting for you in the browser ·  · 1:05" in lines, lines
    script.run.result("l1", "alpha", "ok", 70.0)
    assert script.run.current.waited(70.0) == 70.0
    # A retry after it says how long the wait may still take, and rings no second bell.
    script.run.task("l2", "browser_login | Wait for your browser", "claude_code", 70.0, waits=True)
    assert script.run.settle(73.0) is True
    assert script.run.retry("l2", "alpha", 75.0, 300) is False
    assert script.run.current.wait == (75.0, 300)
    # Skipped on every VM, as it is where nobody need sign in: over before it could be a wait.
    script.run.task("l3", "web_login | Wait for the page", "tailscale", 80.0, waits=True)
    script.run.result("l3", "alpha", "skipped", 80.1)
    assert script.run.settle(85.0) is False and script.run.current.wait is None
    # Without a terminal there is no frame to notice it: a long one is counted as yours when it ends.
    script.run.task("l4", "device_flow | Wait for the approval", "github_projects", 90.0, waits=True)
    script.run.result("l4", "alpha", "ok", 120.0)
    assert script.run.current.waited(120.0) == 30.0


def test_the_summary_drops_a_report_a_later_one_replaced_and_keeps_every_other():
    script = Script().to_configure()
    script.run.result("c6", "alpha", "ok", script.now)
    say = {}
    for key, name in (("g1", "auth | Say how to sign gh in"), ("r1", "clone | Say what was selected")):
        script.task("configure", key, name, "github_projects", took=0.1)
        say[key] = script.run.find(key)
    script.task("configure", "k1", "remote_control | Report where to reach the session", "claude_code", took=0.1)
    # A report and its NEXT STEPS record, as each report task and the set_stats after it make them.
    script.run.note("alpha", "alpha: gh is not signed in. just deploy signs it in next.", task=say["g1"])
    script.run.record("alpha", "github", "alpha: gh is not signed in. just deploy signs it in next.")
    script.run.note("alpha", "24 of 24 repositories selected", task=say["r1"])
    script.run.note("alpha", "Remote Control is live for alpha.", task=script.run.find("k1"))
    script.run.record("alpha", "remote_control", "")
    script.run.record("alpha", "tailscale", "alpha: Tailscale is off.")
    script.run.note("localhost", "VS Code is opening /home/dev/projects on alpha (192.0.2.61).")
    script.run.note("localhost", "Every alias is up to date.")
    # The sign-in: the login's own report clears the record, and the repositories are reported again.
    script.task("login", "l1", "login | Report the login state", "github_projects", took=0.1)
    script.run.note("alpha", "alpha: signed in to GitHub as dev", task=script.run.find("l1"))
    script.run.record("alpha", "github", "")
    script.task("login", "r2", "clone | Say what was selected", "github_projects", took=0.1)
    script.run.note("alpha", "25 of 25 repositories selected", task=script.run.find("r2"))
    script.run.end(script.now)
    text = "\n".join(script.run.summary(script.now, 100, color=False))
    alpha = text.split("ALPHA", 1)[1].split("\n\n", 1)[0]
    assert alpha.splitlines()[1:] == [
        " alpha is up at 192.0.2.61",
        " Remote Control is live for alpha.",
        " VS Code is opening /home/dev/projects on alpha (192.0.2.61).",
        " signed in to GitHub as dev",
        " 25 of 25 repositories selected",
    ], alpha
    assert "\nTHIS MACHINE\n Every alias is up to date.\n" in text
    assert text.startswith("claude-on-proxmox  deploy alpha · done") and "\nSTAGES " in text and "\nSLOWEST " in text


def _report(script, play, key, role, vm, text, records):
    """A report and the set_stats task after it, as every report task is followed by its NEXT STEPS record."""
    script.task(play, key, f"{key} | Report", role, took=0.1)
    script.run.note(vm, text, task=script.run.find(key))
    script.task(play, f"{key}-keep", f"{key} | Keep what is left to do", role, took=0.1)
    for matter, line in records.items():
        script.run.record(vm, matter, line, task=script.run.find(f"{key}-keep"))


@pytest.mark.parametrize("signed_in", [False, True], ids=["signed-in-by-this-run", "signed-in-before"])
def test_a_report_on_a_matter_a_later_report_recorded_again_is_left_out(signed_in):
    script = Script().to_configure()
    script.run.result("c6", "alpha", "ok", script.now)
    if signed_in:
        _report(script, "configure", "rc", "claude_code", "alpha", "Remote Control is live.", {"remote_control": ""})
        _report(script, "login", "li", "claude_code", "alpha", "alpha: signed in as you", {"claude": ""})
    else:
        # Under just deploy configure records nothing left, since its sign-in follows: no line to be replaced.
        said = "Remote Control is configured on alpha but not started: no login."
        _report(script, "configure", "rc", "claude_code", "alpha", said, {"remote_control": "", "claude": ""})
        _report(script, "configure", "gh", "github_projects", "alpha", "gh is not signed in.", {"github": ""})
        _report(script, "login", "li", "claude_code", "alpha", "alpha: signed in as you", {"claude": ""})
        _report(script, "login", "rl", "claude_code", "alpha", "Remote Control is live.", {"remote_control": ""})
        _report(script, "login", "dl", "github_projects", "alpha", "signed in to GitHub", {"github": ""})
    script.run.end(script.now)
    text = "\n".join(script.run.summary(script.now, 100, color=False))
    alpha = text.split("ALPHA", 1)[1].split("\n\n", 1)[0].splitlines()[1:]
    if signed_in:
        assert alpha == [" alpha is up at 192.0.2.61", " Remote Control is live.", " signed in as you"], alpha
    else:
        assert alpha == [
            " alpha is up at 192.0.2.61",
            " signed in as you",
            " Remote Control is live.",
            " signed in to GitHub",
        ], alpha


def test_a_failed_run_says_so_in_the_title():
    script = Script().to_configure()
    script.run.end(script.now, failed=True)
    assert script.run.title_text(script.now) == "deploy alpha · failed"


def test_a_vms_id_is_a_number_or_nothing():
    run = view.Run("deploy alpha", 0.0)
    run.fact("alpha", "proxmox_vm_resolved_id", "104; rm -rf")
    run.fact("alpha", "proxmox_vm_address", "192.0.2.61\nProxyCommand x")
    assert run.facts["alpha"] == {"address": "192.0.2.61 ProxyCommand x"}
    run.fact("alpha", "proxmox_vm_resolved_id", 104)
    assert run.facts["alpha"]["vmid"] == 104


def test_a_running_stage_counts_its_ended_steps_whole():
    # --tags, or a rescue never run: a step ends short of what was counted for it, and the stage goes on.
    plays = [
        (
            "configure",
            "/r/playbooks/configure.yml:20",
            [*((f"c{n}", "common") for n in range(9)), ("v1", "dev_tools"), ("v2", "dev_tools")],
        )
    ]
    script = Script(plays=plays)
    script.task("configure", "c0", "Harden sshd", "common")
    script.task("configure", "v1", "Install the editor", "dev_tools", outcome=None)
    row = next(line for line in script.run.frame(script.now, 100, 40, "⠋", color=False) if "configure" in line)
    assert "  2/3  " in row, row
    assert script.run.title_text(script.now) == "deploy alpha · configure 67%"


@pytest.mark.parametrize(
    ("text", "vm"),
    [
        ("alpha: the alias is ready", "alpha"),
        ("alpha is up at 192.0.2.61", "alpha"),
        ("VS Code is opening /home/dev/projects on beta (192.0.2.62).", "beta"),
        ("the aliases of alpha and beta are written", None),
        ("alphabet soup", None),
        ("on Tailscale as alpha.tail0.ts.net", "alpha"),
        ("nothing to say", None),
    ],
)
def test_a_line_said_on_this_machine_is_the_vms_it_names(text, vm):
    assert view.Run._named(text, ["alpha", "beta"]) == vm


def test_a_title_follows_the_run_to_done():
    script = Script()
    assert script.run.title_text(0) == "deploy alpha"
    script.task("plan", "plan-say", "Report", took=0.1)
    assert script.run.title_text(script.now).startswith("deploy alpha · template ")
    script.run.end(script.now)
    assert script.run.title_text(script.now) == "deploy alpha · done"
