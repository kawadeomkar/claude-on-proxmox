"""Stateful stand-in for the Proxmox VE commands tools/proxmox-access.sh runs: pveum, pvesh, pveversion, hostname, id.

Run as `fake_pve_cli.py <state.json> <command> <args...>`, through a wrapper of
each command's name (install() writes them). It keeps the host in one JSON file
- the version, the node, its storages and bridges, the roles, users, ACLs and
tokens - answers only the calls the script makes, as Proxmox answers them
(compact JSON, `pveum` refusing what exists), and appends every call to
calls.log beside the state. A call whose words contain the state's "fail"
string fails, as a refused write would.
"""

from __future__ import annotations

import json
import shlex
import sys
import uuid
from pathlib import Path

# The privileges each version knows, enough to refuse the one it does not:
# Proxmox VE 9 dropped VM.Monitor for VM.GuestAgent.*.
COMMON = (
    "VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.Memory VM.Config.Network"
    " VM.Config.Options VM.Config.HWType VM.Config.CDROM VM.PowerMgmt VM.Audit VM.Console VM.Backup VM.Snapshot"
    " VM.Migrate Datastore.AllocateSpace Datastore.Audit Datastore.Allocate SDN.Use SDN.Audit SDN.Allocate"
    " Sys.Audit Sys.Modify Permissions.Modify User.Modify"
).split()
BY_MAJOR = {8: ["VM.Monitor"], 9: ["VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"]}

DEFAULT = {
    "version": "9.2.13",
    "node": "pve",
    "uid": 0,
    "storages": [
        {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir"},
        {"storage": "local", "type": "dir", "content": "iso,vztmpl,backup"},
    ],
    "bridges": ["vmbr0"],
    "roles": {"PVEAuditor": ["VM.Audit", "Datastore.Audit", "SDN.Audit", "Sys.Audit"]},
    "users": {"root@pam": {}},
    "acls": [],
    "tokens": {},
    "fail": "",
}


def install(directory: Path, state: dict | None = None) -> Path:
    """Write the state and a wrapper per command into directory; returns it, to put first on PATH."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pve-state.json").write_text(json.dumps(DEFAULT | (state or {})))
    for name in ("pveum", "pvesh", "pveversion", "hostname", "id"):
        wrapper = directory / name
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(__file__)} "
            f'"$(dirname "$0")/pve-state.json" {name} "$@"\n'
        )
        wrapper.chmod(0o755)
    return directory


def _major(state: dict) -> int:
    return int(state["version"].split(".")[0])


def _known(state: dict) -> set:
    return {*COMMON, *BY_MAJOR.get(_major(state), [])}


def _options(words: list[str]) -> tuple[list[str], dict]:
    """Positional words, and --name value pairs."""
    positional, options, index = [], {}, 0
    while index < len(words):
        if words[index].startswith("--"):
            options[words[index][2:]] = words[index + 1]
            index += 2
        else:
            positional.append(words[index])
            index += 1
    return positional, options


def _held(state: dict, user: str, path: str) -> dict:
    """The privileges user holds on path: an ACL on the path, or one that propagates from above it."""
    held = {}
    for acl in state["acls"]:
        above = acl["path"] == "/" or path.startswith(acl["path"].rstrip("/") + "/")
        if acl["ugid"] == user and (acl["path"] == path or (above and acl["propagate"])):
            for privilege in state["roles"].get(acl["roleid"], []):
                held[privilege] = acl["propagate"]
    return held


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _pvesh(state: dict, words: list[str]) -> tuple[int, object]:
    (verb, path, *_), options = _options(words)
    if verb != "get" or options.get("output-format") != "json":
        return _refuse(f"fake pvesh: unsupported call {words}"), None
    parts = path.strip("/").split("/")
    if parts[:2] == ["access", "roles"] and len(parts) == 3:
        if parts[2] not in state["roles"]:
            return _refuse(f"role '{parts[2]}' does not exist"), None
        return 0, dict.fromkeys(state["roles"][parts[2]], 1)
    if parts[:2] == ["access", "users"] and len(parts) == 3:
        if parts[2] not in state["users"]:
            return _refuse(f"no such user ('{parts[2]}')"), None
        return 0, {"enable": 1, "expire": 0}
    if parts[:2] == ["access", "users"] and len(parts) == 5 and parts[3] == "token":
        token = state["tokens"].get(f"{parts[2]}!{parts[4]}")
        if token is None:
            return _refuse(f"no such token '{parts[4]}' for user '{parts[2]}'"), None
        return 0, {"expire": 0, "privsep": token["privsep"]}
    if parts[0] == "nodes" and len(parts) == 3:
        if parts[1] != state["node"]:
            return _refuse(f"hostname lookup '{parts[1]}' failed - failed to get address info"), None
        if parts[2] == "storage" and options.get("content") == "images":
            listed = [s for s in state["storages"] if "images" in s["content"].split(",")]
            return 0, [{"active": 1, "enabled": 1, "shared": 0, **s} for s in listed]
        if parts[2] == "network" and options.get("type") == "any_local_bridge":
            return 0, [{"active": 1, "iface": b, "type": "bridge"} for b in state["bridges"]]
    return _refuse(f"fake pvesh: no such path {path}"), None


def _pveum(state: dict, words: list[str]) -> tuple[int, object]:
    positional, options = _options(words)
    match positional:
        case ["role", "add", role]:
            if role in state["roles"]:
                return _refuse(f"role '{role}' already exists"), None
            privileges = options.get("privs", "").replace(";", ",").replace(" ", ",").split(",")
            unknown = [p for p in privileges if p and p not in _known(state)]
            if unknown:
                return _refuse(f"invalid privilege '{unknown[0]}'"), None
            state["roles"][role] = [p for p in privileges if p]
            return 0, None
        case ["user", "add", user]:
            if user in state["users"]:
                return _refuse(f"create user failed: user '{user}' already exists"), None
            state["users"][user] = {"comment": options.get("comment", "")}
            return 0, None
        case ["acl", "modify", path]:
            role, user = options["roles"], options["users"]
            if role not in state["roles"]:
                return _refuse(f"role '{role}' does not exist"), None
            if user not in state["users"]:
                return _refuse(f"user '{user}' does not exist"), None
            propagate = int(options.get("propagate", "1"))
            state["acls"] = [a for a in state["acls"] if (a["path"], a["ugid"], a["roleid"]) != (path, user, role)]
            state["acls"].append({"path": path, "ugid": user, "roleid": role, "propagate": propagate, "type": "user"})
            return 0, None
        case ["user", "permissions", user]:
            if user not in state["users"]:
                return _refuse(f"user '{user}' does not exist"), None
            path = options["path"]
            return 0, {path: _held(state, user, path)}
        case ["user", "token", "add", user, name]:
            if user not in state["users"]:
                return _refuse(f"no such user ('{user}')"), None
            if f"{user}!{name}" in state["tokens"]:
                return _refuse("Token already exists."), None
            secret = state.pop("next_secret", None) or str(uuid.uuid4())
            privsep = int(options.get("privsep", "1"))
            state["tokens"][f"{user}!{name}"] = {"privsep": privsep, "secret": secret}
            info = {"privsep": str(privsep), "comment": options.get("comment", "")}
            return 0, {"full-tokenid": f"{user}!{name}", "info": info, "value": secret}
        case ["user", "token", "delete" | "remove", user, name]:
            if state["tokens"].pop(f"{user}!{name}", None) is None:
                return _refuse(f"no such token '{name}' for user '{user}'"), None
            return 0, None
    return _refuse(f"fake pveum: unsupported call {words}"), None


def main(argv: list[str]) -> int:
    state_file, command, *words = argv
    state_path = Path(state_file)
    state = json.loads(state_path.read_text())
    with (state_path.parent / "calls.log").open("a") as log:
        log.write(json.dumps([command, *words]) + "\n")
    if state.get("fail") and state["fail"] in " ".join([command, *words]):
        print(f"fake {command}: refused, as the test asked", file=sys.stderr)
        return 1
    if command == "pveversion":
        print(f"pve-manager/{state['version']}/fakefakefakefake (running kernel: 6.8.12-1-pve)")
        return 0
    if command == "hostname":
        print(state["node"])
        return 0
    if command == "id":
        print(state["uid"] if words == ["-u"] else f"uid={state['uid']}")
        return 0
    rc, output = (_pvesh if command == "pvesh" else _pveum)(state, words)
    if rc == 0:
        state_path.write_text(json.dumps(state))
        if output is not None:
            print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
