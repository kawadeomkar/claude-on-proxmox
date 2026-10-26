"""`just deploy`'s live output: the run as stages, steps and tasks, and what is drawn from it.

Loaded by path by callback_plugins/claude_on_proxmox.py, which feeds a Run the playbook's plays and Ansible's
events; everything else here is pure, with the time passed in, so every frame is a unit test
(tests/unit/test_deploy_view.py). docs/guides/runs.md shows what a person sees, and docs/architecture/custom-code.md
§7.5 how it works.

A run is stages - template, provision, configure, sign in, VS Code - each a playbook deploy imports, named by a
table of playbook files (STAGE_FILES). A play from a file the table does not name - discover.yml, which configure
and each sign-in import, or deploy.yml's own - takes the stage of the play after it, else of the one before. Within
a stage, a task's step is its role, or for the sign-ins the sign-in; tasks outside any role are the stage's own.

How far a step is: the tasks it has reached, skipped ones included, of the tasks it will reach - those the plays
compile to (counted when the playbook starts, by task ID), those each include announces (counted from its file when
it is included), and any it reaches beyond them (a serial play's next batch, a handler). A step that has ended is
whole: what it did not reach, a rescue that never ran, was never left to do.

What is drawn: the live frame (stages, the running stage's steps, the running step's last five tasks with a timer
on the one running, and a footer), a line as each step ends for output that is not a terminal, and the summary at
the end. Nothing here prints; the callback does.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict

import yaml

# Each playbook deploy imports, as (stage, step): a step named here is the sign-in's, for all its plays' tasks.
STAGE_FILES = {
    "template.yml": ("template", None),
    "provision.yml": ("provision", None),
    "configure.yml": ("configure", None),
    "ssh_config.yml": ("SSH aliases", None),
    "claude_login.yml": ("sign in", "Claude"),
    "github_login.yml": ("sign in", "GitHub"),
    "tailscale_login.yml": ("sign in", "Tailscale"),
    "code.yml": ("VS Code", None),
}
# How bad an outcome is, for a task several VMs ran: the worst of them is the task's.
RANK = {"skipped": 0, "included": 1, "ok": 2, "changed": 3, "ignored": 4, "rescued": 4, "failed": 5, "unreachable": 5}
SGR = {"b": "1", "d": "90", "h": "1", "g": "32", "y": "33", "r": "31", "c": "36", "m": "35", "w": "1;93"}
WINDOW = 5


def stages_of(files):
    """Each play's (stage, step), from the file of its playbook; one the table does not name takes a neighbour's."""
    known = [STAGE_FILES.get(os.path.basename(str(f).split(":", 1)[0])) for f in files]
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


# --- text ------------------------------------------------------------------------------------------------------
def width(runs):
    return sum(len(text) for text, _ in runs)


def cut(runs, limit):
    """runs cut to limit characters, the last kept one an ellipsis when anything was cut."""
    out, used = [], 0
    for text, style in runs:
        if used + len(text) <= limit:
            out.append((text, style))
            used += len(text)
            continue
        room = limit - used
        if room > 0:
            out.append((text[: room - 1] + "…", style))
        return out
    return out


def right(left, tail, limit):
    """left, then tail aligned to the right edge at limit; without room for both, tail is dropped and left cut."""
    gap = limit - width(left) - width(tail)
    if tail and gap >= 1:
        return [*left, (" " * gap, ""), *tail]
    return cut(left, limit)


def ansi(runs, color):
    if not color:
        return "".join(text for text, _ in runs)
    return "".join(f"\x1b[{SGR[style]}m{text}\x1b[0m" if style in SGR and text else text for text, style in runs)


def fmt(seconds):
    """A duration as the report says it: 0.6 s, 33 s, 3:58."""
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 60:
        return f"{round(seconds)} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def clock(seconds):
    minutes, rest = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{rest:02d}"


def bar(fraction, style, cells=20):
    filled = max(0, min(cells, round(fraction * cells)))
    return [("█" * filled, style), ("░" * (cells - filled), "d")]


def wrap(text, limit, lines):
    out = [""]
    for word in text.split():
        if not out[-1]:
            out[-1] = word
        elif len(out[-1]) + 1 + len(word) <= limit:
            out[-1] += " " + word
        elif len(out) < lines:
            out.append(word)
        else:
            out[-1] = (out[-1] + " " + word)[: limit - 1] + "…"
            break
    return [line for line in out if line]


def one_line(text):
    return " ".join(str(text).split())


def tasks_said(count):
    return f"{count} task" if count == 1 else f"{count} tasks"


# --- the run ---------------------------------------------------------------------------------------------------
class Task:
    def __init__(self, key, name, stage, step, start):
        self.key, self.name, self.stage, self.step, self.start = key, name, stage, step, start
        self.end = None
        self.hosts = OrderedDict()  # host: [start, end, outcome]
        self.outcome = ""
        self.wait = None  # (since, seconds it may take), while the task waits for a person

    def host_start(self, host, now):
        self.hosts.setdefault(host, [now, None, ""])

    def host_end(self, host, outcome, now):
        entry = self.hosts.setdefault(host, [self.start, None, ""])
        entry[1], entry[2] = now, outcome
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
        return self.seconds(now) if self.wait else 0.0


class Step:
    """A stage's step - a role, a sign-in, or the stage's own tasks (key None) - and how far it is."""

    def __init__(self, key):
        self.key = key
        self.static = set()
        self.budget = 0
        self.included = 0
        self.extra = 0
        self.seen = set()
        self.tasks = []

    def expect(self, task_key):
        self.static.add(task_key)

    def announce(self, count):
        self.budget += count
        self.included += count

    def reach(self, task):
        if task.key in self.static and task.key not in self.seen:
            self.seen.add(task.key)
        elif self.budget > 0:
            self.budget -= 1
        else:
            self.extra += 1
        self.tasks.append(task)

    @property
    def expected(self):
        return max(len(self.static) + self.included + self.extra, len(self.tasks))

    @property
    def reached(self):
        return len(self.tasks)

    def start(self):
        return self.tasks[0].start if self.tasks else None

    def end(self, now):
        return max((task.end if task.end is not None else now) for task in self.tasks) if self.tasks else None

    def seconds(self, now):
        return (self.end(now) - self.start()) if self.tasks else 0.0

    def changed(self):
        return sum(task.outcome == "changed" for task in self.tasks)

    def failed(self):
        return sum(task.outcome in ("failed", "unreachable") for task in self.tasks)

    def waited(self, now):
        return sum(task.waited(now) for task in self.tasks)


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


class Run:
    """One just deploy, from the plays it will run and the events Ansible sends."""

    def __init__(self, title, now, check=False, log="", started_at=None):
        self.title, self.check, self.log = title, check, log
        self.begun = now
        self.started_at = started_at if started_at is not None else time.time()
        self.stages = OrderedDict()
        self.play_stage = {}  # play key: (stage, step)
        self.play = None
        self.current = None  # the task running now
        self.order = []  # every task, in the order started
        self.plan = []  # the run's own first words: what it will do
        self.notes = []  # (host, text)
        self.facts = OrderedDict()  # host: {vmid, address, new}
        self.finished = None
        self.said = set()  # steps and stages a line was printed for (output that is not a terminal)

    # --- what the playbook is ---------------------------------------------------------------------------------
    def plays(self, plays):
        """plays: (key, playbook file, [(task key, role or None)...]) in order, as the playbook lists them."""
        for (key, _, tasks), (stage, override) in zip(plays, stages_of([f for _, f, _ in plays]), strict=True):
            self.play_stage[key] = (stage, override)
            for task_key, role in tasks:
                self._stage(stage).step(override or role).expect(task_key)

    def _stage(self, key):
        if key not in self.stages:
            self.stages[key] = Stage(key)
        return self.stages[key]

    def play_start(self, key, name="", path=""):
        if key not in self.play_stage:
            # A play the playbook did not list: its own stage, from its file or name.
            self.play_stage[key] = stages_of([path])[0] if path else (name or "run", None)
        self.play = key

    # --- the events -------------------------------------------------------------------------------------------
    def task(self, key, name, role, now):
        stage, override = self.play_stage.get(self.play, ("run", None))
        if self.current is not None:
            self.current.close(now)
        task = Task(key, name, stage, override or role, now)
        self._stage(stage).step(task.step).reach(task)
        self.current = task
        self.order.append(task)
        return task

    def include(self, role, count):
        stage, override = self.play_stage.get(self.play, ("run", None))
        self._stage(stage).step(override or role).announce(count)

    def find(self, key):
        if self.current is not None and self.current.key == key:
            return self.current
        return next((task for task in reversed(self.order) if task.key == key), None)

    def host_start(self, key, host, now):
        task = self.find(key)
        if task is not None:
            task.host_start(host, now)

    def result(self, key, host, outcome, now):
        task = self.find(key)
        if task is not None:
            task.host_end(host, outcome, now)
        return task

    def retry(self, key, host, now, seconds):
        """A wait for a person: True the first time, for the bell."""
        task = self.find(key)
        if task is None or task.wait is not None:
            return False
        task.wait = (now, seconds)
        return True

    def note(self, host, text, plan=False):
        """A report: plan is the run's own first words, what it will do, which the header shows."""
        text = one_line(text)
        if not text:
            return
        if plan:
            self.plan.append(text)
        else:
            self.notes.append((host, text))

    def fact(self, host, name, value):
        entry = self.facts.setdefault(host, {})
        if name == "proxmox_vm_resolved_id":
            entry["vmid"] = value
        elif name == "proxmox_vm_address":
            entry["address"] = value
        elif name == "proxmox_vm_exists":
            entry.setdefault("new", not value)

    def end(self, now):
        if self.current is not None:
            self.current.close(now)
        self.finished = now

    # --- where it is ------------------------------------------------------------------------------------------
    def running_stage(self):
        return self.current.stage if self.current is not None and self.finished is None else None

    def _state(self, stage):
        """done, running, waiting or to come."""
        if not stage.tasks:
            return "to come"
        if self.finished is None and self.current is not None and self.current.stage == stage.key:
            return "waiting" if self.current.wait and self.current.end is None else "running"
        return "done"

    def _step_state(self, stage, step):
        if not step.tasks:
            return "to come"
        running = self.finished is None and self.current is not None and self.current.stage == stage.key
        if running and self.current.step == step.key:
            return "waiting" if self.current.wait and self.current.end is None else "running"
        return "done"

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

    # --- the live frame ---------------------------------------------------------------------------------------
    def frame(self, now, cols, rows, glyph, color=True):
        """The region's lines: each cut to cols - 1, and few enough for rows - 1 where folding can make them so."""
        limit = cols - 1
        tiers = {"changed": cols >= 80, "counts": cols >= 64, "bars": cols >= 52}
        head = self._header(now, limit)
        body = self._stages(now, limit, glyph, tiers, fold=0)
        for fold in (1, 2, 3):
            if len(head) + len(body) + 2 <= rows - 1:
                break
            body = self._stages(now, limit, glyph, tiers, fold=fold)
        lines = [*head, *body, [], self._footer(now, limit, glyph)]
        return [ansi(cut(line, limit), color) for line in lines]

    def _header(self, now, limit):
        started = time.strftime("%H:%M", time.localtime(self.started_at))
        first = [("claude-on-proxmox", "b"), (f"  {self.title} · started {started}", "")]
        if self.check:
            first.append((" · a dry run: nothing is changed", "y"))
        lines = [first]
        plan = " ".join(self.plan)
        lines += [[("  ", ""), (line, "d")] for line in wrap(plan, limit - 2, 2)]
        lines.append([])
        return lines

    def _row(self, indent, glyph, label, label_width, step, state, now, tiers, info="", limit=99):
        """One stage's or step's line."""
        style = {"done": "g", "running": "c", "waiting": "y", "to come": "d"}[state]
        if step.failed() and state != "to come":
            glyph, style = "✗", "r"
        if state == "to come":
            count = tasks_said(step.expected) if step.expected else ""
            return cut([(" " * indent, ""), ("○ ", "d"), (label.ljust(label_width), "d"), (count, "d")], limit)
        glyph = {"done": "✓", "waiting": "⏸"}.get(state, glyph) if style != "r" else glyph
        left = [(" " * indent, ""), (glyph + " ", style), (label.ljust(label_width), "" if state == "done" else "b")]
        # A step that ended is whole, whatever it would have reached: a rescue that never ran is no task left.
        total = step.reached if state == "done" else max(step.expected, step.reached)
        if tiers["bars"]:
            left += bar(step.reached / total if total else 1, "g" if state == "done" else style)
        if tiers["counts"]:
            left.append((f"  {step.reached:>3}/{total:<4}", ""))
        if tiers["changed"]:
            left.append((f" {self._changed_said(step.changed()):<15}", "y" if step.changed() else ""))
        if info:
            left.append((f" {info}", "d"))
        seconds = step.seconds(now)
        tail = [(fmt(seconds), "")] if state == "done" else [(clock(seconds), "")]
        return right(left, tail, limit)

    def _window(self, tasks, now, glyph, limit):
        """The step's last five tasks, newest at the bottom: the running one with a timer, a skipped one dim."""
        lines = []
        shown = tasks[-WINDOW:]
        width_ = min(limit, 88) - 9
        for task in shown:
            running = task.end is None and self.finished is None and task is self.current
            if running:
                mark, style = ("⏸", "y") if task.wait else (glyph, "c")
                tail = self._hosts_said(task, now, glyph, width_ // 2) or [(clock(task.seconds(now)), style)]
                name = [(task.name, "b")]
            elif task.outcome == "skipped":
                mark, style, name, tail = "–", "d", [(task.name, "d")], [("skipped", "d")]
            else:
                mark, style = {
                    "changed": ("●", "y"),
                    "failed": ("✗", "r"),
                    "unreachable": ("✗", "r"),
                    "rescued": ("↺", "d"),
                    "ignored": ("↺", "d"),
                    "included": ("↳", "d"),
                }.get(task.outcome, ("✓", "g"))
                name = [(task.name, "d" if task.outcome in ("rescued", "ignored", "included") else "")]
                tail = [(fmt(task.seconds(now)), "d")]
            room = width_ - 2 - width(tail) - 2
            lines.append(right([(" " * 9, ""), (mark + " ", style), *cut(name, room)], tail, 9 + width_))
        return lines

    def _hosts_said(self, task, now, glyph, room):
        """For a task several VMs run: each VM's state, the running ones with their timer; in room characters, or
        as counts when the VMs do not fit."""
        if len(task.hosts) < 2:
            return []
        parts = []
        for host, (start, end, outcome) in task.hosts.items():
            if end is None:
                parts += [(f"{host} ", ""), (glyph, "c"), (f" {clock(now - start)}", "c")]
            else:
                mark = "✗" if outcome in ("failed", "unreachable") else "✓"
                parts += [(f"{host} ", ""), (mark, "r" if mark == "✗" else "g"), (f" {fmt(end - start)}", "d")]
            parts.append((" · ", "d"))
        parts = parts[:-1]
        if width(parts) <= room:
            return parts
        ended = [entry for entry in task.hosts.values() if entry[1] is not None]
        failed = sum(outcome in ("failed", "unreachable") for _, _, outcome in ended)
        running = [start for start, end, _ in task.hosts.values() if end is None]
        said = [(f"{len(ended)}/{len(task.hosts)} VMs ", "")]
        if failed:
            said.append((f"{failed} failed ", "r"))
        if running:
            said += [(glyph, "c"), (f" {clock(now - min(running))}", "c")]
        return said

    def _stages(self, now, limit, glyph, tiers, fold):
        lines = []
        pending = []
        for stage in self.stages.values():
            state = self._state(stage)
            if state == "to come" and fold >= 2:
                pending.append(stage.key)
                continue
            if state == "waiting":
                task = self.current
                since, seconds = task.wait
                step = f"{task.step}: " if task.step and stage.key == "sign in" else ""
                said = (
                    f"{step}waiting for you in the browser · {', '.join(task.hosts) or ''} · {clock(now - task.start)}"
                )
                if seconds:
                    said += f" of up to {clock(seconds + (since - task.start))}"
                lines.append(cut([(" ", ""), ("⏸ ", "y"), (stage.key.ljust(13), "b"), (said, "y")], limit))
            else:
                info = self._vm_info() if stage.key == "provision" and state != "to come" else ""
                if state == "done" and not info and not stage.changed() and not stage.failed():
                    info = "no changes"
                lines.append(self._row(1, glyph, stage.key, 13, stage, state, now, tiers, info, limit))
            if state not in ("running", "waiting"):
                continue
            own = stage.steps.get(None)
            running_step = self.current.step if self.current is not None else None
            if running_step is None and own is not None:
                lines += self._window(own.tasks, now, glyph, limit)
            later = []
            for key, step in stage.steps.items():
                if key is None:
                    continue
                step_state = self._step_state(stage, step)
                if step_state == "to come" and fold >= 1:
                    later.append(key)
                    continue
                lines.append(self._row(5, glyph, key, 18, step, step_state, now, tiers, "", limit))
                if step_state in ("running", "waiting"):
                    lines += self._window(step.tasks, now, glyph, limit)[-(1 if fold >= 3 else WINDOW) :]
            if later:
                lines.append(cut([(" " * 5, ""), ("○ ", "d"), (f"then {', '.join(later)}", "d")], limit))
        if pending:
            lines.append(cut([(" ", ""), ("○ ", "d"), (f"then {', '.join(pending)}", "d")], limit))
        return lines

    def _footer(self, now, limit, glyph):
        waiting = self.current is not None and self.current.wait and self.current.end is None
        keys = list(self.stages)
        running = self.running_stage()
        where = f"stage {keys.index(running) + 1} of {len(keys)}" if running in keys else ""
        runs = [("⏸ " if waiting else glyph + " ", "y" if waiting else "c"), (clock(now - self.begun), "b")]
        if where:
            runs.append((f" · {where}", "d"))
        if waiting:
            runs.append((" · waiting for you", "y"))
        if self.log:
            runs.append((f" · {self.log}", "d"))
        return cut(runs, limit)

    def title_text(self, now):
        """The terminal's title: the run, and where it is."""
        if self.finished is not None:
            return f"{self.title} · done"
        if self.current is not None and self.current.wait and self.current.end is None:
            return f"{self.title} · waiting for you"
        stage = self.running_stage()
        if stage is None:
            return self.title
        stages = self.stages.values()
        expected = sum(s.reached if self._state(s) == "done" else max(s.expected, s.reached) for s in stages)
        reached = sum(s.reached for s in stages)
        return f"{self.title} · {stage} {round(100 * reached / expected) if expected else 0}%"

    # --- output that is not a terminal --------------------------------------------------------------------------
    def ended_lines(self, now):
        """A line for each step and stage that has ended since the last call, for output that is not a terminal."""
        out = []
        for stage in self.stages.values():
            if not stage.tasks:
                continue
            # A stage that is one step, a sign-in, is said once: by its step's name.
            only = list(stage.steps) if len(stage.steps) == 1 and None not in stage.steps else []
            for key, step in stage.steps.items():
                if key is None or only or (stage.key, key) in self.said or self._step_state(stage, step) != "done":
                    continue
                self.said.add((stage.key, key))
                out.append(self._ended(f"{stage.key} / {key}", step, now))
            if stage.key not in self.said and self._state(stage) == "done":
                self.said.add(stage.key)
                out.append(self._ended(f"{stage.key} / {only[0]}" if only else stage.key, stage, now))
        return out

    def _ended(self, label, step, now):
        mark = "✗" if step.failed() else "✓"
        said = [tasks_said(step.reached)]
        if step.changed():
            said.append(self._changed_said(step.changed()))
        if step.failed():
            said.append(f"{step.failed()} failed")
        return f"{mark} {label.ljust(28)} {' · '.join(said):<36} {fmt(step.seconds(now))}"

    # --- the summary ------------------------------------------------------------------------------------------
    def summary(self, now, cols, color=True, failed_hosts=()):
        limit = cols - 1
        total = now - self.begun
        minutes, seconds = divmod(round(total), 60)
        took = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
        ended = time.strftime("%H:%M", time.localtime(self.started_at + total))
        lines = []
        if failed_hosts:
            head = f"  {self.title} · stopped after {took} on {', '.join(failed_hosts)} · {ended}"
            lines.append([("claude-on-proxmox", "b"), (head, "r")])
        else:
            lines.append([("claude-on-proxmox", "b"), (f"  {self.title} · done in {took} · {ended}", "")])
        lines.append([])
        changed_head = "WOULD CHANGE" if self.check else "CHANGED"
        lines.append([(f"{'STAGES':<14}{'TIME':>6}{'TASKS':>9}  {changed_head}", "h")])
        for stage in self.stages.values():
            if not stage.tasks:
                continue
            mark, style = ("✗", "r") if stage.failed() else ("✓", "g")
            head = [(" ", ""), (mark + " ", style), (stage.key[:11].ljust(11), "b")]
            head.append((f"{fmt(stage.seconds(now)):>6}{stage.reached:>9}{(stage.changed() or '—'):>9}   ", ""))
            parts = []
            for key, step in stage.steps.items():
                if key is None or not step.tasks:
                    continue
                part = f"{key} {fmt(step.seconds(now))}"
                if step.waited(now):
                    part += f" ({fmt(step.waited(now))} yours)"
                parts.append(part)
            if stage.key == "provision" and self._vm_info():
                parts.insert(0, self._vm_info())
            lines += self._flow(head, parts, limit)
        notes = OrderedDict()
        vms = [host for host in self.facts] + sorted({h for t in self.order for h in t.hosts} - set(self.facts))
        for host, text in self.notes:
            # What a play on this machine said about a VM - its address, its alias - is that VM's.
            if host == "localhost":
                host = next((vm for vm in vms if vm != "localhost" and text.startswith((vm + ":", vm + " "))), host)
            if text.startswith(host + ": "):
                text = text[len(host) + 2 :]
            notes.setdefault(host, [])
            if text not in notes[host]:
                notes[host].append(text)
        for host, texts in notes.items():
            facts = self.facts.get(host, {})
            lines.append([])
            said = " · ".join(
                part for part in (facts.get("address", ""), f"VM {facts['vmid']}" if facts.get("vmid") else "") if part
            )
            heading = "THIS MACHINE" if host == "localhost" else host.upper()
            new = " · new" if facts.get("new") else ""
            lines.append([(heading, "h"), (f"  {said}{new}" if said else "", "")])
            lines += [cut([(" ", ""), (text, "")], limit) for text in texts[-8:]]
        slowest = sorted(
            (task for task in self.order if not task.wait), key=lambda task: task.seconds(now), reverse=True
        )
        if slowest:
            lines.append([])
            parts = [f"{task.name} {fmt(task.seconds(now))}" for task in slowest[:5]]
            lines += self._flow([("SLOWEST", "h"), ("  ", "")], parts, limit)
        lines.append([])
        changed = sum(task.outcome == "changed" for task in self.order)
        failed = sum(task.outcome in ("failed", "unreachable") for task in self.order)
        yours = sum(task.waited(now) for task in self.order)
        totals = [tasks_said(len(self.order)), self._changed_said(changed) or "nothing changed", f"{failed} failed"]
        if yours:
            totals.append(f"{fmt(yours)} of it yours")
        if self.log:
            totals.append(f"log {self.log}")
        lines += self._flow([], totals, limit, style="d")
        return [ansi(cut(line, limit), color) for line in lines]

    @staticmethod
    def _flow(head, parts, limit, style=""):
        """head, then parts joined by " · ", wrapped under the column head ends at."""
        lines, line = [], list(head)
        column = width(head)
        for index, part in enumerate(parts):
            piece = part if index == 0 or width(line) == column else " · " + part
            if width(line) + len(piece) > limit and width(line) > column:
                lines.append(line)
                line = [(" " * column, ""), (part, style)]
            else:
                line.append((piece, style))
        if line and not parts:
            line[-1] = (line[-1][0].rstrip(), line[-1][1])
        lines.append(line)
        return lines


class FilterModule:
    """No filters: the stdout callback loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
