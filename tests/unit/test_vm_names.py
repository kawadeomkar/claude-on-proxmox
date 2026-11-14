"""A VM is never named after a host of the inventory (claude_vm_inventory_hosts, group_vars/all).

add_host merges a VM into the host of its name. A VM named pve - the example inventory's name for the Proxmox host -
gave that host the address its own guest agent reported, and the API token went there in the same run; one named
localhost made this machine a VM for configure to run every role on. On a Proxmox others use too, anyone can make
and tag a VM of either name. Each is run here against the fake Proxmox (tests/molecule/fake_pve_api.py) with an
inventory whose Proxmox host is pve, as hosts.yml.example has it, and whose API address is read from that host, as
group_vars/all reads it:

- discovery leaves such a VM out, and says so;
- just status shows it from the listing, read no further, and reads every other VM through the API's own address;
- a name given that is one is refused before Proxmox is asked anything, by every playbook that takes names;
- a fleet-wide alias refresh removes an alias of such a name, or of an address, that an earlier version wrote.

And the names 1.x left in Proxmox: its default VM's, which a bare run refuses to miss, and its ownership tag, under
which a VM is still the project's, and which provisioning writes under the new one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _messages, _pve, _pve_vm, _results, _run, fake_fleet
from test_status import _status_run, status_root

PLAYBOOKS = REPO / "playbooks"
# The Proxmox host's address read from the inventory's host, as group_vars/all/defaults.yml has it: a VM merged into
# that host would move the API there.
FROM_THE_HOST = {"proxmox_api_host": "{{ hostvars[groups['proxmox'][0]]['ansible_host'] }}"}


@pytest.fixture
def fleet(tmp_path):
    """The fake fleet - alpha and beta running, gamma stopped - and two VMs more, tagged and running, named pve and
    localhost."""
    for base in fake_fleet(tmp_path):
        for vmid, name in [(9013, "pve"), (9014, "localhost")]:
            _pve_vm(base, vmid, name, running=True, tags="agent-warehouse", ciuser="dev", onboot="1")
        yield base


def _proxmox_inventory(root: Path) -> list[str]:
    """The Proxmox host as hosts.yml.example names it, at the fake's address: -i arguments for a run."""
    inventory = root / "proxmox.yml"
    inventory.write_text(yaml.safe_dump({"proxmox": {"hosts": {"pve": {"ansible_host": "127.0.0.1"}}}}))
    return ["-i", str(inventory)]


def _playbook(tmp_path: Path, base: str, path: Path, *extra: str, settings: dict = FROM_THE_HOST) -> dict:
    """A playbook of the project's against the fake, from a run directory of its own with settings, the files it
    imports where they are: deploy.yml's playbooks/, or a playbook's tasks/. extra goes on the command line."""
    root = status_root(tmp_path, base, settings)
    text = path.read_text()
    if path.parent == REPO:
        text = text.replace(" playbooks/", f" {PLAYBOOKS}/")
    else:
        text = text.replace("tasks/", f"{PLAYBOOKS / 'tasks'}/")
    (root / path.name).write_text(text)
    return _run(root, root / path.name, {}, *_proxmox_inventory(root), *extra, timeout=180)


def _discover(tmp_path: Path, base: str, *extra: str) -> dict:
    return _playbook(tmp_path, base, PLAYBOOKS / "discover.yml", *extra)


def _refresh(tmp_path: Path, base: str, *extra: str) -> dict:
    """playbooks/ssh_config.yml against the fake, its managed file the test's own (tmp_path / "ssh.conf")."""
    settings = {**FROM_THE_HOST, "claude_ssh_user_config": str(tmp_path / "config"), "claude_ssh_config_include": False}
    return _playbook(tmp_path, base, PLAYBOOKS / "ssh_config.yml", *extra, settings=settings)


def _creating(tmp_path: Path, base: str, playbook: str, names: str, *inventory: str) -> dict:
    """deploy.yml or provision.yml against the fake, with names given, from a run directory of its own; inventory, more
    -i arguments."""
    path = REPO / "deploy.yml" if playbook == "deploy.yml" else PLAYBOOKS / playbook
    extra = json.dumps({"vm_name": names, "deploy_sign_in": False})
    return _playbook(tmp_path, base, path, *inventory, "-e", extra)


def _block(name: str, origin: str = "") -> str:
    record = f"# origin: {origin}\n" if origin else ""
    body = f"{record}Host {name}\n    HostName 192.0.2.99\n"
    return f"# BEGIN agent-warehouse: {name}\n{body}# END agent-warehouse: {name}\n"


def _recorded(path: Path) -> dict[str, str]:
    """Each block of the managed file at path, by name, and the host it records ("" for none)."""
    blocks = re.findall(
        r"^# BEGIN agent-warehouse: (\S+)\n(.*?)^# END agent-warehouse: \1$", path.read_text(), flags=re.M | re.S
    )
    return {name: (re.findall(r"^# origin: (\S+)$", body, flags=re.M) or [""])[0] for name, body in blocks}


# The checkout's other host, as tools/check host hands it to a run against this one.
NUC = json.dumps({"claude_hosts_other": [{"name": "nuc", "address": "192.0.2.100"}]})


def _task(report: dict, name: str) -> dict:
    """What the first task of that name did on localhost."""
    return next((result for task, host, result in _results(report) if task == name and host == "localhost"), {})


def _refused(report: dict) -> list[dict]:
    """vm_names.yml's failed refusals, on localhost."""
    return [
        result
        for task, host, result in _results(report)
        if host == "localhost" and task.startswith("vm_names | ") and result.get("failed")
    ]


# Discovery and the alias refresh say it alike, from one task file.
LEFT_OUT = "vms_requested | Say which tagged VMs are left out for their name"


def test_discovery_leaves_out_a_vm_named_after_a_host_of_the_inventory_and_says_so(tmp_path, fleet) -> None:
    report = _discover(tmp_path, fleet)
    assert report["rc"] == 0, report["stderr"][-2000:]
    added = _task(report, "Add the reachable ones to the inventory")
    names = [r["claude_vm_found"]["name"] for r in added["results"] if not r.get("skipped")]
    assert sorted(names) == ["alpha", "beta"]
    said = _messages(report, LEFT_OUT)["localhost"]
    assert "localhost" in said and "pve" in said


def test_a_name_given_that_is_a_host_of_the_inventory_is_refused_before_proxmox_is_asked(tmp_path, fleet) -> None:
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    report = _discover(tmp_path, fleet, "-e", json.dumps({"vm_name": "alpha,pve"}))
    assert report["rc"] != 0
    refused = _task(report, "vm_names | Refuse a VM named after a host of the inventory")
    assert refused.get("failed") and "pve is the name of a host in the inventory" in refused["msg"]
    assert calls.read_text() == before


def test_status_shows_such_a_vm_unread_and_reads_the_rest_through_the_api_s_own_address(tmp_path, fleet) -> None:
    root = status_root(tmp_path, fleet, FROM_THE_HOST)
    report, model = _status_run(tmp_path, fleet, *_proxmox_inventory(root), settings=FROM_THE_HOST)
    assert report["rc"] == 0, report["stderr"][-2000:]
    by = {vm["name"]: vm for vm in model["vms"]}
    for name in ("pve", "localhost"):
        assert by[name]["read"] is False and by[name]["skipped"] == "name"
        assert any(it["vm"] == name and it["level"] == "unread" for it in model["attention"])
    # Play 2 asks Proxmox about each VM at proxmox_api_url: had pve been merged, at the address pve reported.
    assert by["alpha"]["api"]["read"] and by["beta"]["api"]["read"]


def test_a_fleet_wide_refresh_removes_an_alias_no_vm_may_have_and_leaves_a_dotted_one(tmp_path, fleet) -> None:
    # An earlier version wrote an alias for any tagged VM, so the managed file may hold one for the VM named pve - the
    # Proxmox host's root SSH - or for one named like an address. Tagged VMs still carry both names here.
    _pve_vm(fleet, 9015, "3232235777", tags="agent-warehouse", ciuser="dev")
    # A dotted name: the user's own, written by a run that named it. Running, so the refresh has its address.
    _pve_vm(fleet, 9016, "alpha.lab", running=True, tags="agent-warehouse", ciuser="dev")
    managed = tmp_path / "ssh.conf"
    managed.write_text(_block("pve") + _block("3232235777") + _block("alpha.lab"))
    report = _refresh(tmp_path, fleet)
    assert report["rc"] == 0, report["stderr"][-2000:]
    text = managed.read_text()
    assert re.findall(r"^# BEGIN agent-warehouse: (\S+)$", text, flags=re.M) == ["alpha.lab", "alpha", "beta"]
    # Left as it was: neither refreshed nor removed by a run that did not name it.
    assert _block("alpha.lab") in text
    said = _messages(report, "Report what was kept, pruned and left alone")["localhost"]
    # alpha and beta: alpha.lab had an address, and was left out.
    assert said.startswith("2 aliases refreshed in ")
    assert "which an earlier version wrote: pve, 3232235777" in said
    assert "just deploy alpha.lab --only ssh-config" in _task(report, "ssh_config | Say which VMs get no alias")["msg"]
    # The tagged VMs named pve and localhost get no alias, and the run says so, as discovery does.
    said = _messages(report, LEFT_OUT)["localhost"]
    assert "localhost" in said and "pve" in said


def test_a_fleet_wide_refresh_records_this_host_and_prunes_only_its_own_blocks(tmp_path, fleet) -> None:
    # The homelab's run (pve here) knows nothing of the NUC's VMs: before each block recorded its host, the NUC's
    # aliases were "gone" from this listing and pruned. The fake lists a tagged VM without a name (a node that is down),
    # which holds the prune: what it would remove is held, and what it never removes is not even that.
    managed = tmp_path / "ssh.conf"
    managed.write_text(_block("old", "pve") + _block("theirs", "nuc") + _block("lab-vm", "lab") + _block("unrecorded"))
    report = _refresh(tmp_path, fleet)
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert _recorded(managed) == {
        "old": "pve",
        "theirs": "nuc",
        "lab-vm": "lab",
        "unrecorded": "",
        "alpha": "pve",
        "beta": "pve",
    }
    # One host: a block with no record is this host's, held with its own; another host's is left, whoever has it.
    prune = _task(report, "Decide what to prune")["ansible_facts"]["claude_ssh_prune"]
    assert prune["held"] == ["old", "unrecorded"] and prune["unknown"] == []
    assert prune["elsewhere"] == [{"name": "theirs", "origin": "nuc"}, {"name": "lab-vm", "origin": "lab"}]
    said = _messages(report, "Report what was kept, pruned and left alone")["localhost"]
    assert "Left alone, recorded for a host this checkout does not have: theirs (nuc), lab-vm (lab)." in said

    # The checkout's other host, as tools/check host says: its blocks are its own, unsaid, and one with no record is
    # anybody's now - left, and said.
    managed.write_text(_block("theirs", "nuc") + _block("unrecorded"))
    report = _refresh(tmp_path, fleet, "-e", NUC)
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert _recorded(managed) == {"theirs": "nuc", "unrecorded": "", "alpha": "pve", "beta": "pve"}
    prune = _task(report, "Decide what to prune")["ansible_facts"]["claude_ssh_prune"]
    assert prune["unknown"] == ["unrecorded"] and prune["held"] == [] and prune["elsewhere"] == []
    said = _messages(report, "Report what was kept, pruned and left alone")["localhost"]
    assert "Left alone, no host recorded: unrecorded." in said and "theirs" not in said


def test_a_name_another_hosts_vm_has_is_refused_before_proxmox_is_asked(tmp_path, fleet) -> None:
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    (tmp_path / "ssh.conf").write_text(_block("delta", "nuc"))
    report = _creating(tmp_path, fleet, "provision.yml", "alpha,delta", "-e", NUC)
    assert report["rc"] != 0
    refused = _refused(report)
    assert len(refused) == 1, refused
    assert refused[0]["msg"].startswith("delta is a VM of nuc, as the SSH alias of the name records"), refused
    assert calls.read_text() == before
    # A host the checkout does not have is no one's to keep the name for: the checks deploy makes first pass it.
    (tmp_path / "ssh.conf").write_text(_block("delta", "lab"))
    root = status_root(tmp_path, fleet, FROM_THE_HOST)
    play = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    tasks = [{**task, "ansible.builtin.import_tasks": f"{PLAYBOOKS / 'tasks'}/vm_names.yml"} for task in play["tasks"]]
    (root / "first.yml").write_text(yaml.safe_dump([{**play, "tasks": tasks}]))
    extra = json.dumps({"vm_name": "delta"})
    report = _run(root, root / "first.yml", {}, *_proxmox_inventory(root), "-e", extra, "-e", NUC, timeout=120)
    assert report["rc"] == 0, report["stderr"][-2000:]


def test_destroy_keeps_the_alias_and_the_name_key_of_another_hosts_vm(tmp_path, fleet) -> None:
    # destroy.yml's last play, from its start to the line that names what stayed: alpha was this host's, and beta's
    # alias records the NUC, whose VM of that name it is.
    root = status_root(
        tmp_path,
        fleet,
        {
            **FROM_THE_HOST,
            "claude_ssh_user_config": str(tmp_path / "config"),
            "claude_ssh_config_include": False,
            "claude_ssh_host_domain": "lan",
        },
    )
    managed = tmp_path / "ssh.conf"
    # gamma's records a host the checkout no longer has - the first host before it was renamed - which is no one's to
    # keep, as the alias write takes it over.
    managed.write_text(_block("alpha", "pve") + _block("beta", "nuc") + _block("gamma", "old-name"))
    plays = yaml.safe_load((PLAYBOOKS / "destroy.yml").read_text())
    forget = next(play for play in plays if play.get("name") == "Forget the SSH alias of each deleted VM")
    names = [task.get("name") for task in forget["tasks"]]
    forget["tasks"] = forget["tasks"][: names.index("Say which aliases stay with another host") + 1]
    forget = json.loads(json.dumps(forget).replace('"tasks/', f'"{PLAYBOOKS / "tasks"}/'))
    (root / "forget.yml").write_text(yaml.safe_dump([forget]))
    doomed = root / "doomed.yml"
    doomed.write_text(
        yaml.safe_dump(
            {
                "claude_vm_doomed": {
                    "hosts": {name: {"claude_vm_deleted": True} for name in ("alpha", "beta", "gamma")},
                }
            }
        )
    )
    others = json.dumps({"claude_hosts_other": [{"name": "nuc", "address": "192.0.2.20"}]})
    report = _run(
        root, root / "forget.yml", {}, *_proxmox_inventory(root), "-i", str(doomed), "-e", others, timeout=120
    )
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert _recorded(managed) == {"beta": "nuc"}
    said = _messages(report, "Say which aliases stay with another host")["localhost"]
    assert said.startswith("Kept the SSH alias of beta (it records nuc): the alias of a name is the VM of the host")

    # And the delete play forgets <name>.<domain> only for this host's: the role's input, as that play sets it.
    delete = next(play for play in plays if play.get("name") == "Delete the VMs on Proxmox")
    domain = delete["vars"]["proxmox_vm_known_hosts_domain"]
    assert "inventory_hostname in claude_ssh_names_elsewhere" in domain and "claude_ssh_host_domain" in domain


@pytest.mark.parametrize("playbook", ["deploy.yml", "provision.yml"])
@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("pve", "pve is the name of a host in the inventory"),
        # The address the inventory reaches the Proxmox host at is one more address, refused as this one is: the
        # inventory's ansible_host is reached only through a host name, the next test's.
        ("3232235777", "ssh reads it as an address"),
    ],
)
def test_a_name_no_vm_may_have_is_refused_before_proxmox_is_asked(tmp_path, fleet, playbook, name, said) -> None:
    # deploy.yml before its template play, which may build a template over root SSH; provision.yml before a clone.
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    report = _creating(tmp_path, fleet, playbook, f"alpha,{name}")
    assert report["rc"] != 0
    refused = _refused(report)
    assert len(refused) == 1 and said in refused[0]["msg"], refused
    assert calls.read_text() == before


def test_a_name_this_machine_reaches_another_host_by_is_refused_before_proxmox_is_asked(tmp_path, fleet) -> None:
    # claude_vm_inventory_names holds each inventory host's ansible_host too: an alias of that name, included ahead of
    # the user's own ssh config, would take this machine's connections to that host to the VM. The Proxmox host is
    # reached at an address here, which the address check refuses first; only a host reached by a name tests this half.
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    nas = tmp_path / "nas.yml"
    nas.write_text(yaml.safe_dump({"all": {"hosts": {"nas": {"ansible_host": "nas.example"}}}}))
    report = _creating(tmp_path, fleet, "provision.yml", "alpha,nas.example", "-i", str(nas))
    assert report["rc"] != 0
    refused = _refused(report)
    assert len(refused) == 1, refused
    assert "nas.example" in refused[0]["msg"] and "it names a host of the inventory" in refused[0]["msg"], refused
    assert calls.read_text() == before


def test_a_name_with_a_dot_passes_the_checks_deploy_makes_first(tmp_path, fleet) -> None:
    root = status_root(tmp_path, fleet, FROM_THE_HOST)
    play = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    tasks = [{**task, "ansible.builtin.import_tasks": f"{PLAYBOOKS / 'tasks'}/vm_names.yml"} for task in play["tasks"]]
    (root / "first.yml").write_text(yaml.safe_dump([{**play, "tasks": tasks}]))
    extra = json.dumps({"vm_name": "alpha.lab,beta"})
    report = _run(root, root / "first.yml", {}, *_proxmox_inventory(root), "-e", extra, timeout=120)
    assert report["rc"] == 0, report["stderr"][-2000:]


@pytest.mark.parametrize("playbook", ["provision.yml", "destroy.yml", "code.yml", "discover.yml", "ssh_config.yml"])
def test_every_playbook_that_takes_names_refuses_a_host_of_the_inventory(playbook: str) -> None:
    # Itself, or through the opening discovery and the alias refresh share, which does first.
    text = (PLAYBOOKS / playbook).read_text()
    opening = yaml.safe_load((PLAYBOOKS / "tasks" / "vms_requested.yml").read_text())
    assert opening[0]["ansible.builtin.import_tasks"] == "vm_names.yml"
    assert "import_tasks: tasks/vm_names.yml" in text or "import_tasks: tasks/vms_requested.yml" in text


def test_the_vm_just_code_opens_counts_as_named_even_in_a_bare_deploy() -> None:
    # A bare deploy has no vm_name, and its code.yml runs discovery: group_vars would choose no name, and a dotted
    # default VM would lose its alias in the window step.
    def imports(tasks):
        for task in tasks:
            yield task
            yield from imports(task.get("block", []) + task.get("rescue", []))

    plays = yaml.safe_load((PLAYBOOKS / "code.yml").read_text())
    alias = [
        task
        for play in plays
        for task in imports(play.get("tasks", []))
        if task.get("ansible.builtin.import_tasks") == "tasks/ssh_config.yml"
    ]
    assert [task["vars"]["claude_vm_names_chosen"] for task in alias] == ["{{ claude_vm_names }}"]


def test_deploy_refuses_a_name_in_its_first_play() -> None:
    # Before the template play, whose build needs root SSH: tested against the fake above.
    first = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    assert [task["ansible.builtin.import_tasks"] for task in first["tasks"]] == ["playbooks/tasks/vm_names.yml"]
    assert first["tasks"][0]["vars"] == {"claude_vm_names_alias": True}


# ------------------------------------------------------------------- the default VM's name through 1.x ----

DEFAULT_1X = "claude-on-proxmox-default"
DEFAULT_REFUSAL = "default_name_1x | Refuse a bare run that would miss the default VM by its 1.x name"


def _default_check(tmp_path: Path, base: str, **given) -> dict:
    """The cluster's listing, then tasks/default_name_1x.yml, on localhost against the fake, with given as -e."""
    root = status_root(tmp_path, base, FROM_THE_HOST)
    tasks = PLAYBOOKS / "tasks"
    play = {
        "name": "Check the default VM's name",
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [
            {"name": "List the cluster", "ansible.builtin.import_tasks": str(tasks / "cluster_vms.yml")},
            {
                "name": "Check it",
                "ansible.builtin.import_tasks": str(tasks / "default_name_1x.yml"),
                "vars": {"claude_vm_default_doing": "it would do something else"},
            },
        ],
    }
    (root / "check.yml").write_text(yaml.safe_dump([play]))
    extra = ["-e", json.dumps(given)] if given else []
    return _run(root, root / "check.yml", {}, *_proxmox_inventory(root), *extra, timeout=120)


@pytest.mark.parametrize(
    ("vms", "given", "refused"),
    [
        # A VM of the project's by the 1.x default's name alone: refused, naming the setting that keeps it.
        ([DEFAULT_1X], {}, True),
        # The setting kept at the 1.x name, names given, or the new default there too: each passes.
        ([DEFAULT_1X], {"vm_default_name": DEFAULT_1X}, False),
        ([DEFAULT_1X], {"vm_name": "alpha"}, False),
        ([DEFAULT_1X, "agent-warehouse-default"], {}, False),
        ([], {}, False),
    ],
    ids=["1x-only", "kept-by-setting", "names-given", "both", "neither"],
)
def test_a_bare_run_is_refused_only_while_the_default_vm_has_its_1x_name_alone(tmp_path, fleet, vms, given, refused):
    for vmid, name in enumerate(vms, start=9021):
        _pve_vm(fleet, vmid, name, running=True, tags="agent-warehouse", ciuser="dev", onboot="1")
    report = _default_check(tmp_path, fleet, **given)
    result = _task(report, DEFAULT_REFUSAL)
    assert bool(result.get("failed")) is refused, report["stderr"][-2000:]
    if refused:
        assert f"just settings vm_default_name --value {DEFAULT_1X}" in result["msg"], result["msg"]


def test_a_bare_provision_with_the_default_vm_by_its_1x_name_creates_nothing(tmp_path, fleet) -> None:
    _pve_vm(fleet, 9021, DEFAULT_1X, running=True, tags="claude-on-proxmox", ciuser="dev", onboot="1")
    calls = tmp_path / "pve" / "calls.log"
    before = len(calls.read_text().splitlines()) if calls.exists() else 0
    report = _playbook(tmp_path, fleet, PLAYBOOKS / "provision.yml", "-e", json.dumps({"deploy_sign_in": False}))
    assert report["rc"] != 0 and _task(report, DEFAULT_REFUSAL).get("failed"), report["stderr"][-2000:]
    sent = [json.loads(line)["method"] for line in calls.read_text().splitlines()[before:]]
    assert set(sent) <= {"GET"}, sent


# ------------------------------------------------------ the tag through 1.x ---
MOVE = "present | Move the VM's tags to their names from 2.0"
SETTINGS = "present | Apply hardware and cloud-init settings"


def _listed(base: str) -> dict:
    return {vm["vmid"]: vm.get("tags", "") for vm in _pve(base, "GET", "cluster/resources")["data"]}


def _ran(report: dict, name: str, vm: str) -> dict:
    """What the role's task of that name did for that VM."""
    return next((result for task, host, result in _results(report) if task.endswith(name) and host == vm), {})


def _provisioning(tmp_path: Path, base: str, name: str) -> dict:
    """provision.yml for that VM against the fake, with a key for its cloud-init user, as a deploy has one."""
    settings = {**FROM_THE_HOST, "vm_ssh_public_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test"]}
    extra = json.dumps({"vm_name": name, "deploy_sign_in": False})
    return _playbook(tmp_path, base, PLAYBOOKS / "provision.yml", "-e", extra, settings=settings)


def test_a_vm_1x_tagged_is_provisioned_as_the_project_s_and_its_tags_move_once(tmp_path, fleet) -> None:
    # Finished, running, its Tailscale recorded and a tag of the user's: owned, changed in nothing but its tags.
    _pve_vm(
        fleet,
        9031,
        "made-by-1x",
        running=True,
        tags="claude-on-proxmox;keepme;claude-on-proxmox-tailscale",
        ciuser="dev",
        onboot="1",
    )
    report = _provisioning(tmp_path, fleet, "made-by-1x")
    assert report["rc"] == 0, _failures(report)
    assert _ran(report, MOVE, "made-by-1x").get("changed") is True
    assert _ran(report, SETTINGS, "made-by-1x").get("skipped") is True
    assert _listed(fleet)[9031] == "agent-warehouse;agent-warehouse-tailscale;keepme"
    again = _provisioning(tmp_path, fleet, "made-by-1x")
    assert again["rc"] == 0, _failures(again)
    assert _ran(again, MOVE, "made-by-1x").get("skipped") is True


def test_a_vm_1x_left_unfinished_is_finished_under_the_new_tag(tmp_path, fleet) -> None:
    _pve_vm(fleet, 9032, "half-by-1x", tags="claude-on-proxmox;claude-on-proxmox-unfinished", ciuser="dev", onboot="1")
    report = _provisioning(tmp_path, fleet, "half-by-1x")
    assert report["rc"] == 0, _failures(report)
    # The settings call writes the tags itself, so the move is left to it.
    assert _ran(report, MOVE, "half-by-1x").get("skipped") is True
    assert _ran(report, SETTINGS, "half-by-1x").get("changed") is True
    assert _listed(fleet)[9032] == "agent-warehouse"


@pytest.mark.parametrize("tags", ["claude-on-proxmox-staging", "not-claude-on-proxmox", "claude-on-proxmox-tailscale"])
def test_a_vm_whose_1x_tag_is_a_lookalike_or_a_mark_alone_is_refused(tmp_path, fleet, tags) -> None:
    _pve_vm(fleet, 9033, "lookalike", running=True, tags=tags, ciuser="dev", onboot="1")
    report = _provisioning(tmp_path, fleet, "lookalike")
    assert report["rc"] != 0
    refusal = _ran(report, "Refuse to change a VM this project does not own", "lookalike")
    assert refusal.get("failed") and f"Its tags are: {tags}." in refusal["msg"], _failures(report)
    assert _listed(fleet)[9033] == tags
