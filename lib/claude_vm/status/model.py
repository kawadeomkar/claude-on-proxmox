"""`just status`: what each VM this project created is doing, and what needs doing next, as one model.

The engine (claude_vm.status.engine) reads, and these turn what it read into one model: what `just status --json`
prints. claude_vm.tui.status draws the report from it twice: once finished, when the run ends, and over and over while
it runs, with a spinner wherever a value is still being read (docs/architecture/lifecycle.md §4.6).

Nothing here reads or writes anything. Every value a VM supplies is cleaned by status_clean before anything keeps
it, and again by status_report: one line of plain text each (lib/claude_vm/text.py's one_line), so a name, a
directory or a line a tool printed can never start a line of the report of its own.
"""

from __future__ import annotations

import functools
import re
import time
from datetime import UTC, datetime

from ..proxmox import (
    NO_TEMPLATE_RECORD,
    built_template,
    proxmox_notes_repos,
    proxmox_tags,
    scsi0_storage,
    ssh_config_blocks,
)
from ..proxmox import proxmox_read_grant as grant
from ..refs import NAME
from ..text import and_list, one_line, plural

# The report's sections, in the order they are drawn: vms, storage and capacity read only the API, the rest the VMs
# over SSH too - access for the tailnet column, sign-ins for the roles' reads, system for the probe's system read, work
# for the checkouts.
SECTIONS = ("vms", "storage", "capacity", "access", "sign-ins", "system", "work")
SSH_SECTIONS = frozenset({"access", "sign-ins", "system", "work"})
# A field a VM supplies is cut to this; a role's whole line, which carries a command, to the longer.
FIELD_LIMIT = 120
LINE_LIMIT = 400
LINE_KEYS = frozenset({"line"})
GIB = 1024**3
LEVELS = ("unread", "problem", "todo")
# The configuration keys the report reads from a VM's config: nothing else, cipassword among what is left out. The
# tags say whether the VM is sized, whose size proxmox_vm_drift then calls no drift.
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
    "tags",
)


# ------------------------------------------------------------------------------------------------- cleaning ----


def _host_flag(settings):
    """What ends every just line status says of a VM, with more than one host: " --host <name>", as a run's own lines
    end - its space put back, since status_clean made the setting one line, trimmed - else ""."""
    flag = str(settings.get("host_flag") or "").strip()
    return f" {flag}" if flag else ""


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
        raise ValueError(f"the report has no section {', '.join(unknown)}: its sections are {' '.join(SECTIONS)}")
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


def _config_value(config, key):
    return config.get(key) if isinstance(config, dict) else None


def _ctime(config):
    match = re.search(r"(?:^|,)ctime=(\d+)", str(_config_value(config, "meta") or ""))
    return int(match.group(1)) if match else None


def _made(entry, config, disk_ctimes):
    """When a VM or the template was made: its disk's ctime, else its config's."""
    return disk_ctimes.get(entry.get("vmid")) or _ctime(config)


def _pick(config):
    return {key: config[key] for key in CONFIG_KEYS if isinstance(config, dict) and key in config}


def _found(entry, config):
    """What reading a VM adds to its listing: its address, the config the report keeps, and its own list of
    repositories, taken from its notes here, since the notes are a person's and stay out of that config."""
    return {
        "address": entry.get("address", ""),
        "config": _pick(config),
        "own_repos": proxmox_notes_repos(config.get("description")),
    }


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
    hosts=None,
    pending=False,
):
    """The fleet's findings, as one value: Proxmox, the VMs this project created (with what the listing, their config
    and their agent's address say), the other VMs as totals, the storages, the template and the SSH aliases.

    ``listing`` is /cluster/resources?type=vm; ``tagged`` and ``requested`` which VMs are this project's and which were
    asked for; ``addressed`` the requested VMs with an address, ``configs`` its config results, each with
    the VM it read as ``claude_vm_item``; ``lists`` the results of reading /version and the storages' listing;
    ``contents`` the results of reading each storage's disks, each with its ``item``; ``template_config`` the result
    of reading the template's config, and ``template_drift`` how it differs from what its settings would build
    (proxmox_template_drift), which the next just deploy rebuilds; ``aliases`` the names the managed SSH config holds,
    or None with the aliases off; ``settings`` what the inventory says; ``unread`` why Proxmox could not be read, if it
    could not; ``hosts`` the inventory's own hosts (claude_vm_inventory_hosts), whose names a VM is never read under;
    with ``pending``, the fleet as the listing alone has it, drawn while the rest of Proxmox is read.
    """
    settings = dict(settings or {})
    if not settings.get("discovery", True):
        return {"settings": settings, "proxmox": {"unread": "", "off": True}, "vms": [], "aliases": aliases}
    if unread or not isinstance(listing, list):
        return {"settings": settings, "proxmox": {"unread": unread or "Proxmox did not list the VMs"}, "vms": []}
    read = {r["item"]: _data(r) for r in lists or [] if isinstance(r, dict) and isinstance(r.get("item"), str)}
    version = read.get("version") if isinstance(read.get("version"), dict) else {}
    storages = read.get("cluster/resources?type=storage") or []
    nodes = read.get("cluster/resources?type=node") or []
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
    tag = str(settings.get("tag", "agent-warehouse")).lower()
    owned = [str(t).lower() for t in settings.get("tags_owned") or [tag]]
    # Each config by the VM its result names, never by position: a read that was skipped would shift the rest.
    read_configs = {}
    for result in (configs or {}).get("results", []) if isinstance(configs, dict) else configs or []:
        if isinstance(result, dict):
            item = result.get("claude_vm_item") or result.get("item") or {}
            if isinstance(item, dict):
                read_configs[item.get("vmid")] = _data(result) or {}
    by_vmid = {
        e.get("vmid"): _found(e, read_configs.get(e.get("vmid")) or {}) for e in addressed or [] if isinstance(e, dict)
    }
    counts = {}
    for entry in requested:
        if "name" in entry:
            counts[entry["name"]] = counts.get(entry["name"], 0) + 1
    template_name = settings.get("template_name")
    # The one whose config was read.
    built = built_template(entries, template_name)
    template = (
        {
            "name": template_name,
            "vmid": built.get("vmid"),
            "node": built.get("node"),
            "storage": scsi0_storage(_config_value(template_config, "scsi0")),
            # Its config's ctime, which a clone of it carries; and when it was made, by its disk as a VM is, since a
            # template made from a clone - a golden, or one of your own - carries its source's ctime too.
            "ctime": _ctime(template_config or {}),
            "built": _made(built, template_config or {}, disk_ctimes),
            "drift": [str(d) for d in template_drift or []],
            "rebuild": bool(settings.get("template_rebuild", True)),
        }
        if built
        else None
    )
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
        elif not NAME.fullmatch(name) or name in (hosts or ["localhost"]):
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
                "unfinished": bool(
                    {f"{t}-unfinished" for t in owned} & {t.lower() for t in proxmox_tags(entry.get("tags"))}
                ),
                "config": config,
                "own_repos": found["own_repos"]["names"] if found.get("own_repos", {}).get("own") else None,
                "created": _made(entry, config, disk_ctimes),
                # Read by name: a VM that is not can only be shown from the listing.
                "read": reason == "",
                "skipped": reason,
            }
        )
    disk_storages = {scsi0_storage(_config_value(vm["config"], "scsi0")) for vm in vms} - {""}
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
        "capacity": status_capacity(
            nodes,
            [e for e in listing if isinstance(e, dict) and e.get("type", "qemu") in ("qemu", "lxc")],
            settings.get("api_user") or "",
            _host_flag(settings),
        ),
        "template": template
        if template
        else {"name": template_name, "missing": True, "custom": bool(settings.get("template_custom"))},
        "goldens": {
            "count": len(
                [e for e in entries if e.get("template") and str(e.get("name", "")).startswith(f"{tag}-golden-")]
            ),
            "building": sorted(
                str(e.get("name"))
                for e in entries
                if not e.get("template")
                and str(e.get("name", "")).startswith(f"{tag}-build-")
                and f"{tag}-build" in proxmox_tags(e.get("tags"))
            ),
        },
        "aliases": aliases,
        "pending": bool(pending),
    }


def status_capacity(nodes, entries, user="", host_flag=""):
    """Each node's CPU threads and memory beside what the guests on it hold - every VM and container, this project's and
    others' - for
    the size of the next one: how much memory is in use now and free, and how much the running VMs could take of it
    (``unallocated``, which ballooning and an idle guest can leave above what is free). A node's figures are read only
    with Sys.Audit; without it Proxmox lists the node alone, ``read`` is false, and ``grant`` says how to give it to
    ``user``, the API token's: the lines the report prints, and an agent reads in the JSON - just init's run, ending
    with ``host_flag``, on a host of one node, and the commands in a cluster, where that run grants it on
    one node only."""
    named = sorted((n for n in nodes or [] if isinstance(n, dict) and n.get("node")), key=lambda n: n["node"])
    shown = []
    for node in named:
        on = [e for e in entries if e.get("node") == node["node"] and not e.get("template")]
        held = {
            state: {
                "vms": sum(1 for e in vms if e.get("type", "qemu") == "qemu"),
                "containers": sum(1 for e in vms if e.get("type") == "lxc"),
                "vcpus": sum(int(e.get("maxcpu") or 0) for e in vms),
                "memory": sum(int(e.get("maxmem") or 0) for e in vms),
            }
            for state, vms in (
                ("running", [e for e in on if e.get("status") == "running"]),
                ("stopped", [e for e in on if e.get("status") != "running"]),
            )
        }
        threads, memory, used = node.get("maxcpu"), node.get("maxmem"), node.get("mem")
        read = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (threads, memory, used))
        shown.append(
            {
                "node": node["node"],
                "status": node.get("status", ""),
                "read": read,
                "threads": int(threads) if read else None,
                "cpu": node.get("cpu") if read else None,
                "memory": int(memory) if read else None,
                "memory_used": int(used) if read else None,
                "memory_free": int(memory) - int(used) if read else None,
                "unallocated": int(memory) - held["running"]["memory"] if read else None,
                **held,
                "grant": []
                if read or node.get("status", "") not in ("online", "")
                else grant(node["node"], user, host_flag, cluster=len(named) > 1),
            }
        )
    return shown


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
    """The VM names the managed SSH config has an alias for, or None with the aliases off: its blocks as the prune and
    the writes read them (``ssh_config_blocks``), by either version's markers."""
    if not enabled:
        return None
    return [block["name"] for block in ssh_config_blocks(text or "")]


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
    """One VM's API reads (status/current, rrddata, pending and the agent) as its "api" part.

    ``results`` is each read's result, with ``item.name`` saying which read it was; ``since`` when
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
    "upgrades_pending",
    "security",
    "claude",
    "vscode_windows",
    "editor_windows",
    "editor_windows_by",
    "unread",
)


def _read(result):
    """Whether a module's registered result holds what it read: a result, not failed, unreachable or skipped."""
    return isinstance(result, dict) and not (result.get("failed") or result.get("unreachable") or result.get("skipped"))


def status_system_read(result):
    """What the probe read of a VM's system, as the "system" part: its fields alone, or None when it was not read."""
    if not _read(result):
        return None
    return {key: result.get(key) for key in SYSTEM_KEYS}


# ------------------------------------------------------------------------------------------------ the model ----


def fmt_gib(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return "—"
    gib = value / GIB
    return f"{gib:.1f}" if gib < 10 else f"{gib:.0f}"


def uptime_said(seconds):
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


def _item(level, vm, text, command="", source="status"):
    """An attention item about vm - a VM, a storage, the template or Proxmox - which is also what it is said under."""
    return {"level": level, "vm": vm, "source": source, "subject": vm, "text": text, "command": command}


def _add(items, vm, level, text, command="", source="status"):
    """An attention item about vm, appended to items."""
    items.append(_item(level, vm, text, command, source))


def _full(used, total):
    """How full a disk is, and what that asks for: a problem from 90%, something to do from 80%, else nothing."""
    fill = used / total
    return fill, "problem" if fill >= 0.9 else "todo" if fill >= 0.8 else None


def _drift_item(vm, drift, settings):
    on = _host_flag(settings)
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
            asks.append(f"{fmt_gib(int(wanted) * 1024**2)} GiB of memory")
            has.append(f"{fmt_gib(int(got) * 1024**2)} GiB")
        elif setting == "balloon":
            asks.append(f"a minimum of {fmt_gib(int(wanted) * 1024**2)} GiB")
            has.append(f"a minimum of {fmt_gib(int(got) * 1024**2)} GiB")
        elif setting == "disk":
            asks.append(f"a {fmt_gib(wanted)} GiB disk")
            has.append(f"a {fmt_gib(got)} GiB disk")
        elif setting == "onboot":
            asks.append("a start at boot" if wanted else "no start at boot")
            has.append("no start at boot" if wanted else "a start at boot")
    name = vm["name"]
    parts = []
    if asks:
        parts.append(f"the settings ask for {and_list(asks)}; {name} has {and_list(has)}")
    if cloud_init:
        parts.append(f"{'Its' if parts else 'its'} cloud-init {and_list(cloud_init)} differ from the settings")
    text = ". ".join(parts)
    command = f"just ANSIBLE_ARGS='-e proxmox_vm_force_update=true' deploy {name} --only provision{on}"
    note = f"(this also resets {name}'s tags, and starts it if it is stopped"
    if cloud_init:
        note += "; its next boot then runs as a new cloud-init instance, which regenerates its SSH host keys"
    return _item("todo", name, text, f"{command} {note})")


def _vm_items(vm, settings, live):
    """The attention items of one VM, from what was read of it."""
    items = []
    name = vm.get("name") or f"VMID {vm.get('vmid')}"
    # With more than one host, what ends each just line, so that pasted later it reaches this host's VM.
    on = _host_flag(settings)
    add = functools.partial(_add, items, name)
    skipped = vm.get("skipped")
    if skipped == "node":
        add(
            "unread",
            f"its node {vm.get('node')} is down, so Proxmox lists no more of it",
            "start the node, or remove the VM from Proxmox if the node is gone",
        )
        return items
    if skipped == "duplicate":
        add(
            "problem",
            f"more than one VM tagged {settings.get('tag', 'agent-warehouse')} is named {name}: deploy and"
            " destroy refuse both, and status reads neither past the listing",
            "rename or remove the duplicates in Proxmox",
        )
        return items
    if skipped == "name":
        add("unread", "its name is not one a recipe takes, so status reads no more of it", "rename it in Proxmox")
        return items
    if skipped == "nameless":
        return items
    if vm.get("unfinished"):
        add("todo", "its first deploy did not finish", f"just deploy {name}{on}")
    api = vm.get("api")
    # A VM of the inventory's, with discovery off, is never asked of Proxmox.
    if api is None and not live and vm.get("read") and not vm.get("inventory"):
        add("unread", "Proxmox did not answer for it")
    status = vm.get("state")
    if status == "running" and not vm.get("address") and api is not None:
        if (vm.get("uptime") or 0) > 180:
            add(
                "unread",
                f"it has run {uptime_said(vm.get('uptime'))} and its guest agent reports no address on"
                f" {settings.get('nic', 'net0')}",
                "check the guest agent in Proxmox's console for it",
            )
    drift = vm.get("drift")
    if drift:
        item = _drift_item(vm, drift, settings)
        if item:
            items.append(item)
    if api and api.get("pending"):
        add(
            "todo",
            f"{and_list(api['pending'])} changed in Proxmox and {'waits' if len(api['pending']) == 1 else 'wait'}"
            " for a restart",
            f"restart {name} from Proxmox",
        )
    root = vm.get("root")
    if root and isinstance(root.get("used"), (int, float)) and root.get("total"):
        fill, level = _full(root["used"], root["total"])
        if level:
            add(
                level,
                f"its root disk is {fill:.0%} full ({fmt_gib(root['used'])} of {fmt_gib(root['total'])} GiB):"
                " Claude, Docker and apt fail on a full one",
                f"free space on {name}, or grow vm_disk_size in local.yml",
            )
    aliases = settings.get("aliases_enabled")
    if aliases and vm.get("address") and vm.get("alias") is False and status == "running":
        add("todo", "it has no SSH alias", f"just deploy --only ssh-config{on}")
    return items


def short_kernel(release):
    """A kernel release as the report shows it: Ubuntu's flavour suffix dropped (6.8.0-85-generic is 6.8.0-85)."""
    return re.sub(r"-(generic|virtual|kvm|lowlatency)$", "", str(release or ""))


def status_work_read(result, directory):
    """What the probe read of a VM's checkouts, as the "work" part: the directory read, how many
    checkouts and extra worktrees it holds, and per checkout what is on no remote or not committed. None when it was
    not read."""
    if not _read(result):
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


def work_said(repository):
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
    on = _host_flag(settings)
    said = ssh.get("unreachable") or ""
    kind = ssh.get("refusal") or status_refusal(said)
    if kind == "changed":
        return (
            f"not read: the SSH host key at {address} is not the one this machine holds. {name} was rebuilt or"
            " renamed, or another machine answers at that address",
            f"find out which first. If {name} was rebuilt or renamed: ssh-keygen -R {address}, then just deploy"
            f" {name} --only configure{on}",
        )
    if kind == "unknown":
        return (
            f"not read: this machine holds no SSH host key for {address}, and status adds none. Its address"
            " changed, or nothing here has connected to it yet",
            f"just deploy {name} --only configure{on} takes a new VM's key, as every run does; or ssh {user}@{address}"
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
    return _ago(now - moment.replace(tzinfo=UTC).timestamp()).replace(" ago", "")


def remote_control(claude, now):
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
    add = functools.partial(_add, items, name)
    user = settings.get("vm_user") or "dev"
    on = _host_flag(settings)
    ssh = vm.get("ssh")
    if not vm.get("needs_ssh"):
        return items
    if ssh is None:
        if not live:
            add("unread", "not read over SSH: the run ended before it was")
        return items
    if ssh.get("unreachable"):
        add("unread", *_ssh_refusal(vm, ssh, settings))
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
            add("unread", f"{said} was not read: {unread[part]}")
    system = ssh.get("system")
    if system:
        if (system.get("unread") or {}).get("upgrades"):
            add("unread", f"apt's upgrades were not read: {system['unread']['upgrades']}")
        if system.get("reboot_required"):
            running, newest = system.get("kernel") or "", system.get("kernel_newest") or ""
            why = (
                f"kernel {short_kernel(newest)} is installed, {short_kernel(running)} runs"
                if newest and running and newest != running
                else f"{and_list(system.get('reboot_packages') or []) or 'an upgrade'} asks for one"
            )
            busy = []
            if system.get("claude"):
                busy.append(plural(system["claude"], "claude process", "claude processes"))
            # Each editor's windows, named as the editors list names it (the settings' editor_names), in its order.
            windows = system.get("editor_windows_by")
            windows = windows if isinstance(windows, dict) else {}
            for key, editor in (settings.get("editor_names") or {}).items():
                if windows.get(key):
                    busy.append(plural(windows[key], f"{editor or key} window"))
            text = f"waits for a reboot: {why}" + (
                f"; {and_list(busy)} {'is' if len(busy) == 1 and busy[0].startswith('1 ') else 'are'} open there"
                if busy
                else ""
            )
            add(
                "todo",
                text,
                f"restart it from Proxmox once they are done, then just deploy --only ssh-config{on}"
                if busy
                else f"restart it from Proxmox, then just deploy --only ssh-config{on}",
            )
        if isinstance(system.get("security"), int) and system["security"] > 0:
            waits = "waits" if system["security"] == 1 else "wait"
            add(
                "todo",
                f"{plural(system['security'], 'security upgrade')} {waits} to be installed",
                f"ssh -t {user}@{vm.get('address') or name} sudo apt-get upgrade",
            )
    claude = ssh.get("claude")
    if claude:
        if not claude.get("installed"):
            add("todo", "Claude Code is not installed", f"just deploy {name} --tags claude_code{on}")
        elif not claude.get("api_key") and not claude.get("login_stored"):
            add(
                "todo",
                "not signed in. Run "
                + (claude.get("login_command") or f"just login {name} --only claude{on}")
                + " to sign in from your machine: it opens the sign-in page and asks for the code the page shows.",
                source="claude_code",
            )
        state, said = remote_control(claude, now)
        unit = claude.get("unit") or "claude-remote-control"
        if state == "down":
            add(
                "problem",
                f"Remote Control is not running: {unit} is {said}",
                f"ssh {user}@{vm.get('address') or name} journalctl -u {unit} -n 50, then just deploy {name}"
                f" --tags claude_code{on} starts it again",
            )
        elif state == "not-set-up":
            add("todo", "Remote Control is not set up", f"just deploy {name} --tags claude_code{on}")
        elif state == "stopped" and claude.get("login_stored"):
            add(
                "todo",
                "Remote Control is stopped",
                f"just deploy {name} --tags claude_code{on} starts it, or says what stops it",
            )
    github = ssh.get("github")
    if github:
        if github.get("unchecked"):
            add("unread", f"gh could not check its login with GitHub: {github.get('error')}")
        elif github.get("left") and github.get("line"):
            add("todo", github["line"], source="github_projects")
    tailscale = ssh.get("tailscale")
    if tailscale and tailscale.get("enabled"):
        case = tailscale.get("case")
        if case == "check-daemon-down":
            add("problem", "tailscaled is not running", f"just deploy {name} --tags tailscale{on} starts it")
        elif case == "check-no-package":
            add("todo", "Tailscale is not installed", f"just deploy {name} --tags tailscale{on} installs it")
        elif case == "would-join":
            add(
                "todo",
                "Tailscale is not signed in; vault_tailscale_auth_key joins it",
                f"just deploy {name} --tags tailscale{on}",
            )
        elif tailscale.get("left") and tailscale.get("line"):
            add("todo", tailscale["line"], source="tailscale")
    return items


def _template_items(template, on=""):
    """The template, when the next just deploy rebuilds it: it differs from what its settings would build, and
    proxmox_template_rebuild leaves rebuilding on. Kept as it is, it is only said on its line."""
    drift = template.get("drift") or []
    if template.get("missing") or not drift or not template.get("rebuild", True):
        return []
    if NO_TEMPLATE_RECORD in drift:
        text = f"the template {template.get('name')} keeps no record of its build, so the next just deploy refuses it"
        fix = "remove it in Proxmox if it is an old build of this project's, else set proxmox_template_rebuild to false"
        return [_item("todo", "template", text, fix)]
    text = (
        f"the template {template.get('name')} differs from its settings ({'; '.join(drift)}), so the next just"
        " deploy rebuilds it, over root SSH"
    )
    return [_item("todo", "template", text, f"just deploy --only template{on} rebuilds it now")]


def _storage_items(storages):
    items = []
    for s in storages:
        used, total = s.get("used"), s.get("total")
        if isinstance(used, (int, float)) and total:
            fill, level = _full(used, total)
            if level:
                _add(
                    items,
                    s["storage"],
                    level,
                    f"it is {fill:.0%} full ({fmt_gib(used)} of {fmt_gib(total)} GiB): as it fills, the VMs on"
                    " it get I/O errors",
                    "free space on it, or move disks off it",
                )
    return items


def status_report(found, read_at="", now=None, live=False):
    """The report's model, from what every host found: localhost's ``fleet``, and each VM's parts by its name.

    ``found`` is {host: status_found}. With ``live``, a VM's part not read yet is pending rather than "not read",
    and nothing is ranked: the model of a frame drawn while the run goes on."""
    now = time.time() if now is None else now
    found = status_clean(found or {})
    read_at = one_line(str(read_at or ""), FIELD_LIMIT)
    fleet = (found.get("localhost") or {}).get("fleet") or {}
    settings = dict(fleet.get("settings") or {})
    # The settings name the editor set, resolved, for the line under ACCESS: a fleet without its name is a bug to
    # fail on, never a line that says "None". Before the fleet is found there is no line to say it on.
    if fleet and not settings.get("editor_name"):
        raise ValueError("the fleet's settings name no editor: the settings give editor_name")
    sections = status_sections(settings.get("sections"))
    aliases = fleet.get("aliases")
    settings["aliases_enabled"] = aliases is not None
    # The fleet as the listing alone has it: a VM's address is still being read, so a running VM may yet be read.
    fleet_pending = bool(live and fleet.get("pending"))
    proxmox = dict(fleet.get("proxmox") or {})
    ssh_wanted = status_needs_ssh(sections)
    template = dict(fleet.get("template") or {})
    if template.get("built"):
        template["built_ago"] = _ago(now - template["built"])
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
        # Read over SSH where a section needs it, and the listing shows the VM running with an address.
        vm["ssh"] = parts.get("ssh")
        vm["needs_ssh"] = bool(
            ssh_wanted and vm.get("read") and vm.get("status") == "running" and (vm.get("address") or fleet_pending)
        )
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
                "own_repos": None,
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
        _add(items, "proxmox", "unread", proxmox["unread"], settings.get("proxmox_hint", ""))
    for name in fleet.get("unmatched") or []:
        nameless = fleet.get("nameless") or []
        text = f"no VM tagged {settings.get('tag', 'agent-warehouse')} is named {name}"
        if nameless:
            vmids = ", ".join(str(n.get("vmid")) for n in nameless)
            text += f"; Proxmox lists {plural(len(nameless), 'VM')} without a name, on a node that is down: {vmids}"
        _add(items, name, "unread", text, "just status lists every VM this project created")
    for vm in vms:
        items += _vm_items(vm, settings, live)
        items += _ssh_items(vm, settings, live, now)
    items += _storage_items(fleet.get("storages") or [])
    items += _template_items(fleet.get("template") or {}, _host_flag(settings))
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
        title = f"agent-warehouse  {read_at} · the inventory's VMs: claude_vm_discovery is off"
    elif proxmox.get("unread"):
        title = f"agent-warehouse  {read_at}"
    else:
        what = (
            ", ".join(settings.get("names"))
            if settings.get("names")
            else "no VMs"
            if not vms
            else plural(len(vms), "VM")
        )
        version = f", Proxmox VE {proxmox['version']}" if proxmox.get("version") else ""
        title = f"agent-warehouse  {read_at} · {', '.join(nodes)}{version} · {what}"
    return {
        "exit": exit_code,
        "read_at": read_at,
        "live": bool(live),
        "fleet_pending": fleet_pending,
        "sections": sections,
        "title": title,
        "proxmox": proxmox,
        "vms": vms,
        "others": fleet.get("others") or {},
        "storages": fleet.get("storages") or [],
        "capacity": fleet.get("capacity") or [],
        "template": template,
        "goldens": fleet.get("goldens") or {"count": 0, "building": []},
        "attention": items,
        "counts": counts,
        "settings": {
            k: settings.get(k)
            for k in (
                "names",
                "tag",
                "tailscale_enabled",
                "vpn",
                "tailscale_off",
                "nic",
                "aliases_enabled",
                "vm_user",
                "editor",
                "editor_name",
            )
        },
        "now": now,
    }
