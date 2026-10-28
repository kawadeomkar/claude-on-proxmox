#!/usr/bin/env python3
"""Minimal stateful stand-in for the Proxmox VE REST API.

Implements just enough of /api2/json for community.proxmox's proxmox_vm_info,
proxmox_kvm (clone / update / start / shutdown / delete) and proxmox_disk
(resize) so roles/proxmox_vm can be exercised without a hypervisor, plus
converting a VM to a template for the tests' fixtures. Every
request is appended as a JSON line to <state_dir>/calls.log and the VM table is
written to <state_dir>/state.json after each mutation, for verification.

One guest is a container, listed by /cluster/resources?type=vm as type "lxc"
the way a real cluster lists containers alongside VMs, and tagged as this
project's. It is not a VM: /nodes/<node>/qemu does not list it and any
/nodes/<node>/qemu/<its id>/... request fails as it would for a VMID that is
not a VM. It proves the consumers of the listing filter on type, and that a
fleet's VMIDs skip the ones containers hold.

The cluster has a second node that is down, holding one VM that is none of this
project's business and one that is, so every test also proves that one
unreachable node does not break commands about VMs on the others. Guests on it
are listed the way a real cluster lists them once the node's statistics have
expired: VMID, node, type, tags and status "unknown", with no name or template
key at all.

/cluster/resources reports each VM's status the moment it changes, unless a
fixture turns on the lag with PUT /api2/json/fake/listing-lag?listings=N: then,
after a start or a stop, the next N listings still report the status from
before it, the way a real cluster lists the status pvestatd last polled, up to
ten seconds old. /nodes/<node>/qemu/<id>/status/current is never stale.

GET /access/permissions answers each token's privileges by path, as Proxmox
does - the privilege's name as the key, 1 or 0 its propagate flag - from a
table a fixture fills with PUT /api2/json/fake/privileges?token=&path=&privs=
(a comma-separated list, empty for none; "VM.Clone:0" without Propagate); a
path the table does not name holds
the whole role tools/proxmox-access.sh makes, its guest-agent
privilege the one of the version /version reports, which PUT
/api2/json/fake/version?version= changes (8.2.4 until then).

Each node lists its storages for VM disks (GET /nodes/<node>/storage) and its
bridges (GET /nodes/<node>/network), each only where the token may see it -
Datastore.Audit or Datastore.AllocateSpace on /storage/<name>, SDN.Audit or
SDN.Use on /sdn/zones/localnetwork/<bridge> - as Proxmox filters them: on
pve, local-lvm (lvmthin) and vmbr0 until a fixture replaces them with PUT
/api2/json/fake/storages?node=&storages=<name>:<type>[:inactive],... or
/api2/json/fake/bridges?node=&bridges=<name>,... A fixture moves a VM to
another node with PUT /api2/json/fake/vm-node?vmid=&node=.

A second token id, "noagent", stands for a token created without the
guest-agent privilege (VM.Monitor on PVE 8, VM.GuestAgent.Audit on 9): every
request is accepted except the agent's, which is refused with 403.

For `just status` (playbooks/status.yml): /cluster/resources?type=storage lists
each storage the token may audit with its fill, and ?type=node the nodes, with
no statistics, as a token without Sys.Audit sees them. A running VM is listed
with its CPU, memory and uptime, and /nodes/<node>/qemu/<id>/status/current,
rrddata (a day of points), pending, and the agent's get-osinfo and get-fsinfo
answer as Proxmox VE does. memhost and the pressure figures are VE 9's: the fake
reports them only once /version says 9 or later, and a day of rrddata is 1440
points then, 70 on 8. /nodes/<node>/storage/<id>/content lists the disks on a
storage. A fixture marks a change waiting for a restart with PUT
/api2/json/fake/pending?vmid=&key=&value=, and sets how full a VM's root disk
is with PUT /api2/json/fake/root?vmid=&used=<bytes>. A clone's config carries the
template's meta ctime, as Proxmox VE 9.2's did when it was checked against a real
clone; the moment it was made is its disk's ctime, in the storage's content, which
PUT /api2/json/fake/disk-ctime?vmid=&ctime= sets for a fixture's VM made earlier.

Usage: fake_pve_api.py <port> <certfile> <keyfile> <state_dir> <expected_token_secret>
"""

import json
import re
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(sys.argv[1])
CERT, KEY = sys.argv[2], sys.argv[3]
STATE_DIR = Path(sys.argv[4])
TOKEN_SECRET = sys.argv[5]
TOKEN_RE = re.compile(r"^PVEAPIToken=([^!]+)!([^=]+)=(.*)$")
# Accepted everywhere but at the guest agent, like a token whose role lacks
# the guest-agent privilege.
NOAGENT_TOKEN_ID = "noagent"
NODE = "pve"
# Listed by /nodes and /cluster/resources, but every request to it fails the
# way pveproxy fails one it cannot forward to a node that is powered off.
DOWN_NODE = "pve2"
# Whether that node currently reports. Off, as above, until a fixture turns
# it on with PUT /api2/json/fake/nodes/pve2?status=online - the one thing
# the playbooks' "a node is down" paths cannot otherwise be shown to recover
# from. While it is on, its guests are listed with their names and as
# stopped, the way Proxmox lists guests on a node that has just come back.
NODES_ONLINE = {DOWN_NODE: False}

STATE_DIR.mkdir(parents=True, exist_ok=True)
VMS = {
    9000: {
        "vmid": 9000,
        "name": "ubuntu-24.04-cloudinit",
        "node": NODE,
        "status": "stopped",
        "template": 1,
        "config": {
            "name": "ubuntu-24.04-cloudinit",
            "cores": 2,
            "memory": 2048,
            "scsi0": "local-lvm:base-9000-disk-0,discard=on,size=3584M",
            "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
            "net0": "virtio=BC:24:11:00:23:28,bridge=vmbr0",
            "template": 1,
            "meta": "creation-qemu=8.1.5,ctime=1760000000",
        },
    },
    # Two guests on the node that is down: a stranger, and one this project
    # created there. Below NEXTID_LOWER, so they never affect the numbering.
    8000: {
        "vmid": 8000,
        "name": "on-a-node-that-is-down",
        "node": DOWN_NODE,
        "status": "unknown",
        "template": 0,
        "config": {"name": "on-a-node-that-is-down"},
    },
    8001: {
        "vmid": 8001,
        "name": "ours-on-a-node-that-is-down",
        "node": DOWN_NODE,
        "status": "unknown",
        "template": 0,
        "config": {"name": "ours-on-a-node-that-is-down", "tags": "claude-on-proxmox"},
    },
    # A container carrying this project's tag, inside the fleet's numbering:
    # the provision scenario numbers alpha and beta from NEXTID_LOWER and must
    # step over it, and discovery, the listing and the destroy pre-check must
    # all leave it alone for being an lxc, tag or no tag.
    9003: {
        "vmid": 9003,
        "name": "ours-container",
        "node": NODE,
        "type": "lxc",
        "status": "running",
        "template": 0,
        "config": {"hostname": "ours-container", "tags": "claude-on-proxmox"},
    },
}
# /cluster/nextid answers with the lowest free VMID, as Proxmox does - not the
# highest in use plus one, which never collided with anything and so never
# exercised the fleet's "skip the VMIDs in use" step. Proxmox lets the
# datacenter raise the floor (Datacenter -> Options -> Next free VMID range);
# the fake's floor keeps the fixtures below it out of the numbering.
NEXTID_LOWER = 9001
TASKS = 0
# The guest agent is not up the moment a VM starts. Fail this many polls first
# so the role's wait loop is actually exercised. Counted per VM: with a single
# global counter the first VM absorbed every refusal and the second one's very
# first poll succeeded, so a fleet never exercised the loop more than once.
AGENT_CALLS = {}
AGENT_READY_AFTER = 2
# The listing's lag (see the docstring): how many listings after a status
# change still report the old status, and per VM, that status and how many
# listings it has left.
LISTING_LAG = {"listings": 0}
STALE = {}
# What /version reports, and each token's privileges by path where a fixture
# set them: {(token id, path): [privilege, ...]}.
VERSION = {"version": "8.2.4"}
PRIVILEGES = {}
# Each node's storages for VM disks, with their type and free bytes, and its bridges.
STORAGES = {NODE: [{"storage": "local-lvm", "type": "lvmthin", "avail": 412 * 1024**3}]}
BRIDGES = {NODE: ["vmbr0"]}
# Each storage's size, for its fill in /cluster/resources?type=storage: used is
# the size less what STORAGES says is free.
STORAGE_SIZE = 465 * 1024**3
# Changes waiting for a restart, by VM, and each VM's root disk use, by VMID
# (11 GiB unless a fixture sets it).
PENDING = {}
ROOT_USED = {}
ROOT_TOTAL = 31 * 1024**3
ROLE = [
    "VM.Allocate",
    "VM.Clone",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.PowerMgmt",
    "VM.Audit",
    "Datastore.AllocateSpace",
    "Datastore.Audit",
    "SDN.Use",
]


def privileges(token_id, path):
    """The token's privileges at path, each with its propagate flag: the fixture's, else the whole role.

    The role's guest-agent privilege is this version's, and the noagent token's
    role lacks it. A fixture's "VM.Clone:0" is granted without Propagate.
    """
    if (token_id, path) in PRIVILEGES:
        listed = PRIVILEGES[(token_id, path)]
    else:
        agent = "VM.GuestAgent.Audit" if int(VERSION["version"].split(".")[0]) >= 9 else "VM.Monitor"
        listed = ROLE + ([] if token_id == NOAGENT_TOKEN_ID else [agent])
    granted = {}
    for entry in listed:
        name, _, flag = entry.partition(":")
        granted[name] = int(flag or "1")
    return granted


def normalise_tags(raw):
    """Store tags the way Proxmox does.

    proxmox_kvm sends them comma-joined, but PVE stores and returns them
    ";"-joined, lowercased and deduplicated, in alphabetical order. Echoing
    the request back verbatim hid that from every test.
    """
    parts = [t.strip().lower() for t in raw.replace(",", ";").split(";")]
    return ";".join(sorted({t for t in parts if t}))


def mac_for(vmid):
    return f"BC:24:11:{vmid // 65536 % 256:02X}:{vmid // 256 % 256:02X}:{vmid % 256:02X}"


def agent_interfaces(vm):
    """What network-get-interfaces reports: loopback, a docker bridge the role
    must ignore, and the VM's own NIC with its DHCP address."""
    vmid = vm["vmid"]
    return [
        {
            "name": "lo",
            "hardware-address": "00:00:00:00:00:00",
            "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}],
        },
        {
            "name": "docker0",
            "hardware-address": "02:42:9A:11:22:33",
            "ip-addresses": [{"ip-address": "172.17.0.1", "ip-address-type": "ipv4", "prefix": 16}],
        },
        {
            "name": "eth0",
            "hardware-address": mac_for(vmid).lower(),
            "ip-addresses": [
                {"ip-address": f"192.0.2.{vmid % 200 + 50}", "ip-address-type": "ipv4", "prefix": 24},
                {"ip-address": "fe80::1", "ip-address-type": "ipv6", "prefix": 64},
            ],
        },
    ]


def guest_type(vm):
    return vm.get("type", "qemu")


UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def disk_bytes(config):
    """scsi0's size=, in bytes, as Proxmox reports maxdisk; 0 when there is none."""
    for option in str(config.get("scsi0", "")).split(","):
        if option.startswith("size=") and option[-1:] in UNITS:
            return int(float(option[5:-1]) * UNITS[option[-1]])
    return 0


def version_9():
    return int(VERSION["version"].split(".")[0]) >= 9


def live(vm):
    """A running VM's CPU, memory and uptime, the same each time it is asked; nothing for one that is not."""
    vmid, maxmem = vm["vmid"], int(vm["config"].get("memory", 512)) * 1024**2
    if vm["status"] != "running":
        return {"cpu": 0, "mem": 0, "uptime": 0}
    stats = {"cpu": round((vmid % 7 + 1) / 50, 4), "mem": maxmem * 3 // 8, "uptime": 3600 * (vmid % 50 + 1)}
    if version_9():
        stats["memhost"] = maxmem // 2
    return stats


def resource(vm):
    entry = {
        "vmid": vm["vmid"],
        "node": vm["node"],
        "type": guest_type(vm),
        "status": vm["status"],
        "id": f"{guest_type(vm)}/{vm['vmid']}",
        # What a guest is given, as Proxmox lists it: cores, and bytes.
        "maxcpu": int(vm["config"].get("cores", 1)),
        "maxmem": int(vm["config"].get("memory", 512)) * 1024**2,
        "maxdisk": disk_bytes(vm["config"]),
        "disk": 0,
        **live(vm),
    }
    # Proxmox fills name, template and the real status from the node's
    # statistics, which the cluster drops five minutes after a node stops
    # reporting. A guest on a node that is down is therefore listed with
    # neither, and consumers that index `name` without a default fall over.
    if vm["node"] != DOWN_NODE or NODES_ONLINE[DOWN_NODE]:
        entry["name"] = vm["name"]
        entry["template"] = vm["template"]
    if vm["node"] == DOWN_NODE and NODES_ONLINE[DOWN_NODE]:
        entry["status"] = "stopped"
    # PVE leaves the key out for a guest with no tags, rather than sending "".
    # Always sending it hid that anything filtering on tags has to allow for
    # a VM without the attribute at all.
    if vm["config"].get("tags"):
        entry["tags"] = vm["config"]["tags"]
    return entry


def save_state():
    STATE_DIR.joinpath("state.json").write_text(json.dumps(VMS, indent=2, sort_keys=True))


def new_task():
    global TASKS
    TASKS += 1
    return f"UPID:{NODE}:0000{TASKS:04X}:00000000:00000000:qmtask:{TASKS}:root@pam!ansible:"


class Handler(BaseHTTPRequestHandler):
    def _params(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        params = {k: v[0] for k, v in parse_qs(body).items()}
        params.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
        return params

    def _reply_empty(self, status, reason):
        self.send_response(status, reason)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reply(self, status, data=None, reason=None):
        # PVE puts the reason for a failure in the HTTP reason phrase, and
        # proxmoxer copies it into the error message ("500 Internal Server
        # Error: VM 4013 is not running"). Callers pattern-match that message,
        # so the fake has to say what Proxmox says.
        payload = json.dumps({"data": data}).encode()
        self.send_response(status, reason)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, method):
        path = urlparse(self.path).path
        params = self._params()
        token = TOKEN_RE.match(self.headers.get("Authorization", ""))
        with STATE_DIR.joinpath("calls.log").open("a") as log:
            # The agent tells a request the playbooks made themselves through
            # `uri` ("ansible-httpget") from one a community.proxmox module
            # made through proxmoxer ("python-requests/...").
            entry = {
                "method": method,
                "path": path,
                "params": params,
                "token": token and token.group(2),
                "agent": self.headers.get("User-Agent", ""),
            }
            log.write(json.dumps(entry) + "\n")

        if not token or token.group(3) != TOKEN_SECRET:
            # Real pveproxy sends 401 with an *empty* body, deliberately
            # withholding the reason; the detail survives only in the HTTP
            # reason phrase. A well-formed {"data": null} here would let the
            # role look better-informed than it can be in production.
            return self._reply_empty(401, "authentication failure")

        parts = [unquote(p) for p in path.removeprefix("/api2/json").strip("/").split("/")]
        if token.group(2) == NOAGENT_TOKEN_ID and "agent" in parts:
            vmid = parts[parts.index("agent") - 1]
            return self._reply(403, None, f"Permission check failed (/vms/{vmid}, VM.GuestAgent.Audit)")
        if parts[:2] == ["fake", "version"] and method == "PUT":
            # Not a PVE endpoint: a fixture's Proxmox version.
            VERSION["version"] = params["version"]
            return self._reply(200, dict(VERSION))
        if parts == ["version"]:
            release = ".".join(VERSION["version"].split(".")[:2])
            return self._reply(200, {"version": VERSION["version"], "release": release, "repoid": "fake"})
        if parts[:2] == ["fake", "privileges"] and method == "PUT":
            # Not a PVE endpoint: a fixture's privileges for one token at one path.
            PRIVILEGES[(params["token"], params["path"])] = [p for p in params.get("privs", "").split(",") if p]
            return self._reply(200, PRIVILEGES[(params["token"], params["path"])])
        if parts == ["access", "permissions"]:
            fixed = [path for token_id, path in PRIVILEGES if token_id == token.group(2)]
            paths = [params["path"]] if "path" in params else ["/vms", *fixed]
            granted = {path: privileges(token.group(2), path) for path in paths}
            return self._reply(200, {path: privs for path, privs in granted.items() if privs})
        if parts[:2] == ["fake", "nodes"] and method == "PUT" and len(parts) == 3:
            # Not a PVE endpoint: a fixture's switch for the node that is down.
            NODES_ONLINE[parts[2]] = params.get("status") == "online"
            return self._reply(200, {"node": parts[2], "status": params.get("status")})
        if parts == ["nodes"]:
            return self._reply(
                200,
                [
                    {"node": NODE, "status": "online"},
                    {"node": DOWN_NODE, "status": "online" if NODES_ONLINE[DOWN_NODE] else "offline"},
                ],
            )
        if parts[:2] == ["fake", "listing-lag"] and method == "PUT":
            # Not a PVE endpoint: a fixture's switch for the listing's lag.
            LISTING_LAG["listings"] = int(params.get("listings", "0"))
            return self._reply(200, dict(LISTING_LAG))
        if parts == ["cluster", "resources"] and params.get("type") == "storage":
            # Only the storages the token may audit, as Proxmox lists them; none on the node that is down.
            listed = []
            for node, entries in STORAGES.items():
                if node == DOWN_NODE and not NODES_ONLINE[DOWN_NODE]:
                    continue
                for entry in entries:
                    if "Datastore.Audit" not in privileges(token.group(2), "/storage/" + entry["storage"]):
                        continue
                    listed.append(
                        {
                            "id": f"storage/{node}/{entry['storage']}",
                            "type": "storage",
                            "storage": entry["storage"],
                            "node": node,
                            "plugintype": entry["type"],
                            "maxdisk": STORAGE_SIZE,
                            "disk": STORAGE_SIZE - entry["avail"],
                            "status": "available" if entry.get("active", 1) else "unknown",
                            "shared": 0,
                            "content": "images,rootdir",
                        }
                    )
            return self._reply(200, listed)
        if parts == ["cluster", "resources"] and params.get("type") == "node":
            # A token without Sys.Audit sees the nodes, and no statistics of theirs.
            nodes = [(NODE, "online"), (DOWN_NODE, "online" if NODES_ONLINE[DOWN_NODE] else "offline")]
            return self._reply(
                200, [{"id": f"node/{n}", "type": "node", "node": n, "status": s, "level": ""} for n, s in nodes]
            )
        if parts[:2] == ["fake", "pending"] and method == "PUT":
            # Not a PVE endpoint: a fixture's change waiting for a restart.
            PENDING.setdefault(int(params["vmid"]), {})[params["key"]] = params.get("value", "")
            return self._reply(200, PENDING[int(params["vmid"])])
        if parts[:2] == ["fake", "disk-ctime"] and method == "PUT":
            # Not a PVE endpoint: when a fixture's VM was made, as its disk's ctime says.
            VMS[int(params["vmid"])]["disk_ctime"] = int(params["ctime"])
            save_state()
            return self._reply(200, {"vmid": int(params["vmid"]), "ctime": int(params["ctime"])})
        if parts[:2] == ["fake", "root"] and method == "PUT":
            # Not a PVE endpoint: how full a fixture's VM's root disk is.
            ROOT_USED[int(params["vmid"])] = int(params["used"])
            return self._reply(200, {"vmid": int(params["vmid"]), "used": int(params["used"])})
        if parts == ["cluster", "resources"]:
            entries = [resource(vm) for vm in VMS.values()]
            for entry in entries:
                stale = STALE.get(entry["vmid"])
                if stale:
                    entry["status"] = stale[0]
                    stale[1] -= 1
                    if stale[1] <= 0:
                        del STALE[entry["vmid"]]
            return self._reply(200, entries)
        if parts == ["cluster", "nextid"]:
            return self._reply(200, str(next(i for i in range(NEXTID_LOWER, NEXTID_LOWER + 10000) if i not in VMS)))
        if parts[:2] == ["fake", "storages"] and method == "PUT":
            # Not a PVE endpoint: a fixture's storages for VM disks on a node.
            # <name>:<type>, or <name>:<type>:inactive for one Proxmox cannot reach.
            entries = [(entry.split(":") + [""])[:3] for entry in params.get("storages", "").split(",") if entry]
            STORAGES[params["node"]] = [
                {"storage": n, "type": t, "avail": 100 * 1024**3, "active": 0 if state == "inactive" else 1}
                for n, t, state in entries
            ]
            return self._reply(200, STORAGES[params["node"]])
        if parts[:2] == ["fake", "bridges"] and method == "PUT":
            # Not a PVE endpoint: a fixture's bridges on a node.
            BRIDGES[params["node"]] = [b for b in params.get("bridges", "").split(",") if b]
            return self._reply(200, BRIDGES[params["node"]])
        if parts[:2] == ["fake", "vm-node"] and method == "PUT":
            # Not a PVE endpoint: a fixture's VM, moved to another node.
            VMS[int(params["vmid"])]["node"] = params["node"]
            save_state()
            return self._reply(200, {"vmid": int(params["vmid"]), "node": params["node"]})
        if parts[:2] == ["nodes", DOWN_NODE] and not NODES_ONLINE[DOWN_NODE]:
            return self._reply_empty(595, "No route to host")
        if parts[:1] == ["nodes"] and parts[2:] == ["storage"]:
            # Only the storages the token may see, as Proxmox lists them.
            sees = {"Datastore.Audit", "Datastore.AllocateSpace"}
            seen = [
                {"active": 1, **entry, "content": "images,rootdir", "enabled": 1, "shared": 0}
                for entry in STORAGES.get(parts[1], [])
                if sees & set(privileges(token.group(2), "/storage/" + entry["storage"]))
            ]
            return self._reply(200, seen)
        if parts[:1] == ["nodes"] and parts[2:3] == ["storage"] and parts[4:] == ["content"]:
            # The disks on one storage, each VM's scsi0 that is on it; only with Datastore.Audit there.
            storage = parts[3]
            if "Datastore.Audit" not in privileges(token.group(2), "/storage/" + storage):
                return self._reply(403, None, f"Permission check failed (/storage/{storage}, Datastore.Audit)")
            volumes = []
            for vm in VMS.values():
                volid = str(vm["config"].get("scsi0", "")).split(",", 1)[0]
                if vm["node"] == parts[1] and volid.startswith(storage + ":"):
                    volumes.append(
                        {
                            "volid": volid,
                            "vmid": vm["vmid"],
                            "size": disk_bytes(vm["config"]),
                            "format": "raw",
                            "content": "images",
                            "ctime": vm.get("disk_ctime", 1760000000),
                        }
                    )
            return self._reply(200, volumes)
        if parts[:1] == ["nodes"] and parts[2:] == ["network"]:
            seen = [
                {"iface": bridge, "type": "bridge", "active": 1, "autostart": 1}
                for bridge in BRIDGES.get(parts[1], [])
                if {"SDN.Audit", "SDN.Use"} & set(privileges(token.group(2), "/sdn/zones/localnetwork/" + bridge))
            ]
            return self._reply(200, seen)
        # Either node, once up: a VM is served on the node it is on.
        if parts[:1] != ["nodes"] or len(parts) < 2 or parts[1] not in (NODE, DOWN_NODE):
            return self._reply(404, None)
        node, rest = parts[1], parts[2:]
        if rest == ["qemu"]:
            return self._reply(
                200, [resource(vm) for vm in VMS.values() if vm["node"] == node and guest_type(vm) == "qemu"]
            )
        if rest[:1] == ["tasks"] and rest[2:] == ["status"]:
            return self._reply(200, {"status": "stopped", "exitstatus": "OK", "upid": rest[1]})
        if rest[:1] == ["tasks"] and rest[2:] == ["log"]:
            return self._reply(200, [])
        if rest[:1] != ["qemu"] or len(rest) < 2 or not rest[1].isdigit():
            return self._reply(404, None)
        vmid, sub = int(rest[1]), rest[2:]
        if vmid not in VMS or VMS[vmid]["node"] != node or guest_type(VMS[vmid]) != "qemu":
            return self._reply(500, None, f"Configuration file 'nodes/{node}/qemu-server/{vmid}.conf' does not exist")
        vm = VMS[vmid]

        if method == "DELETE" and not sub:
            # Proxmox refuses to remove a VM with protection on, which is the
            # simplest way for one deletion of a fleet to fail.
            if str(vm["config"].get("protection", "0")) == "1":
                return self._reply(500, None, f"can't remove VM {vmid} - protection mode enabled")
            del VMS[vmid]
            save_state()
            return self._reply(200, new_task())
        if sub == ["config"] and method == "GET":
            return self._reply(200, dict(vm["config"]))
        if sub == ["config"] and method in ("PUT", "POST"):
            # PVE removes a setting through `delete`, a comma-separated list of
            # keys - not by sending it blank. parse_qs drops blank values, so a
            # fixture that sent `net0=` used to leave the NIC in place and pass
            # as if it had removed it.
            for key in filter(None, (k.strip() for k in params.pop("delete", "").split(","))):
                vm["config"].pop(key, None)
            # Not a PVE parameter: a fixture sends it to keep the tags exactly
            # as given - comma-joined, the way proxmox_kvm sends them and some
            # Proxmox versions echo them back - rather than normalised.
            verbatim = params.pop("fake-verbatim-tags", None)
            vm["config"].update(params)
            if "tags" in params and not verbatim:
                vm["config"]["tags"] = normalise_tags(params["tags"])
            if "name" in params:
                vm["name"] = params["name"]
            save_state()
            return self._reply(200, new_task() if method == "POST" else None)
        if sub == ["clone"] and method == "POST":
            newid = int(params["newid"])
            if newid in VMS:
                return self._reply(500, None)
            VMS[newid] = {
                "vmid": newid,
                "name": params.get("name", f"Copy-of-VM-{vmid}"),
                "node": node,
                "status": "stopped",
                "template": 0,
                "config": {
                    **{k: v for k, v in vm["config"].items() if k != "template"},
                    "name": params.get("name"),
                    "scsi0": vm["config"]["scsi0"].replace(f"base-{vmid}", f"vm-{newid}"),
                    "net0": f"virtio={mac_for(newid)},bridge=vmbr0",
                },
                # Its config keeps the template's meta; its disk is made now.
                "disk_ctime": int(time.time()),
            }
            save_state()
            return self._reply(200, new_task())
        if sub == ["template"] and method == "POST":
            # Only the tests' fixtures call this, to build a template that
            # carries this project's tag. PVE refuses to convert a running VM.
            if vm["status"] == "running":
                return self._reply(500, None)
            vm["template"] = 1
            vm["config"]["template"] = 1
            save_state()
            return self._reply(200, new_task())
        if sub == ["resize"] and method == "PUT":
            disk, size = params["disk"], params["size"]
            opts = vm["config"][disk].split(",")
            vm["config"][disk] = ",".join(o if not o.startswith("size=") else f"size={size}" for o in opts)
            save_state()
            return self._reply(200, new_task())
        if sub == ["agent", "network-get-interfaces"] and method == "GET":
            if vm["status"] != "running":
                return self._reply(500, None, f"VM {vmid} is not running")
            AGENT_CALLS[vmid] = AGENT_CALLS.get(vmid, 0) + 1
            if AGENT_CALLS[vmid] <= AGENT_READY_AFTER:
                return self._reply(500, None, "QEMU guest agent is not running")
            return self._reply(200, {"result": agent_interfaces(vm)})
        if sub == ["status", "current"]:
            maxmem = int(vm["config"].get("memory", 512)) * 1024**2
            current = {
                "status": vm["status"],
                "vmid": vmid,
                "name": vm["name"],
                "cpus": int(vm["config"].get("cores", 1)),
                "maxmem": maxmem,
                "qmpstatus": vm["status"],
                "agent": 1,
                "balloon": int(vm["config"].get("balloon", vm["config"].get("memory", 512))) * 1024**2,
                **live(vm),
            }
            if version_9():
                current.update(
                    {k: 0 for k in ("pressurecpusome", "pressurecpufull", "pressureiosome", "pressureiofull")}
                )
            return self._reply(200, current)
        if sub == ["rrddata"] and method == "GET":
            # A day: one point a minute on VE 9, one every twenty minutes on 8, the VM's CPU rising and falling.
            step = 60 if version_9() else 1200
            now = int(time.time()) // step * step
            points = []
            for i in range(86400 // step):
                t = now - 86400 + (i + 1) * step
                point = {"time": t}
                if vm["status"] == "running":
                    point.update(
                        {"cpu": round(0.05 + 0.04 * ((t // 7200) % 5), 4), "maxcpu": int(vm["config"].get("cores", 1))}
                    )
                    if version_9():
                        point.update({"pressurecpusome": 0.5, "memhost": 1})
                points.append(point)
            return self._reply(200, points)
        if sub == ["pending"] and method == "GET":
            entries = [{"key": k, "value": v} for k, v in sorted(vm["config"].items())]
            for key, value in sorted(PENDING.get(vmid, {}).items()):
                entries = [e for e in entries if e["key"] != key] + [
                    {"key": key, "value": vm["config"].get(key), "pending": value}
                ]
            return self._reply(200, entries)
        if sub[:1] == ["agent"] and sub[1:] in (["get-osinfo"], ["get-fsinfo"]) and method == "GET":
            if vm["status"] != "running":
                return self._reply(500, None, f"VM {vmid} is not running")
            if AGENT_CALLS.get(vmid, 0) <= AGENT_READY_AFTER:
                return self._reply(500, None, "QEMU guest agent is not running")
            if sub[1] == "get-osinfo":
                return self._reply(
                    200,
                    {
                        "result": {
                            "id": "ubuntu",
                            "name": "Ubuntu",
                            "pretty-name": "Ubuntu 24.04.3 LTS",
                            "version-id": "24.04",
                            "kernel-release": "6.8.0-85-generic",
                            "machine": "x86_64",
                        }
                    },
                )
            used = ROOT_USED.get(vmid, 11 * 1024**3)
            return self._reply(
                200,
                {
                    "result": [
                        {
                            "name": "sda1",
                            "mountpoint": "/",
                            "type": "ext4",
                            "used-bytes": used,
                            "total-bytes": ROOT_TOTAL,
                            "disk": [],
                        },
                        {
                            "name": "sda15",
                            "mountpoint": "/boot/efi",
                            "type": "vfat",
                            "used-bytes": 6 * 1024**2,
                            "total-bytes": 105 * 1024**2,
                            "disk": [],
                        },
                    ]
                },
            )
        if sub[:1] == ["status"] and method == "POST":
            action = sub[1]
            if LISTING_LAG["listings"] > 0 and vmid not in STALE:
                STALE[vmid] = [vm["status"], LISTING_LAG["listings"]]
            vm["status"] = "running" if action in ("start", "reboot", "reset", "resume") else "stopped"
            save_state()
            return self._reply(200, new_task())
        return self._reply(501, None)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, *args):
        pass


save_state()
server = HTTPServer(("0.0.0.0", PORT), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
# Given port 0 the system picks a free one, said on stdout: tests/unit/harness.py starts fakes so,
# never on a port it probed and freed, which another xdist worker's fake could take first.
if PORT == 0:
    print(server.server_address[1], flush=True)
server.serve_forever()
