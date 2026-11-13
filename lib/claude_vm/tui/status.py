"""`just status`'s report, drawn from its model (claude_vm.status.model): a section of lines each, every line within
WIDTH, the same functions finished and live (docs/architecture/lifecycle.md §4.6).

The report is drawn as every live output is: lines of styled segments (screen.py).
"""

from __future__ import annotations

import re
import textwrap
import time

from ..proxmox import NO_TEMPLATE_RECORD
from ..status.model import fmt_gib, remote_control, short_kernel, uptime_said, work_said
from .screen import PENDING, bar, cut, plural, render, seg, spark

# The widest line the report draws, a tested limit.
WIDTH = 100
# The widest a VM's name is drawn, in every section: so a row fits WIDTH with every cell at its widest.
NAME_WIDTH = 13


# ------------------------------------------------------------------------------------------------- the text ----


def _dot(state):
    return {"running": ("●", "g"), "stopped": ("○", "d"), "paused": ("●", "y")}.get(state, ("●", "y"))


def _short(name, width):
    """A name cut to width, ending in "…" when it is longer: never cut without a sign, so a cut name is never read as
    another VM's whole one."""
    return name if len(name) <= width else name[: width - 1] + "…"


def _others(others, nodes):
    """The VMs on the cluster that are not this project's, as totals in one sentence: never one by name."""
    return (
        f"+ {plural(others['count'], 'other VM')} on {nodes}, not this project's: {plural(others['vcpus'], 'vCPU')},"
        f" {fmt_gib(others['mem'])} GiB, {others['running']} running"
    )


def _vms_lines(model):
    vms = model["vms"]
    others = model.get("others") or {}
    nodes = ", ".join(model["proxmox"].get("nodes") or [])
    lines = []
    if not vms:
        if others.get("count"):
            lines.append([seg("VMS", "h"), seg(f"  {_others(others, nodes)}", "d")])
        return lines
    vcpus = sum(int(vm.get("vcpus") or 0) for vm in vms)
    mem = sum(int(vm.get("maxmem") or 0) for vm in vms)
    disk = sum(int(vm.get("maxdisk") or 0) for vm in vms)
    lines.append(
        [
            seg("VMS", "h"),
            seg(
                f"  {nodes} · this project: {plural(len(vms), 'VM')}, {plural(vcpus, 'vCPU')}, {fmt_gib(mem)} GiB,"
                f" {fmt_gib(disk)} GiB of disk"
            ),
        ]
    )
    nw = min(NAME_WIDTH, max([5] + [len(vm.get("name") or "(no name)") for vm in vms]))
    lines.append(
        [
            seg(
                f" VMID  {'NAME':<{nw + 4}}UP        vCPU  CPU  LAST 24 H     MEM GiB             ROOT GiB   CREATED",
                "d",
            )
        ]
    )
    for vm in vms:
        name = _short(vm.get("name") or "(no name)", nw)
        state = vm.get("state") or "unknown"
        dot, style = _dot(state)
        if vm.get("skipped") == "node":
            dot, style = "?", "r"
        row = [seg(f" {vm.get('vmid', ''):>4}  "), seg(dot, style), seg(f" {name:<{nw}}  ")]
        if state != "running" or vm.get("skipped") == "node":
            word = "stopped" if state == "stopped" else "node down" if vm.get("skipped") == "node" else state
            mem = "— / " + fmt_gib(vm.get("maxmem"))
            rest = f"{word:<10}{vm.get('vcpus') or '':>4} {'':4}  {'':12}  {mem:<18}  "
            rest += f"{'— / ' + fmt_gib(vm.get('maxdisk')):<9}  {vm.get('created_ago', '')}"
            lines.append(row + [seg(rest, "d")])
            continue
        pending = vm.get("pending_api")
        cpu = vm.get("cpu")
        cpu_text = f"{cpu * 100:.0f}%" if isinstance(cpu, (int, float)) else "—"
        cpu_spark = PENDING if pending else spark((vm.get("api") or {}).get("cpu_24h"))
        mem_text = f"{fmt_gib(vm.get('mem_used', vm.get('mem')))} / {fmt_gib(vm.get('mem_total', vm.get('maxmem')))}"
        if vm.get("min_mem"):
            mem_text += f", min {fmt_gib(vm['min_mem'])}"
        root = vm.get("root")
        root_text = PENDING if pending else f"{fmt_gib(root['used'])} / {fmt_gib(root['total'])}" if root else "—"
        row += [
            seg(f"{uptime_said(vm.get('uptime')):<10}{vm.get('vcpus') or '':>4} {cpu_text:>4}  "),
            seg(f"{cpu_spark:<12}", "c"),
            seg(f"  {mem_text:<18}  {root_text:<9}  {vm.get('created_ago', '') or '—'}"),
        ]
        lines.append(row)
        pressure = (vm.get("api") or {}).get("pressure_1h")
        if isinstance(pressure, (int, float)) and pressure > 0.10:
            lines.append([seg(f"{'':{14 + nw}}waited for the host's CPU {pressure:.0%} of the last hour", "d")])
    if others.get("count"):
        lines.append([seg(f"       {_others(others, ', '.join(others.get('nodes') or []) or nodes)}", "d")])
    return lines


def _storage_lines(model):
    if model.get("fleet_pending"):
        return [
            [seg("STORAGE", "h")],
            [seg(" "), seg(PENDING, "c"), seg(" reading the storages and the template", "d")],
        ]
    if not model.get("storages") and not model.get("template"):
        return []
    lines = [[seg("STORAGE", "h")]]
    for s in model.get("storages") or []:
        used, total = s.get("used"), s.get("total")
        line = [seg(f" {s['storage']:<10} {s.get('type', ''):<8} ")]
        if isinstance(used, (int, float)) and total:
            fraction = used / total
            line += bar(fraction) + [seg(f"  {fraction:>3.0%}  {fmt_gib(used)} / {fmt_gib(total)} GiB")]
        else:
            line.append(seg("not read", "d"))
        line.append(seg(f" · this project's disks {fmt_gib(s['ours']) + ' GiB' if s.get('ours') else 'none'}"))
        lines.append(line)
        if s.get("provisioned") and total and s.get("type") in ("lvmthin", "zfspool") and s["provisioned"] > total:
            lines.append(
                [
                    seg(
                        f"{'':12}its disks are provisioned at {fmt_gib(s['provisioned'])} GiB in all,"
                        " more than the pool holds",
                        "d",
                    )
                ]
            )
    template = model.get("template") or {}
    if template.get("missing"):
        said = (
            "not on Proxmox; just deploy refuses"
            if template.get("custom")
            else "not built yet; the first just deploy builds it"
        )
        lines.append([seg(f" {'template':<10} {template.get('name')}: {said}")])
    elif template:
        built = f" · built {template['built_ago']}" if template.get("built_ago") else ""
        where = f" on {template['storage']}" if template.get("storage") else ""
        line = [seg(f" {'template':<10} {template.get('name')} ({template.get('vmid')}){where}{built}")]
        # How it differs is said under ATTENTION; here only what the next deploy does with it.
        if template.get("drift"):
            line += [
                seg(" · "),
                seg("differs, kept", "d")
                if not template.get("rebuild", True)
                else seg("no record, next deploy refuses it", "y")
                if NO_TEMPLATE_RECORD in template["drift"]
                else seg("next deploy rebuilds it", "y"),
            ]
        lines.append(line)
    goldens = model.get("goldens") or {}
    if goldens.get("count"):
        plural = "" if goldens["count"] == 1 else "s"
        lines.append([seg(f" {'golden':<10} {goldens['count']} golden template{plural}, deleted as they age")])
    for name in goldens.get("building") or []:
        lines.append(
            [seg(f" {'build VM':<10} {name}: being built, or left over; the next build deletes one left", "d")]
        )
    return lines


def _guests(held):
    """What a node's figures count: its VMs, and its containers where it has any."""
    return "VMs and containers" if held.get("containers") else "VMs"


def _capacity_lines(model):
    """Each node's CPU threads and memory, and what the VMs and containers on it hold: what a new VM's size is weighed
    against."""
    if model.get("fleet_pending"):
        return [[seg("CAPACITY", "h")], [seg(" "), seg(PENDING, "c"), seg(" reading the nodes", "d")]]
    nodes = model.get("capacity") or []
    if not nodes:
        return []
    lines = [[seg("CAPACITY", "h")]]
    for node in nodes:
        name = f" {_short(node['node'], 10):<10} "
        if node.get("status") not in ("online", ""):
            lines.append([seg(name), seg(f"{node.get('status')}: nothing on it can be read", "y")])
            continue
        running, stopped = node["running"], node["stopped"]
        held = f"its running {_guests(running)} hold {plural(running['vcpus'], 'vCPU')}"
        held += f" and {fmt_gib(running['memory'])} GiB"
        if node.get("read"):
            used = node["memory_used"] / node["memory"] if node["memory"] else 0
            lines.append(
                [
                    seg(name),
                    seg(f"{plural(node['threads'], 'CPU thread')}, {node['cpu'] or 0:.0%} busy · memory "),
                    *bar(used),
                    seg(
                        f" {fmt_gib(node['memory_used'])} / {fmt_gib(node['memory'])} GiB,"
                        f" {fmt_gib(node['memory_free'])} free"
                    ),
                ]
            )
            share = running["memory"] / node["memory"] if node["memory"] else 0
            held += f" ({share:.0%} of its memory)"
        else:
            lines.append([seg(name), seg("its CPU threads and memory are not readable with the API token", "d")])
        lines.append([seg(f"{'':12}{held}", "d")])
        if stopped["vms"] or stopped.get("containers"):
            more = f"its stopped {_guests(stopped)} would take {plural(stopped['vcpus'], 'vCPU')}"
            more += f" and {fmt_gib(stopped['memory'])} GiB more"
            lines.append([seg(f"{'':12}{more}", "d")])
        # Wrapped to the report's width: just init's line ends with the host's name, which may be a long one.
        for said in node.get("grant") or []:
            for line in textwrap.wrap(said, WIDTH - 13, break_on_hyphens=False, subsequent_indent="  ") or [""]:
                lines.append([seg(f"{'':12}{line}", "d")])
    return lines


def _ssh_vms(model):
    """The VMs a section read over SSH shows: each this project created that is read over SSH, by name."""
    return [vm for vm in model["vms"] if vm.get("name") and vm.get("needs_ssh")]


def _name_width(vms):
    return min(NAME_WIDTH, max([5] + [len(vm["name"]) for vm in vms]))


def _ssh_table(model):
    """A section read over SSH, a row per VM: its VMs, how wide their names are drawn, and where its first column
    starts, past the name and never before the section's title ends."""
    vms = _ssh_vms(model)
    nw = _name_width(vms)
    return vms, nw, max(nw + 3, 10)


def _columns(names, widths):
    """A table's column heads: each but the last padded to its column's width."""
    return "".join(name.ljust(width) for name, width in zip(names[:-1], widths, strict=True)) + names[-1]


def _name_cell(vm, nw, first):
    """A row's first cell: the VM's name, cut to nw, padded to where the first column starts."""
    return seg(f" {_short(vm['name'], nw):<{first - 1}}")


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
    # The VPN's column, from its role's read: whenever the VMs are read over SSH, or Tailscale is not the VPN. Named
    # for the VPN: TAILNET for Tailscale, as 1.0 had it.
    vpn = model["settings"].get("vpn") or ("tailscale" if model["settings"].get("tailscale_enabled") else "none")
    show_tailnet = any(vm.get("needs_ssh") for vm in vms) or vpn != "tailscale"
    column = {"tailscale": "TAILNET"}.get(vpn, "VPN")
    # The alias is what ssh takes, so whole up to 24 characters, the column as wide as the longest and two spaces:
    # one padded to 12 alone ran a 12-character alias into the tailnet column.
    alias_width = min(26, max([12] + [len(vm["name"]) + 2 for vm in vms if vm.get("alias")]))
    head = "LAN           " + (f"{'SSH ALIAS':<{alias_width}}" if aliases else "") + (column if show_tailnet else "")
    # The tailnet names padded to the longest, so their addresses line up.
    dns_width = max([0] + [len(((vm.get("ssh") or {}).get("tailscale") or {}).get("dns_name") or "") for vm in vms])
    lines = [[seg("ACCESS", "h"), seg(" " * max(1, nw + 4 - len("ACCESS")) + head, "d")]]
    for vm in vms:
        state = vm.get("state")
        lan = vm.get("address") or ("stopped" if state == "stopped" else "—")
        if model.get("fleet_pending") and not vm.get("address") and state == "running":
            lan = PENDING
        row = [seg(f" {_short(vm['name'], nw):<{nw}}   "), seg(f"{lan:<14}", "d" if not vm.get("address") else "")]
        if aliases and model.get("fleet_pending"):
            row += [seg(f"{PENDING:<{alias_width}}", "c")]
        elif aliases:
            # A cell one short and a space: two spaces before the tailnet column, an alias cut or not.
            alias = _cell(vm["name"] if vm.get("alias") else "none", alias_width - 1, "" if vm.get("alias") else "y")
            row += [*alias, seg(" ")]
        if show_tailnet:
            row += _tailnet_cells(vm, model, dns_width)
        lines.append(row)
    editor = model["settings"]["editor_name"]
    lines.append([seg(f" {editor}: just code <name> [--project <repo>] [--session <id>]", "d")])
    return lines


def _tailnet_cells(vm, model, dns_width=0):
    vpn = model["settings"].get("vpn") or ("tailscale" if model["settings"].get("tailscale_enabled") else "none")
    if vpn == "none":
        # A Tailscale the project installed is left on the VM, unmanaged, when it is not the VPN: shown as on the
        # tailnet while it is, so the column never says off of a VM that is there.
        tailscale = (vm.get("ssh") or {}).get("tailscale") or {}
        if tailscale.get("case") == "unmanaged" and tailscale.get("backend") == "Running" and tailscale.get("dns_name"):
            shown = f"{tailscale['dns_name']:<{dns_width}}  {tailscale.get('ipv4') or ''}".rstrip()
            return [seg(f"{shown}  unmanaged", "d")]
        # Off by the setting that turned it off: vpn, or tailscale_enabled, which decides vpn while it is not set.
        said = model["settings"].get("tailscale_off") or "vpn is none"
        return [seg(f"off: {said}", "d")]
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
    state, said = remote_control(claude, now)
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
    # Left on the VM under vpn: none, which never removes it: running, but no run keeps it.
    "unmanaged": ("● unmanaged", "d"),
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
    if tailscale.get("case") == "unmanaged":
        return TAILSCALE_CELLS["unmanaged"]
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
    vms, nw, first = _ssh_table(model)
    if not vms:
        return []
    widths = (25, 25, 18)
    columns = _columns(("CLAUDE CODE", "REMOTE CONTROL", "GITHUB", "TAILSCALE"), widths)
    lines = [[seg("SIGN-INS", "h"), seg(" " * (first - len("SIGN-INS")) + columns, "d")]]
    now = model.get("now") or time.time()
    for vm in vms:
        ssh = vm.get("ssh") or {}
        row = [_name_cell(vm, nw, first)]
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
    vms, nw, first = _ssh_table(model)
    if not vms:
        return []
    widths = (16, 29, 17)
    columns = _columns(("OS", "KERNEL", "LOAD 1 / 15 MIN", "UPDATES"), widths)
    lines = [[seg("SYSTEM", "h"), seg(" " * (first - len("SYSTEM")) + columns, "d")]]
    for vm in vms:
        system = (vm.get("ssh") or {}).get("system")
        row = [_name_cell(vm, nw, first)]
        if not system:
            lines.append(row + [seg(*_not_read_cell(vm))])
            continue
        os_name = re.sub(r"\s+LTS$", "", str(system.get("os") or "—"))
        running, newest = short_kernel(system.get("kernel")), short_kernel(system.get("kernel_newest"))
        kernel = running or "—"
        if newest and running and system.get("kernel_newest") != system.get("kernel"):
            kernel += f" · {newest} waits"
        load = system.get("load") or []
        load_text = f"{load[0]:.2f} / {load[2]:.2f}" if len(load) == 3 else "—"
        upgrades, security = system.get("upgrades"), system.get("security")
        if system.get("upgrades_pending"):
            updates, style = PENDING, "c"
        elif not isinstance(upgrades, int):
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
        prefix = f" {_short(it['subject'], 11):<12}"
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
    vms, nw, first = _ssh_table(model)
    if not vms:
        return []
    # Narrow enough that the last column's "10 windows" still fits 100 columns after a name of 13: 16 + 73 + 10.
    widths = (24, 40, 9)
    directory = next(
        (((vm.get("ssh") or {}).get("work") or {}).get("dir") for vm in vms if (vm.get("ssh") or {}).get("work")),
        "~/projects",
    )
    columns = _columns(("CHECKED", "NOT ON ANY REMOTE, OR NOT COMMITTED", "CLAUDE", "EDITOR"), widths)
    lines = [
        [seg("WORK", "h"), seg(f"  in {directory}, as of each checkout's last fetch", "d")],
        [seg(" " * first + columns, "d")],
    ]
    for vm in vms:
        ssh = vm.get("ssh") or {}
        work, system = ssh.get("work"), ssh.get("system")
        row = [_name_cell(vm, nw, first)]
        if not work:
            lines.append(row + [seg(*_not_read_cell(vm))])
            continue
        checked = plural(work.get("checkouts") or 0, "checkout")
        if work.get("worktrees"):
            checked += f", {plural(work['worktrees'], 'tree')}"
        said = " · ".join(s for s in (work_said(r) for r in work.get("repositories") or []) if s) or "nothing"
        wrapped = textwrap.wrap(said, widths[1] - 2, break_on_hyphens=False) or [""]
        claude = system.get("claude") if system else None
        windows = (system.get("editor_windows", system.get("vscode_windows")) if system else None) or None
        row += _cell(checked, widths[0]) + _cell(wrapped[0], widths[1], "y" if said != "nothing" else "")
        row += _cell(str(claude) if claude else "—", widths[2])
        row.append(seg(plural(windows, "window") if windows else "—"))
        lines.append(row)
        lines += [[seg(" " * (first + widths[0]) + rest, "y")] for rest in wrapped[1:]]
        lines += _own_list_lines(vm, work, first + widths[0])
    return lines


def _own_list_lines(vm, work, indent):
    """A VM's own list of repositories, when it has one, and what of it has no checkout: shown, never counted."""
    own = vm.get("own_repos")
    if own is None:
        return []
    there = {repository.get("name") for repository in work.get("repositories") or []}
    missing = [name for name in own if name not in there]
    said = f"its own list: {', '.join(own) or 'empty'}" + (f"; no checkout of {', '.join(missing)}" if missing else "")
    return [[seg(" " * indent + line, "d")] for line in textwrap.wrap(said, WIDTH - indent - 1, break_on_hyphens=False)]


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
    "capacity": _capacity_lines,
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
        tag = settings.get("tag") or "agent-warehouse"
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


SECTION_TITLES = {
    "vms": "VMS",
    "storage": "STORAGE",
    "capacity": "CAPACITY",
    "access": "ACCESS",
    "sign-ins": "SIGN-INS",
    "system": "SYSTEM",
    "work": "WORK",
}


def skeleton_lines(title, sections):
    """The report's outline, each section's title with a spinner: drawn before anything has been read."""
    out = [[seg(title, "b")], []]
    for section in sections:
        out += [[seg(SECTION_TITLES[section], "h"), seg("  "), seg(PENDING, "c")], []]
    out += [[seg("ATTENTION", "h"), seg("  "), seg(PENDING, "c"), seg(" ranked once every VM is read", "d")], []]
    return out


def skeleton_text(title, sections, color=False, glyph=None, cols=None):
    return "\n".join(render(cut(line, cols or WIDTH), color, glyph) for line in skeleton_lines(title, sections))


def status_text(model, color=False, reading="", glyph=None, cols=None):
    """The report as text: the finished report, or with ``glyph`` a live frame, each line cut to ``cols``."""
    return "\n".join(render(cut(line, cols or WIDTH), color, glyph) for line in status_lines(model, reading))
