"""A playbook run drawn live: its stages, steps and tasks, and what is drawn from them - every drawn recipe's.

Loaded by path by filter_plugins/live_ansible.py, the engine the stdout callback hands a drawn run to, which feeds a
Run the playbook's plays and Ansible's events; everything here is pure, with the time passed in, so every frame is a
unit test (tests/unit/test_run_view.py). Lines are drawn with filter_plugins/screen.py and put on the screen by
filter_plugins/live_region.py, as `just status`'s report is (docs/architecture/custom-code.md §7.8);
docs/guides/runs.md shows what a person sees, and §7.5 how this works.

What a run is made of is its recipe's declaration in filter_plugins/live_recipes.py, which the title's first word
names; nothing here names a command. A play's stage is the first stage that names it - its file and play name, else
its file; a play no stage names (discover.yml, which configure and each sign-in import, deploy.yml's first) takes the
stage of the play after it, else of the one before. Within a stage, a task's step is the declared step its play's
file names, else its role; tasks outside any role are the stage's own. A quiet stage - a last play that only raises
again what failed earlier - is counted and never drawn, unless one of its tasks failed. A playbook in the
declaration's plan says what the run will do in its first play's report: printed first, and kept out of the summary.

How far a step is: the tasks it has reached, skipped ones included, of the tasks it will reach - those the plays
compile to (counted when the playbook starts, by task ID), the files of the includes among them whose file is
literal (counted then too, and used up when each include announces itself, never counted twice), those other
includes announce, and any it reaches beyond them (a handler). A serial play runs every task again for each batch:
its counts are multiplied by its batches once it starts. A step that has ended is whole: what it did not reach, a
rescue that never ran, was never left to do. No bar and no percentage ever falls.

What is drawn: the live frame (the stages, the running stage's steps, the running step's last tasks with a timer on
the one running, and a footer; a run of one stage drawn as its steps), a line as each step ends for output that is
not a terminal, and the summary at the end. Nothing here prints; the engine does.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path

import yaml


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem> (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{stem}.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


screen = _sibling("screen")
recipes = _sibling("live_recipes")
_one_line = _sibling("text").one_line


def stage_files(recipe):
    """Each playbook file a recipe's declaration names, as (stage, step): a step named here is the declared step's,
    for all its plays' tasks. A file:play name entry is left out: it names one play, not the file."""
    table = {}
    for stage in recipe.stages:
        for step in stage.steps:
            for play in step.plays:
                table[play] = (stage.key, step.label)
        for play in stage.plays:
            if ":" not in play:
                table.setdefault(play, (stage.key, None))
    return table


def _play_table(recipe):
    """Each file:play name entry a recipe's declaration names, as (stage, None)."""
    return {play: (stage.key, None) for stage in recipe.stages for play in stage.plays if ":" in play}


# just deploy's, which is also what a run of no declaration of its own is drawn by.
STAGE_FILES = stage_files(recipes.RECIPES["deploy"])
PLAN_FILES = recipes.RECIPES["deploy"].plan
RANK = {
    "skipped": 0,
    "included": 1,
    "ok": 2,
    "changed": 3,
    "ignored": 4,
    "rescued": 4,
    "lost": 4,
    "left": 1,
    "declined": 1,
    "raised": 5,
    "stopped": 4,
    "failed": 5,
    "unreachable": 5,
}
# A bar's cells, in a stage's or a step's row.
BAR = 20
# A task tagged as a wait for a person (attention_browser) is one once it has run this long: a skipped one ends first.
WAIT_AFTER = 2.0
# SLOWEST lists only tasks that took this long.
SLOW = 1.0
# The state a run in parts keeps between its parts: a part never takes over one of another version.
STATE_VERSION = 1
# How a VM is marked in a step's row and in the summary.
MARKS = {
    "running": ("", "c"),
    "waiting": ("⏸", "y"),
    "done": ("✓", "g"),
    "skipped": ("–", "d"),
    "failed": ("✗", "r"),
    "unreachable": ("✗", "r"),
    "left": ("–", "d"),
    "to come": ("○", "d"),
}


def stages_for(plays, recipe=None):
    """Each play's (stage, step), from its file and name, (file, play name) each; one the declaration does not name
    takes a neighbour's."""
    recipe = recipe or recipes.RECIPES["deploy"]
    files, named = stage_files(recipe), _play_table(recipe)
    known = []
    for path, name in plays:
        base = os.path.basename(str(path).split(":", 1)[0])
        known.append(named.get(f"{base}:{name}") if name else None)
        if known[-1] is None:
            known[-1] = files.get(base)
    out = []
    for index, entry in enumerate(known):
        if entry is None:
            entry = next((e for e in known[index + 1 :] if e), None) or next(
                (e for e in reversed(known[:index]) if e), ("run", None)
            )
        out.append(entry)
    return out


def stages_of(files, recipe=None):
    """Each play's (stage, step), from the file of its playbook alone."""
    return stages_for([(f, None) for f in files], recipe)


def count_tasks(path, roles=(), depth=0):
    """The tasks a task file runs, as Ansible will start them: a block's children, an import's file's tasks, an
    include as one task (its own file is counted when it is included). 0 for a file that cannot be read."""
    if depth > 8:
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
    except (OSError, yaml.YAMLError):
        return 0
    return _count(data, os.path.dirname(path), roles, depth)


def _count(items, base, roles, depth):
    total = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if "block" in item:
            total += sum(_count(item.get(key) or [], base, roles, depth) for key in ("block", "rescue", "always"))
            continue
        imported = item.get("ansible.builtin.import_tasks") or item.get("import_tasks")
        if isinstance(imported, str) and "{{" not in imported:
            total += count_tasks(os.path.join(base, imported), roles, depth + 1)
            continue
        total += 1
    return total


def role_tasks_file(name, tasks_from, search):
    """Where an included role's tasks are: tasks/<tasks_from>.yml of the first role directory that has them."""
    for directory in search:
        for suffix in (".yml", ".yaml", ""):
            path = os.path.join(directory, name, "tasks", f"{tasks_from or 'main'}{suffix}")
            if os.path.isfile(path):
                return path
    return ""


def batches(hosts, serial):
    """How many batches a serial play runs its hosts in, as Ansible cuts them: serial, a list of sizes (a number or
    a percentage of hosts), the last repeated; none or 0 is one batch of all."""
    if hosts <= 0:
        return 1
    sizes = []
    for value in serial or []:
        text = str(value).strip()
        try:
            size = math.ceil(hosts * float(text[:-1]) / 100) if text.endswith("%") else int(text)
        except ValueError:
            size = 0
        sizes.append(size)
    if not sizes or sizes[0] <= 0:
        return 1
    count, left = 0, hosts
    while left > 0:
        size = sizes[min(count, len(sizes) - 1)]
        if size <= 0:
            return count + 1
        left -= size
        count += 1
    return count


# --- the run ---------------------------------------------------------------------------------------------------
def one_line(text):
    """text as one line of plain text, whole: a note is cut where it is drawn, not where it is kept."""
    return _one_line(text, sys.maxsize)


def first_sentence(text):
    """text up to the end of its first sentence, or whole when it has one."""
    found = re.match(r"(.+?[.!?])(\s+\S)", text)
    return found.group(1) if found else text


class Task:
    def __init__(self, key, name, stage, step, start, role=None, waits=False, hidden=False, ends=False):
        self.key, self.name, self.stage, self.step, self.start = key, name, stage, step, start
        self.ends = ends  # a meta: end_host, whose hosts that send nothing more are left as they are
        self.role = role
        self.waits = waits  # tagged as a wait for a person: one once it has run WAIT_AFTER seconds
        self.hidden = hidden  # counted, never in a window: an include_role's check of its arguments
        self.end = None
        self.hosts = OrderedDict()  # host: [start, end, outcome]
        self.outcome = ""
        self.wait = None  # (since, seconds it may take), while the task waits for a person
        self.tries = None  # (this try, of how many, until when): a retried task that is no wait for a person
        self.batch = 0  # the play, or serial play's batch, it started in
        self.prompted = 0.0  # seconds a prompt of this task had the person's attention
        self.prompting = None  # since when a prompt of this task has been up
        self.detail = ""  # a command's last line, said dim after its name while it runs
        self.version = 0  # how many times its hosts have changed: a step's VMs are worked out again only then

    def host_start(self, host, now):
        if host not in self.hosts:
            self.hosts[host] = [now, None, ""]
            self.version += 1

    def host_end(self, host, outcome, now):
        entry = self.hosts.setdefault(host, [self.start, None, ""])
        entry[1], entry[2] = now, outcome
        self.version += 1
        if RANK.get(outcome, 0) >= RANK.get(self.outcome, -1):
            self.outcome = outcome
        if all(end is not None for _, end, _ in self.hosts.values()):
            self.end = now

    def close(self, now):
        if self.end is None:
            self.end = now

    def seconds(self, now):
        return (self.end if self.end is not None else now) - self.start

    def waited(self, now):
        """The person's time: all of a wait for them, and a prompt's."""
        if self.wait:
            return self.seconds(now)
        return self.prompted + ((now - self.prompting) if self.prompting is not None else 0.0)


class Note:
    """A report: who said it, its words, the task that said it, how many lines it had, and the (VM, matter)s its NEXT
    STEPS record wrote."""

    def __init__(self, host, text, task, lines=1):
        self.host, self.text, self.task, self.lines = host, text, task, lines
        self.matters = set()


class Step:
    """A stage's step - a role, a sign-in, or the stage's own tasks (key None) - and how far it is."""

    def __init__(self, key):
        self.key = key
        self.static = Counter()  # task ID: how many times the plays will start it (a serial play's batches)
        self.seen = Counter()
        self.reserved = Counter()  # include task ID: its literal file's tasks, counted when the playbook started
        self.raised_on = set()  # the hosts a failure a rescue took here was on: raised again later, or declared
        self.budget = 0
        self.included = 0
        self.extra = 0
        self.tasks = []
        self.states = None  # (its tasks as vm_states last saw them, what they said of each VM)

    def expect(self, task_key, times=1):
        self.static[task_key] = times

    def reserve(self, include_key, count):
        """An include whose file is known before it runs: its tasks counted now, used up when it announces them."""
        self.reserved[include_key] += count
        self.included += count

    def announce(self, count, include_key=None):
        if include_key is not None and self.reserved[include_key] >= count > 0:
            self.reserved[include_key] -= count
            self.budget += count
            return
        self.budget += count
        self.included += count

    def reach(self, task):
        if self.seen[task.key] < self.static.get(task.key, 0):
            self.seen[task.key] += 1
        elif self.budget > 0:
            self.budget -= 1
        else:
            self.extra += 1
        self.tasks.append(task)

    @property
    def expected(self):
        return max(sum(self.static.values()) + self.included + self.extra, len(self.tasks))

    @property
    def reached(self):
        return len(self.tasks)

    def start(self):
        return self.tasks[0].start if self.tasks else None

    def end(self, now):
        return max((task.end if task.end is not None else now) for task in self.tasks) if self.tasks else None

    def seconds(self, now):
        # Its own tasks' time, not first to last: a role's handler runs at the end of the play, after the others.
        return sum(task.seconds(now) for task in self.tasks)

    def changed(self):
        return sum(task.outcome == "changed" for task in self.tasks)

    def failed(self):
        return sum(task.outcome in ("failed", "unreachable") for task in self.tasks) + len(self.raised_on)

    def waited(self, now):
        return sum(task.waited(now) for task in self.tasks)

    def skipped(self):
        """Every task it reached was skipped: a step that did nothing reads so, never done."""
        return bool(self.tasks) and all(task.outcome == "skipped" for task in self.tasks)

    def vm_states(self, now, vms, facts=None, spec=None):
        """Each VM's state in the step, of those given, in their order: running, waiting, done, skipped, failed or
        unreachable - what the declared step's outcome facts say of it winning over what its tasks' results say. A
        frame asks it of every step, so what its tasks say of each VM is kept until one of them changes, and is
        worked out in one pass over them."""
        asked = [(task, task.version, task.end, task.wait) for task in self.tasks]
        if self.states is None or self.states[0] != asked:
            self.states = (asked, self._seen())
        seen = self.states[1]
        states = OrderedDict()
        for vm in vms:
            if vm not in seen:
                continue
            said = (facts or {}).get(vm, {})
            declared = next((o.state for o in (spec.outcomes if spec else ()) if said.get(o.fact)), None)
            running, waiting, outcomes = seen[vm]
            if running:
                states[vm] = "waiting" if waiting else "running"
            elif any(outcome in ("unreachable", "lost") for outcome in outcomes):
                states[vm] = "unreachable"
            elif any(outcome == "failed" for outcome in outcomes) or vm in self.raised_on:
                states[vm] = "failed"
            elif declared is not None:
                states[vm] = declared
            elif any(outcome == "left" for outcome in outcomes):
                states[vm] = "left"
            elif outcomes and all(outcome == "skipped" for outcome in outcomes):
                states[vm] = "skipped"
            else:
                states[vm] = "done"
        return states

    def _seen(self):
        """What its tasks say of each VM: whether it still runs in one, whether one it is in waits for the person,
        and the outcomes it ended with."""
        seen = {}
        for task in self.tasks:
            waits = bool(task.wait) and task.end is None
            for vm, (_, end, outcome) in task.hosts.items():
                entry = seen.setdefault(vm, [False, False, []])
                entry[1] = entry[1] or waits
                if end is None:
                    entry[0] = True
                else:
                    entry[2].append(outcome)
        return seen


class Stage:
    def __init__(self, key):
        self.key = key
        self.steps = OrderedDict()

    def step(self, key):
        if key not in self.steps:
            self.steps[key] = Step(key)
        return self.steps[key]

    @property
    def tasks(self):
        return sorted((task for step in self.steps.values() for task in step.tasks), key=lambda task: task.start)

    @property
    def expected(self):
        return sum(step.expected for step in self.steps.values())

    @property
    def reached(self):
        return sum(step.reached for step in self.steps.values())

    def start(self):
        starts = [step.start() for step in self.steps.values() if step.tasks]
        return min(starts) if starts else None

    def end(self, now):
        ends = [step.end(now) for step in self.steps.values() if step.tasks]
        return max(ends) if ends else None

    def seconds(self, now):
        return (self.end(now) - self.start()) if self.start() is not None else 0.0

    def changed(self):
        return sum(step.changed() for step in self.steps.values())

    def failed(self):
        return sum(step.failed() for step in self.steps.values())

    def waited(self, now):
        return sum(step.waited(now) for step in self.steps.values())

    def skipped(self):
        # Its steps' tasks as they are: what is asked of them here needs no order.
        return bool(self.reached) and all(step.skipped() for step in self.steps.values() if step.tasks)

    def vm_states(self, now, vms, facts=None, spec=None):
        merged = Step(None)
        merged.tasks = [task for step in self.steps.values() for task in step.tasks]
        merged.raised_on = set().union(*(step.raised_on for step in self.steps.values()))
        return merged.vm_states(now, vms, facts, spec)


class Run:
    """One drawn run, from the plays it will run and the events Ansible sends."""

    def __init__(self, title, now, check=False, log="", started_at=None, recipe=None, vms=()):
        self.title, self.check, self.log = title, check, log
        self.recipe = recipe if recipe is not None else recipes.recipe(title)
        self.specs = {stage.key: stage for stage in self.recipe.stages}
        self.step_specs = {(stage.key, step.label): step for stage in self.recipe.stages for step in stage.steps}
        self.outcomes = {}  # host: {fact: whether it holds}, for the declared steps' outcomes
        self.begun = now
        self.started_at = started_at if started_at is not None else time.time()
        self.names = [str(vm) for vm in vms]  # the VMs the run was given, by name, in their order
        self.stages = OrderedDict()
        self.play_stage = {}  # play key: (stage, step)
        self.play_tasks = {}  # play key: [(task key, step key)], as compiled
        self.play_reserved = {}  # play key: [(include key, step key, count)]
        self.play_batches = {}  # play key: batches, once it has started
        self.last_play = None
        self.reached_last = False
        self.play = None
        self.current = None  # the task running now
        self.order = []  # every task, in the order started
        self.plan = []  # the run's own first words: what it will do
        self.notes = []  # Note, in the order said
        self.records = OrderedDict()  # (vm, matter): every line recorded for NEXT STEPS, in order
        self.facts = OrderedDict()  # host: {vmid, address, new}
        self.finished = None
        self.failed = False
        self.first_failure = ""  # the step or stage the first failure was drawn at
        self.prompting = None  # since when a prompt with no task of its own (a vars_prompt) has been up
        self.prompted = 0.0  # the person's time at prompts with no task of their own
        self.said = set()  # steps and stages a line was printed for (output that is not a terminal)
        self.best = {}  # what each bar and the title's percentage have shown: none ever falls
        self.shown_stage = None  # the last stage drawn a task of: a quiet stage's tasks never move where the run is
        self.play_hosts = {}  # play key: the hosts it has started a task on, in that order - a serial play's turns
        self.batch = 0  # how many plays, and serial plays' batches, have started: a task starts again only in its own
        self.gone = {}  # play key: {host: left or failed}, the hosts no longer in it
        self.started = {}  # stage key: the step the last task a step starts at began, in the play running
        self.said_info = {}  # stage key: what its row says, from the fact its declaration names
        self.declined = ""  # the line said for an answer that ended the run, not a failure
        self.stopped = False  # a run in parts that Ctrl-C, or anything but a failed part, ended part way
        self.blamed = []  # the VMs a raise on this machine repeated failures of
        self.frame_vms = None  # the run's VMs while a frame is drawn, worked out once for it

    # --- what the playbook is ---------------------------------------------------------------------------------
    def plays(self, plays):
        """plays, in order as the playbook lists them: (key, playbook file, tasks), or (key, file, tasks, name);
        each task (task key, role or None), or (task key, role, meta), meta saying hidden (counted, never shown)
        and, for an include whose file is literal, include (its file's task count), include_role (the role its
        tasks are in, for an include_role) and include_file (the file they come from, for an include_tasks); and
        blocks, file and name, which the declaration's steps match."""
        plays = [tuple(play) + (None,) * (4 - len(play)) for play in plays]
        stages = stages_for([(path, name) for _, path, _, name in plays], self.recipe)
        for (key, _, tasks, _), (stage, override) in zip(plays, stages, strict=True):
            self.play_stage[key] = (stage, override)
            self.play_tasks[key], self.play_reserved[key] = [], []
            self.started = {}
            for task in tasks:
                task_key, role, meta = (tuple(task) + ({},))[:3]
                meta = meta or {}
                blocks, file = meta.get("blocks"), meta.get("file", "")
                step = self._stage(stage).step(self._step_of(stage, override, role, blocks, file, meta.get("name")))
                step.expect(task_key)
                self.play_tasks[key].append((task_key, step.key))
                count = meta.get("include") or 0
                if count:
                    included = meta.get("include_role") or role
                    file = meta.get("include_file") or file
                    target = self._stage(stage).step(self._step_of(stage, override, included, blocks, file))
                    target.reserve(task_key, count)
                    self.play_reserved[key].append((task_key, target.key, count))
        self.started = {}
        if plays:
            self.last_play = plays[-1][0]

    def _step_of(self, stage, override, role, blocks=(), file="", name=None):
        """A task's step: the declared step a block around it names, else the one its task file is, else its play's
        file's, else the one the last task a step starts at began - name, the task's, may begin one - else its
        role."""
        spec = self.specs.get(stage)
        if spec is None:
            return override or role
        found = set(blocks or ())
        for step in spec.steps:
            if step.blocks & found:
                return step.label
        for step in spec.steps:
            if file and any(file == end or file.endswith("/" + end) for end in step.files):
                return step.label
        if override:
            return override
        begun = next((step.label for step in spec.steps if name is not None and name in step.starts), None)
        if begun is not None:
            self.started[stage] = begun
        return self.started.get(stage) or role

    def _stage(self, key):
        if key not in self.stages:
            self.stages[key] = Stage(key)
        return self.stages[key]

    def play_start(self, key, name="", path="", batches=1, now=None):
        """A play starting, or a serial play's next batch: batches, how many it runs its hosts in, multiplies its
        counts the first time."""
        if now is not None and self.current is not None:
            self._left(now)
            self.current.ends = False
        if key not in self.play_stage:
            # A play the playbook did not list: its own stage, from its file and name.
            self.play_stage[key] = stages_for([(path, name)], self.recipe)[0] if path else (name or "run", None)
        if key not in self.play_batches:
            self.play_batches[key] = batches
            if batches > 1:
                stage = self._stage(self.play_stage[key][0])
                for task_key, step_key in self.play_tasks.get(key, []):
                    stage.step(step_key).expect(task_key, batches)
                for include_key, step_key, count in self.play_reserved.get(key, []):
                    stage.step(step_key).reserve(include_key, count * (batches - 1))
        self.play = key
        self.batch += 1
        # Every batch runs the play's tasks from its first: no step has begun.
        self.started = {}
        if key == self.last_play:
            self.reached_last = True

    # --- the events -------------------------------------------------------------------------------------------
    def task(self, key, name, role, now, waits=False, hidden=False, blocks=(), ends=False, file=""):
        # Ansible starts a meta task once per host, each just before that host's result: the same task again, not
        # another, until a play or a serial play's next batch starts.
        if self.current is not None and self.current.key == key and self.current.batch == self.batch:
            self.current.end = None
            return self.current
        stage, override = self.play_stage.get(self.play, ("run", None))
        if self.current is not None:
            self._left(now)
            self.current.close(now)
        step = self._step_of(stage, override, role, blocks, file, name)
        task = Task(key, name, stage, step, now, role=role, waits=waits, hidden=hidden, ends=ends)
        task.batch = self.batch
        if not (self.specs.get(stage) and self.specs[stage].quiet):
            self.shown_stage = stage
        self._stage(stage).step(task.step).reach(task)
        self.current = task
        self.order.append(task)
        return task

    def _left(self, now):
        """After a meta: end_host, each host still in its play that sent no result for it was left as it is."""
        task = self.current
        if task is None or not task.ends:
            return
        gone = set(self.gone.get(self.play, {}))
        for host in sorted(set(self.play_hosts.get(self.play, [])) - set(task.hosts) - gone):
            task.hosts[host] = [task.start, now, "left"]
            self.gone.setdefault(self.play, {})[host] = "left"

    def include(self, role, count, key=None, blocks=(), file=""):
        """An include announcing its tasks: counted in the step its tasks will run in, as when the playbook started."""
        stage, override = self.play_stage.get(self.play, ("run", None))
        self._stage(stage).step(self._step_of(stage, override, role, blocks, file)).announce(count, key)

    def find(self, key):
        if self.current is not None and self.current.key == key:
            return self.current
        return next((task for task in reversed(self.order) if task.key == key), None)

    def host_start(self, key, host, now):
        task = self.find(key)
        if task is not None:
            task.host_start(host, now)
            begun = self.play_hosts.setdefault(self.play, [])
            if host not in begun:
                begun.append(host)

    def result(self, key, host, outcome, now):
        task = self.find(key)
        if task is not None:
            task.host_end(host, outcome, now)
            if outcome in ("failed", "unreachable"):
                # A host whose failure no rescue takes is out of the play from here on.
                self.gone.setdefault(self.play, {})[host] = "failed"
            if outcome in ("failed", "unreachable") and not self.first_failure:
                self.first_failure = task.step or task.stage
            if task.end is not None and task.waits and task.wait is None and task.seconds(now) >= WAIT_AFTER:
                task.wait = (task.start, 0)
        return task

    def retry(self, key, host, now, seconds):
        """A retried wait for a person, with the seconds it may still take: True the first time, for the bell."""
        return self._wait(self.find(key), now, seconds)

    def tried(self, key, now, attempt, total, delay):
        """A retried task that waits for a machine, not a person: which try comes next, of how many, and until when
        it may take, said on its line."""
        task = self.find(key)
        if task is not None:
            left = max(0, total - attempt + 1)
            task.tries = (attempt, total, now + left * max(0, delay))

    def settle(self, now):
        """Whether the running task has just become a wait for a person: one tagged so, running WAIT_AFTER seconds
        (GitHub's device flow, a wait_for that never retries). True once, for the bell."""
        task = self.current
        if task is None or self.finished is not None or task.end is not None or not task.waits:
            return False
        return now - task.start >= WAIT_AFTER and self._wait(task, task.start, 0)

    @staticmethod
    def _wait(task, since, seconds):
        if task is None:
            return False
        if task.wait is None:
            task.wait = (since, seconds)
            return True
        if seconds and not task.wait[1]:
            task.wait = (since, seconds)
        return False

    def prompt(self, up, now):
        """A prompt up (True) or answered (False): the person's time, the running task's, or the run's own for a
        vars_prompt that comes before any task."""
        task = self.current if self.current is not None and self.current.end is None else None
        if up:
            if task is not None:
                task.prompting = now
            else:
                self.prompting = now
            return
        if task is not None and task.prompting is not None:
            task.prompted += now - task.prompting
            task.prompting = None
        elif self.prompting is not None:
            self.prompted += now - self.prompting
            self.prompting = None

    def waiting_for_you(self):
        """Whether the run waits for the person now: at a prompt, or in a wait for the browser."""
        if self.prompting is not None:
            return True
        task = self.current
        return task is not None and task.end is None and (task.prompting is not None or bool(task.wait))

    def note(self, host, text, plan=False, task=None, lines=1):
        """A report, by the task that made it: plan is the run's own first words, what it will do, kept apart."""
        text = one_line(text)
        if not text:
            return
        if plan:
            self.plan.append(text)
        else:
            self.notes.append(Note(host, text, task, lines))

    def record(self, vm, matter, line, task=None):
        """A line recorded for NEXT STEPS, '' once nothing is left of that matter, by the set_stats task given: the
        record of the report the task before it made about that VM, as every report's record follows it."""
        self.records.setdefault((vm, matter), []).append(one_line(line))
        index = next((n for n in range(len(self.order) - 1, -1, -1) if self.order[n] is task), 0)
        if task is None or index == 0:
            return
        report = self.order[index - 1]
        vms = self._vms()
        for note in reversed(self.notes):
            if note.task is report and self._about(note, vms) == vm:
                note.matters.add((vm, matter))
                return

    def _vms(self):
        """The run's VMs: those it was given, then those it learned of, then every host it touched but this one."""
        if self.frame_vms is not None:
            return self.frame_vms
        hosts = [*self.names]
        hosts += [host for host in self.facts if host not in hosts]
        hosts += sorted({h for t in self.order for h in t.hosts} - set(hosts))
        return [host for host in hosts if host != "localhost"]

    def _about(self, note, vms):
        """The VM a note is about: its own host's, or for a play on this machine, the one VM it names, else the run's
        one VM."""
        if note.host != "localhost":
            return note.host
        return self._named(note.text, vms) or (self.names[0] if len(self.names) == 1 else note.host)

    def fact(self, host, name, value):
        entry = self.facts.setdefault(host, {})
        if name == "proxmox_vm_resolved_id":
            if str(value).isdigit():
                entry["vmid"] = int(value)
        elif name == "proxmox_vm_address":
            entry["address"] = one_line(value)
        elif name == "proxmox_vm_exists":
            entry.setdefault("new", not value)

    def raised(self, stage, step, host=None):
        """A failure a rescue took in stage's step, raised again later or declared so: the step's, not the raising
        task's; once for each host, however often it is said."""
        if stage in self.stages and step in self.stages[stage].steps:
            self.stages[stage].steps[step].raised_on.add(host if host is not None else "")

    def outcome(self, host, fact, holds):
        """A declared outcome's fact, set on host: whether it holds, never what it says. One that says the VM failed
        fails the step the task that set it is in, as the failure its rescue took."""
        self.outcomes.setdefault(host, {})[fact] = bool(holds)
        task = self.current
        if holds and task is not None:
            spec = self.step_specs.get((task.stage, task.step)) or self.specs.get(task.stage)
            if any(o.fact == fact and o.state == "failed" for o in (spec.outcomes if spec else ())):
                self.raised(task.stage, task.step, host)

    def turn(self, stage):
        """Whose turn it is in a serial play of stage: (the VM, its place, of how many), or None. Its place is its
        turn's, the order the play reached the VMs in - discovery's, the cluster listing's - never the order they were
        named in."""
        task = self.current
        if task is None or task.stage != stage or self.play_batches.get(self.play, 1) < 2 or len(task.hosts) != 1:
            return None
        vm = next(iter(task.hosts))
        names = self.names or self._vms()
        if vm not in names:
            return None
        begun = self.play_hosts.get(self.play, [])
        return (vm, (begun.index(vm) if vm in begun else names.index(vm)) + 1, len(names))

    def info(self, stage, names):
        """What a stage's row says: its Info's word and the VMs a fact named."""
        spec = self.specs.get(stage)
        if spec is not None and isinstance(spec.info, recipes.Info) and names:
            self.said_info[stage] = f"{spec.info.word} {', '.join(names)}"

    def decline(self, line):
        """An answer that ended the run: said as itself, never as a failure."""
        self.declined = one_line(line)

    def blame(self, host):
        """A VM a raise on this machine repeated a failure of."""
        if host != "localhost" and host not in self.blamed:
            self.blamed.append(host)

    def end(self, now, failed=False, stopped=False):
        """The run's end: failed, or stopped part way - Ctrl-C in a run in parts, whose end is said by its trap."""
        if self.current is not None:
            self._left(now)
            self.current.close(now)
        self.finished = now
        self.failed = failed or stopped
        self.stopped = stopped

    def command(self, stage, step, name, now):
        """A command a run runs as a part of its own - one of just lint's checks, just test's pytest - as one task in
        stage's step, on this machine."""
        key = f"command:{stage}:{step}:{len(self.order)}"
        self.play = key
        self.play_stage[key] = (stage, step)
        self.batch += 1
        self.started = {}
        task = self.task(key, name, None, now)
        self.host_start(key, "localhost", now)
        return task

    # --- a run in parts ---------------------------------------------------------------------------------------
    def dump(self, now, next_records=None, failed_hosts=()):
        """What a later part of a run in parts, and its end, need of this part and the ones before it: every task,
        step, report and record, each time from the run's start, and what NEXT STEPS and the head will say -
        next_records, the parts' NEXT STEPS records merged, {stat: {vm: {matter: line}}}. Every value in it was
        cleaned when the run took it in."""
        index = {id(task): n for n, task in enumerate(self.order)}

        def at(value):
            return None if value is None else value - self.begun

        def ended(value):
            return at(value if value is not None else now)

        return {
            "version": STATE_VERSION,
            "title": self.title,
            "started_at": self.started_at,
            "check": self.check,
            "names": self.names,
            "tasks": [
                {
                    "key": task.key,
                    "name": task.name,
                    "stage": task.stage,
                    "step": task.step,
                    "role": task.role,
                    "hidden": task.hidden,
                    "start": at(task.start),
                    "end": ended(task.end),
                    "hosts": {host: [at(start), ended(end), said] for host, (start, end, said) in task.hosts.items()},
                    "outcome": task.outcome,
                    "wait": [at(task.wait[0]), task.wait[1]] if task.wait else None,
                    "prompted": task.waited(now) if not task.wait else 0.0,
                    "detail": task.detail,
                }
                for task in self.order
            ],
            "stages": [
                {
                    "key": stage.key,
                    "steps": [
                        {
                            "key": step.key,
                            "expected": step.expected,
                            "raised_on": sorted(step.raised_on),
                            "tasks": [index[id(task)] for task in step.tasks],
                        }
                        for step in stage.steps.values()
                    ],
                }
                for stage in self.stages.values()
            ],
            "notes": [
                {
                    "host": note.host,
                    "text": note.text,
                    "task": index.get(id(note.task)),
                    "lines": note.lines,
                    "matters": sorted(list(matter) for matter in note.matters),
                }
                for note in self.notes
            ],
            "plan": self.plan,
            "records": [[vm, matter, lines] for (vm, matter), lines in self.records.items()],
            "facts": self.facts,
            "outcomes": self.outcomes,
            "said_info": self.said_info,
            "said": sorted([list(key) if isinstance(key, tuple) else [key] for key in self.said]),
            "declined": self.declined,
            "blamed": self.blamed,
            "first_failure": self.first_failure,
            "prompted": self.prompted + ((now - self.prompting) if self.prompting is not None else 0.0),
            "reached_last": self.reached_last,
            "best": [[list(key) if isinstance(key, tuple) else key, value] for key, value in self.best.items()],
            "next_records": next_records or {},
            "failed_hosts": sorted(set(failed_hosts)),
        }

    def adopt(self, state, now):
        """The parts of the run before this one, taken over from their state: their stages and steps done, said
        already when the output is lines, and one clock from the run's start."""
        self.started_at = float(state["started_at"])
        self.begun = now - max(0.0, time.time() - self.started_at)

        def at(value):
            return None if value is None else self.begun + float(value)

        tasks = []
        for saved in state["tasks"]:
            task = Task(saved["key"], saved["name"], saved["stage"], saved["step"], at(saved["start"]))
            task.role, task.hidden, task.end, task.outcome = (
                saved["role"],
                saved["hidden"],
                at(saved["end"]),
                saved["outcome"],
            )
            task.hosts = OrderedDict(
                (host, [at(start), at(end), said]) for host, (start, end, said) in saved["hosts"].items()
            )
            task.wait = (at(saved["wait"][0]), saved["wait"][1]) if saved["wait"] else None
            task.prompted, task.detail = float(saved["prompted"]), saved["detail"]
            task.batch = -1
            tasks.append(task)
        for saved in state["stages"]:
            stage = self._stage(saved["key"])
            for saved_step in saved["steps"]:
                step = stage.step(saved_step["key"])
                step.tasks = [tasks[n] for n in saved_step["tasks"]]
                # Counted as it was: what a later task of the same step reaches is counted beyond it.
                step.extra = int(saved_step["expected"])
                step.raised_on = set(saved_step["raised_on"])
        self.order = tasks
        for saved in state["notes"]:
            note = Note(saved["host"], saved["text"], tasks[saved["task"]] if saved["task"] is not None else None)
            note.lines, note.matters = saved["lines"], {tuple(matter) for matter in saved["matters"]}
            self.notes.append(note)
        self.plan = list(state["plan"])
        self.records = OrderedDict(((vm, matter), list(lines)) for vm, matter, lines in state["records"])
        self.facts = OrderedDict(state["facts"])
        self.outcomes = dict(state["outcomes"])
        self.said_info = dict(state["said_info"])
        self.said = {tuple(key) if len(key) > 1 else key[0] for key in state["said"]}
        self.declined = state["declined"]
        self.blamed = list(state["blamed"])
        self.first_failure = state["first_failure"]
        self.prompted = float(state["prompted"])
        self.reached_last = bool(state["reached_last"])
        # No bar and no percentage falls from one part to the next either.
        best = state.get("best") or []
        self.best = {tuple(key) if isinstance(key, list) else key: float(value) for key, value in best}
        self.names = self.names or list(state["names"])

    # --- where it is ------------------------------------------------------------------------------------------
    def running_stage(self):
        """The stage the run is in: the running task's, or while a quiet stage's tasks run, the last stage shown."""
        if self.current is None or self.finished is not None:
            return None
        spec = self.specs.get(self.current.stage)
        return self.shown_stage if spec is not None and spec.quiet else self.current.stage

    def _quiet(self, stage):
        spec = self.specs.get(stage.key)
        return spec is not None and spec.quiet and not stage.failed()

    def _shown(self):
        """The stages drawn: every one but a quiet one where nothing failed."""
        return [stage for stage in self.stages.values() if not self._quiet(stage)]

    def _flat(self):
        """A run of one stage that has steps is drawn as its steps, with no row of its own."""
        stages = self._shown()
        return len(stages) == 1 and any(key is not None for key in stages[0].steps)

    def _state(self, stage):
        """done, running, waiting, skipped or to come."""
        if not stage.reached:
            return "to come"
        if self.running_stage() == stage.key:
            current = self.current
            waiting = current.stage == stage.key and current.wait and current.end is None
            return "waiting" if waiting else "running"
        return "skipped" if stage.skipped() else "done"

    def _step_state(self, stage, step):
        if not step.tasks:
            return "to come"
        running = self.finished is None and self.current is not None and self.current.stage == stage.key
        if running and self.current.step == step.key:
            return "waiting" if self.current.wait and self.current.end is None else "running"
        return "skipped" if step.skipped() else "done"

    def _changed_said(self, count):
        if not count:
            return ""
        return f"{count} would change" if self.check else f"{count} changed"

    def _vm_info(self):
        vms = [(host, facts) for host, facts in self.facts.items() if facts.get("vmid") or facts.get("address")]
        if len(vms) == 1:
            host, facts = vms[0]
            parts = [f"VM {facts['vmid']}" if facts.get("vmid") else "", facts.get("address", "")]
            return " · ".join(part for part in parts if part)
        if vms:
            return f"{len(vms)} VMs"
        return ""

    def _shows_vm(self, stage):
        spec = self.specs.get(stage.key)
        return spec is not None and spec.info == "vm"

    def _fleet(self):
        return len(self._vms()) >= 2

    def _marks(self, states, glyph, room, turn=None):
        """Each VM's mark, as segments: alpha ✓ · beta ⠴ (2 of 3) · gamma ○; as counts when they do not fit room."""
        parts = []
        for vm, state in states.items():
            mark, style = MARKS[state]
            parts += [(f"{vm} ", ""), (mark or glyph, style)]
            if turn and turn[0] == vm:
                parts.append((f" ({turn[1]} of {turn[2]})", style))
            parts.append((" · ", "d"))
        parts = parts[:-1]
        if screen.width(parts) <= room:
            return parts
        done = sum(state in ("done", "skipped") for state in states.values())
        failed = sum(state in ("failed", "unreachable") for state in states.values())
        said = [(f"{done}/{len(states)} VMs", "")]
        if failed:
            said.append((f" {failed} failed", "r"))
        return said

    def _vm_marks(self, stage_key, step, now, glyph, room, state):
        """A step's VMs, each marked: every VM given - while it runs, each still in its play at it, unless its serial
        batch is still to come, and each its play left or failed as so - and none it never reached once it ended;
        empty for a run of fewer than two VMs."""
        vms = self._vms()
        if len(vms) < 2 or not step.tasks:
            return []
        spec = self.step_specs.get((stage_key, step.key)) or self.specs.get(stage_key)
        states = step.vm_states(now, vms, self.outcomes, spec)
        marked = OrderedDict()
        gone = self.gone.get(self.play, {}) if state in ("running", "waiting") else {}
        for vm in self.names or vms:
            if vm in states:
                marked[vm] = states[vm]
            elif state in ("running", "waiting"):
                marked[vm] = gone.get(vm, "to come")
        for vm, said in states.items():
            marked.setdefault(vm, said)
        turn = self.turn(stage_key) if state in ("running", "waiting") else None
        if turn is not None:
            # A VM whose batch of this serial play has not begun is still to come, whatever an earlier play of the
            # step (a ping of every VM) did; and between two of its tasks the VM whose turn it is is still at it.
            begun = self.play_hosts.get(self.play, [])
            for vm, said in marked.items():
                if vm not in begun and said in ("done", "skipped"):
                    marked[vm] = "to come"
            if marked.get(turn[0]) in ("done", "skipped", "to come"):
                marked[turn[0]] = "running"
        elif state in ("running", "waiting") and self.play_batches.get(self.play, 1) < 2:
            # A play that is not serial takes every VM still in it through each task: between two, or with its own
            # result in, a VM is still at the step until the step ends.
            still = set(self.play_hosts.get(self.play, [])) - set(gone)
            for vm, said in marked.items():
                if vm in still and said in ("done", "skipped", "to come"):
                    marked[vm] = "running"
        return self._marks(marked, glyph, room, turn)

    def _clamped(self, key, fraction):
        best = max(self.best.get(key, 0.0), fraction)
        self.best[key] = best
        return best

    # --- the live frame ---------------------------------------------------------------------------------------
    def frame(self, now, cols, rows, glyph, color=True):
        """The region's lines: each cut to cols - 1, and few enough for rows - 1 where folding can make them so."""
        limit = cols - 1
        tiers = {"changed": cols >= 80, "counts": cols >= 64, "bars": cols >= 52}
        # Nothing changes while a frame is drawn: the VMs, which every stage and window asks for, are worked out once.
        self.frame_vms = self._vms()
        try:
            head = self._header(now, limit)
            body = self._stages(now, limit, glyph, tiers, fold=0)
            for fold in (1, 2, 3):
                if len(head) + len(body) + 2 <= rows - 1:
                    break
                body = self._stages(now, limit, glyph, tiers, fold=fold)
            lines = [*head, *body, [], self._footer(now, limit, glyph)]
        finally:
            self.frame_vms = None
        return [screen.render(screen.cut(line, limit), color) for line in lines]

    def _header(self, now, limit):
        # The plan is printed whole above the region, as every report is: here it could only be cut.
        started = time.strftime("%H:%M", time.localtime(self.started_at))
        first = [("claude-on-proxmox", "b"), (f"  {self.title} · started {started}", "")]
        if self.check:
            first.append((" · a dry run: nothing is changed", "y"))
        # A run drawn as its steps has no stage row to say its VM on: the header does, at its right.
        shown = self._shown()
        if self._flat() and self._shows_vm(shown[0]) and self._vm_info():
            first = screen.right(first, [(self._vm_info(), "d")], limit)
        return [first, []]

    def _whole(self, stage):
        """The tasks a stage will reach: an ended step's as it reached them, its own tasks' all of them."""
        if self._state(stage) in ("done", "skipped"):
            return stage.reached
        return sum(
            step.reached
            if key is not None and self._step_state(stage, step) in ("done", "skipped")
            else max(step.expected, step.reached)
            for key, step in stage.steps.items()
        )

    def _row(self, indent, glyph, label, label_width, step, state, now, tiers, info=(), limit=99, total=None):
        """One stage's or step's line. info: segments said after the counts."""
        style = {"done": "g", "running": "c", "waiting": "y", "to come": "d", "skipped": "d"}[state]
        if step.failed() and state != "to come":
            glyph, style = "✗", "r"
        if state == "to come":
            count = screen.plural(step.expected, "task") if step.expected else ""
            return screen.cut([(" " * indent, ""), ("○ ", "d"), (label.ljust(label_width), "d"), (count, "d")], limit)
        glyph = {"done": "✓", "waiting": "⏸", "skipped": "–"}.get(state, glyph) if style != "r" else glyph
        bold = "" if state in ("done", "skipped") else "b"
        weight = "d" if state == "skipped" else bold
        left = [(" " * indent, ""), (glyph + " ", style), (label.ljust(label_width), weight)]
        # A step that ended is whole, whatever it would have reached: a rescue that never ran is no task left.
        if total is None:
            total = step.reached if state in ("done", "skipped") else max(step.expected, step.reached)
        fraction = self._clamped((indent, label), step.reached / total if total else 1)
        if tiers["bars"]:
            left += screen.bar(fraction, BAR, "g" if state == "done" else "d" if state == "skipped" else style)
        if tiers["counts"]:
            left.append((f"  {step.reached:>3}/{total:<4}", ""))
        if tiers["changed"]:
            left.append((f" {self._changed_said(step.changed()):<15}", "y" if step.changed() else ""))
        if info:
            left += [(" ", ""), *info]
        seconds = step.seconds(now)
        tail = [(screen.duration(seconds), "")] if state in ("done", "skipped") else [(screen.clock(seconds), "")]
        return screen.right(left, tail, limit)

    def _window(self, tasks, now, glyph, limit, indent=9):
        """The step's last tasks, newest at the bottom: the running one with a timer, a skipped one dim, each with
        its VM when the run has several."""
        lines = []
        shown = [task for task in tasks if not task.hidden][-self.recipe.window :]
        width_ = min(limit, 88) - indent
        fleet = self._fleet()
        for task in shown:
            running = task.end is None and self.finished is None and task is self.current
            vm = [(f"{next(iter(task.hosts))}  ", "d")] if fleet and len(task.hosts) == 1 else []
            if running:
                mark, style = ("⏸", "y") if task.wait else (glyph, "c")
                if task.tries:
                    attempt, total, until = task.tries
                    said = f"try {attempt} of {total} · up to {screen.clock(until - now)}"
                    tail = [*vm, (said, style)]
                else:
                    hosts = self._hosts_said(task, now, glyph, width_ // 2)
                    tail = hosts or [*vm, (screen.clock(task.seconds(now)), style)]
                name = [(task.name, "b"), *([(f"  {task.detail}", "d")] if task.detail else [])]
            elif task.outcome == "skipped":
                mark, style, name, tail = "–", "d", [(task.name, "d")], [*vm, ("skipped", "d")]
            else:
                mark, style = {
                    "changed": ("●", "y"),
                    "failed": ("✗", "r"),
                    "unreachable": ("✗", "r"),
                    "rescued": ("↺", "d"),
                    "lost": ("↺", "d"),
                    "raised": ("↑", "d"),
                    "ignored": ("↺", "d"),
                    "included": ("↳", "d"),
                    "stopped": ("■", "y"),
                }.get(task.outcome, ("✓", "g"))
                dim = ("rescued", "lost", "raised", "ignored", "included", "stopped")
                name = [(task.name, "d" if task.outcome in dim else "")]
                tail = [*vm, (screen.duration(task.seconds(now)), "d")]
            room = width_ - 2 - screen.width(tail) - 2
            line = [(" " * indent, ""), (mark + " ", style), *screen.cut(name, room)]
            lines.append(screen.right(line, tail, indent + width_))
        return lines

    def _hosts_said(self, task, now, glyph, room):
        """For a task several VMs run: each VM's state, the running ones with their timer; in room characters, or
        as counts when the VMs do not fit."""
        if len(task.hosts) < 2:
            return []
        parts = []
        for host, (start, end, outcome) in task.hosts.items():
            if end is None:
                parts += [(f"{host} ", ""), (glyph, "c"), (f" {screen.clock(now - start)}", "c")]
            else:
                mark = "✗" if outcome in ("failed", "unreachable") else "✓"
                parts += [
                    (f"{host} ", ""),
                    (mark, "r" if mark == "✗" else "g"),
                    (f" {screen.duration(end - start)}", "d"),
                ]
            parts.append((" · ", "d"))
        parts = parts[:-1]
        if screen.width(parts) <= room:
            return parts
        ended = [entry for entry in task.hosts.values() if entry[1] is not None]
        failed = sum(outcome in ("failed", "unreachable") for _, _, outcome in ended)
        running = [start for start, end, _ in task.hosts.values() if end is None]
        said = [(f"{len(ended)}/{len(task.hosts)} VMs ", "")]
        if failed:
            said.append((f"{failed} failed ", "r"))
        if running:
            said += [(glyph, "c"), (f" {screen.clock(now - min(running))}", "c")]
        return said

    def _waiting_line(self, indent, label, now, limit):
        task = self.current
        since, seconds = task.wait
        step = f"{task.step}: " if task.step and (self.specs.get(task.stage) or recipes.Stage("")).steps else ""
        waited = screen.clock(now - task.start)
        said = f"{step}waiting for you in the browser · {', '.join(task.hosts) or ''} · {waited}"
        if seconds:
            said += f" of up to {screen.clock(seconds + (since - task.start))}"
        head = [(" " * indent, ""), ("⏸ ", "y")]
        if label is not None:
            head.append((label.ljust(13), "b"))
        return screen.cut([*head, (said, "y")], limit)

    def _stages(self, now, limit, glyph, tiers, fold):
        lines = []
        pending = []
        stages = self._shown()
        flat = self._flat()
        vms = self._vms()
        for stage in stages:
            state = self._state(stage)
            if not flat:
                if state == "to come" and fold >= 2:
                    pending.append(stage.key)
                    continue
                if state == "waiting":
                    lines.append(self._waiting_line(1, stage.key, now, limit))
                else:
                    info = [(self._vm_info(), "d")] if self._shows_vm(stage) and state != "to come" else []
                    if stage.key in self.said_info and state != "to come":
                        info = [(self.said_info[stage.key], "d")]
                    if state == "done" and not info and not stage.changed() and not stage.failed():
                        info = [("no changes", "d")]
                    total = self._whole(stage)
                    lines.append(self._row(1, glyph, stage.key, 13, stage, state, now, tiers, info, limit, total))
                if state not in ("running", "waiting"):
                    continue
            lines += self._steps(stage, now, limit, glyph, tiers, fold, flat, vms)
        if pending:
            lines.append(screen.cut([(" ", ""), ("○ ", "d"), (f"then {', '.join(pending)}", "d")], limit))
        return lines

    def _steps(self, stage, now, limit, glyph, tiers, fold, flat, vms):
        """A stage's steps: under its row, or as the rows of a run of one stage."""
        lines = []
        indent, window = (1, 5) if flat else (5, 9)
        own = stage.steps.get(None)
        running_step = self.current.step if self.current is not None and self.current.stage == stage.key else ""
        if running_step is None and own is not None and self._state(stage) in ("running", "waiting"):
            if flat and self._state(stage) == "waiting":
                lines.append(self._waiting_line(1, None, now, limit))
            lines += self._window(own.tasks, now, glyph, limit, window)
        later = []
        for key, step in stage.steps.items():
            if key is None:
                continue
            step_state = self._step_state(stage, step)
            if step_state == "to come" and fold >= 1:
                later.append(key)
                continue
            if flat and step_state == "waiting":
                lines.append(self._waiting_line(1, None, now, limit))
            else:
                info = self._vm_marks(stage.key, step, now, glyph, 44, step_state)
                lines.append(self._row(indent, glyph, key, 18, step, step_state, now, tiers, info, limit))
            if step_state in ("running", "waiting"):
                shown = 1 if fold >= 3 else self.recipe.window
                lines += self._window(step.tasks, now, glyph, limit, window)[-shown:]
        if later:
            lines.append(screen.cut([(" " * indent, ""), ("○ ", "d"), (f"then {', '.join(later)}", "d")], limit))
        return lines

    def _footer(self, now, limit, glyph):
        waiting = self.waiting_for_you()
        stages = self._shown()
        running = self.running_stage()
        where = ""
        if self._flat() and self.current is not None and running == stages[0].key:
            steps = [key for key in stages[0].steps if key is not None]
            if self.current.step in steps:
                where = f"{self.recipe.noun} {steps.index(self.current.step) + 1} of {len(steps)}"
        else:
            keys = [stage.key for stage in stages]
            where = f"stage {keys.index(running) + 1} of {len(keys)}" if running in keys else ""
        runs = [("⏸ " if waiting else glyph + " ", "y" if waiting else "c"), (screen.clock(now - self.begun), "b")]
        if where:
            runs.append((f" · {where}", "d"))
        if waiting:
            runs.append((" · waiting for you", "y"))
        if self.log:
            runs.append((f" · {self.log}", "d"))
        return screen.cut(runs, limit)

    def title_text(self, now):
        """The terminal's title: the run, and where it is; its percentage never falls."""
        if self.finished is not None:
            ended = "declined" if self.declined else "stopped" if self.stopped else "failed" if self.failed else "done"
            return f"{self.title} · {ended}"
        if self.waiting_for_you():
            return f"{self.title} · waiting for you"
        stage = self.running_stage()
        if stage is None:
            return self.title
        stages = self.stages.values()
        expected = sum(self._whole(s) for s in stages)
        reached = sum(s.reached for s in stages)
        percent = round(100 * self._clamped("title", reached / expected if expected else 0))
        where = stage
        if self._flat() and self.current is not None and self.current.step is not None:
            where = self.current.step
        return f"{self.title} · {where} {percent}%"

    # --- output that is not a terminal --------------------------------------------------------------------------
    def ended_lines(self, now, final=False):
        """A line for each step and stage that has ended since the last call, for output that is not a terminal;
        final, at a part's end, its running step and stage too."""
        if final:
            current, self.current = self.current, None
            try:
                return self.ended_lines(now)
            finally:
                self.current = current
        out = []
        for stage in self.stages.values():
            if not stage.reached or self._quiet(stage):
                continue
            # A stage that is one step, a sign-in, is said once: by its step's name.
            only = list(stage.steps) if len(stage.steps) == 1 and None not in stage.steps else []
            for key, step in stage.steps.items():
                if key is None or only or (stage.key, key) in self.said:
                    continue
                if self._step_state(stage, step) not in ("done", "skipped"):
                    continue
                self.said.add((stage.key, key))
                out.append(self._ended(f"{stage.key} / {key}", step, now))
            # A run of one stage is said by its steps alone.
            if stage.key not in self.said and self._state(stage) in ("done", "skipped") and not self._flat():
                self.said.add(stage.key)
                out.append(self._ended(f"{stage.key} / {only[0]}" if only else stage.key, stage, now))
        return out

    def _ended(self, label, step, now):
        stopped = any(task.outcome == "stopped" for task in step.tasks)
        mark = "✗" if step.failed() else "■" if stopped else "–" if step.skipped() else "✓"
        said = [screen.plural(step.reached, "task")]
        if step.changed():
            said.append(self._changed_said(step.changed()))
        if step.failed():
            said.append(f"{step.failed()} failed")
        if step.skipped():
            said.append("skipped")
        return f"{mark} {label.ljust(28)} {' · '.join(said):<36} {screen.duration(step.seconds(now))}"

    # --- the summary ------------------------------------------------------------------------------------------
    def failed_vms(self, failed_hosts):
        """The VMs a run failed on: each failed host; for this machine, the VMs a raise there blamed, else the run's
        one VM when it has one."""
        vms = []
        for host in failed_hosts:
            hosts = [host]
            if host == "localhost":
                hosts = self.blamed or ([self.names[0]] if len(self.names) == 1 else [host])
            vms += [vm for vm in hosts if vm not in vms]
        return vms

    def _head(self, now, failed_hosts):
        """The summary's first line: done; done, with the VMs that failed; failed, and where; or declined."""
        total = now - self.begun
        minutes, seconds = divmod(round(total), 60)
        took = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
        ended = time.strftime("%H:%M", time.localtime(self.started_at + total))
        failed = self.failed_vms(failed_hosts)
        everyone = self._vms()
        if self.declined:
            return [("claude-on-proxmox", "b"), (f"  {self.title} · declined · {took}", "y"), (f" · {ended}", "")]
        if self.stopped:
            # Where the run was: the step, or the stage, of the last task it started.
            last = self.order[-1] if self.order else None
            where = f" at {last.step or last.stage}" if last is not None else ""
            return [
                ("claude-on-proxmox", "b"),
                (f"  {self.title} · stopped after {took}{where}", "y"),
                (f" · {ended}", ""),
            ]
        if not failed_hosts and not self.failed:
            return [("claude-on-proxmox", "b"), (f"  {self.title} · done in {took}", ""), (f" · {ended}", "")]
        if self.reached_last and len(everyone) >= 2 and len([vm for vm in failed if vm in everyone]) < len(everyone):
            said = f"{screen.plural(len(failed), 'VM')} failed: {', '.join(failed)}"
            return [("claude-on-proxmox", "b"), (f"  {self.title} · done in {took}", ""), (f" · {said}", "r")]
        where = f" at {self.first_failure}" if self.first_failure else ""
        return [("claude-on-proxmox", "b"), (f"  {self.title} · failed after {took}{where}", "r"), (f" · {ended}", "")]

    def summary(self, now, cols, color=True, failed_hosts=(), next_steps=()):
        limit = cols - 1
        if self.declined:
            # An answer that ended the run before it did anything: its line, said above, and the head.
            return [screen.render(self._cut_head(self._head(now, failed_hosts), limit), color)]
        lines = [self._head(now, failed_hosts), []]
        flat = self._flat()
        vms = self._vms()
        columns = self.recipe.columns
        heads = [("TIME", 6, "time"), ("TASKS", 9, "tasks")]
        title = f"{self.recipe.noun}s".upper() if flat else "STAGES"
        if flat:
            stage = self._shown()[0]
            rows = [(key, step) for key, step in stage.steps.items() if key is not None and step.tasks]
            # The stage's own tasks, a row only when they did something: just code's skips just deploy's fleet.
            own = stage.steps.get(None)
            if own is not None and own.tasks and not own.skipped():
                rows.insert(0, (stage.key, own))
        else:
            stage = None
            rows = [(stage_.key, stage_) for stage_ in self._shown() if stage_.tasks]
        # Each name whole, up to a step's width in the frame: just code's "read the sessions".
        label = min(18, max([11, *(len(str(key)) for key, _ in rows)]))
        said_head = f"{title:<{label + 3}}"
        said_head += "".join(f"{name:>{width}}" for name, width, key in heads if key in columns)
        if "changed" in columns:
            said_head += f"  {'WOULD CHANGE' if self.check else 'CHANGED'}"
        elif len(vms) >= 2:
            said_head += "    VMS"
        lines.append([(said_head, "h")])
        for key, part in rows:
            stopped = any(task.outcome == "stopped" for task in part.tasks)
            mark, style = (
                ("✗", "r") if part.failed() else ("■", "y") if stopped else ("–", "d") if part.skipped() else ("✓", "g")
            )
            head = [(" ", ""), (mark + " ", style), (str(key)[:label].ljust(label), "b")]
            counts = f"{screen.duration(part.seconds(now)):>6}" if "time" in columns else ""
            counts += f"{part.reached:>9}" if "tasks" in columns else ""
            counts += f"{(part.changed() or '—'):>9}   " if "changed" in columns else "    "
            head.append((counts, ""))
            parts = []
            if len(vms) >= 2:
                if flat:
                    spec = self.step_specs.get((stage.key, key)) or self.specs.get(stage.key)
                else:
                    spec = self.specs.get(key)
                states = part.vm_states(now, vms, self.outcomes, spec)
                if states:
                    parts.append(" · ".join(f"{vm} {MARKS[state][0] or '…'}" for vm, state in states.items()))
            if not flat:
                for step_key, step in part.steps.items():
                    if step_key is None or not step.tasks:
                        continue
                    piece = f"{step_key} {screen.duration(step.seconds(now))}"
                    if step.waited(now):
                        piece += f" ({screen.duration(step.waited(now))} yours)"
                    parts.append(piece)
            elif part.waited(now):
                parts.append(f"({screen.duration(part.waited(now))} yours)")
            if self._shows_vm(part) and self._vm_info():
                parts.insert(0, self._vm_info())
            if not flat and key in self.said_info:
                parts.insert(0, self.said_info[key])
            lines += self._flow(head, parts, limit)
        # What a play on this machine said about a VM - its address, its alias, its window - is that VM's.
        said = [(self._about(note, vms), note) for note in self.notes]
        # A report on a matter a later report recorded again is over: configure's "not signed in" once the sign-in
        # reports the login, under just deploy, where configure records nothing left since the sign-in follows.
        later = set()
        over = set()
        for _, note in reversed(said):
            if note.matters & later:
                over.add(id(note))
            later |= note.matters
        # A line left to do that a later record replaced, though no report of its own said so.
        replaced = set()
        for (vm, _), recorded in self.records.items():
            replaced |= {(vm, self._unnamed(vm, line)) for line in recorded if line and line != recorded[-1]}
        # What NEXT STEPS will say whole is not said here too.
        left_to_do = {one_line(line) for line in next_steps}
        # The same report again, in a later play - configure's repositories, then the sign-in's: its last run holds.
        last = {(host, note.task.role, note.task.name): note.task for host, note in said if note.task is not None}
        notes = OrderedDict()
        for host, note in said:
            text = self._unnamed(host, note.text)
            if id(note) in over or (host, text) in replaced:
                continue
            if note.text in left_to_do or f"{host}: {text}" in left_to_do or text in left_to_do:
                continue
            if note.task is not None and last[(host, note.task.role, note.task.name)] is not note.task:
                continue
            # A report of several lines, or longer than a line, is printed whole above: here, its first sentence.
            if note.lines > 1 or len(text) > limit - 2:
                text = first_sentence(text)
            notes.setdefault(host, [])
            if text not in notes[host]:
                notes[host].append(text)
        ordered = [vm for vm in vms if vm in notes] + [host for host in notes if host not in vms]
        for host in ordered:
            texts = notes[host]
            facts = self.facts.get(host, {})
            lines.append([])
            said_facts = " · ".join(
                part for part in (facts.get("address", ""), f"VM {facts['vmid']}" if facts.get("vmid") else "") if part
            )
            heading = "THIS MACHINE" if host == "localhost" else host.upper()
            new = " · new" if facts.get("new") else ""
            lines.append([(heading, "h"), (f"  {said_facts}{new}" if said_facts else "", "")])
            lines += [screen.cut([(" ", ""), (text, "")], limit) for text in texts[-10:]]
        slowest = sorted(
            (task for task in self.order if not task.waited(now) and task.seconds(now) >= SLOW),
            key=lambda task: task.seconds(now),
            reverse=True,
        )
        # A task run again for each VM of a serial play is listed once, by its slowest run.
        slowest = sorted(
            {task.name: task for task in reversed(slowest)}.values(), key=lambda task: task.seconds(now), reverse=True
        )
        if slowest:
            lines.append([])
            parts = [f"{task.name} {screen.duration(task.seconds(now))}" for task in slowest[:5]]
            lines += self._flow([("SLOWEST", "h"), ("  ", "")], parts, limit)
        lines.append([])
        changed = sum(task.outcome == "changed" for task in self.order)
        failed = sum(task.outcome in ("failed", "unreachable", "raised") for task in self.order)
        yours = sum(task.waited(now) for task in self.order) + self.prompted
        failed_said = f"{failed} failed"
        failed_vms = [vm for vm in self.failed_vms(failed_hosts) if vm != "localhost"]
        if failed_vms and len(vms) >= 2:
            failed_said += f" ({screen.plural(len(failed_vms), 'VM')})"
        totals = [
            screen.plural(len(self.order), "task"),
            self._changed_said(changed) or "nothing changed",
            failed_said,
        ]
        if yours:
            totals.append(f"{screen.duration(yours)} of it yours")
        if self.log:
            totals.append(f"log {self.log}")
        lines += self._flow([], totals, limit, style="d")
        return [
            screen.render(self._cut_head(line, limit) if index == 0 else screen.cut(line, limit), color)
            for index, line in enumerate(lines)
        ]

    @staticmethod
    def _cut_head(line, limit):
        """The head cut from its right, the clock first, never into its outcome."""
        if screen.width(line) > limit and re.fullmatch(r" · \d\d:\d\d", line[-1][0]):
            line = line[:-1]
        return screen.cut(line, limit)

    @staticmethod
    def _unnamed(host, text):
        return text[len(host) + 2 :] if text.startswith(host + ": ") else text

    @staticmethod
    def _named(text, vms):
        """The VM a line said on this machine is about: the one it starts with, else the only one it names."""
        for vm in vms:
            if text.startswith((vm + ":", vm + " ")):
                return vm
        named = [vm for vm in vms if re.search(rf"(?<![\w-]){re.escape(vm)}(?![\w-])", text)]
        return named[0] if len(named) == 1 else None

    @staticmethod
    def _flow(head, parts, limit, style=""):
        """head, then parts joined by " · ", wrapped under the column head ends at."""
        lines, line = [], list(head)
        column = screen.width(head)
        for index, part in enumerate(parts):
            piece = part if index == 0 or screen.width(line) == column else " · " + part
            if screen.width(line) + len(piece) > limit and screen.width(line) > column:
                lines.append(line)
                line = [(" " * column, ""), (part, style)]
            else:
                line.append((piece, style))
        if line and not parts:
            line[-1] = (line[-1][0].rstrip(), line[-1][1])
        lines.append(line)
        return lines


class FilterModule:
    """No filters: the engine loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
