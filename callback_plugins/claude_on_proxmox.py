"""The default stdout callback, with the moments a run waits for the person set apart, and just deploy's live output.

A debug task tagged attention_browser - the page to approve in the browser -
is drawn as a block titled ACTION REQUIRED IN YOUR BROWSER instead of an
"ok: [host] =>" and a YAML dump, and a retried task with that tag says once
that it is waiting instead of a FAILED - RETRYING line every few seconds.
After PLAY RECAP it prints NEXT STEPS: what the report tasks recorded with
set_stats as still left to do, per VM and matter, the last record of each
winning (docs/architecture/custom-code.md §7.5).

Everything else is the default callback's, options included: a subclass
without DOCUMENTATION has no options, every hook then fails on its first
get_option, and Ansible only warns while each task's result disappears. Only
ansible.cfg wires this in; the tests and Molecule run their own callbacks.

A recipe drawn live sets CLAUDE_ON_PROXMOX_LIVE to what it runs (deploy alpha, say), and with it, at verbosity
0 and with a run log, every event goes to filter_plugins/live_ansible.py's engine, which draws the run rather than
streaming it (docs/guides/runs.md); this module then keeps only its classic output, the blocks and NEXT STEPS, which
the engine prints through it too. Any -v, or ANSIBLE_STDOUT_CALLBACK=default, is the stream as before.
"""

from __future__ import annotations

DOCUMENTATION = """
    name: claude_on_proxmox
    type: stdout
    short_description: the default output, with banners for the waits and NEXT STEPS at the end
    description:
      - The default callback, drawing debug results tagged attention_browser as a block, saying once that a
        retried task with that tag is waiting, and printing what set_stats recorded as left to do after the
        play recap.
      - With CLAUDE_ON_PROXMOX_LIVE set, as just deploy sets it, and at verbosity 0, the run drawn live at a
        terminal, or a line per step otherwise, every task's output in the log alone.
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout_callback in ansible.cfg
"""

import importlib.util
import sys
from pathlib import Path

from ansible.plugins.callback.default import CallbackModule as DefaultCallback


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem>, since a callback cannot ask for a
    # filter module by name (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "filter_plugins" / f"{stem}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


# The filter the prompts use, so a prompt's block and a message's look the same.
text = _sibling("text")
banner = text.banner
live = _sibling("live_ansible")

WAITING_TAG = live.WAITING_TAG
DEBUG_ACTIONS = live.DEBUG_ACTIONS
# A wait's block names the sign-in it belongs to, by the role the task is in.
TOPICS = {
    "claude_code": "Claude Code sign-in",
    "github_projects": "GitHub sign-in",
    "tailscale": "Tailscale sign-in",
}
# The set_stats names the report tasks record under, each owner's own prefix,
# and the order a VM's matters are listed in: just init's two first, as it comes first.
NEXT_STATS = ("claude_vm_next", "claude_code_next", "github_projects_next", "tailscale_next", "code_next")
MATTERS = (
    "init_settings",
    "init_check",
    "vm",
    "claude",
    "remote_control",
    "github",
    "github_identity",
    "tailscale",
    "vscode",
    "destroy",
)
# Bright, so the blocks stand out from ok's green and changed's yellow.
COLOR = "bright yellow"


def next_steps(run_stats):
    """The lines left to do, from the run's custom stats: by VM name, then matter, each text once."""
    records = {}
    for stat in NEXT_STATS:
        for vm, matters in (run_stats.get(stat) or {}).items():
            if isinstance(matters, dict):
                records.setdefault(str(vm), {}).update(matters)
    rank = {matter: index for index, matter in enumerate(MATTERS)}
    lines = []
    for vm in sorted(records):
        for matter in sorted(records[vm], key=lambda m: (rank.get(m, len(MATTERS)), m)):
            line = records[vm][matter]
            if isinstance(line, str) and line.strip() and line not in lines:
                lines.append(line)
    return lines


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "claude_on_proxmox"
    NEXT_STATS = NEXT_STATS
    COLOR = COLOR

    def __init__(self):
        super().__init__()
        self._waiting = set()
        # The live run's engine, when the run is drawn: decided when the playbook starts, once the verbosity is known.
        self._live = None

    # --- the classic output --------------------------------------------------------------------------------------
    # Every block is banner's own width - 80 columns, or the terminal's when
    # narrower - the same as the prompts', which are drawn before any callback.

    def _banner_or_default(self, result):
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            if self._last_task_banner != result.task._uuid:
                self._print_task_banner(result.task)
            name = result.task.get_name()
            role = name.split(" : ", 1)[0] if " : " in name else ""
            self._display.display(
                banner(
                    msg,
                    "ACTION REQUIRED IN YOUR BROWSER",
                    host=result.host.get_name(),
                    topic=TOPICS.get(role, ""),
                ),
                color=COLOR,
            )
            return True
        return False

    def _retry_once(self, result):
        # One line per wait and host, not one per retry: the instruction above
        # it stays on the screen.
        key = (result.task._uuid, result.host.get_name())
        if key in self._waiting:
            return
        self._waiting.add(key)
        left = result.result.get("retries", 0) - result.result.get("attempts", 0)
        try:
            seconds = left * int(result.task.delay)
        except (TypeError, ValueError):
            seconds = 0
        said = (
            f"about {round(seconds / 60)} minutes"
            if seconds >= 120
            else f"about {seconds} seconds"
            if seconds > 0
            else f"{left} more checks"
        )
        self._display.display(
            f"Waiting for you in the browser for {result.host.get_name()} ({said} left; Ctrl-C ends the run) ...",
            color=COLOR,
        )

    def next_steps_of(self, stats):
        return next_steps(stats.custom.get("_run", {}))

    # --- the playbook, each hook handed to the live engine when the run is drawn ------------------------------------
    def v2_playbook_on_start(self, playbook):
        self._live = live.from_environment(self, playbook)
        super().v2_playbook_on_start(playbook)

    def v2_playbook_on_play_start(self, play):
        if self._live:
            self._live.play_start(play)
        super().v2_playbook_on_play_start(play)

    def v2_playbook_on_task_start(self, task, is_conditional):
        if self._live:
            self._live.task_start(task)
        super().v2_playbook_on_task_start(task, is_conditional)

    def v2_playbook_on_handler_task_start(self, task):
        if self._live:
            self._live.task_start(task)
        super().v2_playbook_on_handler_task_start(task)

    def v2_playbook_on_include(self, included_file):
        if self._live:
            self._live.include(included_file)
        super().v2_playbook_on_include(included_file)

    def v2_runner_on_start(self, host, task):
        if self._live:
            self._live.runner_start(host, task)
        super().v2_runner_on_start(host, task)

    def v2_runner_on_ok(self, result):
        if self._live:
            if self._live.ok(result):
                super().v2_runner_on_ok(result)
        elif not self._banner_or_default(result):
            super().v2_runner_on_ok(result)

    def v2_runner_item_on_ok(self, result):
        if self._live:
            self._live.item_ok(result)
        super().v2_runner_item_on_ok(result)

    def v2_runner_on_skipped(self, result):
        if self._live:
            self._live.skipped(result)
        super().v2_runner_on_skipped(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if self._live:
            self._live.failed(result, ignore_errors)
        super().v2_runner_on_failed(result, ignore_errors)

    def v2_runner_item_on_failed(self, result):
        if self._live:
            self._live.item_failed(result)
        super().v2_runner_item_on_failed(result)

    def v2_runner_on_unreachable(self, result):
        if self._live:
            self._live.unreachable(result)
        super().v2_runner_on_unreachable(result)

    def v2_runner_retry(self, result):
        if self._live:
            if self._live.retry(result):
                super().v2_runner_retry(result)
        elif WAITING_TAG not in result.task.tags:
            super().v2_runner_retry(result)
        else:
            self._retry_once(result)

    def v2_on_file_diff(self, result):
        # A dry run is read for its diffs: drawn, they go above the region, to the screen and the log.
        if self._live:
            with self._live.say():
                super().v2_on_file_diff(result)
            return
        super().v2_on_file_diff(result)

    def v2_playbook_on_stats(self, stats):
        if self._live:
            self._live.stats(stats, lambda s: DefaultCallback.v2_playbook_on_stats(self, s))
            return
        super().v2_playbook_on_stats(stats)
        lines = self.next_steps_of(stats)
        if lines:
            self._display.display(banner(lines, "NEXT STEPS"), color=COLOR)
