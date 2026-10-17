"""The default stdout callback, with the moments a run waits for the person set apart.

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


def _load_banner():
    # The filter the prompts use, so a prompt's block and a message's look the
    # same; loaded by path, since a callback cannot ask for a filter by name.
    name = "claude_on_proxmox_text_filters"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "filter_plugins" / "text.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name].banner


banner = _load_banner()

WAITING_TAG = "attention_browser"
DEBUG_ACTIONS = frozenset({"debug", "ansible.builtin.debug", "ansible.legacy.debug"})
# A wait's block names the sign-in it belongs to, by the role the task is in.
TOPICS = {
    "claude_code": "Claude Code sign-in",
    "github_projects": "GitHub sign-in",
    "tailscale": "Tailscale sign-in",
}
# The set_stats names the report tasks record under, each owner's own prefix,
# and the order a VM's matters are listed in.
NEXT_STATS = ("claude_vm_next", "claude_code_next", "github_projects_next", "tailscale_next", "code_next")
MATTERS = ("vm", "claude", "remote_control", "github", "github_identity", "tailscale", "vscode", "destroy")
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

    def __init__(self):
        super().__init__()
        self._waiting = set()

    # Every block is banner's own width - 80 columns, or the terminal's when
    # narrower - the same as the prompts', which are drawn before any callback.

    def v2_runner_on_ok(self, result):
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
            return
        super().v2_runner_on_ok(result)

    def v2_runner_retry(self, result):
        if WAITING_TAG not in result.task.tags:
            super().v2_runner_retry(result)
            return
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

    def v2_playbook_on_stats(self, stats):
        super().v2_playbook_on_stats(stats)
        lines = next_steps(stats.custom.get("_run", {}))
        if lines:
            self._display.display(banner(lines, "NEXT STEPS"), color=COLOR)
