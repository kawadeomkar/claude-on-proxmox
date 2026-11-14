"""A playbook run drawn live: Ansible's events turned into the region, the words above it, and the summary.

The stdout callback (callback_plugins/agent_warehouse.py) hands every event to an Engine when a recipe has exported
AGENT_WAREHOUSE_LIVE, its title, and keeps only its classic output for itself. Everything a live run does is here:

- live, at a terminal that can draw (lib/claude_vm/tui/live_region.py): the run's model (lib/claude_vm/run/model.py)
  drawn (lib/claude_vm/tui/run.py) in place at the bottom of the screen, with the reports, the blocks, the diffs,
  warnings and failures printed above it for good;
- lines, any other output: a line as each step ends, and the same reports, blocks and failures.

Either way the default callback still renders every task, sent to the run's log alone (display's log_only), so the
log is what it would be without the engine; the run's own words go to the screen in colour and to the log without;
and a summary replaces PLAY RECAP on the screen, which the log keeps, followed by NEXT STEPS. What the run is made
of - its stages and steps, the failures it raises again - is its recipe's declaration
(lib/claude_vm/run/recipes.py); nothing here names a command (docs/architecture/custom-code.md §7.5, §7.8).

Imported as claude_vm.ansible.engine by the stdout callback (docs/architecture/custom-code.md §7.10).
"""

from __future__ import annotations

import atexit
import contextlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path

from ansible import constants as C
from ansible import context
from ansible.errors import AnsiblePromptInterrupt
from ansible.utils import display as ansible_display

from .. import text
from ..refs import NAME
from ..run import model as view
from ..run import next_steps as steps
from ..tui import choose
from ..tui import run as drawn
from . import parts as commands

plain_text = text.plain_text

LIVE_ENV = commands.LIVE_ENV
STATE_ENV = commands.STATE_ENV
# The run's host, where the checkout has more than one (the justfile's, from tools/check host): said beside the title.
HOST_ENV = "AGENT_WAREHOUSE_LIVE_HOST"
WAITING_TAG = "attention_browser"


def _actions(name):
    """An action as a task may name it: short, or under ansible.builtin or ansible.legacy."""
    return frozenset({name, f"ansible.builtin.{name}", f"ansible.legacy.{name}"})


DEBUG_ACTIONS = _actions("debug")
STATS_ACTIONS = _actions("set_stats")
INCLUDE_TASKS = _actions("include_tasks")
IMPORT_TASKS = _actions("import_tasks")
INCLUDE_ROLE = _actions("include_role")
ARGUMENT_CHECKS = _actions("validate_argument_spec")
ADD_HOST = _actions("add_host")
META = _actions("meta")
# The facts the provision stage says a VM by.
VM_FACTS = ("proxmox_vm_resolved_id", "proxmox_vm_address", "proxmox_vm_exists")
# A prompt rings the bell when the person has had nothing to answer for this long: they may have looked away.
QUIET_SECONDS = 10.0
# A task name's file prefix, as an included file's tasks are named: "session_read | Read the sessions".
FILE_PREFIX = re.compile(r"^[a-z0-9_]+ \| ")


def choosing(display, original):
    """prompt_until, with a question whose answers ride along (tui/choose.py's marker) asked as a menu: in this
    process, which holds the terminal, at one that can draw, and under Display's lock as prompt_until reads, so nothing
    else is written while it waits. The question's text first, through display() as prompt_until says it; the answer
    after, on one line, to the screen and the log. Anything else - no menu, a hidden question, a timeout, no terminal -
    goes to Ansible's own."""

    def prompt_until(msg, private=False, seconds=None, interrupt_input=None, complete_input=None):
        menu = choose.menu_of(complete_input)
        if menu is None or private or seconds is not None or not choose.can_choose(sys.stdin, sys.stdout):
            return original(
                msg, private=private, seconds=seconds, interrupt_input=interrupt_input, complete_input=complete_input
            )
        with display._lock:
            display.display(choose.question(msg, menu))
            try:
                value = choose.ask(menu, sys.stdin, sys.stdout, color=not C.ANSIBLE_NOCOLOR)
            except choose.Interrupted:
                raise AnsiblePromptInterrupt("user interrupt") from None
            display.display(choose.collapsed(menu, value))
        return value.encode()

    return prompt_until


def from_environment(callback, playbook):
    """The engine for this run, or None for the stream: AGENT_WAREHOUSE_LIVE set, verbosity 0, and a log to send
    the stream to (LOG_DIR= turns it off). The variable, and the state's path of a run in parts
    (AGENT_WAREHOUSE_LIVE_STATE), are taken out of the environment either way, so nothing the run starts - the editor,
    whose terminals a later just destroy may run in - inherits them."""
    title = os.environ.pop(LIVE_ENV, "").strip()
    state = os.environ.pop(STATE_ENV, "").strip()
    host = os.environ.pop(HOST_ENV, "").strip()
    if not title or callback._display.verbosity != 0 or ansible_display.logger is None:
        return None
    engine = Engine(callback, title, state, _vm_name(host))
    engine.start(playbook)
    return engine


def _vm_name(value):
    """value as a VM's name: plain text, stripped, and whole by a VM name's pattern; else ""."""
    name = plain_text(str(value)).strip()
    return name if NAME.fullmatch(name) else ""


def _file_of(item):
    """The file a task or a play comes from, without its line."""
    return str(item.get_path() or "").split(":", 1)[0]


def _role_search():
    """Where an included role's tasks are looked for: the checkout's roles, then Ansible's roles path."""
    return [os.path.join(os.getcwd(), "roles"), *C.DEFAULT_ROLES_PATH]


def vm_names(extra_vars):
    """The VMs a run was given (vm_name, comma-separated), from the command line's extra vars: each a plain name."""
    names = ""
    for given in extra_vars or ():
        given = str(given).strip()
        if given.startswith("{"):
            try:
                names = str(json.loads(given).get("vm_name", names) or "")
            except (ValueError, AttributeError):
                continue
        elif given.startswith("vm_name="):
            names = given.split("=", 1)[1]
    out = []
    for name in names.split(","):
        name = _vm_name(name)
        if name and name not in out:
            out.append(name)
    return out


# --- what a play compiled to -------------------------------------------------------------------------------------
def tasks_of(blocks, out, play=None, search=()):
    """Each task a play compiled to, as (its ID, its role's name or None, meta), blocks' rescue and always included,
    and only those --tags and --skip-tags leave, as the play's iterator filters them. meta says, for an include whose
    file is literal, how many tasks it will announce (include) and, for include_role, the role they are in."""
    for block in blocks:
        for part in (block.block, block.rescue, block.always):
            for item in part or []:
                if hasattr(item, "block"):
                    tasks_of([item], out, play, search)
                elif not getattr(item, "implicit", False) and tagged_in(item, play):
                    meta = {"blocks": blocks_of(item), "file": task_file(item), "name": drawn_name(item)}
                    out.append((item._uuid, role_of(item), {**_include_meta(item, search), **meta}))
    return out


def _include_meta(task, search):
    """For an include whose file a playbook names literally: the count of the tasks it will announce."""
    args = getattr(task, "args", None) or {}
    try:
        if task.action in INCLUDE_TASKS:
            target = args.get("file") or args.get("_raw_params")
            if not isinstance(target, str) or "{{" in target:
                return {}
            base = os.path.dirname(_file_of(task))
            path = target if os.path.isabs(target) else os.path.join(base, target)
            if not os.path.isfile(path):
                return {}
            return {"include": view.count_tasks(path), "include_file": _included_file(task, path)}
        if task.action in INCLUDE_ROLE:
            name, tasks_from = args.get("name"), args.get("tasks_from")
            if not isinstance(name, str) or "{{" in name or "{{" in str(tasks_from or ""):
                return {}
            path = view.role_tasks_file(name.split(".")[-1], tasks_from, search)
            return {"include": view.count_tasks(path), "include_role": name.split(".")[-1]} if path else {}
    except Exception:  # noqa: BLE001 - a count is a nicety: an include whose file cannot be read is counted as it runs
        return {}
    return {}


def tagged_in(task, play):
    if play is None:
        return True
    try:
        return bool(task.evaluate_tags(play.only_tags, play.skip_tags, all_vars={}))
    except Exception:  # noqa: BLE001 - tags that need a variable to say: counted, as a run may reach them
        return True


def role_of(task):
    return task._role.get_name(include_role_fqcn=False) if getattr(task, "_role", None) else None


def drawn_name(task):
    """A task's name as drawn: without its role's prefix, which its step says, nor its file's."""
    name = task.get_name().strip()
    role = role_of(task)
    if role and name.startswith(role + " : "):
        name = name[len(role) + 3 :]
    return FILE_PREFIX.sub("", name)


def task_file(task):
    """The task file a task comes from as its play reaches it, without a line: the file the import or include nearest
    the play names - a file another file includes stays its includer's - else the task's own file."""
    own = _file_of(task)
    outer, node = None, getattr(task, "_parent", None)
    while node is not None:
        if getattr(node, "action", None) in INCLUDE_TASKS | IMPORT_TASKS:
            outer = node
        node = getattr(node, "_parent", None)
    if outer is None:
        return own
    args = getattr(outer, "args", None) or {}
    target = args.get("file") or args.get("_raw_params")
    if not isinstance(target, str) or "{{" in target:
        return own
    base = os.path.dirname(_file_of(outer))
    return os.path.normpath(os.path.join(base, target))


def _included_file(task, target):
    """The task file an include's tasks come from, as task_file says it of each: the import the include is in, else
    its target."""
    own = _file_of(task)
    outer = task_file(task)
    return outer if outer != own else os.path.normpath(target)


def blocks_of(task):
    """The names of the blocks around a task, innermost first."""
    names, node = [], getattr(task, "_parent", None)
    while node is not None:
        if hasattr(node, "block") and getattr(node, "name", None):
            names.append(str(node.name))
        node = getattr(node, "_parent", None)
    return names


def rescued(task, play=None):
    """Whether a block's rescue takes the task's failure: the task is among that block's own tasks, at any depth, and
    the rescue has a task the play's tags leave. One in a rescue or an always is not, unless a block further out has
    it among its own.

    Any link between the task and the block may be the one the block lists: a static import puts the file's own block
    there, under a TaskInclude that no block lists, so the link right under the block is not always it."""
    below, node = [], task
    while node is not None:
        below.append(getattr(node, "_uuid", None))
        parent = getattr(node, "_parent", None)
        if parent is not None and getattr(parent, "rescue", None) and _keeps(parent.rescue, play):
            ids = set(below)
            if not ids & (_uuids(parent.rescue) | _uuids(parent.always)) and ids & _uuids(parent.block):
                return True
        node = parent
    return False


def _uuids(items):
    return {getattr(item, "_uuid", None) for item in items or []}


def _keeps(items, play):
    """Whether any task among items, at any depth, is one the play's --tags and --skip-tags leave: a rescue they filter
    out entirely rescues nothing, and Ansible fails the host."""
    for item in items or []:
        if hasattr(item, "block"):
            if any(_keeps(part, play) for part in (item.block, item.rescue, item.always)):
                return True
        elif tagged_in(item, play):
            return True
    return False


def retry_left(result):
    """A retried task's runs still to come, and the seconds of delay before each. Ansible says the next attempt
    (attempts) and how many runs there are in all, the first among them (retries), and sleeps the delay before each
    run left: one more than its own "N retries left" counts, which would make a wait end a delay early."""
    try:
        delay = int(result.task.delay)
    except (TypeError, ValueError):
        delay = 0
    return result.result.get("retries", 0) - result.result.get("attempts", 0) + 1, delay


def play_batches(play):
    """How many batches a play runs in: one, unless it is serial."""
    try:
        # Each batch starts with the inventory restricted to it: the play's hosts are all of them.
        inventory = play._variable_manager._inventory
        hosts = len(inventory.get_hosts(play.hosts, order=play.order, ignore_restrictions=True))
        return view.batches(hosts, play.serial)
    except Exception:  # noqa: BLE001 - a count is a nicety
        return 1


class Engine(commands.Drawn):
    """One live run: the callback's events in; the region, the run's own words and the summary out."""

    def __init__(self, callback, title, state_path="", host=""):
        super().__init__(callback._display, title, state_path, on_prompt=self.prompt)
        self.cb = callback
        self.host = host
        self.saved = False  # a part's state saved, at its end
        self.plan_play = None
        self._failed_items = {}  # (task ID, host): the results of a loop's items that failed, said with the task
        self._attended = self._now()  # when the person last had something to answer: the start, a bell, a prompt
        self._facts = set()  # the facts the declaration reads each VM's outcome from
        self._infos = {}  # stage key: the fact its row's info names VMs from
        self._checklists = {}  # stage key: the fact its playbook keeps its checks in

    # --- what the run says, and where it goes ------------------------------------------------------------------
    # The run's own words and anything on stderr - warnings, deprecations, errors - stay on the screen; the rest,
    # the default callback's every task and profile_tasks' timings, goes to the log alone (live_region.Output).
    def say(self):
        return self.out.say()

    def print(self, lines):
        """Lines of the run's own, (coloured, plain) each: the colour to the screen, the plain text to the log."""
        self.out.print(lines)

    def _settle(self, now):
        # A tagged wait that never retries, GitHub's device flow, is known by how long it runs.
        if self.run.settle(now):
            self._bell(now)

    def _bell(self, now):
        self._attended = now
        self.out.bell()

    def diffed(self, said):
        """A dry run's diff, as the callback prints it."""
        with self.display._lock:
            if self.run is not None:
                self.run.diff(said)

    def warned(self, said):
        """What a module returned as warnings and deprecations, plain, as the callback prints them."""
        with self.display._lock:
            if self.run is not None:
                for each in said:
                    self.run.warning(each)

    def _who(self, host):
        """Whom a line is about: a VM, or for this machine the run's one VM, else this machine."""
        if host != "localhost":
            return host
        return self.run.names[0] if len(self.run.names) == 1 else "this machine"

    # --- the playbook ------------------------------------------------------------------------------------------
    def start(self, playbook):
        cli = context.CLIARGS or {}
        self.run = drawn.Run(
            self.title,
            self._now(),
            check=bool(cli.get("check")),
            log=commands.log_shown(),
            vms=vm_names(cli.get("extra_vars")),
            host=self.host,
        )
        if self.state_path:
            # A part of a run in parts: the parts before it, done, drawn above its own stages.
            self._take_over(self._now())
        search = _role_search()
        plays = []
        for play in playbook.get_plays():
            try:
                tasks = tasks_of(play.compile(), [], play, search)
            except Exception:  # noqa: BLE001 - a count is a nicety: a play that will not compile here still runs
                tasks = []
            plays.append((play._uuid, play.get_path(), tasks, play.get_name()))
        self.run.plays(plays)
        stages = self.run.recipe.stages
        self._facts = {o.fact for stage in stages for step in (*stage.steps, stage) for o in step.outcomes}
        self._infos = {stage.key: stage.info.fact for stage in stages if isinstance(stage.info, view.recipes.Info)}
        self._checklists = {stage.key: stage.checklist for stage in stages if stage.checklist}
        first = playbook.get_plays()[:1]
        if first and Path(_file_of(first[0])).name in self.run.recipe.plan:
            self.plan_play = first[0]._uuid
        # Under the region's own wrapper, which steps aside for every prompt: the menu is drawn while it is away.
        self.display.prompt_until = choosing(self.display, self.display.prompt_until)
        self.out.start()
        self.out.redraw()
        self.out.spin()
        if self.mode == "live":
            # Registered after the region's own, so it runs first: a run that ends without its stats, at Ctrl-C,
            # does not leave the tab saying it is still at work.
            atexit.register(self._stopped_title)
        if self.state_path:
            # A part that ends without its stats - Ctrl-C, after which ansible-playbook exits 99 - still saves what it
            # did, said stopped, for the run's end.
            atexit.register(self._save_stopped)

    def _save_stopped(self):
        if self.saved or self.run is None:
            return
        now = self._now()
        with self.display._lock:
            if self.run.current is not None and self.run.current.end is None:
                for host, entry in self.run.current.hosts.items():
                    if entry[1] is None:
                        self.run.current.host_end(host, "stopped", now)
            state = self.run.dump(now, self.records, self.failed_hosts, stopped=True)
        with contextlib.suppress(OSError):
            commands.save_state(self.state_path, state)
        self.saved = True

    def _stopped_title(self):
        # A part that saved its state is no stopped run: the next part, or the run's end, goes on with the title.
        if self.run.finished is None and not self.saved:
            self.out.title(f"{self.run.title} · stopped")

    def play_start(self, play):
        batches = play_batches(play) if play._uuid not in self.run.play_batches else 1
        with self.display._lock:
            self.run.play_start(play._uuid, play.get_name(), play.get_path(), batches=batches, now=self._now())

    def task_start(self, task):
        with self.display._lock:
            waits = WAITING_TAG in task.tags and task.action not in DEBUG_ACTIONS
            hidden = bool(getattr(task, "implicit", False)) or task.action in ARGUMENT_CHECKS
            # A meta: end_host leaves each host it ends as it is, which Ansible says nothing of.
            ends = task.action in META and str((task.args or {}).get("_raw_params", "")).strip() == "end_host"
            self.run.task(
                task._uuid,
                drawn_name(task),
                role_of(task),
                self._now(),
                waits=waits,
                hidden=hidden,
                blocks=blocks_of(task),
                ends=ends,
                file=task_file(task),
            )
            self._after()

    def include(self, included_file):
        with self.display._lock:
            task = included_file._task
            if getattr(included_file, "_is_role", False):
                role = getattr(task, "_role_name", None) or str(included_file._filename)
                search = _role_search()
                tasks_from = (getattr(task, "_from_files", None) or {}).get("tasks", "main")
                path = view.role_tasks_file(role, tasks_from, search)
                file = ""
            else:
                role, path = role_of(task), str(included_file._filename)
                file = _included_file(task, path)
            count = view.count_tasks(path) if path else 0
            self.run.include(role, count, key=task._uuid, blocks=blocks_of(task), file=file)

    def prompt(self, up):
        """A prompt took the screen (up) or was answered: the person's time; and the bell, when they have had nothing
        to answer for a while."""
        now = self._now()
        with self.display._lock:
            self.run.prompt(up, now)
        if up:
            if now - self._attended >= QUIET_SECONDS:
                self._bell(now)
            self.out.title(f"{self.run.title} · waiting for you")
        else:
            self._attended = now

    # --- results -----------------------------------------------------------------------------------------------
    def runner_start(self, host, task):
        with self.display._lock:
            self.run.host_start(task._uuid, host.get_name(), self._now())

    def _result(self, result, outcome):
        with self.display._lock:
            self.run.result(result.task._uuid, result.host.get_name(), outcome, self._now())
            self._after()

    def ok(self, result):
        """A task that ended well. False when the callback should render nothing more of it: a browser block."""
        self._result(result, "changed" if result.result.get("changed") else "ok")
        host = result.host.get_name()
        facts = result.result.get("ansible_facts") or {}
        # The VM's ID and address as proxmox_vm sets them, and only then: a task on the VM could set any fact.
        vm_facts = facts if role_of(result.task) == "proxmox_vm" else {}
        with self.display._lock:
            for name in VM_FACTS:
                if name in vm_facts:
                    value = vm_facts[name]
                    self.run.fact(host, name, plain_text(str(value)) if name == "proxmox_vm_address" else value)
            # The declared outcomes' facts: only whether each holds is kept.
            for name in self._facts & set(facts):
                self.run.outcome(host, name, facts[name])
            # A stage's info: the VMs a fact names, each held to a VM name's pattern.
            for stage, fact in self._infos.items():
                if fact in facts:
                    self.run.info(stage, self._names(facts[fact]))
            # A stage's checks, as its playbook records each: only whether it passed, and its text as plain text.
            for stage, fact in self._checklists.items():
                checks = facts.get(fact)
                if isinstance(checks, list):
                    items = [
                        (bool(check.get("ok")), "skipped" in check, plain_text(str(check.get("text", ""))))
                        for check in checks
                        if isinstance(check, dict)
                    ]
                    self.run.checklist(stage, items)
            self._discovered(result)
        if result.task.action in STATS_ACTIONS:
            self._records(result)
        # A browser block, drawn by the callback, to the screen: where the route would send it to the log alone.
        with self.say():
            if self.cb._banner_or_default(result):
                return False
        # A looped report's words are its items'; its own msg is the loop's "All items completed".
        if result.task.action in DEBUG_ACTIONS and "results" not in result.result:
            self._report(result)
        return True

    def item_ok(self, result):
        if result.task.action in DEBUG_ACTIONS:
            self._report(result)

    @staticmethod
    def _names(value):
        """The VM names a fact holds: a list of names, or of mappings with one; each a plain name, or left out."""
        names = []
        for item in value if isinstance(value, list) else []:
            name = _vm_name(item.get("name", "") if isinstance(item, dict) else item)
            if name and name not in names:
                names.append(name)
        return names

    def _discovered(self, result):
        """A VM's address and ID as Proxmox and its guest agent gave them, from a file the declaration trusts - the
        add_host of discovery, the claude_vm_addressed of vm_addresses.yml - and only these two: an address that
        parses as one, an ID that is a number."""
        if os.path.basename(_file_of(result.task)) not in self.run.recipe.addresses_from:
            return
        found = []
        if result.task.action in ADD_HOST:
            for item in [result.result, *(result.result.get("results") or [])]:
                added = item.get("add_host") if isinstance(item, dict) else None
                if isinstance(added, dict):
                    hostvars = added.get("host_vars") or {}
                    found.append((added.get("host_name"), hostvars.get("ansible_host"), hostvars.get("proxmox_vm_id")))
        addressed = (result.result.get("ansible_facts") or {}).get("claude_vm_addressed")
        for entry in addressed if isinstance(addressed, list) else []:
            if isinstance(entry, dict):
                found.append((entry.get("name"), entry.get("address"), entry.get("vmid")))
        for name, address, vmid in found:
            name = _vm_name(name or "")
            if not name:
                continue
            try:
                address = str(ipaddress.IPv4Address(str(address or "").strip()))
            except ValueError:
                address = ""
            if address:
                self.run.fact(name, "proxmox_vm_address", address)
            self.run.fact(name, "proxmox_vm_resolved_id", str(vmid or "").strip())

    def _records(self, result):
        """What a report recorded for NEXT STEPS, as it goes: a report on a matter a later report recorded again is
        not repeated in the summary."""
        data = (result.result.get("ansible_stats") or {}).get("data") or {}
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            for stat in steps.NEXT_STATS:
                for vm, matters in (data.get(stat) or {}).items() if isinstance(data.get(stat), dict) else ():
                    for matter, line in matters.items() if isinstance(matters, dict) else ():
                        if isinstance(line, str):
                            self.run.record(str(vm), str(matter), plain_text(line), task=task)

    def _report(self, result):
        """A debug message, a report: whole above the region, each of its lines kept as a line and wrapped under
        its own indent, and a line of the summary, under its VM."""
        msg = result.result.get("msg")
        if msg in (None, ""):
            return
        host = result.host.get_name()
        parts = msg if isinstance(msg, list) else str(msg).splitlines()
        # Each line as it reads, indent and all: no escape, control or format character - a bidi override - is left.
        lines = [text.visible_text(str(part)).rstrip().expandtabs(4) for part in parts]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        plan = self.plan_play is not None and self.run.play == self.plan_play
        kept = [line for line in lines if line.strip()]
        marks = self.run.recipe.marks
        styles = [marks.get((line.split() or [""])[0], "") for line in lines]
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            note = " ".join(view.one_line(line) for line in kept)
            # A stage's checks, reported line by line, are its checklist's, which the summary says whole.
            listed = task is not None and task.stage in self.run.checklists and any(styles)
            if not listed:
                self.run.note(host, note, plan=plan, task=task, lines=len(kept))
            said = self.run.report(host, lines, styles, plan)
        self.print(drawn.report_lines(said, commands.terminal_width(), self.color))

    def skipped(self, result):
        self._result(result, "skipped")

    def failed(self, result, ignore_errors=False):
        outcome = "ignored" if ignore_errors else "rescued" if rescued(result.task, self.cb._play) else "failed"
        declined = self.run.recipe.declines.get(drawn_name(result.task)) if outcome == "failed" else None
        if declined is not None:
            # A person's answer, not a failure: said as itself, once.
            self._result(result, "declined")
            with self.display._lock:
                self.run.decline(declined)
            self.print([("", ""), (declined, declined)])
            return
        causes = self._causes(result) if outcome == "failed" else []
        before = self.run.first_failure
        # A failure that only raises again what rescues took is the causes' steps', not its own task's.
        self._result(result, "raised" if causes else outcome)
        if outcome == "rescued":
            self._remember(result)
        elif causes:
            self._raised(result, causes, first=not before)
        elif outcome == "failed":
            self._failure(result, "failed")

    def _remember(self, result, unreachable=False):
        """A failure a rescue took, or an unreachable result a task ignored, kept in the model for the task that may
        raise it again later."""
        said = self._said(result.result)
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
            self.run.rescue(
                result.host.get_name(),
                {
                    "name": drawn_name(result.task),
                    "where": where,
                    "stage": task.stage if task is not None else None,
                    "step": task.step if task is not None else None,
                    "blocks": set(blocks_of(result.task)),
                    "said": said,
                    "unreachable": unreachable,
                    "host": result.host.get_name(),
                },
            )

    def _causes(self, result):
        """The failures a declared raise repeats: those rescued on its host inside the blocks it names, and the
        unreachable results its tasks ignored, where it says so."""
        declared = self.run.recipe.raises.get(drawn_name(result.task))
        if declared is None:
            return []
        if declared.checklist:
            # The checks of the raise's stage that did not pass, each a failure of that stage's, on this machine.
            with self.display._lock:
                task = self.run.find(result.task._uuid)
                stage = task.stage if task is not None else ""
                failed = self.run.checks_failed(stage)
            host = result.host.get_name()
            return [
                {"name": text, "where": stage, "stage": stage, "step": None, "said": [], "host": host}
                for text in failed
            ]
        hosts = list(self.run.rescued) if declared.hosts == "any" else [result.host.get_name()]
        return [
            kept
            for host in hosts
            for kept in self.run.rescued.get(host, [])
            if kept["blocks"] & declared.blocks or (declared.unreachable and kept.get("unreachable"))
        ]

    def item_failed(self, result):
        with self.display._lock:
            self._failed_items.setdefault((result.task._uuid, result.host.get_name()), []).append(result.result)

    def unreachable(self, result):
        ignored = bool(getattr(result.task, "ignore_unreachable", False))
        # An unreachable VM a task ignores (the sign-ins' ping) is lost to the run, and a cause a later task may
        # raise: the VM is marked so, the run goes on.
        self._result(result, "lost" if ignored else "unreachable")
        if ignored:
            self._remember(result, unreachable=True)
        else:
            self._failure(result, "could not be reached")

    def _failure(self, result, how):
        """A failed task, for good above the region: the step, the VM, the task, what it said, and where the log has
        it."""
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
            seconds = task.seconds(self._now()) if task is not None else None
            name = task.name if task is not None else drawn_name(result.task)
        said = self._said(result.result)
        # A loop's own msg is "One or more items failed": what each failed item said is the failure.
        for item in self._failed_items.pop((result.task._uuid, result.host.get_name()), []):
            said.append(f"{view.one_line(self.cb._get_item_label(item))}:")
            said += [f"  {line}" for line in self._said(item)]
        host = result.host.get_name()
        with self.display._lock:
            failure = self.run.failure(where, host, self._who(host), name, seconds, how, said, self.run.log)
        self.print(drawn.failure_lines(failure, commands.terminal_width(), self.color))

    def _raised(self, result, causes, first):
        """A failure that raises again what rescues took earlier: drawn under each cause, then its own words, but
        those that only repeat a cause's."""
        repeated = set()
        for cause in causes:
            repeated |= {line.strip() for line in cause["said"]}
            with self.display._lock:
                self.run.raised(cause["stage"], cause["step"], cause["host"])
                self.run.blame(cause["host"])
        own = [line for line in self._said(result.result) if line.strip() and line.strip() not in repeated]
        with self.display._lock:
            said = [{key: cause[key] for key in ("where", "host", "name", "said")} for cause in causes]
            said = [cause | {"who": self._who(cause["host"])} for cause in said]
            if first:
                self.run.first_failure = causes[0]["where"]
            failure = self.run.raised_again(said, own, self.run.log)
        self.print(drawn.failure_lines(failure, commands.terminal_width(), self.color))

    @staticmethod
    def _said(res):
        """What a failed result says: its message, then the last lines of its output - a module's own, should it
        have crashed."""
        said = []
        for key in ("msg", "reason"):
            if res.get(key):
                said += plain_text(str(res[key])).splitlines()
        for key in ("stderr_lines", "module_stderr", "stdout_lines", "module_stdout"):
            value = res.get(key)
            if value:
                lines = value if isinstance(value, list) else str(value).splitlines()
                said += [plain_text(str(line)) for line in lines][-view.FAILURE_LINES :]
                break
        return said

    def retry(self, result):
        """A retried task. False when the callback should render nothing more of it: a wait for the person, said
        once; a wait for a machine is said on its line instead, its retries in the log alone."""
        now = self._now()
        left, delay = retry_left(result)
        if WAITING_TAG not in result.task.tags:
            with self.display._lock:
                # Ansible says the next attempt and how many there are, the first run among them.
                attempt, total = result.result.get("attempts", 0), result.result.get("retries", 0)
                self.run.tried(result.task._uuid, now, attempt, total, delay)
            self._after()
            return True
        with self.display._lock:
            first = self.run.retry(result.task._uuid, result.host.get_name(), now, left * delay)
        if first:
            self._bell(now)
        if self.mode == "lines":
            with self.say():
                self.cb._retry_once(result)
        else:
            self.cb._retry_once(result)
        self._after()
        return False

    # --- the end -----------------------------------------------------------------------------------------------
    def stats(self, stats, recap):
        """The end: the region gone, PLAY RECAP (recap) to the log alone, the summary and NEXT STEPS on the screen -
        or, for a part of a run in parts, its state saved for the next part, whose end says them."""
        now = self._now()
        failed = sorted(set(stats.failures) | set(stats.dark))
        if self.state_path:
            if self.mode == "lines":
                self._after(final=True)
            self.out.finish()
            recap(stats)
            records = commands.merged_records(self.records, self.cb.next_records())
            commands.save_state(self.state_path, self.run.dump(now, records, [*self.failed_hosts, *failed]))
            self.saved = True
            return
        with self.display._lock:
            self.run.end(now, failed=bool(failed))
            self.out.title(self.run.title_text(now))
        if self.mode == "lines":
            self._after()
        self.out.finish()
        recap(stats)
        commands.say_end(self.out, self.run, now, failed, self.cb.next_steps_of())
        self.out.bell()
