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
from pathlib import Path

from ansible.errors import AnsibleFilterError


def _load_text():
    # The filters that clean text a VM printed. Loaded by path, as callback_plugins/claude_on_proxmox.py loads
    # them: a filter plugin is not a module another can import, and the callback loads this file the same way.
    name = "claude_on_proxmox_text_filters"
    if name not in sys.modules:
        path = Path(__file__).resolve().with_name("text.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


one_line = _load_text().one_line

# The report's sections, in the order they are drawn: vms and storage read only the API, the rest the VMs too.
SECTIONS = ("vms", "storage", "access")
SSH_SECTIONS = frozenset()
# The widest line the report draws, a tested limit.
WIDTH = 100
# A field a VM supplies is cut to this; a role's whole line, which carries a command, to the longer.
FIELD_LIMIT = 120
LINE_LIMIT = 400
LINE_KEYS = frozenset({"line"})
# A VM name the recipe takes, as tools/check names checks it: a name is how a role's commands name a VM.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GIB = 1024**3
SPARKS = "▁▂▃▄▅▆▇█"
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
    of reading the template's config; ``aliases`` the names the managed SSH config holds, or None with the aliases
    off; ``settings`` what the inventory says; ``unread`` why Proxmox could not be read, if it could not.
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
                "created": _ctime(config),
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


def _buckets(points, now, hours=24, count=12, key="cpu"):
    """``points`` (rrddata) as ``count`` averages of ``key`` over the last ``hours``, by timestamp; None where the
    VM has no point in a bucket. Either resolution reads the same: Proxmox VE 9's minute or 8's twenty minutes."""
    span = hours * 3600 / count
    start = now - hours * 3600
    sums, ns = [0.0] * count, [0] * count
    for point in points or []:
        if not isinstance(point, dict) or not isinstance(point.get(key), (int, float)):
            continue
        t = point.get("time")
        if not isinstance(t, (int, float)) or t < start or t > now:
            continue
        i = min(int((t - start) // span), count - 1)
        sums[i] += float(point[key])
        ns[i] += 1
    return [round(sums[i] / ns[i], 4) if ns[i] else None for i in range(count)]


def _pressure(points, now):
    """The VM's CPU pressure ("some") over the last hour, as a fraction; None where Proxmox keeps none (VE 8)."""
    values = [
        float(p["pressurecpusome"])
        for p in points or []
        if isinstance(p, dict)
        and isinstance(p.get("pressurecpusome"), (int, float))
        and isinstance(p.get("time"), (int, float))
        and now - 3600 <= p["time"] <= now
    ]
    if not values:
        return None
    # A percentage, as the kernel's pressure files give it (some avg10=0.50 is half a percent).
    return round(sum(values) / len(values) / 100, 4)


def status_vm_api(results, now=None):
    """One VM's API reads (play 2's loop over status/current, rrddata, pending and the agent) as its "api" part.

    ``results`` is the loop's registered results, each with ``item.name`` saying which read it was."""
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
        "cpu_24h": _buckets(rrd, now),
        "pressure_1h": _pressure(rrd, now),
        "pending": sorted(
            p.get("key") for p in pending if isinstance(p, dict) and ("pending" in p or "delete" in p) and p.get("key")
        ),
        "root": root,
        "os": {"pretty": osinfo.get("pretty-name"), "kernel": osinfo.get("kernel-release")}
        if isinstance(osinfo, dict)
        else None,
    }


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


def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else plural or word + 's'}"


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
            asks.append(_plural(int(wanted), "core"))
            has.append(_plural(int(got), "core"))
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
    if api is None and not live and vm.get("read"):
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
            text += f"; Proxmox lists {_plural(len(nameless), 'VM')} without a name, on a node that is down: {vmids}"
        items.append(_item("unread", name, name, text, "just status lists every VM this project created"))
    for vm in vms:
        items += _vm_items(vm, settings, live)
    items += _storage_items(fleet.get("storages") or [])
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
            else _plural(len(vms), "VM")
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
        "settings": {k: settings.get(k) for k in ("names", "tag", "tailscale_enabled", "nic", "aliases_enabled")},
    }


# ------------------------------------------------------------------------------------------------- the text ----

# Styles, as ANSI codes: the report's own palette, plain where colour is off.
STYLES = {"h": "1;36", "b": "1", "d": "2", "g": "32", "y": "33", "r": "31", "c": "36", "m": "35"}
PENDING = "\x00"


def _seg(text, style=""):
    return (str(text), style)


def _plain(line):
    return "".join(text for text, _ in line)


def _pad(line, width):
    return line + [_seg(" " * max(0, width - len(_plain(line))))]


def _cut(line, cols):
    """``line`` cut to ``cols`` characters, ending in "…" when cut: a live line that wrapped would break the count."""
    if cols is None or len(_plain(line)) <= cols:
        return line
    out, used = [], 0
    for text, style in line:
        room = cols - 1 - used
        if room <= 0:
            break
        out.append((text[:room], style))
        used += len(text[:room])
    out.append(("…", "d"))
    return out


def _render(line, color, glyph):
    def piece(text, style):
        text = text.replace(PENDING, glyph or "·")
        if color and style and text:
            return f"\x1b[{STYLES[style]}m{text}\x1b[0m"
        return text

    return "".join(piece(text, style) for text, style in line).rstrip()


def _dot(state):
    return {"running": ("●", "g"), "stopped": ("○", "d"), "paused": ("●", "y")}.get(state, ("●", "y"))


def _spark(values):
    out = []
    for v in values or []:
        out.append(" " if v is None else SPARKS[min(len(SPARKS) - 1, max(0, int(round(v * (len(SPARKS) - 1)))))])
    return "".join(out) if out else ""


def _bar(fraction, cells=12):
    filled = max(0, min(cells, int(round(fraction * cells))))
    return [
        _seg("█" * filled, "r" if fraction >= 0.9 else "y" if fraction >= 0.8 else "g"),
        _seg("░" * (cells - filled), "d"),
    ]


def _vms_lines(model):
    vms = model["vms"]
    others = model.get("others") or {}
    nodes = ", ".join(model["proxmox"].get("nodes") or [])
    lines = []
    if not vms:
        if others.get("count"):
            lines.append(
                [
                    _seg("VMS", "h"),
                    _seg(
                        f"  + {_plural(others['count'], 'other VM')} on {nodes}, not this project's:"
                        f" {_plural(others['vcpus'], 'vCPU')}, {_fmt_gib(others['mem'])} GiB,"
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
            _seg("VMS", "h"),
            _seg(
                f"  {nodes} · this project: {_plural(len(vms), 'VM')}, {_plural(vcpus, 'vCPU')}, {_fmt_gib(mem)} GiB,"
                f" {_fmt_gib(disk)} GiB of disk"
            ),
        ]
    )
    # A name past 13 characters is cut, so the row fits 100 columns with every cell at its widest.
    nw = min(13, max([5] + [len(vm.get("name") or "(no name)") for vm in vms]))
    lines.append(
        [
            _seg(
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
        row = [_seg(f" {vm.get('vmid', ''):>4}  "), _seg(dot, style), _seg(f" {name:<{nw}}  ")]
        if state != "running" or vm.get("skipped") == "node":
            word = "stopped" if state == "stopped" else "node down" if vm.get("skipped") == "node" else state
            mem = "— / " + _fmt_gib(vm.get("maxmem"))
            rest = f"{word:<10}{vm.get('vcpus') or '':>4} {'':4}  {'':12}  {mem:<18}  "
            rest += f"{'— / ' + _fmt_gib(vm.get('maxdisk')):<9}  {vm.get('created_ago', '')}"
            lines.append(row + [_seg(rest, "d")])
            continue
        pending = vm.get("pending_api")
        cpu = vm.get("cpu")
        cpu_text = f"{cpu * 100:.0f}%" if isinstance(cpu, (int, float)) else "—"
        spark = PENDING if pending else _spark((vm.get("api") or {}).get("cpu_24h"))
        mem_text = f"{_fmt_gib(vm.get('mem'))} / {_fmt_gib(vm.get('maxmem'))}"
        if vm.get("min_mem"):
            mem_text += f", min {_fmt_gib(vm['min_mem'])}"
        root = vm.get("root")
        root_text = PENDING if pending else f"{_fmt_gib(root['used'])} / {_fmt_gib(root['total'])}" if root else "—"
        row += [
            _seg(f"{_uptime(vm.get('uptime')):<10}{vm.get('vcpus') or '':>4} {cpu_text:>4}  "),
            _seg(f"{spark:<12}", "c"),
            _seg(f"  {mem_text:<18}  {root_text:<9}  {vm.get('created_ago', '') or '—'}"),
        ]
        lines.append(row)
        pressure = (vm.get("api") or {}).get("pressure_1h")
        if isinstance(pressure, (int, float)) and pressure > 0.10:
            lines.append([_seg(f"{'':{14 + nw}}waited for the host's CPU {pressure:.0%} of the last hour", "d")])
    if others.get("count"):
        lines.append(
            [
                _seg(
                    f"       + {_plural(others['count'], 'other VM')} on"
                    f" {', '.join(others.get('nodes') or []) or nodes},"
                    f" not this project's: {_plural(others['vcpus'], 'vCPU')}, {_fmt_gib(others['mem'])} GiB,"
                    f" {others['running']} running",
                    "d",
                )
            ]
        )
    return lines


def _storage_lines(model):
    if not model.get("storages") and not model.get("template"):
        return []
    lines = [[_seg("STORAGE", "h")]]
    for s in model.get("storages") or []:
        used, total = s.get("used"), s.get("total")
        line = [_seg(f" {s['storage']:<10} {s.get('type', ''):<8} ")]
        if isinstance(used, (int, float)) and total:
            fraction = used / total
            line += _bar(fraction) + [_seg(f"  {fraction:>3.0%}  {_fmt_gib(used)} / {_fmt_gib(total)} GiB")]
        else:
            line.append(_seg("not read", "d"))
        line.append(_seg(f" · this project's disks {_fmt_gib(s['ours']) + ' GiB' if s.get('ours') else 'none'}"))
        lines.append(line)
        if s.get("provisioned") and total and s.get("type") in ("lvmthin", "zfspool") and s["provisioned"] > total:
            lines.append(
                [
                    _seg(
                        f"{'':12}its disks are provisioned at {_fmt_gib(s['provisioned'])} GiB in all,"
                        " more than the pool holds",
                        "d",
                    )
                ]
            )
    template = model.get("template") or {}
    if template.get("missing"):
        lines.append(
            [_seg(f" {'template':<10} {template.get('name')}: not built yet; the first just deploy builds it")]
        )
    elif template:
        built = f" · built {template['built_ago']}" if template.get("built_ago") else ""
        where = f" on {template['storage']}" if template.get("storage") else ""
        lines.append([_seg(f" {'template':<10} {template.get('name')} ({template.get('vmid')}){where}{built}")])
    return lines


def _access_lines(model):
    vms = [vm for vm in model["vms"] if vm.get("name")]
    if not vms:
        return []
    aliases = model["settings"].get("aliases_enabled")
    nw = min(20, max([5] + [len(vm["name"]) for vm in vms]))
    # The tailnet column, from the Tailscale role's own read, once the report reads the VMs (S2).
    show_tailnet = status_needs_ssh(model["sections"]) or not model["settings"].get("tailscale_enabled")
    head = "LAN           " + ("SSH ALIAS   " if aliases else "") + ("TAILNET" if show_tailnet else "")
    lines = [[_seg("ACCESS", "h"), _seg(" " * (nw + 4 - len("ACCESS")) + head, "d")]]
    for vm in vms:
        state = vm.get("state")
        lan = vm.get("address") or ("stopped" if state == "stopped" else "—")
        name = vm["name"] if len(vm["name"]) <= nw else vm["name"][: nw - 1] + "…"
        row = [_seg(f" {name:<{nw}}   "), _seg(f"{lan:<14}", "d" if not vm.get("address") else "")]
        if aliases:
            row.append(_seg(f"{vm['name'] if vm.get('alias') else 'none':<12}", "" if vm.get("alias") else "y"))
        if show_tailnet:
            row += _tailnet_cells(vm, model)
        lines.append(row)
    lines.append([_seg(" VS Code: just code <name> [--project <repo>] [--session <id>]", "d")])
    return lines


def _tailnet_cells(vm, model):
    if not model["settings"].get("tailscale_enabled"):
        return [_seg("off: tailscale_enabled is false", "d")]
    return [_seg("—", "d")]


def _attention_lines(model):
    counts = model["counts"]
    items = model["attention"]
    head = [_seg("ATTENTION", "h"), _seg("  ")]
    parts = []
    if counts["unread"]:
        parts.append((f"{counts['unread']} not read", "r"))
    if counts["problem"]:
        parts.append((_plural(counts["problem"], "problem"), "r"))
    if counts["todo"]:
        parts.append((f"{counts['todo']} to do", "y"))
    for i, (text, style) in enumerate(parts):
        head += ([_seg(" · ")] if i else []) + [_seg(text, style)]
    lines = [head]
    for it in items:
        style = {"unread": "r", "problem": "r", "todo": "y"}[it["level"]]
        prefix = f" {it['subject'][:11]:<12}"
        source = f"{it['source'].split('_')[0]}: " if it.get("source") not in (None, "status") else ""
        text = textwrap.wrap(source + it["text"], WIDTH - 15, break_on_hyphens=False) or [""]
        first = [_seg(" "), _seg("●", style), _seg(prefix)]
        if source:
            first += [_seg(source.rstrip(" "), "m"), _seg(" " + text[0][len(source) :])]
        else:
            first.append(_seg(text[0]))
        lines.append(first)
        lines += [[_seg(" " * 15 + rest)] for rest in text[1:]]
        if it.get("command"):
            wrapped = textwrap.wrap("→ " + it["command"], WIDTH - 15, break_on_hyphens=False, subsequent_indent="  ")
            lines += [[_seg(" " * 15 + part, "c")] for part in wrapped]
    return lines


def _footer(model, reading):
    counts = model["counts"]
    code = model["exit"]
    lines = []
    if reading:
        lines.append([_seg(reading, "d")])
    if model["proxmox"].get("unread"):
        said = "Proxmox was not read"
    elif code == 2:
        said = f"{counts['unread']} not read"
    elif code == 1:
        said = _plural(counts["problem"], "problem")
    else:
        said = "nothing is wrong" + (
            f"; {_plural(counts['todo'], 'thing')} {'waits' if counts['todo'] == 1 else 'wait'} for you"
            if counts["todo"]
            else ""
        )
    line = [_seg(f"exit {code}", "r" if code else "g"), _seg(f" · {said}")]
    if model["vms"]:
        line += [_seg(" · "), _seg(f"sections: {' '.join(SECTIONS)} · --only · --json", "d")]
    lines.append(line)
    return lines


SECTION_LINES = {"vms": _vms_lines, "storage": _storage_lines, "access": _access_lines}


def status_lines(model, reading=""):
    """The report as lines of (text, style) pieces; PENDING marks where a value is still being read."""
    out = [[_seg(model["title"], "b")], []]
    proxmox = model["proxmox"]
    settings = model.get("settings") or {}
    if not model["vms"] and not proxmox.get("unread") and not proxmox.get("off") and not settings.get("names"):
        nodes = ", ".join(proxmox.get("nodes") or []) or "the cluster"
        tag = settings.get("tag") or "claude-on-proxmox"
        out += [[_seg(f"No VM on {nodes} carries the {tag} tag. Create one with: just deploy alpha")], []]
    # Without the API there is nothing to show but why.
    for section in [] if proxmox.get("unread") else model["sections"]:
        body = SECTION_LINES[section](model)
        if body:
            out += body + [[]]
    if model.get("live"):
        out += [
            [_seg("ATTENTION", "h"), _seg("  "), _seg(PENDING, "c"), _seg(" ranked once every VM is read", "d")],
            [],
        ]
    elif model["attention"]:
        out += _attention_lines(model) + [[]]
    if not model.get("live"):
        out += _footer(model, reading)
    return out


def status_text(model, color=False, reading="", glyph=None, cols=None):
    """The report as text: the finished report, or with ``glyph`` a live frame, each line cut to ``cols``."""
    return "\n".join(_render(_cut(line, cols or WIDTH), color, glyph) for line in status_lines(model, reading))


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
            "status_report": status_report,
            "status_text": status_text,
        }
