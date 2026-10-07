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

A second token id, "noagent", stands for a token created without the
guest-agent privilege (VM.Monitor on PVE 8, VM.GuestAgent.Audit on 9): every
request is accepted except the agent's, which is refused with 403.

Usage: fake_pve_api.py <port> <certfile> <keyfile> <state_dir> <expected_token_secret>
"""

import json
import re
import ssl
import sys
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
            "template": 1,
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


def resource(vm):
    entry = {
        "vmid": vm["vmid"],
        "node": vm["node"],
        "type": guest_type(vm),
        "status": vm["status"],
        "id": f"{guest_type(vm)}/{vm['vmid']}",
    }
    # Proxmox fills name, template and the real status from the node's
    # statistics, which the cluster drops five minutes after a node stops
    # reporting. A guest on a node that is down is therefore listed with
    # neither, and consumers that index `name` without a default fall over.
    if vm["node"] != DOWN_NODE:
        entry["name"] = vm["name"]
        entry["template"] = vm["template"]
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
        if parts == ["version"]:
            return self._reply(200, {"version": "8.2.4", "release": "8.2", "repoid": "fake"})
        if parts == ["nodes"]:
            return self._reply(200, [{"node": NODE, "status": "online"}, {"node": DOWN_NODE, "status": "offline"}])
        if parts == ["cluster", "resources"]:
            return self._reply(200, [resource(vm) for vm in VMS.values()])
        if parts == ["cluster", "nextid"]:
            return self._reply(200, str(next(i for i in range(NEXTID_LOWER, NEXTID_LOWER + 10000) if i not in VMS)))
        if parts[:2] == ["nodes", DOWN_NODE]:
            return self._reply_empty(595, "No route to host")
        if parts[:2] != ["nodes", NODE]:
            return self._reply(404, None)
        rest = parts[2:]
        if rest == ["qemu"]:
            return self._reply(
                200, [resource(vm) for vm in VMS.values() if vm["node"] == NODE and guest_type(vm) == "qemu"]
            )
        if rest[:1] == ["tasks"] and rest[2:] == ["status"]:
            return self._reply(200, {"status": "stopped", "exitstatus": "OK", "upid": rest[1]})
        if rest[:1] == ["tasks"] and rest[2:] == ["log"]:
            return self._reply(200, [])
        if rest[:1] != ["qemu"] or len(rest) < 2 or not rest[1].isdigit():
            return self._reply(404, None)
        vmid, sub = int(rest[1]), rest[2:]
        if vmid not in VMS or VMS[vmid]["node"] != NODE or guest_type(VMS[vmid]) != "qemu":
            return self._reply(500, None, f"Configuration file 'nodes/{NODE}/qemu-server/{vmid}.conf' does not exist")
        vm = VMS[vmid]

        if method == "DELETE" and not sub:
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
                "node": NODE,
                "status": "stopped",
                "template": 0,
                "config": {
                    **{k: v for k, v in vm["config"].items() if k != "template"},
                    "name": params.get("name"),
                    "scsi0": vm["config"]["scsi0"].replace(f"base-{vmid}", f"vm-{newid}"),
                    "net0": f"virtio={mac_for(newid)},bridge=vmbr0",
                },
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
            return self._reply(200, {"status": vm["status"], "vmid": vmid, "name": vm["name"]})
        if sub[:1] == ["status"] and method == "POST":
            action = sub[1]
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
server.serve_forever()
