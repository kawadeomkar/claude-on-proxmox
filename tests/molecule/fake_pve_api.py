#!/usr/bin/env python3
"""Minimal stateful stand-in for the Proxmox VE REST API.

Implements just enough of /api2/json for community.proxmox's proxmox_vm_info,
proxmox_kvm (clone / update / start / shutdown / delete) and proxmox_disk
(resize) so roles/proxmox_vm can be exercised without a hypervisor. Every
request is appended as a JSON line to <state_dir>/calls.log and the VM table is
written to <state_dir>/state.json after each mutation, for verification.

Usage: fake_pve_api.py <port> <certfile> <keyfile> <state_dir> <expected_token_secret>
"""

import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(sys.argv[1])
CERT, KEY = sys.argv[2], sys.argv[3]
STATE_DIR = Path(sys.argv[4])
TOKEN_SECRET = sys.argv[5]
NODE = "pve"

STATE_DIR.mkdir(parents=True, exist_ok=True)
VMS = {
    9000: {
        "vmid": 9000,
        "name": "ubuntu-24.04-cloudinit",
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
    }
}
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


def resource(vm):
    return {
        "vmid": vm["vmid"],
        "name": vm["name"],
        "node": NODE,
        "type": "qemu",
        "status": vm["status"],
        "template": vm["template"],
        "tags": vm["config"].get("tags", ""),
        "id": f"qemu/{vm['vmid']}",
    }


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

    def _reply(self, status, data=None):
        payload = json.dumps({"data": data}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, method):
        path = urlparse(self.path).path
        params = self._params()
        with STATE_DIR.joinpath("calls.log").open("a") as log:
            log.write(json.dumps({"method": method, "path": path, "params": params}) + "\n")

        auth = self.headers.get("Authorization", "")
        if not (auth.startswith("PVEAPIToken=") and auth.endswith(f"={TOKEN_SECRET}")):
            # Real pveproxy sends 401 with an *empty* body, deliberately
            # withholding the reason; the detail survives only in the HTTP
            # reason phrase. A well-formed {"data": null} here would let the
            # role look better-informed than it can be in production.
            return self._reply_empty(401, "authentication failure")

        parts = [unquote(p) for p in path.removeprefix("/api2/json").strip("/").split("/")]
        if parts == ["version"]:
            return self._reply(200, {"version": "8.2.4", "release": "8.2", "repoid": "fake"})
        if parts == ["nodes"]:
            return self._reply(200, [{"node": NODE, "status": "online"}])
        if parts == ["cluster", "resources"]:
            return self._reply(200, [resource(vm) for vm in VMS.values()])
        if parts == ["cluster", "nextid"]:
            return self._reply(200, str(max(VMS) + 1))
        if parts[:2] != ["nodes", NODE]:
            return self._reply(404, None)
        rest = parts[2:]
        if rest == ["qemu"]:
            return self._reply(200, [resource(vm) for vm in VMS.values()])
        if rest[:1] == ["tasks"] and rest[2:] == ["status"]:
            return self._reply(200, {"status": "stopped", "exitstatus": "OK", "upid": rest[1]})
        if rest[:1] == ["tasks"] and rest[2:] == ["log"]:
            return self._reply(200, [])
        if rest[:1] != ["qemu"] or len(rest) < 2 or not rest[1].isdigit():
            return self._reply(404, None)
        vmid, sub = int(rest[1]), rest[2:]
        if vmid not in VMS:
            return self._reply(500, None)
        vm = VMS[vmid]

        if method == "DELETE" and not sub:
            del VMS[vmid]
            save_state()
            return self._reply(200, new_task())
        if sub == ["config"] and method == "GET":
            return self._reply(200, dict(vm["config"]))
        if sub == ["config"] and method in ("PUT", "POST"):
            vm["config"].update(params)
            if "tags" in params:
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
        if sub == ["resize"] and method == "PUT":
            disk, size = params["disk"], params["size"]
            opts = vm["config"][disk].split(",")
            vm["config"][disk] = ",".join(o if not o.startswith("size=") else f"size={size}" for o in opts)
            save_state()
            return self._reply(200, new_task())
        if sub == ["agent", "network-get-interfaces"] and method == "GET":
            AGENT_CALLS[vmid] = AGENT_CALLS.get(vmid, 0) + 1
            if vm["status"] != "running" or AGENT_CALLS[vmid] <= AGENT_READY_AFTER:
                return self._reply(500, None)
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
