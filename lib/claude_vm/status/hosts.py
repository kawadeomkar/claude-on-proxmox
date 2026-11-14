"""just status across the checkout's hosts: one child process per host - today's engine, reading that host alone - and
one screen, which draws what each child sends as it lands (docs/architecture/lifecycle.md §4.6).

The engine is not safe to run twice in one process: reach ends every probe at one host's refusal (its _over), and
Ansible's GlobalCLIArgs is one per process. So each host's read is a process of its own, run with --emit, which writes
what it lands as JSON lines on its standard output and draws nothing; this process holds what each host found, builds
each host's report with the same model, and draws them all through the one live region, every VM filling in as its
host's child reads it. A host that does not answer costs no more than its own timeout, the others drawn meanwhile.

After every child has ended, what only the whole checkout shows: an alias recorded for a host whose listing has no
such VM (the prune against that host clears it), and a VM name two hosts each have. Read-only, as every child is.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import proc
from ..proxmox import ssh_config_blocks
from ..tui import live_region as live
from ..tui import status as status_view
from . import model
from .engine import Display, _color_on

STATUS = Path(__file__).resolve().parents[3] / "tools" / "status"


class Child:
    """One host's read: its process, and what it sent."""

    def __init__(self, host: dict):
        self.host = host
        self.name = host["name"]
        self.found: dict = {}
        self.settings: dict = {}
        self.end: dict | None = None
        self.stderr = ""
        self.process: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None


def command(opts, host: dict) -> list[str]:
    """The child's command: this tool, emitting, with the host's sources, vaults and folder for what it keeps."""
    argv = [sys.executable, str(STATUS), "--emit"]
    if opts.names:
        argv += ["--names", opts.names]
    if opts.only:
        argv += ["--only", opts.only]
    if opts.cache:
        argv += ["--cache", str(Path(opts.cache) / host["name"])]
    for vault in host["vault_files"]:
        argv += ["--vault-file", vault]
    if opts.vault_password_file:
        argv += ["--vault-password-file", opts.vault_password_file]
    if host.get("extra"):
        argv += ["--host-extra", json.dumps(host["extra"])]
    for source in host["sources"]:
        argv += ["-i", source]
    for extra in opts.extra:
        argv += ["-e", extra]
    if opts.private_key:
        argv += ["--private-key", opts.private_key]
    return argv


def _environment() -> dict:
    """The child's: this run's, but its inventory, which -i gives it, and its log, which this process keeps whole."""
    return {k: v for k, v in os.environ.items() if k not in ("ANSIBLE_INVENTORY", "ANSIBLE_LOG_PATH")}


class HostsScreen:
    """Every host's report as it fills, one block each, under one title."""

    def __init__(self, children: list[Child], hosts: dict, sections, quiet, log_path, draw=True):
        self.children = children
        self.hosts = hosts
        self.order = list(hosts)
        self.sections = sections
        self.quiet = quiet
        self.started = time.monotonic()
        self.display = Display(log_path)
        self.lock = threading.RLock()
        drawing = draw and live.can_draw() and not quiet
        more = "⋮ {} more lines, shown when the run ends"
        self.out = live.Output(self.display, frame=self.frame, live=drawing, color=_color_on(), more=more)
        self.color = self.out.color
        if drawing:
            self.out.start()
            self.out.spin()
            self.out.redraw()

    @staticmethod
    def now_said() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()

    def child(self, name: str) -> Child | None:
        return next((one for one in self.children if one.name == name), None)

    def title(self, reports: dict) -> str:
        vms = sum(len(report["vms"]) for report in reports.values() if report)
        hosts = model.plural(len(self.order), "host")
        return f"agent-warehouse  {self.now_said()} · {hosts} · {model.plural(vms, 'VM')}"

    def block(self, name: str, report: dict | None, color: bool, glyph=None, cols=None) -> str:
        """One host's part: its report under its own title, what it is still reading, or why it is not read - wrapped
        to the report's width when it is printed whole, and cut to the frame's while it is drawn."""
        current = "▸ " if self.hosts[name].get("current") else ""
        problem = self.hosts[name].get("problem")
        if problem:
            return _said(f"{current}{name} · cannot be read: {problem}", cols)
        if report is None:
            child = self.child(name)
            if child is not None and child.end is not None and child.end.get("failed"):
                return _said(f"{current}{name} · the read failed: {child.end['failed']}", cols)
            title = f"{current}{name} · reading Proxmox"
            return status_view.skeleton_text(title, self.sections, color, glyph=glyph, cols=cols)
        titled = {**report, "title": _titled(report, name, current)}
        return status_view.status_text(titled, color, glyph=glyph, cols=cols)

    def live_reports(self) -> dict:
        with self.lock:
            found = {child.name: dict(child.found) for child in self.children}
        return {
            name: model.status_report(found[name], self.now_said(), time.time(), live=True)
            if "localhost" in found.get(name, {})
            else None
            for name in self.order
        }

    def frame(self, cols, rows, glyph):
        reports = self.live_reports()
        elapsed = f"{time.monotonic() - self.started:.1f} s"
        lines = [model.one_line(self.title(reports), cols - 1), ""]
        for name in self.order:
            lines += self.block(name, reports.get(name), self.color, glyph, cols - 1).split("\n") + [""]
        reading = [name for name in self.order if (child := self.child(name)) and child.end is None]
        self.out.title(f"status · reading {', '.join(reading)}" if reading else "status")
        doing = f"reading {', '.join(reading)}" if reading else "ranking what needs attention"
        return lines + [model.one_line(f"{glyph} {doing} · {elapsed}", cols - 1)]

    def land(self, child: Child, message: dict) -> None:
        with self.lock:
            if "land" in message:
                host, part, merge = message["land"]
                child.found[host] = {**child.found.get(host, {}), **part} if merge else part
            elif "settings" in message:
                child.settings = message["settings"]
            elif "end" in message:
                child.end = message["end"]
        self.out.redraw()

    def text(self, reports: dict, color: bool) -> str:
        lines = [self.title(reports), ""]
        for name in self.order:
            lines += [self.block(name, reports.get(name), color), ""]
        lines.append(f"read in {time.monotonic() - self.started:.1f} s")
        return "\n".join(lines)

    def finish(self, reports: dict) -> None:
        self.out.title("status · done")
        self.out.finish()
        plain = self.text(reports, False)
        if self.quiet:
            # Under --json the screen gets the JSON alone; the run log still gets the report.
            self.display.display(plain, log_only=True)
        else:
            self.out.print([(self.text(reports, True) if self.color else plain, plain)])


def _said(line: str, cols) -> str:
    """A host's one line: cut to the frame while it is drawn, wrapped to the report's width once it is printed whole,
    a report being wrapped, never cut."""
    if cols:
        return model.one_line(line, cols)
    return "\n".join(textwrap.wrap(model.one_line(line, 4000), status_view.WIDTH, break_on_hyphens=False))


def _titled(report: dict, name: str, current: str) -> str:
    """A host's report's title, its own: the host's name in place of the run's, which the whole report's title says."""
    title = report.get("title") or ""
    rest = title.split(" · ", 1)[1] if " · " in title else ("not read" if report["proxmox"].get("unread") else "")
    return f"{current}{name}{' · ' + rest if rest else ''}"


def _read(child: Child, screen: HostsScreen) -> None:
    for line in child.process.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            screen.land(child, message)


def _ended(children: list[Child]) -> None:
    for child in children:
        if child.process is not None and child.process.poll() is None:
            proc.end(child.process.pid, signum=signal.SIGTERM)


def reconcile(reports: dict, blocks: list[dict], current: str | None = None) -> None:
    """What only every host's listing together shows, added to the hosts' own ATTENTION: an alias recorded for a host
    whose listing has no VM of that name, one that records no host whose VM no host lists - on ``current``'s - and a
    name two hosts each have a VM of."""
    listed = {name: {vm.get("name") for vm in report["vms"]} for name, report in reports.items() if report is not None}
    for name, report in reports.items():
        if report is None or report["proxmox"].get("unread") or None in listed[name] or report["settings"]["names"]:
            continue
        for block in blocks:
            if block["origin"] == name and block["name"] not in listed[name]:
                report["attention"].append(
                    model._item(
                        "todo",
                        block["name"],
                        f"its SSH alias records {name}, which lists no VM of that name: gone, or renamed",
                        f"just deploy --only ssh-config --host {name}",
                    )
                )
    for name, report in reports.items():
        if report is None:
            continue
        for vm in report["vms"]:
            others = sorted(host for host, names in listed.items() if host != name and vm.get("name") in names)
            if vm.get("name") and others:
                origin = next((block["origin"] for block in blocks if block["name"] == vm["name"]), "")
                alias = f"its SSH alias is {origin}'s" if origin else "neither has its SSH alias"
                report["attention"].append(
                    model._item(
                        "problem",
                        vm["name"],
                        f"{' and '.join(others)} {'has' if len(others) == 1 else 'have'} a VM of this name too, and a "
                        f"VM name belongs to one host: {alias}",
                        "rename one of them in Proxmox",
                    )
                )
    # An alias that records no host - written before 2.5 - whose VM no host lists: no prune removes it, since any host
    # could have had it. Said once every host's listing is whole, on the current host's report.
    whole = all(
        report is not None and not report["proxmox"].get("unread") and None not in listed[name]
        for name, report in reports.items()
    )
    if whole and current in reports:
        everywhere = set().union(*listed.values())
        for block in blocks:
            if not block["origin"] and block["name"] not in everywhere:
                reports[current]["attention"].append(
                    model._item(
                        "todo",
                        block["name"],
                        "its SSH alias records no host, from before 2.5, and no host lists a VM of that name: no "
                        "prune removes it",
                        "remove its block from the managed SSH config by hand",
                    )
                )
    for report in reports.values():
        if report is not None:
            counts = {level: sum(1 for it in report["attention"] if it["level"] == level) for level in model.LEVELS}
            report["counts"] = counts
            report["exit"] = 2 if counts["unread"] else 1 if counts["problem"] else 0


def combined(reports: dict, problems: dict, failed: dict) -> dict:
    """The JSON: the current host's report, every key 1.0 promised where it was, and every host's rows with their
    host's name as host; each host whole under hosts, its row naming it name, as just host --json's rows do. The exit
    is the highest of the hosts', a host that did not answer counting only where it is the current one (decided: plan
    D4)."""
    current = next((name for name, one in problems.items() if one.get("current")), None)
    base = dict(reports.get(current) or next((one for one in reports.values() if one), {}) or {})
    out = dict(base)
    out["hosts"] = []
    exits = []
    for name, report in reports.items():
        unread = report is None or bool(report["proxmox"].get("unread"))
        exit_code = 3 if failed.get(name) else 2 if report is None else report["exit"]
        out["hosts"].append(
            {
                "name": name,
                "current": bool(problems[name].get("current")),
                "exit": exit_code,
                "failed": failed.get(name) or problems[name].get("problem") or None,
                "report": report,
            }
        )
        if unread and not problems[name].get("current"):
            exit_code = 1 if report and report["counts"]["problem"] else 0
        exits.append(exit_code)
    for key in ("vms", "storages", "capacity", "attention"):
        out[key] = [{**row, "host": name} for name, report in reports.items() if report for row in report[key]]
    out["counts"] = {level: sum(1 for it in out["attention"] if it["level"] == level) for level in model.LEVELS}
    out["exit"] = max(exits, default=0)
    return out


def run(opts, hosts: list[dict]) -> int:
    """Every host's read at once, drawn as one report; its exit code. With --taken, nothing drawn: a line for each of
    the names a host has a VM of (taken <name> <host>), and one for each host that did not answer (unread <host>)."""
    from . import settings

    if opts.inventory:
        print(
            "just status: -i names one inventory, and this run reads every host's: give --host <name>", file=sys.stderr
        )
        return 3
    try:
        sections = settings.sections(opts.only)
    except ValueError as error:
        print(f"just status: {error}", file=sys.stderr)
        return 3
    problems = {host["name"]: host for host in hosts}
    children = [Child(host) for host in hosts if not host.get("problem")]
    log = "" if opts.taken else os.environ.get("ANSIBLE_LOG_PATH", "")
    # A vault password script may ask at the terminal: then each host's read runs in turn, the terminal its own while
    # it asks, and the report is drawn once they have all ended.
    asks = settings.may_ask(opts)
    screen = HostsScreen(children, problems, sections, opts.json or opts.taken, log, draw=not asks)
    try:
        for child in children:
            child.process = subprocess.Popen(
                command(opts, child.host),
                stdin=None if asks else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=None if asks else subprocess.PIPE,
                text=True,
                env=_environment(),
                start_new_session=not asks,
            )
            child.reader = threading.Thread(target=_read, args=(child, screen), daemon=True)
            child.reader.start()
            if asks:
                child.process.wait()
                child.reader.join()
        for child in children:
            child.stderr = "" if asks else child.process.stderr.read()
            child.process.wait()
            child.reader.join()
    except KeyboardInterrupt:
        _ended(children)
        screen.out.finish()
        print("just status: interrupted", file=sys.stderr)
        return 3
    reports, failed = {}, {}
    for name in problems:
        child = screen.child(name)
        if child is None:
            reports[name] = None
            continue
        if child.process.returncode not in (0, 1, 2) or "localhost" not in child.found:
            said = child.stderr.strip().splitlines()
            failed[name] = said[-1] if said else f"its read exited {child.process.returncode}"
            child.end = {"failed": failed[name]}
            reports[name] = None
            continue
        reports[name] = model.status_report(child.found, screen.now_said(), time.time())
    if opts.taken:
        wanted = [name for name in opts.names.split(",") if name]
        for name, report in reports.items():
            if report is None or report["proxmox"].get("unread"):
                print(f"unread {name}")
                continue
            for vm in report["vms"]:
                if vm.get("name") in wanted:
                    print(f"taken {vm['name']} {name}")
        return 0
    blocks = _blocks(next((child.settings for child in children if child.settings), {}))
    reconcile(reports, blocks, next((name for name, one in problems.items() if one.get("current")), None))
    screen.finish(reports)
    for child in children:
        for line in child.stderr.strip().splitlines():
            if line and not (child.name in failed and line == failed[child.name]):
                print(f"{child.name}: {line}", file=sys.stderr)
    report = combined(reports, problems, failed)
    if opts.json:
        sys.stdout.write(json.dumps(report, indent=4, sort_keys=True) + "\n")
        sys.stdout.flush()
    return report["exit"] if report["exit"] in (0, 1, 2) else 3


def _blocks(found_settings: dict) -> list[dict]:
    """The managed SSH config's blocks, each with the host it records: where the children's settings say it is."""
    if not found_settings.get("ssh_config"):
        return []
    for path in (found_settings.get("ssh_config_file"), found_settings.get("ssh_config_legacy")):
        if path and os.path.isfile(os.path.expanduser(path)):
            try:
                return ssh_config_blocks(Path(os.path.expanduser(path)).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
    return []
