"""A playbook run drawn live: Ansible's events turned into the region, the words above it, and the summary.

The stdout callback (callback_plugins/claude_on_proxmox.py) hands every event to an Engine when a recipe has exported
CLAUDE_ON_PROXMOX_LIVE, its title, and keeps only its classic output for itself. Everything a live run does is here:

- live, at a terminal that can draw (filter_plugins/live_region.py): the run's model (filter_plugins/run_view.py)
  drawn in place at the bottom of the screen, with the reports, the blocks, the diffs, warnings and failures printed
  above it for good;
- lines, any other output: a line as each step ends, and the same reports, blocks and failures.

Either way the default callback still renders every task, sent to the run's log alone (display's log_only), so the
log is what it would be without the engine; the run's own words go to the screen in colour and to the log without;
and a summary replaces PLAY RECAP on the screen, which the log keeps, followed by NEXT STEPS. What the run is made
of - its stages and steps, the failures it raises again - is its recipe's declaration
(filter_plugins/live_recipes.py); nothing here names a command (docs/architecture/custom-code.md §7.5, §7.8).

Loaded by path as claude_on_proxmox_live_ansible, as every module here loads another; it has no filters of its own.
"""

from __future__ import annotations

import atexit
import importlib.util
import ipaddress
import json
import os
import re
import sys
import time
from pathlib import Path

from ansible import constants as C
from ansible import context
from ansible.utils import display as ansible_display


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem> (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{stem}.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


text = _sibling("text")
plain_text = text.plain_text
screen = _sibling("screen")
view = _sibling("run_view")
live = _sibling("live_region")

LIVE_ENV = "CLAUDE_ON_PROXMOX_LIVE"
WAITING_TAG = "attention_browser"
DEBUG_ACTIONS = frozenset({"debug", "ansible.builtin.debug", "ansible.legacy.debug"})
STATS_ACTIONS = frozenset({"set_stats", "ansible.builtin.set_stats", "ansible.legacy.set_stats"})
INCLUDE_TASKS = frozenset({"include_tasks", "ansible.builtin.include_tasks", "ansible.legacy.include_tasks"})
INCLUDE_ROLE = frozenset({"include_role", "ansible.builtin.include_role", "ansible.legacy.include_role"})
ARGUMENT_CHECKS = frozenset({"validate_argument_spec", "ansible.builtin.validate_argument_spec"})
ADD_HOST = frozenset({"add_host", "ansible.builtin.add_host", "ansible.legacy.add_host"})
# The facts the provision stage says a VM by.
VM_FACTS = ("proxmox_vm_resolved_id", "proxmox_vm_address", "proxmox_vm_exists")
FAILURE_LINES = 20
# A prompt rings the bell when the person has had nothing to answer for this long: they may have looked away.
QUIET_SECONDS = 10.0
# A task name's file prefix, as an included file's tasks are named: "session_read | Read the sessions".
FILE_PREFIX = re.compile(r"^[a-z0-9_]+ \| ")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def from_environment(callback, playbook):
    """The engine for this run, or None for the stream: CLAUDE_ON_PROXMOX_LIVE set, verbosity 0, and a log to send
    the stream to (LOG_DIR= turns it off). The variable is taken out of the environment either way, so nothing the
    run starts - VS Code, whose terminals a later just destroy may run in - inherits it."""
    title = os.environ.pop(LIVE_ENV, "").strip()
    if not title or callback._display.verbosity != 0 or ansible_display.logger is None:
        return None
    engine = Engine(callback, title)
    engine.start(playbook)
    return engine


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
        name = plain_text(name).strip()
        if NAME.match(name) and name not in out:
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
                    out.append((item._uuid, role_of(item), _include_meta(item, search)))
    return out


def _include_meta(task, search):
    """For an include whose file a playbook names literally: the count of the tasks it will announce."""
    args = getattr(task, "args", None) or {}
    try:
        if task.action in INCLUDE_TASKS:
            target = args.get("file") or args.get("_raw_params")
            if not isinstance(target, str) or "{{" in target:
                return {}
            base = os.path.dirname(str(task.get_path()).split(":", 1)[0])
            path = target if os.path.isabs(target) else os.path.join(base, target)
            return {"include": view.count_tasks(path)} if os.path.isfile(path) else {}
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


def play_batches(play):
    """How many batches a play runs in: one, unless it is serial."""
    try:
        # Each batch starts with the inventory restricted to it: the play's hosts are all of them.
        inventory = play._variable_manager._inventory
        hosts = len(inventory.get_hosts(play.hosts, order=play.order, ignore_restrictions=True))
        return view.batches(hosts, play.serial)
    except Exception:  # noqa: BLE001 - a count is a nicety
        return 1


class Engine:
    """One live run: the callback's events in; the region, the run's own words and the summary out."""

    def __init__(self, callback, title):
        self.cb = callback
        self.display = callback._display
        self.mode = "live" if live.can_draw() else "lines"
        self.run = None
        self.out = live.Output(
            self.display,
            frame=self._frame,
            live=self.mode == "live",
            color=not C.ANSIBLE_NOCOLOR,
            to_log=True,
            on_prompt=self.prompt,
        )
        self.color = self.out.color
        self.region = self.out.region
        self.plan_play = None
        self.title = title
        self._failed_items = {}  # (task ID, host): the results of a loop's items that failed, said with the task
        self._rescued = {}  # host: what each failure a rescue took said, for a later raise of it
        self._attended = self._now()  # when the person last had something to answer: the start, a bell, a prompt
        self._facts = set()  # the facts the declaration reads each VM's outcome from

    # --- what the run says, and where it goes ------------------------------------------------------------------
    # The run's own words and anything on stderr - warnings, deprecations, errors - stay on the screen; the rest,
    # the default callback's every task and profile_tasks' timings, goes to the log alone (live_region.Output).
    def say(self):
        return self.out.say()

    def print(self, lines):
        """Lines of the run's own, (coloured, plain) each: the colour to the screen, the plain text to the log."""
        self.out.print(lines)

    def _width(self):
        try:
            return os.get_terminal_size(sys.stdout.fileno()).columns
        except (OSError, ValueError, AttributeError):
            return 100

    def _now(self):
        return time.monotonic()

    def _wrapped(self, head, lines, style=""):
        """head, then lines wrapped under where head ends, whole - never cut: (coloured, plain) pairs for print. A
        line keeps its own indent, its wrapped pieces under where its words start."""
        column = screen.width(head)
        room = max(20, self._width() - 1 - column)
        out = []
        first = True
        for line in lines:
            indent = len(line) - len(line.lstrip(" "))
            pieces = screen.fill(line.strip(), max(10, room - indent)) if line.strip() else [""]
            for piece in pieces:
                lead = head if first else [(" " * column, "")]
                first = False
                runs = [*lead, (" " * indent + piece, style)]
                out.append((screen.render(runs, self.color), screen.render(runs, False)))
        return out

    def _frame(self, cols, rows, glyph):
        with self.display._lock:
            if self.run is None:
                return []
            now = self._now()
            # A tagged wait that never retries, GitHub's device flow, is known by how long it runs.
            if self.run.settle(now):
                self._bell(now)
            self.out.title(self.run.title_text(now))
            return self.run.frame(now, cols, rows, glyph, self.color)

    def _bell(self, now):
        self._attended = now
        if self.mode == "live":
            self.out.bell()

    def _after(self):
        """After each event: in lines mode, a line for each step that ended; live, a redraw now."""
        if self.mode == "lines":
            ended = self.run.ended_lines(self._now())
            if ended:
                self.print([(line, line) for line in ended])
        else:
            self.out.redraw()

    def _log_path(self):
        path = os.environ.get("ANSIBLE_LOG_PATH", "")
        if not path:
            return ""
        try:
            return os.path.relpath(path) if not os.path.relpath(path).startswith("..") else path
        except ValueError:
            return path

    def _who(self, host):
        """Whom a line is about: a VM, or for this machine the run's one VM, else this machine."""
        if host != "localhost":
            return host
        return self.run.names[0] if len(self.run.names) == 1 else "this machine"

    @staticmethod
    def _name(task):
        """A task's name as drawn: without its role's prefix, which its step says, nor its file's."""
        name = task.get_name().strip()
        role = role_of(task)
        if role and name.startswith(role + " : "):
            name = name[len(role) + 3 :]
        return FILE_PREFIX.sub("", name)

    # --- the playbook ------------------------------------------------------------------------------------------
    def start(self, playbook):
        cli = context.CLIARGS or {}
        self.run = view.Run(
            self.title,
            self._now(),
            check=bool(cli.get("check")),
            log=self._log_path(),
            vms=vm_names(cli.get("extra_vars")),
        )
        search = [os.path.join(os.getcwd(), "roles"), *C.DEFAULT_ROLES_PATH]
        plays = []
        for play in playbook.get_plays():
            try:
                tasks = tasks_of(play.compile(), [], play, search)
            except Exception:  # noqa: BLE001 - a count is a nicety: a play that will not compile here still runs
                tasks = []
            plays.append((play._uuid, play.get_path(), tasks, play.get_name()))
        self.run.plays(plays)
        self._facts = {o.fact for stage in self.run.recipe.stages for step in stage.steps for o in step.outcomes}
        first = playbook.get_plays()[:1]
        if first and Path(str(first[0].get_path()).split(":", 1)[0]).name in self.run.recipe.plan:
            self.plan_play = first[0]._uuid
        self.out.start()
        self.out.redraw()
        self.out.spin()
        if self.mode == "live":
            # Registered after the region's own, so it runs first: a run that ends without its stats, at Ctrl-C,
            # does not leave the tab saying it is still at work.
            atexit.register(self._stopped_title)

    def _stopped_title(self):
        if self.run.finished is None:
            self.out.title(f"{self.run.title} · stopped")

    def play_start(self, play):
        batches = play_batches(play) if play._uuid not in self.run.play_batches else 1
        with self.display._lock:
            self.run.play_start(play._uuid, play.get_name(), play.get_path(), batches=batches)

    def task_start(self, task):
        with self.display._lock:
            waits = WAITING_TAG in task.tags and task.action not in DEBUG_ACTIONS
            hidden = bool(getattr(task, "implicit", False)) or task.action in ARGUMENT_CHECKS
            self.run.task(task._uuid, self._name(task), role_of(task), self._now(), waits=waits, hidden=hidden)
            self._after()

    def include(self, included_file):
        with self.display._lock:
            task = included_file._task
            if getattr(included_file, "_is_role", False):
                role = getattr(task, "_role_name", None) or str(included_file._filename)
                search = [os.path.join(os.getcwd(), "roles"), *C.DEFAULT_ROLES_PATH]
                tasks_from = (getattr(task, "_from_files", None) or {}).get("tasks", "main")
                path = view.role_tasks_file(role, tasks_from, search)
            else:
                role, path = role_of(task), str(included_file._filename)
            self.run.include(role, view.count_tasks(path) if path else 0, key=task._uuid)

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
        # The VM's ID and address as proxmox_vm sets them, and only then: a task on the VM could set any fact.
        facts = (result.result.get("ansible_facts") or {}) if role_of(result.task) == "proxmox_vm" else {}
        with self.display._lock:
            for name in VM_FACTS:
                if name in facts:
                    value = facts[name]
                    self.run.fact(host, name, plain_text(str(value)) if name == "proxmox_vm_address" else value)
            # The declared outcomes' facts: only whether each holds is kept.
            for name in self._facts & set(result.result.get("ansible_facts") or {}):
                self.run.outcome(host, name, result.result["ansible_facts"][name])
            self._discovered(result)
        if result.task.action in STATS_ACTIONS:
            self._records(result)
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            with self.say():
                self.cb._banner_or_default(result)
            return False
        # A looped report's words are its items'; its own msg is the loop's "All items completed".
        if result.task.action in DEBUG_ACTIONS and "results" not in result.result:
            self._report(result)
        return True

    def item_ok(self, result):
        if result.task.action in DEBUG_ACTIONS:
            self._report(result)

    def _discovered(self, result):
        """A VM's address and ID as discovery read them from Proxmox and its guest agent, from the add_host of a
        playbook the declaration trusts, and only these two: an address that parses as one, an ID that is a number."""
        if result.task.action not in ADD_HOST:
            return
        if os.path.basename(str(result.task.get_path()).split(":", 1)[0]) not in self.run.recipe.addresses_from:
            return
        found = [result.result, *(result.result.get("results") or [])]
        for item in found:
            added = item.get("add_host") if isinstance(item, dict) else None
            if not isinstance(added, dict):
                continue
            name = plain_text(str(added.get("host_name", ""))).strip()
            hostvars = added.get("host_vars") or {}
            if not NAME.match(name):
                continue
            try:
                address = str(ipaddress.IPv4Address(str(hostvars.get("ansible_host", "")).strip()))
            except ValueError:
                address = ""
            if address:
                self.run.fact(name, "proxmox_vm_address", address)
            self.run.fact(name, "proxmox_vm_resolved_id", str(hostvars.get("proxmox_vm_id", "")).strip())

    def _records(self, result):
        """What a report recorded for NEXT STEPS, as it goes: a report on a matter a later report recorded again is
        not repeated in the summary."""
        data = (result.result.get("ansible_stats") or {}).get("data") or {}
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            for stat in self.cb.NEXT_STATS:
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
        with self.display._lock:
            note = " ".join(view.one_line(line) for line in kept)
            self.run.note(host, note, plan=plan, task=self.run.find(result.task._uuid), lines=len(kept))
        # A play on this machine names the VM in its words; anywhere else the VM leads, once.
        if lines[0].startswith(host + ": "):
            lines[0] = lines[0][len(host) + 2 :]
        who = [] if host == "localhost" or lines[0].startswith(host + " ") else [(host, "b"), ("  ", "")]
        self.print(self._wrapped([("  ", ""), *who], lines))

    def skipped(self, result):
        self._result(result, "skipped")

    def failed(self, result, ignore_errors=False):
        outcome = "ignored" if ignore_errors else "rescued" if rescued(result.task, self.cb._play) else "failed"
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
        """A failure a rescue took, or an unreachable result a task ignored, kept for the task that may raise it again
        later."""
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
        self._rescued.setdefault(result.host.get_name(), []).append(
            {
                "name": self._name(result.task),
                "where": where,
                "stage": task.stage if task is not None else None,
                "step": task.step if task is not None else None,
                "blocks": set(blocks_of(result.task)),
                "said": self._said(result.result),
                "unreachable": unreachable,
            }
        )

    def _causes(self, result):
        """The failures a declared raise repeats: those rescued on its host inside the blocks it names, and the
        unreachable results its tasks ignored, where it says so."""
        declared = self.run.recipe.raises.get(self._name(result.task))
        if declared is None:
            return []
        return [
            kept
            for kept in self._rescued.get(result.host.get_name(), [])
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

    def _block(self, where, who, name, took, how, said):
        """A failure's lines: where and whom, the task, and what it said."""
        out = [("", "")]
        out += self._wrapped([("✗ ", "r")], [f"{where} · {who}: {name}{took}{how}"], "r")
        out += self._wrapped([("    ", "")], [line for line in said if line.strip()][: FAILURE_LINES * 2])
        return out

    def _failure(self, result, how):
        """A failed task, for good above the region: the step, the VM, the task, what it said, and where the log has
        it."""
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
            took = f" ({screen.duration(task.seconds(self._now()))})" if task is not None else ""
            name = task.name if task is not None else self._name(result.task)
        said = self._said(result.result)
        # A loop's own msg is "One or more items failed": what each failed item said is the failure.
        for item in self._failed_items.pop((result.task._uuid, result.host.get_name()), []):
            said.append(f"{view.one_line(self.cb._get_item_label(item))}:")
            said += [f"  {line}" for line in self._said(item)]
        said_how = "" if how == "failed" else f" · {how}"
        out = self._block(where, self._who(result.host.get_name()), name, took, said_how, said)
        if self.run.log:
            out += self._wrapped([("    ", "")], [f"the whole output: {self.run.log}"], "d")
        self.print([*out, ("", "")])

    def _raised(self, result, causes, first):
        """A failure that raises again what rescues took earlier: drawn under each cause, then its own words, but
        those that only repeat a cause's."""
        who = self._who(result.host.get_name())
        out = []
        repeated = set()
        for cause in causes:
            out += self._block(cause["where"], who, cause["name"], "", "", cause["said"])
            repeated |= {line.strip() for line in cause["said"]}
            with self.display._lock:
                self.run.raised(cause["stage"], cause["step"], result.host.get_name())
        own = [line for line in self._said(result.result) if line.strip() and line.strip() not in repeated]
        if own:
            out += self._wrapped([("    ", "")], own)
        if self.run.log:
            out += self._wrapped([("    ", "")], [f"the whole output: {self.run.log}"], "d")
        if first:
            with self.display._lock:
                self.run.first_failure = causes[0]["where"]
        self.print([*out, ("", "")])

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
                said += [plain_text(str(line)) for line in lines][-FAILURE_LINES:]
                break
        return said

    def retry(self, result):
        """A retried task. False when the callback should render nothing more of it: a wait for the person, said
        once; a wait for a machine is said on its line instead, its retries in the log alone."""
        now = self._now()
        try:
            delay = int(result.task.delay)
        except (TypeError, ValueError):
            delay = 0
        if WAITING_TAG not in result.task.tags:
            with self.display._lock:
                # Ansible says the next attempt and how many there are, the first run among them.
                attempt, total = result.result.get("attempts", 0), result.result.get("retries", 0)
                self.run.tried(result.task._uuid, now, attempt, total, delay)
            self._after()
            return True
        left = result.result.get("retries", 0) - result.result.get("attempts", 0) + 1
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
        """The end: the region gone, PLAY RECAP (recap) to the log alone, the summary and NEXT STEPS on the screen."""
        now = self._now()
        failed = sorted(set(stats.failures) | set(stats.dark))
        with self.display._lock:
            self.run.end(now, failed=bool(failed))
            if self.mode == "live":
                self.out.title(self.run.title_text(now))
        if self.mode == "lines":
            self._after()
        self.out.finish()
        recap(stats)
        lines = self.cb.next_steps_of(stats)
        width = self._width()
        summary = self.run.summary(now, width, self.color, failed_hosts=failed, next_steps=lines)
        plain = self.run.summary(now, width, False, failed_hosts=failed, next_steps=lines)
        self.print([("", ""), *zip(summary, plain, strict=True)])
        if lines:
            # Each line whole, as the stream prints it: a command or a URL in one is copied from it, and the terminal
            # wraps it, never a word of the run's.
            with self.say():
                self.display.display(text.banner(lines, "NEXT STEPS"), color=self.cb.COLOR)
        if self.mode == "live":
            self.out.bell()


class FilterModule:
    """No filters: the stdout callback loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
