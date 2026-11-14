"""A playbook run as a person reads it: its stages, steps and tasks, from the plays it will run and Ansible's events,
every drawn recipe's.

The model reads and draws nothing. The engine (claude_vm.ansible.engine) feeds it the playbook's plays and
Ansible's events, with the time passed in, and claude_vm.tui.run draws it, so every frame is a unit test
(tests/unit/test_run_view.py); docs/architecture/custom-code.md §7.5 says how this works, §7.10 where it lives.

What a run is made of is its recipe's declaration in lib/claude_vm/run/recipes.py, which the title's first word
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
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter, OrderedDict

from ..refs import VmRef
from ..text import one_line as _one_line
from . import recipes


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

# A task tagged as a wait for a person (attention_browser) is one once it has run this long: a skipped one ends first.
WAIT_AFTER = 2.0

# The state a run in parts keeps between its parts: a part never takes over one of another version.
STATE_VERSION = 1
# How much of a failed task's output the run keeps: its last lines, and twice that drawn.
FAILURE_LINES = 20


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


def count_tasks(path, roles=(), depth=0):
    """The tasks a task file runs, as Ansible will start them: a block's children, an import's file's tasks, an
    include as one task (its own file is counted when it is included). 0 for a file that cannot be read."""
    import yaml

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
    return _one_line(text, None)


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
        self.parallel = False  # one of several running at once - pytest's tests under xdist: never closed by another
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
        self.planned = False  # listed by a run in parts before any part reached it: how many tasks it has is unknown
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
        # A step a run in parts has only listed is one task until a part reaches it.
        listed = 1 if self.planned and not self.tasks else 0
        return max(sum(self.static.values()) + self.included + self.extra, len(self.tasks), listed)

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
        self.planned = False  # listed by a run in parts before any part reached it: how many tasks it has is unknown

    def step(self, key):
        if key not in self.steps:
            self.steps[key] = Step(key)
        return self.steps[key]

    @property
    def tasks(self):
        return sorted((task for step in self.steps.values() for task in step.tasks), key=lambda task: task.start)

    @property
    def expected(self):
        # A stage a run in parts has only listed is one task until a part reaches it.
        listed = 1 if self.planned and not self.tasks else 0
        return max(sum(step.expected for step in self.steps.values()), listed)

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

    def __init__(self, title, now, check=False, log="", started_at=None, recipe=None, vms=(), host=""):
        self.title, self.check, self.log = title, check, log
        # The host the run is against, said beside its title where the checkout has more than one: never part of the
        # title, whose first word names the run's declaration.
        self.host = host
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
        self.checklists = OrderedDict()  # stage key: [(done, failed or skipped, the check's text)], as recorded
        self.parts = False  # a part of a run in parts, whose state carries it to the next part
        self.said_info = {}  # stage key: what its row says, from the fact its declaration names
        self.declined = ""  # the line said for an answer that ended the run, not a failure
        self.stopped = False  # a run in parts that Ctrl-C, or anything but a failed part, ended part way
        self.blamed = []  # the VMs a raise on this machine repeated failures of
        self.frame_vms = None  # the run's VMs while a frame is drawn, worked out once for it
        # Everything said above the region, as data, in the order said: what a person reads of the run, which the
        # terminal draws (tui/run.py) and any other renderer can.
        self.reports = []  # a report: its VM, its lines and their marks, and whether it names the VM itself
        self.failures = []  # a failure: where, whom, the task, how long, how, what it said; or causes raised again
        self.rescued = {}  # host: each failure a rescue took, for a later task that raises it again
        self.diffs = []  # a dry run's diffs, as text
        self.warnings = []  # what modules returned as warnings and deprecations, as text

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
        if self.current is not None and not self.current.parallel:
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

    def _ran_steps(self):
        """The steps a run reached: a run whose steps are commands counts them, not the commands - lint's python is
        two."""
        return [
            step
            for stage in self.stages.values()
            for key, step in stage.steps.items()
            if key is not None and step.tasks
        ]

    def unrun(self):
        """The steps a run in parts listed and never reached: a run that ended with none failing but these left was
        stopped between its parts."""
        steps = [
            step.key
            for stage in self.stages.values()
            for step in stage.steps.values()
            if step.planned and not step.tasks
        ]
        return steps + [stage.key for stage in self.stages.values() if stage.planned and not stage.tasks]

    def _vms(self):
        """The run's VMs: those it was given, then those it learned of, then every host it touched but this one and
        those of a stage whose hosts are not VMs (the template's Proxmox host)."""
        if self.frame_vms is not None:
            return self.frame_vms
        hosts = [*self.names]
        hosts += [host for host in self.facts if host not in hosts]
        hosts += sorted({h for t in self.order if self._on_vms(t.stage) for h in t.hosts} - set(hosts))
        return [host for host in hosts if host != "localhost"]

    def _on_vms(self, stage):
        """Whether a stage's hosts are the run's VMs, as its declaration says."""
        spec = self.specs.get(stage)
        return spec is None or spec.vms

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

    def vm_ref(self, host):
        """A VM of the run as the core names one, by its name and VM ID, once its ID is known: None before, and for a
        name or an ID no VM of the project's can have."""
        vmid = self.facts.get(host, {}).get("vmid")
        try:
            return VmRef(name=host, vmid=vmid) if vmid is not None else None
        except ValueError:
            return None

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

    def checklist(self, stage, items):
        """A stage's checks as its playbook has recorded them so far: (passed, skipped, text) each, cleaned."""
        said = []
        for passed, skipped, text in items:
            state = "skipped" if skipped else "done" if passed else "failed"
            said.append((state, one_line(text)))
        self.checklists[stage] = said

    def checks_failed(self, stage):
        """The checks of a stage's checklist that did not pass: a declared raise's causes."""
        return [text for state, text in self.checklists.get(stage, []) if state == "failed"]

    def decline(self, line):
        """An answer that ended the run: said as itself, never as a failure."""
        self.declined = one_line(line)

    def report(self, host, lines, styles, plan=False):
        """A report said above the region: its lines whole, a play on this machine naming the VM in its words and
        anywhere else the VM leading, once."""
        if lines and lines[0].startswith(host + ": "):
            lines = [lines[0][len(host) + 2 :], *lines[1:]]
        named = host != "localhost" and not (lines and lines[0].startswith(host + " "))
        said = {"host": host, "lines": list(lines), "styles": list(styles), "plan": plan, "named": named}
        self.reports.append(said)
        return said

    def failure(self, where, host, who, task, seconds, how, said, log=""):
        """A failed task said above the region: the step, the VM, the task, how long it took, how it failed ("failed",
        "could not be reached"), and what it said."""
        record = {"where": where, "host": host, "who": who, "task": task, "seconds": seconds, "how": how}
        record |= {"said": list(said), "log": log}
        self.failures.append(record)
        return record

    def raised_again(self, causes, own, log=""):
        """A failure that raises again what rescues took: each cause, then its own words but those that repeat one."""
        record = {"causes": [dict(cause) for cause in causes], "own": list(own), "log": log}
        self.failures.append(record)
        return record

    def rescue(self, host, kept):
        """A failure a rescue took, or an unreachable result a task ignored, kept for a task that may raise it."""
        self.rescued.setdefault(host, []).append(kept)

    def diff(self, text):
        self.diffs.append(text)

    def warning(self, text):
        self.warnings.append(text)

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

    def planned_stages(self, stages):
        """The stages a run in parts will run, in order: each listed to come until a part reaches it, and counted as
        one task till then."""
        for key in stages:
            stage = self._stage(key)
            stage.planned = stage.planned or not stage.tasks

    def planned(self, stage, steps):
        """The steps of stage a run in parts will run, in order: each listed to come until a part reaches it, and
        counted as one task till then, so that no percentage reads whole while one is still to come."""
        for step in steps:
            planned = self._stage(stage).step(step)
            planned.planned = planned.planned or not planned.tasks

    def part(self, stage, step, kind="part"):
        """A part of a run whose reader makes its tasks - pytest's tests, Molecule's actions - in stage's step, begun
        as a play is: its key, kind first."""
        key = f"{kind}:{stage}:{step}:{len(self.order)}"
        self.play = key
        self.play_stage[key] = (stage, step)
        self.batch += 1
        self.started = {}
        return key

    def expect(self, stage, step, count):
        """How many tasks a part's step will reach, once its reader knows - pytest's tests, once collected - each it
        reaches from then on counted against it."""
        target = self._stage(stage).step(step)
        target.included, target.extra = max(0, int(count)), 0
        target.budget = max(0, target.included - len(target.tasks))

    def parallel(self, key, name, now):
        """A task that runs beside others - a test under xdist - started without ending the one running."""
        stage, override = self.play_stage.get(self.play, ("run", None))
        task = Task(key, name, stage, override, now)
        task.batch, task.parallel = self.batch, True
        self.shown_stage = stage
        self._stage(stage).step(task.step).reach(task)
        task.host_start("localhost", now)
        self.current = task
        self.order.append(task)
        return task

    def command(self, stage, step, name, now):
        """A command a run runs as a part of its own - one of just lint's checks, just test's pytest - as one task in
        stage's step, on this machine."""
        key = self.part(stage, step, "command")
        task = self.task(key, name, None, now)
        self.host_start(key, "localhost", now)
        return task

    # --- a run in parts ---------------------------------------------------------------------------------------
    def dump(self, now, next_records=None, failed_hosts=(), stopped=False):
        """What a later part of a run in parts, and its end, need of this part and the ones before it: every task,
        step, report and record, each time from the run's start, and what NEXT STEPS and the head will say -
        next_records, the parts' NEXT STEPS records merged, {stat: {vm: {matter: line}}}, and stopped, whether
        Ctrl-C or a signal ended this part. Every value in it was cleaned when the run took it in."""
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
                    "planned": stage.planned,
                    "steps": [
                        {
                            "key": step.key,
                            "expected": step.expected if step.tasks or not step.planned else 0,
                            "planned": step.planned,
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
            "checklists": [[stage, [list(item) for item in items]] for stage, items in self.checklists.items()],
            "stopped": bool(stopped),
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
            stage.planned = bool(saved.get("planned"))
            for saved_step in saved["steps"]:
                step = stage.step(saved_step["key"])
                step.tasks = [tasks[n] for n in saved_step["tasks"]]
                # Counted as it was: what a later task of the same step reaches is counted beyond it.
                step.extra = int(saved_step["expected"])
                step.planned = bool(saved_step.get("planned"))
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
        self.checklists = OrderedDict((stage, [tuple(item) for item in items]) for stage, items in state["checklists"])

    @staticmethod
    def _named(text, vms):
        """The VM a line said on this machine is about: the one it starts with, else the only one it names."""
        for vm in vms:
            if text.startswith((vm + ":", vm + " ")):
                return vm
        named = [vm for vm in vms if re.search(rf"(?<![\w-]){re.escape(vm)}(?![\w-])", text)]
        return named[0] if len(named) == 1 else None
