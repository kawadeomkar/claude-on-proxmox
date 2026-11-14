"""A run drawn, from its model (claude_vm.run.model): every drawn recipe's.

Lines are drawn with screen.py and put on the screen by live_region.py, as `just status`'s report is
(docs/architecture/custom-code.md §7.8); docs/guides/runs.md shows what a person sees, and §7.5 how this works.

What is drawn: the live frame (the stages, the running stage's steps, the running step's last tasks with a timer on
the one running, and a footer; a run of one stage drawn as its steps), a line as each step ends for output that is
not a terminal, and the summary at the end. Nothing here prints; the engine does.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict

from ..run import model, recipes
from ..run.model import one_line
from . import screen

# A bar's cells, in a stage's or a step's row.
BAR = 20

# SLOWEST lists only tasks that took this long.
SLOW = 1.0

# How many failures a summary that lists them names before it counts the rest.
FAILURES_LISTED = 10

# How a state is marked - a VM's in a step's row and in the summary, a stage's or a step's, a check's, a run's end -
# and in what style; running's mark is the spinner's glyph.
MARKS = {
    "running": ("", "c"),
    "waiting": ("⏸", "y"),
    "done": ("✓", "g"),
    "skipped": ("–", "d"),
    "failed": ("✗", "r"),
    "unreachable": ("✗", "r"),
    "left": ("–", "d"),
    "to come": ("○", "d"),
    "stopped": ("■", "y"),
}


def first_sentence(text):
    """text up to the end of its first sentence, or whole when it has one."""
    found = re.match(r"(.+?[.!?])(\s+\S)", text)
    return found.group(1) if found else text


def _end_mark(part):
    """How a step or a stage ended, as its mark and style: failed, stopped by Ctrl-C, skipped, or done."""
    if part.failed():
        return MARKS["failed"]
    if any(task.outcome == "stopped" for task in part.tasks):
        return MARKS["stopped"]
    return MARKS["skipped" if part.skipped() else "done"]


class Run(model.Run):
    """A run's model, drawn: its frame, a line as each step ends, and the summary, from the model's state alone."""

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
        """A run of one stage that has steps is drawn as its steps, with no row of its own - but never a part of a run
        in parts whose declaration has more stages, whose later parts draw them all."""
        if self.parts and sum(not stage.quiet for stage in self.recipe.stages) > 1:
            return False
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
            ref = self.vm_ref(host)
            parts = [f"VM {ref.vmid}" if ref else "", facts.get("address", "")]
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
        if len(vms) < 2 or not step.tasks or not self._on_vms(stage_key):
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
        counts = self.recipe.counts
        tiers = {"changed": cols >= 80, "counts": counts and cols >= 64, "bars": counts and cols >= 52}
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

    def _on(self):
        """The run's host, as the header and the summary say it: nothing with one host."""
        return f" · {self.host}" if self.host else ""

    def _header(self, now, limit):
        # The plan is printed whole above the region, as every report is: here it could only be cut.
        started = time.strftime("%H:%M", time.localtime(self.started_at))
        first = [("agent-warehouse", "b"), (f"  {self.title}{self._on()} · started {started}", "")]
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
        mark, style = MARKS[state]
        if state == "to come":
            known = step.expected and not getattr(step, "planned", False)
            count = screen.plural(step.expected, "task") if known else ""
            said = label.ljust(label_width) + count if count else label
            return screen.cut([(" " * indent, ""), (f"{mark} ", style), (said, style)], limit)
        if step.failed():
            mark, style = MARKS["failed"]
        glyph = mark or glyph
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
        # Tasks running side by side - pytest's under xdist - are the window while they run, the longest running first.
        running = [task for task in tasks if task.parallel and task.end is None]
        if running and self.finished is None:
            shown = sorted(running, key=lambda task: task.start)[: self.recipe.window]
        width_ = min(limit, 88) - indent
        fleet = self._fleet()
        for task in shown:
            running = task.end is None and self.finished is None and (task is self.current or task.parallel)
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
                mark, style = MARKS["failed" if outcome in ("failed", "unreachable") else "done"]
                parts += [(f"{host} ", ""), (mark, style), (f" {screen.duration(end - start)}", "d")]
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
            lines += self._checks(stage.key, limit, 1 if flat else 5, 5 if fold else None)
            lines += self._steps(stage, now, limit, glyph, tiers, fold, flat, vms)
        if pending:
            lines.append(screen.cut([(" ", ""), ("○ ", "d"), (f"then {', '.join(pending)}", "d")], limit))
        return lines

    def _checks(self, stage, limit, indent, last=None):
        """A stage's checklist, as recorded so far: each check passed, failed or not made; the last ones when folded,
        and every one that failed."""
        items = self.checklists.get(stage, [])
        if last is not None and len(items) > last:
            items = [item for item in items[:-last] if item[0] == "failed"] + items[-last:]
        lines = []
        for state, text in items:
            mark, style = MARKS[state]
            lines.append(
                screen.cut([(" " * indent, ""), (f"{mark} ", style), (text, "d" if state == "skipped" else "")], limit)
            )
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
        where = stage
        if self._flat() and self.current is not None and self.current.step is not None:
            where = self.current.step
        # A stage a run in parts has listed and not begun has no size yet - just test's unit, its tests uncounted - and
        # a percentage that could not fall would read whole by its first stage's end: none until each has begun.
        if any(listed.planned and not listed.tasks for listed in stages):
            return f"{self.title} · {where}"
        expected = sum(self._whole(s) for s in stages)
        reached = sum(s.reached for s in stages)
        percent = round(100 * self._clamped("title", reached / expected if expected else 0))
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
        mark, _ = _end_mark(step)
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
        # The clock it ended at, last: what a head too long for the width loses first (_cut_head).
        tail = (f" · {time.strftime('%H:%M', time.localtime(self.started_at + total))}", "")
        failed = self.failed_vms(failed_hosts)
        everyone = self._vms()
        if self.declined:
            said, style = f"declined · {took}", "y"
        elif self.stopped:
            # Where the run was: the step, or the stage, of the last task it started.
            last = self.order[-1] if self.order else None
            where = f" at {last.step or last.stage}" if last is not None else ""
            said, style = f"stopped after {took}{where}", "y"
        elif not failed_hosts and not self.failed:
            said, style = f"done in {took}", ""
        elif self.reached_last and len(everyone) >= 2 and len([vm for vm in failed if vm in everyone]) < len(everyone):
            said, style = f"done in {took}", ""
            tail = (f" · {screen.plural(len(failed), 'VM')} failed: {', '.join(failed)}", "r")
        else:
            where = f" at {self.first_failure}" if self.first_failure else ""
            said, style = f"failed after {took}{where}", "r"
        return [("agent-warehouse", "b"), (f"  {self.title}{self._on()} · {said}", style), tail]

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
            mark, style = _end_mark(part)
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
        # A run that failed or stopped part way says what it never reached: the steps, or the stages, still to come.
        if self.failed or failed_hosts:
            if flat:
                never = [key for key, step in stage.steps.items() if key is not None and not step.tasks]
            else:
                never = [stage_.key for stage_ in self._shown() if not stage_.tasks]
            if never:
                lines += self._flow([(" not run  ", "d")], never, limit, style="d")
        # What failed, by name, where the declaration lists them: just test's tests, which scroll away above.
        failures = [task for task in self.order if task.outcome in ("failed", "unreachable")]
        if self.recipe.failures and failures:
            lines += [[], [("FAILED", "h")]]
            for task in failures[:FAILURES_LISTED]:
                lines.append(screen.cut([(" ✗ ", "r"), (f"{task.step or task.stage}  ", "d"), (task.name, "")], limit))
            if len(failures) > FAILURES_LISTED:
                lines.append([(f" and {len(failures) - FAILURES_LISTED} more", "d")])
        # Each checklist whole: a check a person must fix is never only in the window.
        for stage_key, items in self.checklists.items():
            if items:
                lines += [[], [(str(stage_key).upper(), "h")], *self._checks(stage_key, limit, 1)]
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
            ref = self.vm_ref(host)
            said_facts = " · ".join(
                part for part in (facts.get("address", ""), f"VM {ref.vmid}" if ref else "") if part
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
            # A run whose steps are commands counts them as its checks, and says nothing of changes it never makes.
            screen.plural(
                len(self.order) if self.recipe.counts else len(self._ran_steps()),
                "task" if self.recipe.counts else self.recipe.noun,
            ),
            *([self._changed_said(changed) or "nothing changed"] if "changed" in self.recipe.columns else []),
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


# --- what the run says above the region, drawn from the model's records ----------------------------------------
def wrapped(head, lines, width, color, style="", styles=None):
    """head, then lines wrapped under where head ends, whole - never cut: (coloured, plain) pairs to print. A line
    keeps its own indent, its wrapped pieces under where its words start; styles, one for each line, colour each its
    own way."""
    column = screen.width(head)
    room = max(20, width - 1 - column)
    out = []
    first = True
    for number, line in enumerate(lines):
        style_ = (styles[number] if styles else "") or style
        indent = len(line) - len(line.lstrip(" "))
        pieces = screen.fill(line.strip(), max(10, room - indent)) if line.strip() else [""]
        for piece in pieces:
            lead = head if first else [(" " * column, "")]
            first = False
            runs = [*lead, (" " * indent + piece, style_)]
            out.append((screen.render(runs, color), screen.render(runs, False)))
    return out


def report_lines(report, width, color):
    """A report, whole: under its VM, which leads unless the report names it itself."""
    who = [(report["host"], "b"), ("  ", "")] if report["named"] else []
    return wrapped([("  ", ""), *who], report["lines"], width, color, styles=report["styles"])


def _block(where, who, task, took, how, said, width, color):
    out = [("", "")]
    out += wrapped([("✗ ", "r")], [f"{where} · {who}: {task}{took}{how}"], width, color, "r")
    out += wrapped([("    ", "")], [line for line in said if line.strip()][: model.FAILURE_LINES * 2], width, color)
    return out


def failure_lines(failure, width, color):
    """A failure, for good above the region: the step, the VM, the task, what it said, and where the log has it; a
    raise of earlier failures, each cause so, then its own words."""
    if "causes" in failure:
        out = []
        for cause in failure["causes"]:
            out += _block(cause["where"], cause["who"], cause["name"], "", "", cause["said"], width, color)
        if failure["own"]:
            out += wrapped([("    ", "")], failure["own"], width, color)
    else:
        took = f" ({screen.duration(failure['seconds'])})" if failure["seconds"] is not None else ""
        how = "" if failure["how"] == "failed" else f" · {failure['how']}"
        out = _block(failure["where"], failure["who"], failure["task"], took, how, failure["said"], width, color)
    if failure["log"]:
        out += wrapped([("    ", "")], [f"the whole output: {failure['log']}"], width, color, "d")
    return [*out, ("", "")]
