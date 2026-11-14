"""just status without ansible-playbook: what every VM this project created is doing, in the time it takes to ask.

The outline is drawn first, before anything is read. The settings come from a snapshot, or from Ansible in this
process (claude_vm.status.settings). The Proxmox API is read in two waves of GETs at once, each VM drawn the moment
the listing lands, and each VM's probe (claude_vm.status.reach) starts as soon as its own address is read. One
connection per VM, one program there reading everything at once: docs/architecture/lifecycle.md §4.6.

Read-only information, and it must never change anything: GETs to the Proxmox API, and over SSH only reads. It writes
only into its own folder (.cache/status/) and the run's log.

Drawn through the one live region every live output shares (claude_vm.tui.live_region), from the same model and the
same functions for the frames and the finished report. Piped, under --json, or at a terminal that cannot draw it,
nothing is drawn.
"""

from __future__ import annotations

import getpass
import http.client
import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .. import proxmox as proxmox_reads
from ..text import one_line
from ..tui import live_region as live
from ..tui import status as status_view
from . import found as vm_found
from . import model, reach, settings

GET_WORKERS = 8
# What is read before anything else, the listing first: the version, and the storages' and the nodes' figures.
FIRST_WAVE = ("cluster/resources?type=vm", "version", "cluster/resources?type=storage", "cluster/resources?type=node")


class Display:
    """What the live region asks of Ansible's Display: a lock, and display(), with the run's log as Ansible keeps it."""

    def __init__(self, log_path: str):
        self._lock = threading.RLock()
        self._log = None
        if log_path:
            try:
                handler = logging.FileHandler(log_path, encoding="utf-8")
            except OSError as error:
                # As ansible-core does with a log it cannot write: said, and the run goes on.
                print(f"[WARNING]: just status cannot write its log: {error}", file=sys.stderr)
                return
            self._log = logging.getLogger("status")
            handler.setFormatter(
                logging.Formatter(
                    f"%(asctime)s p=%(process)d u={getpass.getuser()} n=ansible %(levelname)s| %(message)s"
                )
            )
            self._log.addHandler(handler)
            self._log.setLevel(logging.INFO)
            self._log.propagate = False

    def display(self, msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None):
        if not log_only:
            stream = sys.stderr if stderr else sys.stdout
            stream.write(msg + ("\n" if newline else ""))
            stream.flush()
        if self._log and not screen_only:
            self._log.info(msg)

    def prompt_until(self, *args, **kwargs):
        raise RuntimeError("just status never asks")

    do_var_prompt = prompt_until


def _color_on() -> bool:
    return not (
        os.environ.get("NO_COLOR") or os.environ.get("ANSIBLE_NOCOLOR", "").lower() in ("1", "true", "yes", "on")
    )


class Screen:
    """The report as it fills: the outline until the fleet lands, then each VM as what is read of it does."""

    def __init__(self, sections, quiet, log_path):
        self.found: dict = {}
        self.sections = sections
        self.quiet = quiet
        self.started = time.monotonic()
        self.display = Display(log_path)
        self.fleet_at = None
        self.done_at: dict = {}
        drawing = live.can_draw() and not quiet
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

    def frame(self, cols, rows, glyph):
        elapsed = f"{time.monotonic() - self.started:.1f} s"
        if "localhost" not in self.found:
            title = f"agent-warehouse  {self.now_said()} · reading Proxmox"
            text = status_view.skeleton_text(title, self.sections, self.color, glyph=glyph, cols=cols - 1)
            return text.split("\n") + [model.one_line(f"{glyph} listing the VMs · {elapsed}", cols - 1)]
        report = model.status_report(dict(self.found), self.now_said(), time.time(), live=True)
        text = status_view.status_text(report, self.color, glyph=glyph, cols=cols - 1)
        api = [vm["name"] for vm in report["vms"] if vm.get("pending_api")]
        ssh = [vm["name"] for vm in report["vms"] if vm.get("pending_ssh")]
        if api:
            doing = f"reading {', '.join(api)}"
        elif ssh:
            doing = f"reading {', '.join(ssh)} over SSH"
        else:
            doing = "ranking what needs attention"
        reading = len(api) + len(ssh)
        self.out.title(f"status · reading {reading} VM{'' if reading == 1 else 's'}" if reading else "status")
        return text.split("\n") + [model.one_line(f"{glyph} {doing} · {elapsed}", cols - 1)]

    def land(self, host, part, merge=False):
        """What was read of a host drawn. ``merge`` adds its keys to what the host has, under the lock: a VM's Proxmox
        part and its SSH part land in either order, from two threads, and neither replaces the other."""
        now = time.monotonic() - self.started
        with self.display._lock:
            self.found[host] = {**self.found.get(host, {}), **part} if merge else part
            if host == "localhost":
                self.fleet_at = self.fleet_at if self.fleet_at is not None else now
            else:
                self.done_at[host] = now
        self.out.redraw()

    def reading(self, report):
        """The line on what was read, and how long it took: proxmox, then the VMs."""
        parts = [f"read in {time.monotonic() - self.started:.1f} s"]
        proxmox = report["proxmox"]
        if proxmox.get("off"):
            parts.append("proxmox not asked (claude_vm_discovery is off)")
        elif proxmox.get("unread"):
            parts.append("proxmox did not answer")
        elif self.fleet_at is not None:
            parts.append(f"proxmox {self.fleet_at:.1f} s")
        read = [vm for vm in report["vms"] if vm.get("read")]
        if read:
            done = [vm for vm in read if vm["name"] in self.done_at]
            last = max((self.done_at[vm["name"]] for vm in done), default=0.0)
            parts.append(f"vms {len(done)} of {len(read)} {last:.1f} s")
        return " · ".join(parts)

    def finish(self, report):
        self.out.title("status · done")
        self.out.finish()
        reading = self.reading(report)
        plain = status_view.status_text(report, False, reading=reading)
        if self.quiet:
            # Under --json the screen gets the JSON alone; the run log still gets the report.
            self.display.display(plain, log_only=True)
        else:
            coloured = status_view.status_text(report, True, reading=reading) if self.color else plain
            self.out.print([(coloured, plain)])


class Emitter(Screen):
    """The screen of a host's read for just status across hosts (claude_vm.status.hosts): nothing drawn, and what
    lands written as one JSON line each on standard output, for the run that draws every host's."""

    def __init__(self, sections):
        super().__init__(sections, True, "")

    def emit(self, message: dict) -> None:
        with self.display._lock:
            sys.stdout.write(json.dumps(message, sort_keys=True) + "\n")
            sys.stdout.flush()

    def land(self, host, part, merge=False):
        super().land(host, part, merge)
        self.emit({"land": [host, part, merge]})

    def finish(self, report):
        self.out.finish()
        self.emit({"end": {"exit": report["exit"], "fleet_at": self.fleet_at, "done_at": self.done_at}})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The token is never carried to another address: a redirect is an answer, not a place to go."""

    def redirect_request(self, *args, **kwargs):
        return None


class Proxmox:
    """GETs, a few at a time, each result in the shape of a registered uri task: item, status, json, msg."""

    def __init__(self, fleet):
        self.base = fleet["url"].rstrip("/")
        self.headers = fleet["headers"]
        self.timeout = fleet["timeout"]
        context = ssl.create_default_context(cafile=fleet["ca_path"] or None)
        if not fleet["validate"]:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))
        self.pool = ThreadPoolExecutor(max_workers=GET_WORKERS)

    def get(self, item):
        if isinstance(item, dict) and item.get("skip"):
            return {"item": item, "skipped": True}
        path = item["path"] if isinstance(item, dict) else item
        one = {"item": item, "status": -1, "msg": "", "json": None}
        body = b""
        try:
            request = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", headers=self.headers, method="GET")
            with self.opener.open(request, timeout=self.timeout) as response:
                one["status"], body = response.status, response.read()
        except urllib.error.HTTPError as error:
            one["status"], one["msg"] = error.code, f"HTTP Error {error.code}: {error.reason}"
            try:
                body = error.read()
            except (OSError, http.client.HTTPException):
                body = b""  # the answer's status is what is known of it
        except ValueError:
            # http.client's words for a header it refuses quote the header, the token in it.
            one["msg"] = "the request could not be made"
        except (OSError, http.client.HTTPException) as error:
            # A connection cut short (IncompleteRead) or an answer that is no HTTP (BadStatusLine): this read failed.
            one["msg"] = f"Request failed: {str(error) or type(error).__name__}"
        try:
            one["json"] = reach.loads(body) if body else None
        except (RecursionError, ValueError):
            one["json"] = None
        return one

    def later(self, item):
        return self.pool.submit(self.get, item)


def _data(result):
    body = result.get("json") if isinstance(result, dict) else None
    return body.get("data") if isinstance(body, dict) else None


def _second_wave(requested, template, storages, reading):
    """Every GET that follows the listing: each VM's config, address, state and history, the template's config and each
    storage's disks. A VM's reads each say which VM's they are, and which read. Every VM's config and address come
    first, so that no VM's probe waits behind another VM's history. Only a VM read further (``reading``, by name) has
    reads of its own: one on a node that is down would wait out the timeout of each, holding up the fleet."""
    addresses, items = [], []
    for vm in requested:
        status = vm.get("status", "unknown")
        path = f"nodes/{vm.get('node')}/qemu/{vm.get('vmid')}"
        vmid = vm.get("vmid")
        addresses.append({"kind": "config", "vmid": vmid, "path": f"{path}/config", "skip": status == "unknown"})
        addresses.append(
            {
                "kind": "network",
                "vmid": vmid,
                "path": f"{path}/agent/network-get-interfaces",
                "skip": status != "running",
            }
        )
        if vm.get("name") not in reading:
            continue
        reads = [("current", "status/current"), ("rrddata", "rrddata?timeframe=day&cf=AVERAGE"), ("pending", "pending")]
        if status == "running":
            reads += [("osinfo", "agent/get-osinfo"), ("fsinfo", "agent/get-fsinfo")]
        items += [{"kind": "api", "vmid": vmid, "name": name, "path": f"{path}/{tail}"} for name, tail in reads]
    items = addresses + items
    if template:
        path = f"nodes/{template.get('node')}/qemu/{template.get('vmid')}/config"
        items.append({"kind": "template", "path": path})
    for storage in storages:
        path = f"nodes/{storage['node']}/storage/{storage['storage']}/content?content=images"
        items.append({"kind": "content", "path": path, "node": storage["node"], "storage": storage["storage"]})
    return items


def _picked(results, kind):
    return [one for one in results if one["item"].get("kind") == kind]


def _denied(results):
    """Proxmox's refusal of the token for a VM, as vm_addresses.yml says it, or ""."""
    for one in results:
        if one["item"].get("kind") in ("config", "network") and one.get("status") in (401, 403):
            return (
                f'Proxmox refused the API token for VM "{one["item"].get("vmid")}":'
                f" {one.get('msg') or 'HTTP ' + str(one.get('status'))}. Reading a VM needs VM.Audit, and its address"
                " needs the guest-agent privilege: VM.Monitor on Proxmox VE 8, VM.GuestAgent.Audit on 9"
                ' (docs/getting-started/proxmox-access.md, "The API token").'
            )
    return ""


def _wanted(vm_settings, config):
    """What the settings ask of a VM, as the role would send it. One sized when it was created, which its own tag says,
    has a size of its own: read from its config at each run, so no snapshot holds it."""
    tags = proxmox_reads.proxmox_tags((config or {}).get("tags"))
    return {**vm_settings["drift"], "sized": vm_settings["sized_tag"] in tags}


def _aliases(fleet):
    text = ""
    if fleet["ssh_config"]:
        for path in (fleet["ssh_config_file"], fleet["ssh_config_legacy"]):
            if path and os.path.isfile(os.path.expanduser(path)):
                try:
                    text = Path(os.path.expanduser(path)).read_text(encoding="utf-8")
                except OSError:
                    text = ""
                break
    return model.status_aliases(text, fleet["ssh_config"])


class Run:
    """One run: the fleet read, each VM read beside it, each piece drawn as it lands."""

    def __init__(self, opts, screen, config):
        self.opts = opts
        self.screen = screen
        self.config = config
        self.fleet = config["fleet"]
        self.names = config["run"]["names"]
        self.sections = list(screen.sections)
        self.settings = {**self.fleet["settings"], "names": self.names, "sections": self.sections}
        self.over_ssh = model.status_needs_ssh(self.sections)
        self.cache = Path(opts.cache) if opts.cache else None
        self.known = (settings.read_json(self.cache / "upgrades.json") or {}) if self.cache else {}
        self.kept: dict = {}
        self.lock = threading.Lock()
        self.password_lock = threading.Lock()
        self.passwords: dict = {}

    # --- a read of one VM -----------------------------------------------------------------------------------------
    def password(self, name):
        """The VM user's password on the VM called ``name``, for the sudo that asks for it: read once in a run, and only
        if one does."""
        with self.password_lock:
            if name not in self.passwords:
                self.passwords[name] = self.config["password"](name)
            return self.passwords[name]

    def asked(self, vm_settings, key):
        wanted = vm_found.parts_wanted(self.sections)
        asked = {k: v for k, v in vm_settings["asked"].items() if k in wanted or k in ("user", "limit")}
        if "system" in asked:
            asked["system"] = {**asked["system"], "known": self.known.get(key) or {}}
        return asked

    def probe(self, name, how, vm_settings, joined_before, key):
        """A VM's reads: what is drawn as soon as only apt's simulation is left, then the whole of them. Merged into
        what the VM has, beside its Proxmox part, whichever lands first."""
        host = how["host"]
        sections = self.sections

        def land(read):
            part = vm_found.ssh_found(read, vm_settings, name, host, sections, joined_before)
            self.screen.land(name, model.status_clean({"ssh": part}), merge=True)

        def early(read):
            try:
                land(read)
            except Exception:  # noqa: BLE001 - what its root sent: the whole answer, next, says so of this VM
                return

        try:
            read = reach.read(how, self.asked(vm_settings, key), early, lambda: self.password(name))
            land(read)
        except Exception as error:  # noqa: BLE001 - a failure, or what its root sent, is that VM's, said as not read
            read = {"failed": True, "msg": str(error) or type(error).__name__}
            land(read)
        system = (read.get("user") or {}).get("system") if isinstance(read.get("user"), dict) else None
        if isinstance(system, dict) and isinstance(system.get("upgrades"), int) and system.get("upgrades_key"):
            with self.lock:
                self.kept[key] = {
                    "key": system["upgrades_key"],
                    "upgrades": system["upgrades"],
                    "security": system.get("security"),
                }

    def probes(self, count):
        """The pool the probes run in: no more connections at once than Ansible's forks would open."""
        return ThreadPoolExecutor(max_workers=max(1, min(count, int(self.fleet["forks"]))))

    # --- the fleet ------------------------------------------------------------------------------------------------
    def unread(self, unread, hint):
        fleet = model.status_fleet(
            None, settings={**self.settings, "proxmox_hint": hint}, unread=unread, hosts=self.fleet["hosts"]
        )
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})

    def from_listing(self):
        api = Proxmox(self.fleet)
        try:
            self.read_fleet(api)
        finally:
            # However the read ended, no GET is left to be sent.
            api.pool.shutdown(wait=False, cancel_futures=True)

    def read_fleet(self, api):
        first = [api.later(path) for path in FIRST_WAVE]
        listing = first[0].result()
        if listing["status"] != 200 or not isinstance(_data(listing), list):
            status = listing["status"]
            if status in (401, 403):
                self.unread(
                    f"Proxmox refused the token: {listing['msg']}",
                    "check proxmox_api_user, proxmox_api_token_id and vault_proxmox_api_token_secret"
                    " (docs/getting-started/proxmox-access.md)",
                )
            else:
                self.unread(
                    f"Proxmox did not answer: {listing['msg'] or 'no response'}",
                    "check proxmox_api_host in inventory/hosts.yml, and that the Proxmox host is up",
                )
            return
        data = _data(listing)
        pattern = self.fleet["tag_pattern"]
        # Never a template, though it carry the ownership tag, as claude_vm_tagged has it: a VM made one by hand.
        tagged = proxmox_reads.not_templates(
            [
                one
                for one in data
                if isinstance(one, dict)
                and one.get("type") == "qemu"
                and "tags" in one
                and re.search(pattern, one["tags"])
            ]
        )
        requested = [one for one in tagged if "name" in one and one["name"] in self.names] if self.names else tagged
        early = model.status_clean(
            model.status_fleet(
                data,
                tagged=tagged,
                requested=requested,
                aliases=[] if self.fleet["ssh_config"] else None,
                settings=self.settings,
                hosts=self.fleet["hosts"],
                pending=True,
            )
        )
        self.screen.land("localhost", {"fleet": early})
        lists = [one.result() for one in first[1:]]
        template = proxmox_reads.built_template(data, self.fleet["template_name"]) or None
        storages = model.status_storages(lists, self.fleet["storage"])
        vms = [vm for vm in early["vms"] if vm.get("read")]
        items = _second_wave(requested, template, storages, {vm["name"] for vm in vms})
        futures = [api.later(item) for item in items]
        pool = self.probes(len(vms))
        try:
            denied, jobs = self.reach_each(vms, requested, items, futures, pool)
            results = [] if denied else [future.result() for future in futures]
            denied = denied or _denied(results)
            if denied:
                reach.end_all()
                self.unread(denied, "check the privileges of the token's role (docs/getting-started/proxmox-access.md)")
                return
            self.fill(data, tagged, requested, lists, results, vms)
            for job in jobs:
                job.result()
        except BaseException:
            # A failure, or a Ctrl-C: no probe is left running, nor started after.
            reach.end_all()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def reach_each(self, vms, requested, items, futures, pool):
        """Each running VM's probe, started the moment its own config and agent answer give its address: (the token's
        refusal for a VM, said, with no probe started after it, or ""; the probes)."""
        if not self.over_ssh:
            return "", []
        running = [vm for vm in vms if vm.get("status") == "running"]
        # Each running VM's config and agent answer, by the VM's place in running.
        place = {vm["vmid"]: at for at, vm in enumerate(running)}
        pairs: list[dict] = [{} for _ in running]
        for item, future in zip(items, futures, strict=True):
            at = place.get(item["vmid"]) if item["kind"] in ("config", "network") else None
            if at is not None:
                pairs[at][item["kind"]] = future
        waiting = {future: at for at, pair in enumerate(pairs) for future in pair.values()}
        left = [len(pair) for pair in pairs]
        jobs = []
        for future in as_completed(waiting):
            at = waiting[future]
            left[at] -= 1
            if left[at] or len(pairs[at]) < 2:
                continue
            config, network = pairs[at]["config"].result(), pairs[at]["network"].result()
            denied = _denied([config, network])
            if denied:
                return denied, jobs
            jobs.append(pool.submit(self.reach_vm, running[at], requested, config, network))
        return "", jobs

    def reach_vm(self, vm, requested, config, network):
        """One VM's probe, at the address its config and agent answer give."""
        entry = next((one for one in requested if one.get("vmid") == vm["vmid"]), vm)
        address = proxmox_reads.guest_addresses([entry], [config], [network], self.fleet["nic"])[0]["address"]
        if not address:
            return
        name = vm["name"]
        how = {**settings.reach_for(self.config, name), "host": address}
        joined = self.fleet["tailscale_tag_pattern"]
        tags = str(entry.get("tags") or "")
        before = bool(re.search(joined, tags))
        self.probe(name, how, settings.for_vm(self.config, name), before, f"{name}/{vm['vmid']}")

    def fill(self, data, tagged, requested, lists, results, vms):
        """The whole fleet from every read, then each VM's Proxmox part."""
        # Copies: a probe's thread may still be reading the same answers for its address.
        configs = [{**one, "claude_vm_item": {"vmid": one["item"]["vmid"]}} for one in _picked(results, "config")]
        networks = _picked(results, "network")
        template_config = (_picked(results, "template") or [{}])[0]
        contents = [
            {**one, "item": {"node": one["item"]["node"], "storage": one["item"]["storage"]}}
            for one in _picked(results, "content")
        ]
        drift = []
        # A template the user built is compared with nothing: the project never rebuilds it.
        if template_config.get("status") == 200 and not self.settings.get("template_custom"):
            drift = proxmox_reads.proxmox_template_drift(_data(template_config), self.fleet["template_wanted"])
        whole = model.status_clean(
            model.status_fleet(
                data,
                tagged=tagged,
                requested=requested,
                addressed=proxmox_reads.guest_addresses(requested, configs, networks, self.fleet["nic"]),
                configs=configs,
                lists=lists,
                contents=contents,
                template_config=template_config,
                template_drift=drift,
                aliases=_aliases(self.fleet),
                settings={**self.settings, "proxmox_hint": ""},
                unread="",
                hosts=self.fleet["hosts"],
            )
        )
        self.screen.land("localhost", {"fleet": whole})
        for vm in vms:
            name = vm["name"]
            listed = next((one for one in whole["vms"] if one["name"] == name), vm)
            vmid = listed["vmid"]
            mine = [one for one in results if one["item"].get("kind") == "api" and one["item"]["vmid"] == vmid]
            part = {
                "api": model.status_vm_api(mine, time.time(), listed.get("created")),
                "drift": proxmox_reads.proxmox_vm_drift(
                    listed.get("config") or {}, _wanted(settings.for_vm(self.config, name), listed.get("config"))
                ),
            }
            self.screen.land(name, model.status_clean(part), merge=True)

    def from_inventory(self):
        """Discovery off: no Proxmox, the inventory's claude_vms each read as it says it is reached."""
        fleet = model.status_fleet([], settings=self.settings, aliases=_aliases(self.fleet))
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})
        if not self.over_ssh:
            return
        hosts = self.config["inventory"]
        pool = self.probes(len(hosts))
        try:
            jobs = [
                pool.submit(self.probe, name, one["reach"], one["vm"], False, f"{name}/inventory")
                for name, one in hosts.items()
            ]
            for job in jobs:
                job.result()
        except BaseException:
            reach.end_all()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def go(self):
        if self.fleet["settings"]["discovery"]:
            self.from_listing()
        else:
            self.from_inventory()
        if self.kept and self.cache:
            settings.write_json(self.cache / "upgrades.json", {**self.known, **self.kept})


def _end_for_an_interrupt(screen):
    reach.end_all()
    if screen:
        screen.out.finish()
    print("just status: interrupted", file=sys.stderr)
    sys.stdout.flush()
    os._exit(3)


def run(opts) -> int:
    """One just status: the report on screen and in the log, printed as JSON with ``--json``; its exit code."""
    try:
        sections = settings.sections(opts.only)
    except ValueError as error:
        print(f"just status: {error}", file=sys.stderr)
        return 3

    def drawn():
        if opts.emit:
            return Emitter(sections)
        return Screen(sections, opts.json, os.environ.get("ANSIBLE_LOG_PATH", ""))

    # A password script may ask at the terminal: the outline waits for its answer rather than being drawn over it.
    screen = None if settings.may_ask(opts) else drawn()
    try:
        config = settings.load(opts, screen.display.display if screen else None)
        screen = screen or drawn()
        if opts.emit:
            fleet = config["fleet"]
            keys = ("ssh_config", "ssh_config_file", "ssh_config_legacy")
            screen.emit({"settings": {key: fleet.get(key) for key in keys}})
        if config["run"].get("only") and not opts.only:
            screen.sections = sections = settings.sections(config["run"]["only"])
        Run(opts, screen, config).go()
        report = model.status_report(dict(screen.found), screen.now_said(), time.time())
        screen.finish(report)
        if opts.json and not opts.emit:
            sys.stdout.write(json.dumps(report, indent=4, sort_keys=True) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        _end_for_an_interrupt(screen)
    except Exception as error:  # noqa: BLE001 - said once, whatever failed, and the run's exit is the one for that
        if screen:
            screen.out.finish()
        print(f"just status: the run failed: {one_line(str(error), 2000)}", file=sys.stderr)
        reach.end_all()
        return 3
    return report["exit"] if report["exit"] in (0, 1, 2) else 3
