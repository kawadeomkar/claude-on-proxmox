"""`just status`: what each VM this project created is doing, and what needs doing next.

playbooks/status.yml reads, and these turn what it read into one model and the text of the report. The model is
what `just status --json` prints and what the report file holds, and the same functions draw the report twice: once
finished, when the run ends, and over and over while it runs, when callback_plugins/claude_on_proxmox_status.py
draws it live with a spinner wherever a value is still being read (docs/architecture/lifecycle.md §4.6).

Nothing here reads or writes anything. Every value a VM supplies is cleaned by status_clean before anything keeps
it, and again by status_report: one line of plain text each (filter_plugins/text.py's one_line), so a name, a
directory or a line a tool printed can never start a line of the report of its own.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from ansible.errors import AnsibleFilterError


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem>: a filter plugin is no module another
    # can import, and every module here loads another this way (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{stem}.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


one_line = _sibling("text").one_line
# The report is drawn as every live output is: lines of styled segments (filter_plugins/screen.py).
_screen = _sibling("screen")
seg, plain, pad, cut, render, bar, spark, plural = (
    _screen.seg,
    _screen.plain,
    _screen.pad,
    _screen.cut,
    _screen.render,
    _screen.bar,
    _screen.spark,
    _screen.plural,
)
PENDING = _screen.PENDING

# The report's sections, in the order they are drawn: vms and storage read only the API, the rest the VMs over SSH
# too - access for the tailnet column, sign-ins for the roles' reads, system for vm_status_info, work for the
# checkouts (git_checkouts_info's work).
SECTIONS = ("vms", "storage", "access", "sign-ins", "system", "work")
SSH_SECTIONS = frozenset({"access", "sign-ins", "system", "work"})
# The widest line the report draws, a tested limit.
WIDTH = 100
# A field a VM supplies is cut to this; a role's whole line, which carries a command, to the longer.
FIELD_LIMIT = 120
LINE_LIMIT = 400
LINE_KEYS = frozenset({"line"})
# A VM name the recipe takes, as tools/check names checks it: a name is how a role's commands name a VM.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GIB = 1024**3
LEVELS = ("unread", "problem", "todo")
# The configuration keys the report reads from a VM's config: nothing else, cipassword among what is left out.
CONFIG_KEYS = (
    "cores",
    "sockets",
    "memory",
    "balloon",
    "onboot",
    "ciuser",
    "sshkeys",
    "ipconfig0",
    "nameserver",
    "searchdomain",
    "scsi0",
    "meta",
    "lock",
)


# ------------------------------------------------------------------------------------------------- cleaning ----


def status_clean(value, limit=FIELD_LIMIT):
    """``value`` with every string in it made one line of plain text: a role's line (key "line") cut to LINE_LIMIT,
    every other string to ``limit``. Keys are cleaned too. Numbers, booleans and None are kept."""
    if isinstance(value, dict):
        return {one_line(k, limit): status_clean(v, LINE_LIMIT if k in LINE_KEYS else limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [status_clean(v, limit) for v in value]
    if isinstance(value, str):
        return one_line(value, limit)
    return value


def status_sections(only):
    """The sections asked for, in report order: all of them for none. Refuses one the report has not got."""
    if only in (None, "", []):
        return list(SECTIONS)
    asked = [s.strip() for s in (only.split(",") if isinstance(only, str) else only) if str(s).strip()]
    unknown = [s for s in asked if s not in SECTIONS]
    if unknown:
        raise AnsibleFilterError(
            f"the report has no section {', '.join(unknown)}: its sections are {' '.join(SECTIONS)}"
        )
    return [s for s in SECTIONS if s in asked]


def status_needs_ssh(sections):
    """Whether any of these sections reads the VMs over SSH."""
    return any(s in SSH_SECTIONS for s in sections)


# ------------------------------------------------------------------------------------------------ the fleet ----


def _data(result):
    """The "data" of a registered uri result, or None when the request failed or was skipped."""
    if not isinstance(result, dict) or result.get("status") != 200:
        return None
    body = result.get("json")
    return body.get("data") if isinstance(body, dict) else None


def _tags(entry):
    return [t for t in re.split(r"[;,]", str(entry.get("tags") or "").lower()) if t]


def _config_value(config, key):
    return config.get(key) if isinstance(config, dict) else None


def _ctime(config):
    match = re.search(r"(?:^|,)ctime=(\d+)", str(_config_value(config, "meta") or ""))
    return int(match.group(1)) if match else None


def _disk(config):
    """scsi0 as {storage, bytes}; bytes from size=, in Proxmox's own units."""
    text = str(_config_value(config, "scsi0") or "")
    storage = text.split(":", 1)[0] if ":" in text.split(",", 1)[0] else ""
    match = re.search(r"(?:^|,)size=([0-9.]+)([KMGT])", text)
    size = int(float(match.group(1)) * 1024 ** "KMGT".index(match.group(2)) * 1024) if match else None
    return {"storage": storage, "bytes": size}


def _pick(config):
    return {key: config[key] for key in CONFIG_KEYS if isinstance(config, dict) and key in config}


def status_fleet(
    listing,
    tagged=None,
    requested=None,
    addressed=None,
    configs=None,
    lists=None,
    contents=None,
    template_config=None,
    template_drift=None,
    aliases=None,
    settings=None,
    unread="",
):
    """Play 1's findings, as one value: Proxmox, the VMs this project created (with what the listing, their config
    and their agent's address say), the other VMs as totals, the storages, the template and the SSH aliases.

    ``listing`` is /cluster/resources?type=vm; ``tagged`` and ``requested`` the group vars claude_vm_tagged and
    claude_vm_requested, the one definition of which VMs are this project's and which were asked for;
    ``addressed`` the requested VMs with an address (vm_addresses.yml), ``configs`` its config results, each with
    the VM it read as ``claude_vm_item``; ``lists`` the results of reading /version and the storages' listing;
    ``contents`` the results of reading each storage's disks, each with its ``item``; ``template_config`` the result
    of reading the template's config, and ``template_drift`` how it differs from what its settings would build
    (roles/proxmox_template's drift entry point), which the next just deploy rebuilds; ``aliases`` the names the
    managed SSH config holds, or None with the aliases off; ``settings`` what the inventory says; ``unread`` why
    Proxmox could not be read, if it could not.
    """
    settings = dict(settings or {})
    if not settings.get("discovery", True):
        return {"settings": settings, "proxmox": {"unread": "", "off": True}, "vms": [], "aliases": aliases}
    if unread or not isinstance(listing, list):
        return {"settings": settings, "proxmox": {"unread": unread or "Proxmox did not list the VMs"}, "vms": []}
    read = {r["item"]: _data(r) for r in lists or [] if isinstance(r, dict) and isinstance(r.get("item"), str)}
    version = read.get("version") if isinstance(read.get("version"), dict) else {}
    storages = read.get("cluster/resources?type=storage") or []
    contents = {
        f"{r['item'].get('node')}/{r['item'].get('storage')}": _data(r) or []
        for r in contents or []
        if isinstance(r, dict) and isinstance(r.get("item"), dict)
    }
    template_config = _data(template_config) or {}
    # When each VM was made: its disk's ctime in the storage's content. A clone's config carries the template's
    # meta ctime (Proxmox VE 9.2, checked on a real clone), so that says nothing of the clone.
    disk_ctimes = {}
    for volumes in contents.values():
        for volume in volumes if isinstance(volumes, list) else []:
            if not isinstance(volume, dict) or not re.search(r":(base|vm)-\d+-disk-\d+$", str(volume.get("volid"))):
                continue
            try:
                vmid, ctime = int(volume.get("vmid")), int(volume.get("ctime"))
            except (TypeError, ValueError):
                continue
            disk_ctimes[vmid] = min(ctime, disk_ctimes.get(vmid, ctime))
    given = [n for n in settings.get("names") or [] if n]
    entries = [e for e in listing if isinstance(e, dict) and e.get("type", "qemu") == "qemu"]
    tagged_ids = {e.get("vmid") for e in tagged or [] if isinstance(e, dict)}
    ours = [e for e in tagged or [] if isinstance(e, dict) and not e.get("template")]
    requested = [e for e in requested or [] if isinstance(e, dict) and not e.get("template")]
    others = [e for e in entries if e.get("vmid") not in tagged_ids and not e.get("template")]
    named = {e.get("name") for e in ours}
    unmatched = [n for n in given if n not in named]
    nameless = [{"vmid": e.get("vmid"), "node": e.get("node")} for e in ours if "name" not in e] if unmatched else []
    tag = str(settings.get("tag", "claude-on-proxmox")).lower()
    # Each config by the VM its result names, never by position: a read that was skipped would shift the rest.
    read_configs = {}
    for result in (configs or {}).get("results", []) if isinstance(configs, dict) else configs or []:
        if isinstance(result, dict):
            item = result.get("claude_vm_item") or result.get("item") or {}
            if isinstance(item, dict):
                read_configs[item.get("vmid")] = _data(result) or {}
    by_vmid = {
        e.get("vmid"): {"address": e.get("address", ""), "config": _pick(read_configs.get(e.get("vmid")) or {})}
        for e in addressed or []
        if isinstance(e, dict)
    }
    counts = {}
    for entry in requested:
        if "name" in entry:
            counts[entry["name"]] = counts.get(entry["name"], 0) + 1
    template = None
    template_name = settings.get("template_name")
    for entry in entries:
        if entry.get("template") and entry.get("name") == template_name:
            template = {
                "name": template_name,
                "vmid": entry.get("vmid"),
                "node": entry.get("node"),
                "storage": _disk(template_config or {}).get("storage", ""),
                "ctime": _ctime(template_config or {}),
                "drift": [str(d) for d in template_drift or []],
                "rebuild": bool(settings.get("template_rebuild", True)),
            }
    vms = []
    for entry in sorted(requested, key=lambda e: ("name" not in e, str(e.get("name", "")), e.get("vmid", 0))):
        found = by_vmid.get(entry.get("vmid"), {})
        name = entry.get("name")
        status = entry.get("status", "unknown")
        reason = ""
        if status == "unknown":
            reason = "node"
        elif name is None:
            reason = "nameless"
        elif counts.get(name, 0) > 1:
            reason = "duplicate"
        elif not NAME_RE.match(name) or name == "localhost":
            reason = "name"
        config = found.get("config", {})
        vms.append(
            {
                "name": name,
                "vmid": entry.get("vmid"),
                "node": entry.get("node"),
                "status": status,
                "cpu": entry.get("cpu"),
                "vcpus": entry.get("maxcpu"),
                "mem": entry.get("mem"),
                "maxmem": entry.get("maxmem"),
                "uptime": entry.get("uptime"),
                "maxdisk": entry.get("maxdisk"),
                "address": found.get("address", ""),
                "unfinished": f"{tag}-unfinished" in _tags(entry),
                "config": config,
                "created": disk_ctimes.get(entry.get("vmid")) or _ctime(config),
                # Read in play 2, by name: a VM that is not can only be shown from the listing.
                "read": reason == "",
                "skipped": reason,
            }
        )
    disk_storages = {_disk(vm["config"]).get("storage") for vm in vms} - {""}
    wanted = disk_storages | {settings.get("storage", "")} - {""}
    ours_vmids = {vm["vmid"] for vm in vms} | {e.get("vmid") for e in ours}
    shown = []
    for entry in storages or []:
        if not isinstance(entry, dict) or entry.get("storage") not in wanted:
            continue
        volumes = contents.get(f"{entry.get('node')}/{entry.get('storage')}") or []
        sizes = [v for v in volumes if isinstance(v, dict) and isinstance(v.get("size"), (int, float))]
        shown.append(
            {
                "storage": entry.get("storage"),
                "node": entry.get("node"),
                "type": entry.get("plugintype", ""),
                "used": entry.get("disk"),
                "total": entry.get("maxdisk"),
                "ours": sum(v["size"] for v in sizes if v.get("vmid") in ours_vmids),
                "provisioned": sum(v["size"] for v in sizes) if sizes else None,
                "shared": bool(entry.get("shared")),
            }
        )
    shown.sort(key=lambda s: (s["storage"] != settings.get("storage"), s["storage"], s["node"]))
    return {
        "settings": settings,
        "proxmox": {
            "unread": "",
            "version": version.get("version", ""),
            "nodes": sorted({e.get("node") for e in entries if e.get("node")}),
        },
        "vms": vms,
        "unmatched": unmatched,
        "nameless": nameless,
        "others": {
            "count": len(others),
            "vcpus": sum(int(e.get("maxcpu") or 0) for e in others),
            "mem": sum(int(e.get("maxmem") or 0) for e in others),
            "running": sum(1 for e in others if e.get("status") == "running"),
            "nodes": sorted({e.get("node") for e in others if e.get("node")}),
        },
        "storages": shown,
        "template": template if template else {"name": template_name, "missing": True},
        "aliases": aliases,
    }


def status_storages(lists, storage):
    """The storages to read the disks of: those named ``storage`` in the storages' listing, one per node."""
    for result in lists or []:
        if isinstance(result, dict) and result.get("item") == "cluster/resources?type=storage":
            return [
                {"node": e.get("node"), "storage": e.get("storage")}
                for e in _data(result) or []
                if isinstance(e, dict) and e.get("storage") == storage and e.get("status", "available") == "available"
            ]
    return []


def status_aliases(text, enabled=True):
    """The VM names the managed SSH config has an alias for, or None with the aliases off."""
    if not enabled:
        return None
    return re.findall(r"(?m)^# BEGIN claude-on-proxmox: (\S+)$", text or "")


# ------------------------------------------------------------------------------------------- one VM's API ----


def _buckets(points, now, hours=24, count=12, key="cpu", since=None):
    """``points`` (rrddata) as ``count`` averages of ``key`` over the last ``hours``, by timestamp; None where the
    VM has no point in a bucket. Either resolution reads the same: Proxmox VE 9's minute or 8's twenty minutes.
    Nothing from before ``since``, when the VM was made: Proxmox keeps a VMID's history, and an ID used again
    carries the deleted VM's."""
    span = hours * 3600 / count
    start = now - hours * 3600
    floor = max(start, since) if isinstance(since, (int, float)) else start
    sums, ns = [0.0] * count, [0] * count
    for point in points or []:
        if not isinstance(point, dict) or not isinstance(point.get(key), (int, float)):
            continue
        t = point.get("time")
        if not isinstance(t, (int, float)) or t < floor or t > now:
            continue
        i = min(int((t - start) // span), count - 1)
        sums[i] += float(point[key])
        ns[i] += 1
    return [round(sums[i] / ns[i], 4) if ns[i] else None for i in range(count)]


def _pressure(points, now, since=None):
    """The VM's CPU pressure ("some") over the last hour, as a fraction; None where Proxmox keeps none (VE 8)."""
    floor = max(now - 3600, since) if isinstance(since, (int, float)) else now - 3600
    values = [
        float(p["pressurecpusome"])
        for p in points or []
        if isinstance(p, dict)
        and isinstance(p.get("pressurecpusome"), (int, float))
        and isinstance(p.get("time"), (int, float))
        and floor <= p["time"] <= now
    ]
    if not values:
        return None
    # A percentage, as the kernel's pressure files give it (some avg10=0.50 is half a percent).
    return round(sum(values) / len(values) / 100, 4)


def status_vm_api(results, now=None, since=None):
    """One VM's API reads (play 2's loop over status/current, rrddata, pending and the agent) as its "api" part.

    ``results`` is the loop's registered results, each with ``item.name`` saying which read it was; ``since`` when
    the VM was made, before which its history is another VM's of the same ID."""
    now = time.time() if now is None else now
    by = {}
    for result in results or []:
        if isinstance(result, dict) and isinstance(result.get("item"), dict):
            by[result["item"].get("name")] = _data(result)
    current = by.get("current") if isinstance(by.get("current"), dict) else {}
    rrd = by.get("rrddata") if isinstance(by.get("rrddata"), list) else []
    pending = by.get("pending") if isinstance(by.get("pending"), list) else []
    fsinfo = (by.get("fsinfo") or {}).get("result") if isinstance(by.get("fsinfo"), dict) else None
    osinfo = (by.get("osinfo") or {}).get("result") if isinstance(by.get("osinfo"), dict) else None
    root = None
    for fs in fsinfo or []:
        if isinstance(fs, dict) and fs.get("mountpoint") == "/" and isinstance(fs.get("total-bytes"), (int, float)):
            root = {"used": fs.get("used-bytes"), "total": fs.get("total-bytes"), "type": fs.get("type", "")}
    return {
        "read": bool(current),
        "state": current.get("status"),
        "qmpstatus": current.get("qmpstatus"),
        "uptime": current.get("uptime"),
        "cpu": current.get("cpu"),
        "mem": current.get("mem"),
        "maxmem": current.get("maxmem"),
        "cpu_24h": _buckets(rrd, now, since=since),
        "pressure_1h": _pressure(rrd, now, since),
        "pending": sorted(
            p.get("key") for p in pending if isinstance(p, dict) and ("pending" in p or "delete" in p) and p.get("key")
        ),
        "root": root,
        "os": {"pretty": osinfo.get("pretty-name"), "kernel": osinfo.get("kernel-release")}
        if isinstance(osinfo, dict)
        else None,
    }


SYSTEM_KEYS = (
    "os",
    "kernel",
    "kernel_newest",
    "reboot_required",
    "reboot_packages",
    "load",
    "memory",
    "uptime",
    "upgrades",
    "security",
    "claude",
    "vscode_windows",
    "unread",
)


def status_system_read(result):
    """What vm_status_info read of a VM, as the "system" part: its fields alone, or None when it was not read."""
    if not isinstance(result, dict) or result.get("failed") or result.get("unreachable") or result.get("skipped"):
        return None
    return {key: result.get(key) for key in SYSTEM_KEYS}


# ------------------------------------------------------------------------------------------------ the model ----


def _fmt_gib(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return "—"
    gib = value / GIB
    return f"{gib:.1f}" if gib < 10 else f"{gib:.0f}"


def _uptime(seconds):
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    s = int(seconds)
    if s >= 86400:
        return f"{s // 86400} d {s % 86400 // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60} m"
    if s >= 60:
        return f"{s // 60} m"
    return f"{s} s"


def _ago(seconds):
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return ""
    s = int(seconds)
    if s >= 86400:
        return f"{s // 86400} d ago"
    if s >= 3600:
        return f"{s // 3600} h ago"
    if s >= 60:
        return f"{s // 60} m ago"
    return "just now"


def _item(level, vm, subject, text, command="", source="status"):
    return {"level": level, "vm": vm, "source": source, "subject": subject, "text": text, "command": command}


def _and(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _drift_item(vm, drift, settings):
    hardware = drift.get("hardware") or []
    cloud_init = drift.get("cloud_init") or []
    if not hardware and not cloud_init:
        return None
    asks, has = [], []
    wanted = {d.get("setting"): d.get("wanted") for d in hardware}
    # A fixed allocation, the default, asks for its memory twice: said once.
    if "memory" in wanted and wanted.get("balloon") == wanted.get("memory"):
        hardware = [d for d in hardware if d.get("setting") != "balloon"]
    for d in hardware:
        setting, wanted, got = d.get("setting"), d.get("wanted"), d.get("has")
        if setting == "cores":
            asks.append(plural(int(wanted), "core"))
            has.append(plural(int(got), "core"))
        elif setting == "memory":
            asks.append(f"{_fmt_gib(int(wanted) * 1024**2)} GiB of memory")
            has.append(f"{_fmt_gib(int(got) * 1024**2)} GiB")
        elif setting == "balloon":
            asks.append(f"a minimum of {_fmt_gib(int(wanted) * 1024**2)} GiB")
            has.append(f"a minimum of {_fmt_gib(int(got) * 1024**2)} GiB")
        elif setting == "disk":
            asks.append(f"a {_fmt_gib(wanted)} GiB disk")
            has.append(f"a {_fmt_gib(got)} GiB disk")
        elif setting == "onboot":
            asks.append("a start at boot" if wanted else "no start at boot")
            has.append("no start at boot" if wanted else "a start at boot")
    name = vm["name"]
    parts = []
    if asks:
        parts.append(f"local.yml asks for {_and(asks)}; {name} has {_and(has)}")
    if cloud_init:
        parts.append(f"{'Its' if parts else 'its'} cloud-init {_and(cloud_init)} differ from the settings")
    text = ". ".join(parts)
    command = f"just ANSIBLE_ARGS='-e proxmox_vm_force_update=true' deploy {name} --only provision"
    note = f"(this also resets {name}'s tags, and starts it if it is stopped"
    if cloud_init:
        note += "; its next boot then runs as a new cloud-init instance, which regenerates its SSH host keys"
    return _item("todo", name, name, text, f"{command} {note})")


def _vm_items(vm, settings, live):
    """The attention items of one VM, from what was read of it."""
    items = []
    name = vm.get("name") or f"VMID {vm.get('vmid')}"
    skipped = vm.get("skipped")
    if skipped == "node":
        items.append(
            _item(
                "unread",
                name,
                name,
                f"its node {vm.get('node')} is down, so Proxmox lists no more of it",
                "start the node, or remove the VM from Proxmox if the node is gone",
            )
        )
        return items
    if skipped == "duplicate":
        items.append(
            _item(
                "problem",
                name,
                name,
                f"more than one VM tagged {settings.get('tag', 'claude-on-proxmox')} is named {name}: deploy and"
                " destroy refuse both, and status reads neither past the listing",
                "rename or remove the duplicates in Proxmox",
            )
        )
        return items
    if skipped == "name":
        items.append(
            _item(
                "unread",
                name,
                name,
                "its name is not one a recipe takes, so status reads no more of it",
                "rename it in Proxmox",
            )
        )
        return items
    if skipped == "nameless":
        return items
    if vm.get("unfinished"):
        items.append(_item("todo", name, name, "its first deploy did not finish", f"just deploy {name}"))
    api = vm.get("api")
    # A VM of the inventory's, with discovery off, is never asked of Proxmox.
    if api is None and not live and vm.get("read") and not vm.get("inventory"):
        items.append(_item("unread", name, name, "Proxmox did not answer for it"))
    status = vm.get("state")
    if status == "running" and not vm.get("address") and api is not None:
        if (vm.get("uptime") or 0) > 180:
            items.append(
                _item(
                    "unread",
                    name,
                    name,
                    f"it has run {_uptime(vm.get('uptime'))} and its guest agent reports no address on"
                    f" {settings.get('nic', 'net0')}",
                    "check the guest agent in Proxmox's console for it",
                )
            )
    drift = vm.get("drift")
    if drift:
        item = _drift_item(vm, drift, settings)
        if item:
            items.append(item)
    if api and api.get("pending"):
        items.append(
            _item(
                "todo",
                name,
                name,
                f"{_and(api['pending'])} changed in Proxmox and {'waits' if len(api['pending']) == 1 else 'wait'}"
                " for a restart",
                f"restart {name} from Proxmox",
            )
        )
    root = vm.get("root")
    if root and isinstance(root.get("used"), (int, float)) and root.get("total"):
        fill = root["used"] / root["total"]
        if fill >= 0.8:
            items.append(
                _item(
                    "problem" if fill >= 0.9 else "todo",
                    name,
                    name,
                    f"its root disk is {fill:.0%} full ({_fmt_gib(root['used'])} of {_fmt_gib(root['total'])} GiB):"
                    " Claude, Docker and apt fail on a full one",
                    f"free space on {name}, or grow vm_disk_size in local.yml",
                )
            )
    aliases = settings.get("aliases_enabled")
    if aliases and vm.get("address") and vm.get("alias") is False and status == "running":
        items.append(_item("todo", name, name, "it has no SSH alias", "just deploy --only ssh-config"))
    return items


def _short_kernel(release):
    """A kernel release as the report shows it: Ubuntu's flavour suffix dropped (6.8.0-85-generic is 6.8.0-85)."""
    return re.sub(r"-(generic|virtual|kvm|lowlatency)$", "", str(release or ""))


def status_work_read(result, directory):
    """What git_checkouts_info's work read of a VM's checkouts, as the "work" part: the directory read, how many
    checkouts and extra worktrees it holds, and per checkout what is on no remote or not committed. None when it was
    not read."""
    if not isinstance(result, dict) or result.get("failed") or result.get("unreachable") or result.get("skipped"):
        return None
    checkouts = [c for c in result.get("checkouts") or [] if isinstance(c, dict) and isinstance(c.get("work"), dict)]
    repositories = []
    for checkout in checkouts:
        work = checkout["work"]
        trees = [t for t in work.get("worktrees") or [] if isinstance(t, dict)]
        unpushed = [u for u in work.get("unpushed") or [] if isinstance(u, dict)]
        repositories.append(
            {
                "name": checkout.get("name"),
                "worktrees": len(trees),
                "commits_on_no_remote": sum(int(u.get("commits") or 0) for u in unpushed),
                "branches_on_no_remote": len(unpushed),
                "detached_commits": sum(int(t.get("own_commits") or 0) for t in trees),
                "dirty_worktrees": sum(1 for t in trees if t.get("dirty")),
                "stashes": int(work.get("stashes") or 0),
            }
        )
    return {
        "dir": directory,
        "checkouts": len(checkouts),
        "worktrees": sum(max(0, r["worktrees"] - 1) for r in repositories),
        "repositories": repositories,
    }


def _work_said(repository):
    """What of one checkout is on no remote or not committed, in words; "" for nothing."""
    parts = []
    if repository.get("commits_on_no_remote"):
        parts.append(
            f"{plural(repository['commits_on_no_remote'], 'commit')} on"
            f" {plural(repository['branches_on_no_remote'], 'branch', 'branches')}"
        )
    if repository.get("detached_commits"):
        parts.append(f"{plural(repository['detached_commits'], 'commit')} on a detached HEAD")
    if repository.get("stashes"):
        parts.append(plural(repository["stashes"], "stash", "stashes"))
    if repository.get("dirty_worktrees"):
        dirty = repository["dirty_worktrees"]
        parts.append(
            "changes not committed"
            if dirty == 1 and repository.get("worktrees", 1) == 1
            else f"changes in {plural(dirty, 'worktree')}"
        )
    return f"{repository.get('name')}: {', '.join(parts)}" if parts else ""


def status_refusal(said):
    """What kind of refusal ssh's message is, read from the whole of it before anything is cut: "changed" (a host
    key other than the one this machine holds), "unknown" (none held), "timeout", "refused", "denied", or ""."""
    low = str(said or "").lower()
    if "host identification has changed" in low or ("host key for" in low and "has changed" in low):
        return "changed"
    if "host key verification failed" in low or "host key is known" in low:
        return "unknown"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "connection refused" in low:
        return "refused"
    if "permission denied" in low:
        return "denied"
    return ""


def _ssh_refusal(vm, ssh, settings):
    """What ssh's refusal of a VM means, and what to do about it: (text, command)."""
    name = vm.get("name")
    address = vm.get("address") or name
    user = settings.get("vm_user") or "dev"
    said = ssh.get("unreachable") or ""
    kind = ssh.get("refusal") or status_refusal(said)
    if kind == "changed":
        return (
            f"not read: the SSH host key at {address} is not the one this machine holds. {name} was rebuilt or"
            " renamed, or another machine answers at that address",
            f"find out which first. If {name} was rebuilt or renamed: ssh-keygen -R {address}, then just deploy"
            f" {name} --only configure",
        )
    if kind == "unknown":
        return (
            f"not read: this machine holds no SSH host key for {address}, and status adds none. Its address"
            " changed, or nothing here has connected to it yet",
            f"just deploy {name} --only configure takes a new VM's key, as every run does; or ssh {user}@{address}"
            " true, which shows you the fingerprint and asks first",
        )
    if kind == "timeout":
        return (f"not read: it did not answer SSH at {address}", f"check {name} in Proxmox's console")
    if kind == "refused":
        return (
            f"not read: it refused SSH at {address}: sshd is not running there",
            f"check {name} in Proxmox's console",
        )
    if kind == "denied":
        return (
            f"not read: it refused this machine's SSH key as {user}",
            "check vm_ssh_public_keys in local.yml against the key this machine offers",
        )
    return (f"not read: SSH to {address} failed: {one_line(said, 160)}", "")


def _when(stamp, now):
    """How long ago systemd's timestamp (\"Sat 2026-10-25 10:02:11 UTC\") was, or "" when it cannot be read."""
    try:
        moment = datetime.strptime(" ".join(str(stamp).split()[1:3]), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    if str(stamp).split()[-1] not in ("UTC", "GMT"):
        return ""
    return _ago(now - moment.replace(tzinfo=timezone.utc).timestamp()).replace(" ago", "")


def _remote_control(claude, now):
    """Remote Control as one state, ("running" | "down" | "waits" | "stopped" | "not-set-up" | "off"), and its words."""
    if not claude.get("remote_control_wanted"):
        return "off", "off: API key" if claude.get("api_key") else "off"
    service = claude.get("service") or {}
    path = claude.get("path_unit") or {}
    load, active, sub = service.get("LoadState"), service.get("ActiveState"), service.get("SubState")
    if load in (None, "", "not-found"):
        return "not-set-up", "not set up"
    since = _when(service.get("StateChangeTimestamp"), now)
    if active == "active" and sub == "running":
        return "running", "running" + (f" {since}" if since else "")
    restarts = str(service.get("NRestarts") or "0")
    if active == "failed" or sub == "auto-restart" or (active == "activating" and restarts != "0"):
        said = "down" + (f" {since}" if since else "")
        if restarts not in ("", "0"):
            said += f", {plural(int(restarts), 'restart')}" if restarts.isdigit() else ""
        return "down", said
    if path.get("ActiveState") == "active":
        return "waits", "waits for the login"
    return "stopped", "stopped"


def _ssh_items(vm, settings, live, now):
    """The attention items of what was read of a VM over SSH, each role's own line among them."""
    items = []
    name = vm.get("name")
    ssh = vm.get("ssh")
    if not vm.get("needs_ssh"):
        return items
    if ssh is None:
        if not live:
            items.append(_item("unread", name, name, "not read over SSH: the run ended before it was"))
        return items
    if ssh.get("unreachable"):
        text, command = _ssh_refusal(vm, ssh, settings)
        items.append(_item("unread", name, name, text, command))
        return items
    unread = ssh.get("unread") or {}
    for part, said in (
        ("system", "its system"),
        ("claude", "Claude Code"),
        ("github", "gh"),
        ("tailscale", "Tailscale"),
        ("work", "its checkouts"),
    ):
        if unread.get(part):
            items.append(_item("unread", name, name, f"{said} was not read: {unread[part]}"))
    system = ssh.get("system")
    if system:
        if (system.get("unread") or {}).get("upgrades"):
            items.append(_item("unread", name, name, f"apt's upgrades were not read: {system['unread']['upgrades']}"))
        if system.get("reboot_required"):
            running, newest = system.get("kernel") or "", system.get("kernel_newest") or ""
            why = (
                f"kernel {_short_kernel(newest)} is installed, {_short_kernel(running)} runs"
                if newest and running and newest != running
                else f"{_and(system.get('reboot_packages') or []) or 'an upgrade'} asks for one"
            )
            busy = []
            if system.get("claude"):
                busy.append(plural(system["claude"], "claude process", "claude processes"))
            if system.get("vscode_windows"):
                busy.append(plural(system["vscode_windows"], "VS Code window"))
            text = f"waits for a reboot: {why}" + (
                f"; {_and(busy)} {'is' if len(busy) == 1 and busy[0].startswith('1 ') else 'are'} open there"
                if busy
                else ""
            )
            items.append(
                _item(
                    "todo",
                    name,
                    name,
                    text,
                    "restart it from Proxmox once they are done, then just deploy --only ssh-config"
                    if busy
                    else "restart it from Proxmox, then just deploy --only ssh-config",
                )
            )
        if isinstance(system.get("security"), int) and system["security"] > 0:
            user = settings.get("vm_user") or "dev"
            waits = "waits" if system["security"] == 1 else "wait"
            items.append(
                _item(
                    "todo",
                    name,
                    name,
                    f"{plural(system['security'], 'security upgrade')} {waits} to be installed",
                    f"ssh -t {user}@{vm.get('address') or name} sudo apt-get upgrade",
                )
            )
    claude = ssh.get("claude")
    if claude:
        if not claude.get("installed"):
            items.append(
                _item("todo", name, name, "Claude Code is not installed", f"just deploy {name} --tags claude_code")
            )
        elif not claude.get("api_key") and not claude.get("login_stored"):
            items.append(
                _item(
                    "todo",
                    name,
                    name,
                    "not signed in. Run "
                    + (claude.get("login_command") or f"just login {name} --only claude")
                    + " to sign in from your machine: it opens the sign-in page and asks for the code the page shows.",
                    source="claude_code",
                )
            )
        state, said = _remote_control(claude, now)
        unit = claude.get("unit") or "claude-remote-control"
        user = settings.get("vm_user") or "dev"
        if state == "down":
            items.append(
                _item(
                    "problem",
                    name,
                    name,
                    f"Remote Control is not running: {unit} is {said}",
                    f"ssh {user}@{vm.get('address') or name} journalctl -u {unit} -n 50, then just deploy {name}"
                    " --tags claude_code starts it again",
                )
            )
        elif state == "not-set-up":
            items.append(
                _item("todo", name, name, "Remote Control is not set up", f"just deploy {name} --tags claude_code")
            )
        elif state == "stopped" and claude.get("login_stored"):
            items.append(
                _item(
                    "todo",
                    name,
                    name,
                    "Remote Control is stopped",
                    f"just deploy {name} --tags claude_code starts it, or says what stops it",
                )
            )
    github = ssh.get("github")
    if github:
        if github.get("unchecked"):
            items.append(
                _item("unread", name, name, f"gh could not check its login with GitHub: {github.get('error')}")
            )
        elif github.get("left") and github.get("line"):
            items.append(_item("todo", name, name, github["line"], source="github_projects"))
    tailscale = ssh.get("tailscale")
    if tailscale and tailscale.get("enabled"):
        case = tailscale.get("case")
        if case == "check-daemon-down":
            items.append(
                _item(
                    "problem", name, name, "tailscaled is not running", f"just deploy {name} --tags tailscale starts it"
                )
            )
        elif case == "check-no-package":
            items.append(
                _item(
                    "todo", name, name, "Tailscale is not installed", f"just deploy {name} --tags tailscale installs it"
                )
            )
        elif case == "would-join":
            items.append(
                _item(
                    "todo",
                    name,
                    name,
                    "Tailscale is not signed in; vault_tailscale_auth_key joins it",
                    f"just deploy {name} --tags tailscale",
                )
            )
        elif tailscale.get("left") and tailscale.get("line"):
            items.append(_item("todo", name, name, tailscale["line"], source="tailscale"))
    return items


def _template_items(template):
    """The template, when the next just deploy rebuilds it: it differs from what its settings would build, and
    proxmox_template_rebuild leaves rebuilding on. Kept as it is, it is only said on its line."""
    drift = template.get("drift") or []
    if template.get("missing") or not drift or not template.get("rebuild", True):
        return []
    text = (
        f"the template {template.get('name')} differs from its settings ({'; '.join(drift)}), so the next just"
        " deploy rebuilds it, over root SSH"
    )
    return [_item("todo", "template", "template", text, "just deploy --only template rebuilds it now")]


def _storage_items(storages):
    items = []
    for s in storages:
        used, total = s.get("used"), s.get("total")
        if isinstance(used, (int, float)) and total:
            fill = used / total
            if fill >= 0.8:
                items.append(
                    _item(
                        "problem" if fill >= 0.9 else "todo",
                        s["storage"],
                        s["storage"],
                        f"it is {fill:.0%} full ({_fmt_gib(used)} of {_fmt_gib(total)} GiB): as it fills, the VMs on"
                        " it get I/O errors",
                        "free space on it, or move disks off it",
                    )
                )
    return items


def status_report(found, read_at="", now=None, live=False):
    """The report's model, from what every host found: localhost's ``fleet``, and each VM's parts by its name.

    ``found`` is {host: status_found}. With ``live``, a VM's part not read yet is pending rather than "not read",
    and nothing is ranked: the model of a frame the callback draws while the run goes on."""
    now = time.time() if now is None else now
    found = status_clean(found or {})
    read_at = one_line(str(read_at or ""), FIELD_LIMIT)
    fleet = (found.get("localhost") or {}).get("fleet") or {}
    settings = dict(fleet.get("settings") or {})
    sections = status_sections(settings.get("sections"))
    aliases = fleet.get("aliases")
    settings["aliases_enabled"] = aliases is not None
    proxmox = dict(fleet.get("proxmox") or {})
    ssh_wanted = status_needs_ssh(sections)
    template = dict(fleet.get("template") or {})
    if template.get("ctime"):
        template["built_ago"] = _ago(now - template["ctime"])
    vms = []
    for listed in fleet.get("vms") or []:
        vm = dict(listed)
        parts = found.get(vm.get("name"), {}) if vm.get("read") else {}
        api = parts.get("api")
        vm["api"] = api
        vm["state"] = (api or {}).get("state") or vm.get("status")
        if api:
            for key in ("uptime", "cpu", "mem", "maxmem"):
                if api.get(key) is not None:
                    vm[key] = api[key]
        vm["root"] = (api or {}).get("root")
        vm["os"] = (api or {}).get("os")
        vm["drift"] = parts.get("drift")
        vm["alias"] = (vm.get("name") in aliases) if aliases is not None and vm.get("name") else None
        created = vm.get("created")
        if created and template.get("ctime") and created == template.get("ctime"):
            created = None
        vm["created_ago"] = _ago(now - created) if created else ""
        balloon = vm.get("config", {}).get("balloon")
        memory = vm.get("config", {}).get("memory")
        vm["min_mem"] = (
            int(balloon) * 1024**2
            if balloon not in (None, "") and memory and int(balloon) < int(memory) and int(balloon) > 0
            else None
        )
        vm["pending_api"] = live and vm.get("read") and api is None
        # Read over SSH by play 2's own test: a section that needs it, and a VM the listing shows running with an
        # address.
        vm["ssh"] = parts.get("ssh")
        vm["needs_ssh"] = bool(ssh_wanted and vm.get("read") and vm.get("status") == "running" and vm.get("address"))
        vm["pending_ssh"] = live and vm["needs_ssh"] and vm["ssh"] is None
        # Memory in use as the VM counts it, where it was read over SSH: Proxmox's own figure counts the page cache
        # as used, which on a live VM was twice the VM's MemTotal - MemAvailable. Proxmox's otherwise.
        memory = ((vm["ssh"] or {}).get("system") or {}).get("memory") or {}
        if isinstance(memory.get("total"), int) and isinstance(memory.get("available"), int) and memory["total"]:
            vm["mem_used"], vm["mem_total"], vm["mem_source"] = (
                memory["total"] - memory["available"],
                memory["total"],
                "guest",
            )
        else:
            vm["mem_used"], vm["mem_total"], vm["mem_source"] = vm.get("mem"), vm.get("maxmem"), "proxmox"
        vms.append(vm)
    # With discovery off the VMs are the inventory's (claude_vms), which the API never listed: each is shown from
    # what was read of it over SSH.
    listed = {vm.get("name") for vm in vms}
    inventory = (
        sorted(h for h in found if h != "localhost" and h not in listed and found[h]) if proxmox.get("off") else []
    )
    for host in inventory:
        vms.append(
            {
                "name": host,
                "read": True,
                "status": "running",
                "state": "running",
                "api": None,
                "address": "",
                "inventory": True,
                "config": {},
                "created_ago": "",
                "pending_api": False,
                "alias": None,
                "ssh": found[host].get("ssh"),
                "needs_ssh": ssh_wanted,
                "pending_ssh": live and ssh_wanted and found[host].get("ssh") is None,
            }
        )
    items = []
    if proxmox.get("unread"):
        items.append(_item("unread", "proxmox", "proxmox", proxmox["unread"], settings.get("proxmox_hint", "")))
    for name in fleet.get("unmatched") or []:
        nameless = fleet.get("nameless") or []
        text = f"no VM tagged {settings.get('tag', 'claude-on-proxmox')} is named {name}"
        if nameless:
            vmids = ", ".join(str(n.get("vmid")) for n in nameless)
            text += f"; Proxmox lists {plural(len(nameless), 'VM')} without a name, on a node that is down: {vmids}"
        items.append(_item("unread", name, name, text, "just status lists every VM this project created"))
    for vm in vms:
        items += _vm_items(vm, settings, live)
        items += _ssh_items(vm, settings, live, now)
    items += _storage_items(fleet.get("storages") or [])
    items += _template_items(fleet.get("template") or {})
    # Two VMs of one name each say the same thing about it: once is enough.
    seen = set()
    items = [it for it in items if (key := (it["level"], it["vm"], it["text"])) not in seen and not seen.add(key)]
    order = {level: i for i, level in enumerate(LEVELS)}
    vm_order = {vm.get("name"): i for i, vm in enumerate(vms)}
    items.sort(key=lambda it: (order[it["level"]], vm_order.get(it["vm"], -1)))
    counts = {level: sum(1 for it in items if it["level"] == level) for level in LEVELS}
    exit_code = 2 if counts["unread"] else 1 if counts["problem"] else 0
    nodes = proxmox.get("nodes") or []
    if proxmox.get("off"):
        title = f"claude-on-proxmox  {read_at} · the inventory's VMs: claude_vm_discovery is off"
    elif proxmox.get("unread"):
        title = f"claude-on-proxmox  {read_at}"
    else:
        what = (
            ", ".join(settings.get("names"))
            if settings.get("names")
            else "no VMs"
            if not vms
            else plural(len(vms), "VM")
        )
        version = f", Proxmox VE {proxmox['version']}" if proxmox.get("version") else ""
        title = f"claude-on-proxmox  {read_at} · {', '.join(nodes)}{version} · {what}"
    return {
        "exit": exit_code,
        "read_at": read_at,
        "live": bool(live),
        "sections": sections,
        "title": title,
        "proxmox": proxmox,
        "vms": vms,
        "others": fleet.get("others") or {},
        "storages": fleet.get("storages") or [],
        "template": template,
        "attention": items,
        "counts": counts,
        "settings": {
            k: settings.get(k) for k in ("names", "tag", "tailscale_enabled", "nic", "aliases_enabled", "vm_user")
        },
        "now": now,
    }


# ------------------------------------------------------------------------------------------------- the text ----


def _dot(state):
    return {"running": ("●", "g"), "stopped": ("○", "d"), "paused": ("●", "y")}.get(state, ("●", "y"))


def _vms_lines(model):
    vms = model["vms"]
    others = model.get("others") or {}
    nodes = ", ".join(model["proxmox"].get("nodes") or [])
    lines = []
    if not vms:
        if others.get("count"):
            lines.append(
                [
                    seg("VMS", "h"),
                    seg(
                        f"  + {plural(others['count'], 'other VM')} on {nodes}, not this project's:"
                        f" {plural(others['vcpus'], 'vCPU')}, {_fmt_gib(others['mem'])} GiB,"
                        f" {others['running']} running",
                        "d",
                    ),
                ]
            )
        return lines
    vcpus = sum(int(vm.get("vcpus") or 0) for vm in vms)
    mem = sum(int(vm.get("maxmem") or 0) for vm in vms)
    disk = sum(int(vm.get("maxdisk") or 0) for vm in vms)
    lines.append(
        [
            seg("VMS", "h"),
            seg(
                f"  {nodes} · this project: {plural(len(vms), 'VM')}, {plural(vcpus, 'vCPU')}, {_fmt_gib(mem)} GiB,"
                f" {_fmt_gib(disk)} GiB of disk"
            ),
        ]
    )
    # A name past 13 characters is cut, so the row fits 100 columns with every cell at its widest.
    nw = min(13, max([5] + [len(vm.get("name") or "(no name)") for vm in vms]))
    lines.append(
        [
            seg(
                f" VMID  {'NAME':<{nw + 4}}UP        vCPU  CPU  LAST 24 H     MEM GiB             ROOT GiB   CREATED",
                "d",
            )
        ]
    )
    for vm in vms:
        name = vm.get("name") or "(no name)"
        name = name if len(name) <= nw else name[: nw - 1] + "…"
        state = vm.get("state") or "unknown"
        dot, style = _dot(state)
        if vm.get("skipped") == "node":
            dot, style = "?", "r"
        row = [seg(f" {vm.get('vmid', ''):>4}  "), seg(dot, style), seg(f" {name:<{nw}}  ")]
        if state != "running" or vm.get("skipped") == "node":
            word = "stopped" if state == "stopped" else "node down" if vm.get("skipped") == "node" else state
            mem = "— / " + _fmt_gib(vm.get("maxmem"))
            rest = f"{word:<10}{vm.get('vcpus') or '':>4} {'':4}  {'':12}  {mem:<18}  "
            rest += f"{'— / ' + _fmt_gib(vm.get('maxdisk')):<9}  {vm.get('created_ago', '')}"
            lines.append(row + [seg(rest, "d")])
            continue
        pending = vm.get("pending_api")
        cpu = vm.get("cpu")
        cpu_text = f"{cpu * 100:.0f}%" if isinstance(cpu, (int, float)) else "—"
        cpu_spark = PENDING if pending else spark((vm.get("api") or {}).get("cpu_24h"))
        mem_text = f"{_fmt_gib(vm.get('mem_used', vm.get('mem')))} / {_fmt_gib(vm.get('mem_total', vm.get('maxmem')))}"
        if vm.get("min_mem"):
            mem_text += f", min {_fmt_gib(vm['min_mem'])}"
        root = vm.get("root")
        root_text = PENDING if pending else f"{_fmt_gib(root['used'])} / {_fmt_gib(root['total'])}" if root else "—"
        row += [
            seg(f"{_uptime(vm.get('uptime')):<10}{vm.get('vcpus') or '':>4} {cpu_text:>4}  "),
            seg(f"{cpu_spark:<12}", "c"),
            seg(f"  {mem_text:<18}  {root_text:<9}  {vm.get('created_ago', '') or '—'}"),
        ]
        lines.append(row)
        pressure = (vm.get("api") or {}).get("pressure_1h")
        if isinstance(pressure, (int, float)) and pressure > 0.10:
            lines.append([seg(f"{'':{14 + nw}}waited for the host's CPU {pressure:.0%} of the last hour", "d")])
    if others.get("count"):
        lines.append(
            [
                seg(
                    f"       + {plural(others['count'], 'other VM')} on"
                    f" {', '.join(others.get('nodes') or []) or nodes},"
                    f" not this project's: {plural(others['vcpus'], 'vCPU')}, {_fmt_gib(others['mem'])} GiB,"
                    f" {others['running']} running",
                    "d",
                )
            ]
        )
    return lines


def _storage_lines(model):
    if not model.get("storages") and not model.get("template"):
        return []
    lines = [[seg("STORAGE", "h")]]
    for s in model.get("storages") or []:
        used, total = s.get("used"), s.get("total")
        line = [seg(f" {s['storage']:<10} {s.get('type', ''):<8} ")]
        if isinstance(used, (int, float)) and total:
            fraction = used / total
            line += bar(fraction) + [seg(f"  {fraction:>3.0%}  {_fmt_gib(used)} / {_fmt_gib(total)} GiB")]
        else:
            line.append(seg("not read", "d"))
        line.append(seg(f" · this project's disks {_fmt_gib(s['ours']) + ' GiB' if s.get('ours') else 'none'}"))
        lines.append(line)
        if s.get("provisioned") and total and s.get("type") in ("lvmthin", "zfspool") and s["provisioned"] > total:
            lines.append(
                [
                    seg(
                        f"{'':12}its disks are provisioned at {_fmt_gib(s['provisioned'])} GiB in all,"
                        " more than the pool holds",
                        "d",
                    )
                ]
            )
    template = model.get("template") or {}
    if template.get("missing"):
        lines.append([seg(f" {'template':<10} {template.get('name')}: not built yet; the first just deploy builds it")])
    elif template:
        built = f" · built {template['built_ago']}" if template.get("built_ago") else ""
        where = f" on {template['storage']}" if template.get("storage") else ""
        line = [seg(f" {'template':<10} {template.get('name')} ({template.get('vmid')}){where}{built}")]
        # How it differs is said under ATTENTION; here only what the next deploy does with it.
        if template.get("drift"):
            line += [
                seg(" · "),
                seg("next deploy rebuilds it", "y") if template.get("rebuild", True) else seg("differs, kept", "d"),
            ]
        lines.append(line)
    return lines


def _ssh_vms(model):
    """The VMs a section read over SSH shows: each this project created that is read over SSH, by name."""
    return [vm for vm in model["vms"] if vm.get("name") and vm.get("needs_ssh")]


def _name_width(vms):
    return min(13, max([5] + [len(vm["name"]) for vm in vms]))


def _cell(text, width, style=""):
    """One column's cell: ``text`` cut to ``width`` - 1 and padded, so a long value never pushes the next column."""
    text = text if len(text) < width else text[: width - 2] + "…"
    return [seg(f"{text:<{width}}", style)]


def _access_lines(model):
    vms = [vm for vm in model["vms"] if vm.get("name")]
    if not vms:
        return []
    aliases = model["settings"].get("aliases_enabled")
    nw = _name_width(vms)
    # The tailnet column, from the Tailscale role's read: whenever the VMs are read over SSH, or Tailscale is off.
    show_tailnet = any(vm.get("needs_ssh") for vm in vms) or not model["settings"].get("tailscale_enabled")
    head = "LAN           " + ("SSH ALIAS   " if aliases else "") + ("TAILNET" if show_tailnet else "")
    # The tailnet names padded to the longest, so their addresses line up.
    dns_width = max([0] + [len(((vm.get("ssh") or {}).get("tailscale") or {}).get("dns_name") or "") for vm in vms])
    lines = [[seg("ACCESS", "h"), seg(" " * max(1, nw + 4 - len("ACCESS")) + head, "d")]]
    for vm in vms:
        state = vm.get("state")
        lan = vm.get("address") or ("stopped" if state == "stopped" else "—")
        name = vm["name"] if len(vm["name"]) <= nw else vm["name"][: nw - 1] + "…"
        row = [seg(f" {name:<{nw}}   "), seg(f"{lan:<14}", "d" if not vm.get("address") else "")]
        if aliases:
            row.append(seg(f"{vm['name'] if vm.get('alias') else 'none':<12}", "" if vm.get("alias") else "y"))
        if show_tailnet:
            row += _tailnet_cells(vm, model, dns_width)
        lines.append(row)
    lines.append([seg(" VS Code: just code <name> [--project <repo>] [--session <id>]", "d")])
    return lines


def _tailnet_cells(vm, model, dns_width=0):
    if not model["settings"].get("tailscale_enabled"):
        return [seg("off: tailscale_enabled is false", "d")]
    if vm.get("pending_ssh"):
        return [seg(PENDING, "c")]
    tailscale = (vm.get("ssh") or {}).get("tailscale")
    if not tailscale:
        return [seg("—", "d")]
    if tailscale.get("backend") == "Running" and tailscale.get("dns_name"):
        return [seg(f"{tailscale['dns_name']:<{dns_width}}  {tailscale.get('ipv4') or ''}".rstrip())]
    return [seg(_tailscale_cell(tailscale)[0][2:], "d")]


def _claude_cell(claude):
    if not claude.get("installed"):
        return "○ not installed", "y"
    version = f"{claude['version']} " if claude.get("version") else ""
    if claude.get("api_key"):
        return f"● {version}API key", "g"
    if claude.get("login_stored"):
        return f"● {version}signed in", "g"
    return f"○ {version}not signed in", "y"


def _remote_control_cell(claude, now):
    state, said = _remote_control(claude, now)
    if state == "running":
        return f"● {said}", "g"
    if state == "down":
        return f"● {said}", "r"
    if state in ("not-set-up", "stopped"):
        return f"○ {said}", "y"
    return f"○ {said}", "d"


def _github_cell(github):
    if not github.get("installed"):
        return "○ no gh", "d"
    if not github.get("readable"):
        return "? gh too old to say", "y"
    if github.get("unchecked"):
        return "? not checked", "r"
    if github.get("signed_in"):
        return f"● {github.get('account') or 'signed in'}", "g"
    if not github.get("managed"):
        return "○ left alone", "d"
    return "○ not signed in", "y"


TAILSCALE_CELLS = {
    "off": ("○ off", "d"),
    "not-ours": ("● installed by hand", "d"),
    "Running": ("● running", "g"),
    "NeedsLogin": ("○ needs a login", "y"),
    "would-join": ("○ needs a login", "y"),
    "page-pending": ("○ page waiting", "y"),
    "expired": ("○ key expired", "y"),
    "refused": ("○ key refused", "y"),
    "NeedsMachineAuth": ("○ needs approval", "y"),
    "Stopped": ("○ stopped", "y"),
    "connecting": ("○ connecting", "y"),
    "check-no-package": ("○ not installed", "y"),
    "check-daemon-down": ("● daemon down", "r"),
}


def _tailscale_cell(tailscale):
    if not tailscale.get("enabled"):
        return TAILSCALE_CELLS["off"]
    return TAILSCALE_CELLS.get(tailscale.get("case"), (f"○ {tailscale.get('backend') or 'unknown'}", "y"))


def _not_read_cell(vm):
    """The cell of a VM read over SSH that a part could not be read of: pending while live, else why not."""
    if vm.get("pending_ssh"):
        return PENDING, "c"
    if (vm.get("ssh") or {}).get("unreachable"):
        return "? not reached", "r"
    return "? not read", "r"


def _signins_lines(model):
    vms = _ssh_vms(model)
    if not vms:
        return []
    nw = _name_width(vms)
    first = max(nw + 3, len("SIGN-INS") + 2)
    widths = (25, 25, 18)
    head = [
        seg("SIGN-INS", "h"),
        seg(
            " " * (first - len("SIGN-INS"))
            + "CLAUDE CODE".ljust(widths[0])
            + "REMOTE CONTROL".ljust(widths[1])
            + "GITHUB".ljust(widths[2])
            + "TAILSCALE",
            "d",
        ),
    ]
    lines = [head]
    now = model.get("now") or time.time()
    for vm in vms:
        ssh = vm.get("ssh") or {}
        row = [seg(f" {vm['name'][:nw]:<{first - 1}}")]
        claude, github, tailscale = ssh.get("claude"), ssh.get("github"), ssh.get("tailscale")
        cells = [
            _claude_cell(claude) if claude else _not_read_cell(vm),
            _remote_control_cell(claude, now) if claude else _not_read_cell(vm),
            _github_cell(github) if github else _not_read_cell(vm),
        ]
        for (text, style), width in zip(cells, widths, strict=True):
            row += _cell(text, width, style)
        text, style = _tailscale_cell(tailscale) if tailscale else _not_read_cell(vm)
        row.append(seg(text, style))
        lines.append(row)
    return lines


def _system_lines(model):
    vms = _ssh_vms(model)
    if not vms:
        return []
    nw = _name_width(vms)
    first = max(nw + 3, len("SYSTEM") + 4)
    widths = (16, 29, 17)
    head = [
        seg("SYSTEM", "h"),
        seg(
            " " * (first - len("SYSTEM"))
            + "OS".ljust(widths[0])
            + "KERNEL".ljust(widths[1])
            + "LOAD 1 / 15 MIN".ljust(widths[2])
            + "UPDATES",
            "d",
        ),
    ]
    lines = [head]
    for vm in vms:
        system = (vm.get("ssh") or {}).get("system")
        row = [seg(f" {vm['name'][:nw]:<{first - 1}}")]
        if not system:
            text, style = _not_read_cell(vm)
            lines.append(row + [seg(text, style)])
            continue
        os_name = re.sub(r"\s+LTS$", "", str(system.get("os") or "—"))
        running, newest = _short_kernel(system.get("kernel")), _short_kernel(system.get("kernel_newest"))
        kernel = running or "—"
        if newest and running and system.get("kernel_newest") != system.get("kernel"):
            kernel += f" · {newest} waits"
        load = system.get("load") or []
        load_text = f"{load[0]:.2f} / {load[2]:.2f}" if len(load) == 3 else "—"
        upgrades, security = system.get("upgrades"), system.get("security")
        if not isinstance(upgrades, int):
            updates, style = "not read", "d"
        elif upgrades == 0:
            updates, style = "none", ""
        else:
            updates = f"{upgrades}" + (f", {security} of them security" if security else "")
            style = "y" if security else ""
        row += _cell(os_name, widths[0]) + _cell(kernel, widths[1], "y" if " waits" in kernel else "")
        row += _cell(load_text, widths[2]) + [seg(updates, style)]
        lines.append(row)
    return lines


def _attention_lines(model):
    counts = model["counts"]
    items = model["attention"]
    head = [seg("ATTENTION", "h"), seg("  ")]
    parts = []
    if counts["unread"]:
        parts.append((f"{counts['unread']} not read", "r"))
    if counts["problem"]:
        parts.append((plural(counts["problem"], "problem"), "r"))
    if counts["todo"]:
        parts.append((f"{counts['todo']} to do", "y"))
    for i, (text, style) in enumerate(parts):
        head += ([seg(" · ")] if i else []) + [seg(text, style)]
    lines = [head]
    for it in items:
        style = {"unread": "r", "problem": "r", "todo": "y"}[it["level"]]
        prefix = f" {it['subject'][:11]:<12}"
        source = f"{it['source'].split('_')[0]}: " if it.get("source") not in (None, "status") else ""
        text = textwrap.wrap(source + it["text"], WIDTH - 15, break_on_hyphens=False) or [""]
        first = [seg(" "), seg("●", style), seg(prefix)]
        if source:
            first += [seg(source.rstrip(" "), "m"), seg(" " + text[0][len(source) :])]
        else:
            first.append(seg(text[0]))
        lines.append(first)
        lines += [[seg(" " * 15 + rest)] for rest in text[1:]]
        if it.get("command"):
            wrapped = textwrap.wrap("→ " + it["command"], WIDTH - 15, break_on_hyphens=False, subsequent_indent="  ")
            lines += [[seg(" " * 15 + part, "c")] for part in wrapped]
    return lines


def _work_lines(model):
    vms = _ssh_vms(model)
    if not vms:
        return []
    nw = _name_width(vms)
    first = max(nw + 3, 10)
    widths = (24, 48, 9)
    directory = next(
        (((vm.get("ssh") or {}).get("work") or {}).get("dir") for vm in vms if (vm.get("ssh") or {}).get("work")),
        "~/projects",
    )
    lines = [
        [seg("WORK", "h"), seg(f"  in {directory}, as of each checkout's last fetch", "d")],
        [
            seg(
                " " * first
                + "CHECKED".ljust(widths[0])
                + "NOT ON ANY REMOTE, OR NOT COMMITTED".ljust(widths[1])
                + "CLAUDE".ljust(widths[2])
                + "VS CODE",
                "d",
            )
        ],
    ]
    for vm in vms:
        ssh = vm.get("ssh") or {}
        work, system = ssh.get("work"), ssh.get("system")
        row = [seg(f" {vm['name'][:nw]:<{first - 1}}")]
        if not work:
            text, style = _not_read_cell(vm)
            lines.append(row + [seg(text, style)])
            continue
        checked = plural(work.get("checkouts") or 0, "checkout")
        if work.get("worktrees"):
            checked += f", {plural(work['worktrees'], 'tree')}"
        said = " · ".join(s for s in (_work_said(r) for r in work.get("repositories") or []) if s) or "nothing"
        wrapped = textwrap.wrap(said, widths[1] - 2, break_on_hyphens=False) or [""]
        claude = system.get("claude") if system else None
        windows = system.get("vscode_windows") if system else None
        row += _cell(checked, widths[0]) + _cell(wrapped[0], widths[1], "y" if said != "nothing" else "")
        row += _cell(str(claude) if claude else "—", widths[2])
        row.append(seg(plural(windows, "window") if windows else "—"))
        lines.append(row)
        lines += [[seg(" " * (first + widths[0]) + rest, "y")] for rest in wrapped[1:]]
    return lines


def _footer(model, reading):
    counts = model["counts"]
    code = model["exit"]
    lines = []
    if reading:
        lines.append([seg(reading, "d")])
    if model["proxmox"].get("unread"):
        said = "Proxmox was not read"
    elif code == 2:
        said = f"{counts['unread']} not read"
    elif code == 1:
        said = plural(counts["problem"], "problem")
    else:
        said = "nothing is wrong" + (
            f"; {plural(counts['todo'], 'thing')} {'waits' if counts['todo'] == 1 else 'wait'} for you"
            if counts["todo"]
            else ""
        )
    line = [seg(f"exit {code}", "r" if code else "g"), seg(f" · {said}")]
    if model["vms"]:
        line += [seg(" · "), seg("--only, --json: just help status", "d")]
    lines.append(line)
    return lines


SECTION_LINES = {
    "vms": _vms_lines,
    "storage": _storage_lines,
    "access": _access_lines,
    "sign-ins": _signins_lines,
    "system": _system_lines,
    "work": _work_lines,
}


def status_lines(model, reading=""):
    """The report as lines of (text, style) pieces; PENDING marks where a value is still being read."""
    out = [[seg(model["title"], "b")], []]
    proxmox = model["proxmox"]
    settings = model.get("settings") or {}
    if not model["vms"] and not proxmox.get("unread") and not proxmox.get("off") and not settings.get("names"):
        nodes = ", ".join(proxmox.get("nodes") or []) or "the cluster"
        tag = settings.get("tag") or "claude-on-proxmox"
        out += [[seg(f"No VM on {nodes} carries the {tag} tag. Create one with: just deploy alpha")], []]
    # Without the API there is nothing to show but why.
    for section in [] if proxmox.get("unread") else model["sections"]:
        body = SECTION_LINES[section](model)
        if body:
            out += body + [[]]
    if model.get("live"):
        out += [
            [seg("ATTENTION", "h"), seg("  "), seg(PENDING, "c"), seg(" ranked once every VM is read", "d")],
            [],
        ]
    elif model["attention"]:
        out += _attention_lines(model) + [[]]
    if not model.get("live"):
        out += _footer(model, reading)
    return out


def status_text(model, color=False, reading="", glyph=None, cols=None):
    """The report as text: the finished report, or with ``glyph`` a live frame, each line cut to ``cols``."""
    return "\n".join(render(cut(line, cols or WIDTH), color, glyph) for line in status_lines(model, reading))


class FilterModule:
    def filters(self):
        return {
            "status_clean": status_clean,
            "status_sections": status_sections,
            "status_needs_ssh": status_needs_ssh,
            "status_fleet": status_fleet,
            "status_aliases": status_aliases,
            "status_storages": status_storages,
            "status_vm_api": status_vm_api,
            "status_system_read": status_system_read,
            "status_refusal": status_refusal,
            "status_work_read": status_work_read,
            "status_report": status_report,
            "status_text": status_text,
        }
