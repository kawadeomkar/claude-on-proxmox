#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Read what a VM is running and what waits on it - its OS, kernel, load, memory, upgrades - changing nothing."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: vm_status_info
short_description: Read a VM's OS, kernel, load, memory, waiting upgrades and the user's own processes
version_added: "1.0.0"
description:
  - For C(just status) (playbooks/status.yml). Reads the OS and the running kernel, the newest kernel installed,
    whether a reboot is waiting and for which packages, the load, memory and uptime, the upgrades apt would make
    and how many are security ones, and how many claude processes and VS Code windows the user has open.
  - Every read is fixed here, and none changes anything. The upgrades come from C(apt-get -s), a simulation that
    takes no lock and reads the lists as the last C(apt update) left them; nothing here refreshes them. Each read
    runs in a thread of its own with its own time limit, and one that runs out is said as such rather than holding
    the others.
  - Programs are run by absolute path, since the user's PATH over SSH starts with directories the user owns.
options:
  root:
    description: The directory the files read are under, C(/) on a VM; a fixture tree in the unit tests.
    type: str
    default: /
  upgrades:
    description: Whether to ask apt what an upgrade would install.
    type: bool
    default: true
  timeout:
    description: Seconds each read may take; apt's simulation gets four times as long.
    type: int
    default: 5
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Read the VM's system
  vm_status_info:
  register: found
"""

RETURN = r"""
os:
  description: C(PRETTY_NAME) from os-release, or "".
  returned: always
  type: str
kernel:
  description: The kernel running, from C(/proc/sys/kernel/osrelease).
  returned: always
  type: str
kernel_newest:
  description: The newest kernel in C(/boot), by version, or "".
  returned: always
  type: str
reboot_required:
  description: Whether C(/run/reboot-required) exists, as Ubuntu's update-notifier leaves it.
  returned: always
  type: bool
reboot_packages:
  description: The packages C(/run/reboot-required.pkgs) names, each once.
  returned: always
  type: list
  elements: str
load:
  description: The load over 1, 5 and 15 minutes, or an empty list.
  returned: always
  type: list
  elements: float
memory:
  description: C(total) and C(available) in bytes, from C(/proc/meminfo).
  returned: always
  type: dict
uptime:
  description: Seconds since boot, or 0.
  returned: always
  type: int
upgrades:
  description: The packages an upgrade would install, or null when not asked or not read.
  returned: always
  type: int
security:
  description: How many of them come from a C(-security) pocket, or null.
  returned: always
  type: int
claude:
  description: How many claude processes the user runs.
  returned: always
  type: int
vscode_windows:
  description: How many VS Code extension hosts the user runs, one per window.
  returned: always
  type: int
unread:
  description: Each read that failed or ran out of time, by name, with why.
  returned: always
  type: dict
"""

import os
import re
import subprocess
import threading

from ansible.module_utils.basic import AnsibleModule

APT_GET = "/usr/bin/apt-get"
VERSION = re.compile(r"(\d+)")
INST = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")


def _read(root, path):
    with open(os.path.join(root, path.lstrip("/")), encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _version_key(release):
    return [int(part) for part in VERSION.findall(release)]


def os_release(root):
    for name in ("etc/os-release", "usr/lib/os-release"):
        try:
            text = _read(root, name)
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def kernels(root):
    running = _read(root, "proc/sys/kernel/osrelease").strip()
    try:
        names = os.listdir(os.path.join(root, "boot"))
    except OSError:
        names = []
    installed = [name[len("vmlinuz-") :] for name in names if name.startswith("vmlinuz-")]
    newest = max(installed, key=_version_key) if installed else ""
    return {"kernel": running, "kernel_newest": newest}


def reboot(root):
    required = os.path.exists(os.path.join(root, "run/reboot-required"))
    packages = []
    try:
        for line in _read(root, "run/reboot-required.pkgs").splitlines():
            if line.strip() and line.strip() not in packages:
                packages.append(line.strip())
    except OSError:
        pass
    return {"reboot_required": required, "reboot_packages": packages}


def load_memory_uptime(root):
    load = [float(value) for value in _read(root, "proc/loadavg").split()[:3]]
    memory = {}
    for line in _read(root, "proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            memory["total" if key == "MemTotal" else "available"] = int(value.split()[0]) * 1024
    uptime = int(float(_read(root, "proc/uptime").split()[0]))
    return {"load": load, "memory": memory, "uptime": uptime}


def upgrades(timeout):
    """What `apt-get -s dist-upgrade` would install: a simulation, which takes no lock and changes nothing."""
    result = subprocess.run(
        [APT_GET, "-s", "-q", "-o", "Debug::NoLocking=1", "dist-upgrade"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"exit {result.returncode}")
    found = [match.group(2) for match in map(INST.match, result.stdout.splitlines()) if match]
    return {"upgrades": len(found), "security": sum(1 for pocket in found if "-security" in pocket)}


def processes(root):
    """This user's claude processes, and VS Code's extension hosts - one per window - from /proc."""
    proc = os.path.join(root, "proc")
    claude = windows = 0
    uid = os.getuid()
    try:
        names = os.listdir(proc)
    except OSError:
        names = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc, name)).st_uid != uid:
                continue
            with open(os.path.join(proc, name, "cmdline"), "rb") as handle:
                argv = [arg.decode("utf-8", "replace") for arg in handle.read().split(b"\0") if arg]
        except OSError:
            continue
        if not argv:
            continue
        if os.path.basename(argv[0]) == "claude" or any("@anthropic-ai/claude-code" in arg for arg in argv[:2]):
            claude += 1
        elif ".vscode-server" in argv[0] and "--type=extensionHost" in argv:
            windows += 1
    return {"claude": claude, "vscode_windows": windows}


def run_all(reads, timeout):
    """Each read in a thread of its own, joined for its own limit: one that hangs is said, the rest still land."""
    results, unread = {}, {}
    threads = []
    for name, (read, limit) in reads.items():
        box = {}

        def work(read=read, box=box):
            try:
                box["value"] = read()
            except Exception as error:  # noqa: BLE001 - any failure is the read's own, said by name
                box["error"] = str(error) or type(error).__name__

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        threads.append((name, thread, box, limit or timeout))
    for name, thread, box, limit in threads:
        thread.join(limit)
        if thread.is_alive():
            unread[name] = f"it took longer than {limit} s"
        elif "error" in box:
            unread[name] = box["error"]
        else:
            results.update(box["value"])
    return results, unread


def main():
    module = AnsibleModule(
        argument_spec={
            "root": {"type": "str", "default": "/"},
            "upgrades": {"type": "bool", "default": True},
            "timeout": {"type": "int", "default": 5},
        },
        supports_check_mode=True,
    )
    root, timeout = module.params["root"], module.params["timeout"]
    reads = {
        "os": (lambda: {"os": os_release(root)}, None),
        "kernel": (lambda: kernels(root), None),
        "reboot": (lambda: reboot(root), None),
        "load": (lambda: load_memory_uptime(root), None),
        "processes": (lambda: processes(root), None),
    }
    if module.params["upgrades"]:
        reads["upgrades"] = (lambda: upgrades(timeout * 4), timeout * 4 + 1)
    found = {
        "os": "",
        "kernel": "",
        "kernel_newest": "",
        "reboot_required": False,
        "reboot_packages": [],
        "load": [],
        "memory": {},
        "uptime": 0,
        "upgrades": None,
        "security": None,
        "claude": 0,
        "vscode_windows": 0,
    }
    results, unread = run_all(reads, timeout)
    found.update(results)
    module.exit_json(changed=False, unread=unread, **found)


if __name__ == "__main__":
    main()
