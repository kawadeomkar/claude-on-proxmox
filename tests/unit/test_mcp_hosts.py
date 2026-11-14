"""What the MCP server hands just, tool by tool, with more than one host (lib/claude_vm/mcp/server.py): the read it asks
which host a run would reach, just host's JSON given the names and the host, and the run. The server imports nothing
of the hosts; what can still go wrong is how each tool builds the read and what it does with the answer, which is where
the stack's review found three of its four faults in MCP's host handling (docs/architecture/custom-code.md §7.12).

As test_mcp_confirm.py, the server runs a copy of this checkout through the real just with stand-ins, its just
recording every command line it is given; the checkout has two hosts, pve (the first) and nuc, and an alias file whose
blocks place beta and my-vm on nuc and gamma on pve."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from test_hosts import ALWAYS, PROMISED_KEYS
from test_mcp_confirm import REPORT, SETTING, _talk, _typed
from test_mcp_server import JUST, _copy, _text

from claude_vm.mcp.results import json_result
from claude_vm.mcp.server import HOSTS_KEEP

ALIASES = {"beta": "nuc", "my-vm": "nuc", "gamma": "pve"}


def _just_recording(root: Path, bin_dir: Path, calls: Path) -> Path:
    """test_mcp_server.py's just, which also writes each command line it is given, one a line, its words split by a
    unit separator."""
    wrapper = root / "just"
    wrapper.write_text(
        f'#!/bin/sh\nfor word in "$@"; do printf "%s\\037" "$word"; done >> "{calls}"\necho >> "{calls}"\n'
        f'a=$1 b=$2\nshift 2\ncase "$1" in --*) exec "{JUST}" "$a" "$b" "$@" ;; esac\n'
        f'exec "{JUST}" "$a" "$b" BIN="{bin_dir}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return wrapper


def _hosts(checkout: Path, root: Path) -> None:
    """pve in inventory/, nuc in hosts/nuc/, every vault a stub that looks encrypted: the resolver opens none, and
    says so."""
    (checkout / "inventory" / "hosts.yml").write_text(
        yaml.safe_dump({"all": {"children": {"proxmox": {"hosts": {"pve": {"ansible_host": "192.0.2.10"}}}}}})
    )
    (checkout / "inventory" / "group_vars" / "all" / "local.yml").write_text(
        yaml.safe_dump({"claude_ssh_config_file": str(root / "aliases.conf")})
    )
    nuc = checkout / "hosts" / "nuc"
    (nuc / "group_vars" / "all").mkdir(parents=True)
    (nuc / "hosts.yml").write_text(
        yaml.safe_dump({"all": {"children": {"proxmox": {"hosts": {"nuc": {"ansible_host": "192.0.2.20"}}}}}})
    )
    (nuc / "group_vars" / "all" / "local.yml").write_text(yaml.safe_dump(ALWAYS))
    (nuc / "group_vars" / "all" / "vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n3132\n")
    (root / "aliases.conf").write_text(
        "".join(
            f"# BEGIN agent-warehouse: {name}\n# origin: {origin}\nHost {name}\n# END agent-warehouse: {name}\n"
            for name, origin in ALIASES.items()
        )
    )


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp-hosts")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    runs = root / "runs"
    runs.mkdir()
    (bin_dir / "python").write_text(
        "#!/bin/sh\n"
        'case "${1:-}" in tools/settings)\n'
        f"  printf '%s\\n' '{json.dumps(SETTING)}'\n  exit 0 ;;\n"
        f"tools/status) printf '%s\\n' '{json.dumps(REPORT)}'; exit 0 ;;\nesac\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    (bin_dir / "python").chmod(0o755)
    (bin_dir / "ansible-galaxy").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "ansible-galaxy").chmod(0o755)
    (bin_dir / "ansible-playbook").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{runs}/playbook.$$"\necho done\n')
    (bin_dir / "ansible-playbook").chmod(0o755)
    home = root / "home"
    home.mkdir()
    checkout = _copy(root)
    _hosts(checkout, root)
    calls = root / "just.calls"
    return {
        "root": root,
        "runs": runs,
        "calls": calls,
        "checkout": checkout,
        "bin": bin_dir,
        "just": _just_recording(root, bin_dir, calls),
        "home": home,
        "path": f"{Path(shutil.which('just')).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }


def _called(world: dict) -> list[list[str]]:
    """Every just command line since the last, each as its recipe and words: just's own options and the settings
    before the recipe left out, --dump's calls too."""
    try:
        lines = world["calls"].read_text().splitlines()
    except FileNotFoundError:
        lines = []
    world["calls"].write_text("")
    found = []
    for line in lines:
        words = line.split("\x1f")[:-1]
        rest = words[2:]  # --justfile <path>
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            rest = rest[1:]
        if rest and not rest[0].startswith("-"):
            found.append(rest)
    return found


def _reads(called: list[list[str]]) -> list[list[str]]:
    return [words[1:] for words in called if words[0] == "host"]


def _ran(called: list[list[str]], recipe: str) -> list[list[str]]:
    return [words[1:] for words in called if words[0] == recipe]


def _host_option(words: list[str]) -> str:
    return next((word.split("=", 1)[1] for word in words if word.startswith("--host=")), "")


def test_names_reach_the_read_hyphenated_and_the_run_gets_no_host_it_was_not_given(world):
    _called(world)
    _, _, (result,) = _talk(world, [("login", {"names": ["my_vm"]})])
    assert not result.is_error, _text(result)
    called = _called(world)
    # The job's title says where the run goes; the run itself is given no --host, and finds my-vm where its alias is.
    assert _reads(called) == [["--json", "--for=my-vm"]]
    (login,) = _ran(called, "login")
    assert _host_option(login) == "" and "my_vm" in login
    assert "--host=nuc" in _text(result)


def test_a_host_given_reaches_both_the_read_and_the_run(world):
    _called(world)
    _, _, (result,) = _talk(world, [("deploy", {"names": ["newvm"], "host": "nuc"})])
    assert not result.is_error, _text(result)
    called = _called(world)
    (deploy,) = _ran(called, "deploy")
    assert _host_option(deploy) == "nuc"
    # Its title names the host given; no read asks which host, since the call said.
    assert _reads(called) == []


def test_a_deploy_asks_whether_a_name_is_placed_and_refuses_one_no_alias_places_without_a_host(world):
    _called(world)
    _, _, (placed, unplaced) = _talk(world, [("deploy", {"names": ["beta"]}), ("deploy", {"names": ["newvm"]})])
    assert not placed.is_error, _text(placed)
    assert unplaced.is_error and "name the host" in _text(unplaced)
    called = _called(world)
    # The read deploy's refusal is made from, never with a --host the call did not give; and one run, the placed VM's.
    reads = _reads(called)
    assert ["--json", "--for=newvm"] in reads and ["--json", "--for=beta"] in reads
    assert all(_host_option(words) == "" for words in reads), reads
    (deploy,) = _ran(called, "deploy")
    assert _host_option(deploy) == "" and "beta" in deploy


def test_init_never_gets_a_host_it_was_not_given(world):
    _called(world)
    _talk(world, [("init", {"github_user": "someone"})])
    called = _called(world)
    assert _reads(called) == []
    (init,) = _ran(called, "init")
    assert _host_option(init) == ""


def test_destroy_pins_the_host_the_person_confirmed(world):
    _called(world)
    _, asked, (result,) = _talk(
        world, [("destroy", {"names": ["beta"]})], lambda message: ("accept", {"confirm": _typed(message)})
    )
    assert not result.is_error, _text(result)
    assert "on the Proxmox host nuc" in asked[0]
    called = _called(world)
    # Asked for the dialog and again in the body, each time for beta alone; the run is given the host confirmed.
    reads = _reads(called)
    assert len(reads) >= 2 and all(words == ["--json", "--for=beta"] for words in reads), reads
    (destroy,) = _ran(called, "destroy")
    assert _host_option(destroy) == "nuc" and "--confirm=beta" in destroy


def test_the_settings_dialog_names_the_file_the_host_given_reads(world):
    _called(world)
    _, asked, _ = _talk(
        world,
        [("settings_set", {"name": "vm_cores", "value": "4", "host": "nuc"})],
        lambda message: ("decline", None),
    )
    assert "hosts/nuc/group_vars/all/local.yml, nuc's own" in asked[0]
    _, asked, _ = _talk(
        world, [("settings_set", {"name": "vm_cores", "value": "4"})], lambda message: ("decline", None)
    )
    assert "inventory/group_vars/all/local.yml, every host's" in asked[0]
    # The list alone: which file a setting is in needs no run's host.
    assert {tuple(words) for words in _reads(_called(world))} == {("--json",)}


def test_the_settings_dialog_is_not_shown_when_the_hosts_cannot_be_read(world):
    # just host --json fails; every other command line runs as before. Read as one host, the dialog would name the
    # shared file plain and show the current host's value for it, which the write never reads.
    wrapper = world["just"]
    text = wrapper.read_text()
    wrapper.write_text(
        text.replace("#!/bin/sh\n", '#!/bin/sh\nfor word in "$@"; do [ "$word" = host ] && exit 1; done\n', 1)
    )
    try:
        _, asked, (result,) = _talk(
            world, [("settings_set", {"name": "vm_cores", "value": "4"})], lambda message: ("decline", None)
        )
    finally:
        wrapper.write_text(text)
    assert asked == [] and result.is_error and "could not be read" in _text(result), _text(result)
    assert _ran(_called(world), "settings") == []


def test_a_hosts_result_cut_to_fit_keeps_keys_just_host_promises():
    assert set(HOSTS_KEEP) <= set(PROMISED_KEYS)
    listed = {"current_host": "pve", "hosts": [{"name": "pve"}], "where": {"notes": "n" * 50_000}}
    cut = json_result(listed, "on main at abc", HOSTS_KEEP, "name the host")
    assert "where" not in cut and {key: cut[key] for key in HOSTS_KEEP} == {key: listed[key] for key in HOSTS_KEEP}
