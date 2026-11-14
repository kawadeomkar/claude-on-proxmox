"""The hosts core in-process (lib/claude_vm/hosts/): the records, choosing a run's host, resolving it, the vault check
as a decision, init as a plan; and what tools/host, its one adapter, keeps of today's contract - stderr's three moments
and the JSON's bytes. test_hosts.py holds the adapter's contract as a process; this holds the core behind it
(docs/architecture/custom-code.md §7.14). Made-up layouts in tmp_path, with fake secrets only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_hosts import ALWAYS, _checkout, _host, _refused, _resolve, _tool

from claude_vm import vault, wire
from claude_vm.hosts import choose, resolve, setup
from claude_vm.hosts.layout import Layout
from claude_vm.hosts.model import Choice, Host, HostList, HostRow, Note, Refused, Request, VaultCheck

REPO = Path(__file__).resolve().parents[2]
# A host whose hosts.yml gives it an address that is not one word: refused to every other host's run.
LAB_SPACED = "all:\n  children:\n    proxmox:\n      hosts:\n        lab:\n          ansible_host: 'a b'\n"
ROW = {"name": "", "address": "", "folder": "", "settings": {}, "first": False, "current": False, "problem": ""}


def _layout(root: Path) -> Layout:
    return Layout(root, root / "hosts")


def _aliases(root: Path, **origins: str) -> None:
    """The managed SSH config the test's checkout names, each VM's block recording its host."""
    blocks = "".join(
        f"# BEGIN agent-warehouse: {name}\n# origin: {origin}\nHost {name}\n# END agent-warehouse: {name}\n"
        for name, origin in origins.items()
    )
    (root / ".ssh").mkdir(exist_ok=True)
    (root / ".ssh" / "agent-warehouse.conf").write_text(blocks)


def _request(root: Path, **given) -> Request:
    return Request(vault_pass=str(root / ".vault_pass"), **given)


# ------------------------------------------------------------------------------------------------- the records ----


def test_a_request_takes_its_names_hyphenated_as_the_justfile_does() -> None:
    assert Request(names=("my_vm", "alpha")).names == ("my-vm", "alpha")


@pytest.mark.parametrize(
    ("record", "fields"),
    [
        (Note, {"level": "error", "text": "x"}),
        (Request, {"names": ["alpha"]}),
        (Host, {"name": "nuc", "folder": "hosts/nuc", "first": "no"}),
        (Choice, {"host": None, "many": True, "routed": False, "current": "", "origins": (("alpha",),)}),
        (HostRow, {**ROW, "host_kind": 1}),
    ],
)
def test_every_record_refuses_what_its_fields_cannot_hold(record, fields) -> None:
    with pytest.raises(ValueError):
        record(**fields)


# -------------------------------------------------------------------------------------------------- choosing ----


def test_the_host_given_then_the_vms_alias_then_hosts_current_then_the_first(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, beta="nuc", gamma="pve")
    layout = _layout(root)
    assert choose.choose(layout, _request(root)).host.name == "pve"
    (root / "hosts" / "current").write_text("nuc\n")
    assert choose.choose(layout, _request(root)).host.name == "nuc"
    routed = choose.choose(layout, _request(root, names=("gamma",)))
    assert (routed.host.name, routed.routed, routed.current) == ("pve", True, "nuc")
    given = choose.choose(layout, _request(root, host="pve", names=("beta",)))
    assert given.host.name == "pve" and not given.routed
    assert [note.level for note in given.notes] == ["warning"] and "beta is nuc's" in given.notes[0].text
    assert choose.choose(layout, _request(root, nested="pve")).host.name == "pve"
    with pytest.raises(Refused, match="one host per run"):
        choose.choose(layout, _request(root, host="nuc", nested="pve"))
    with pytest.raises(Refused, match="are on 2 hosts"):
        choose.choose(layout, _request(root, names=("beta", "gamma")))
    # A name no alias places, which a creating deploy asks the other hosts about.
    assert choose.choose(layout, _request(root, names=("beta", "newvm"))).unplaced == ("newvm",)


def test_one_host_and_nothing_asked_reads_nothing_and_chooses_none(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    choice = choose.choose(_layout(root), _request(root, names=("alpha",)))
    assert choice.host is None and not choice.many and choice.unplaced == ()


def test_a_refusal_after_a_note_carries_the_note(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    _aliases(root, beta="nuc")
    with pytest.raises(Refused) as refused:
        choose.choose(_layout(root), _request(root, host="lab", names=("beta",)))
    assert "which cannot be used" in str(refused.value)
    assert [note.level for note in refused.value.notes] == ["warning"]


def test_choosing_and_listing_never_open_a_vault_or_run_a_password_script(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, beta="nuc")

    def refuse(*_args, **_kwargs):
        raise AssertionError("a vault was read")

    for name in ("names", "values", "password", "open_text"):
        monkeypatch.setattr(vault, name, refuse)
    layout = _layout(root)
    assert choose.choose(layout, _request(root, names=("beta",))).host.name == "nuc"
    assert [row.name for row in choose.listing(layout).hosts] == ["pve", "nuc"]


# ------------------------------------------------------------------------------------------------- resolving ----


def test_resolve_reads_the_environment_it_is_given_never_its_own(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    layout = _layout(root)
    request = _request(root, host="nuc")
    choice = choose.choose(layout, request)
    monkeypatch.setenv("ANSIBLE_INVENTORY", "elsewhere")
    run = resolve.resolve(layout, choice, request, {}, {})
    assert run.sources == ("inventory/controller.yml", "hosts/nuc")
    with pytest.raises(Refused, match="ANSIBLE_INVENTORY is exported"):
        resolve.resolve(layout, choice, request, {"ANSIBLE_INVENTORY": "elsewhere"}, {})


def test_the_vault_check_decides_and_says_whether_tools_host_may_keep_it(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    layout = _layout(root)
    nuc = layout.named("nuc", "--host names")
    files = (str(root / ".vault_pass"),)
    passed = resolve.vault_check(layout, nuc, files, {})
    assert passed.refusal == "" and passed.keep and passed.key
    # Kept already, under the same files: not run again, and nothing more to keep.
    assert resolve.vault_check(layout, nuc, files, {"nuc": passed.key}) == VaultCheck(key=passed.key)
    # No password here: said, and never kept.
    unopened = resolve.vault_check(layout, nuc, (), {})
    assert unopened.unopened == ("inventory/group_vars/all/vault.yml", "hosts/nuc/group_vars/all/vault.yml")
    assert not unopened.keep
    # Nothing was written: tools/host keeps a check, the core never does.
    assert not (root / "hosts" / ".checked.json").exists()


def test_a_refusal_after_the_unopened_vault_carries_the_warning(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _host(root, "lab", hosts_yml=LAB_SPACED)
    layout = _layout(root)
    request = Request(host="nuc")  # no password file: the vaults are not opened here
    with pytest.raises(Refused, match="lab's ansible_host is no address") as refused:
        resolve.resolve(layout, choose.choose(layout, request), request, {}, {})
    assert [note.level for note in refused.value.notes] == ["warning"]
    assert "could not be opened here" in refused.value.notes[0].text


# ------------------------------------------------------------------------------------------------ setting up ----


def test_init_is_a_plan_and_writes_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    plan = setup.plan_init(
        _layout(root),
        "nuc",
        address="192.0.2.20",
        asking=False,
        shared=False,
        secret_file=False,
        vault_pass=str(root / ".vault_pass"),
        environ={},
        templates=REPO / "tools" / "templates" / "host",
    )
    assert [new.said for new in plan.files] == [
        "hosts/nuc/hosts.yml",
        "hosts/nuc/group_vars/all/local.yml",
        "hosts/nuc/group_vars/all/vault.yml",
    ]
    assert "\n        nuc:\n" in plan.files[0].text and plan.payload["init_host"]["default_vm"] == "nuc-default"
    assert not (root / "hosts" / "nuc").exists()
    with pytest.raises(Refused, match="AGENT_WAREHOUSE_TOKEN_SECRET is exported"):
        setup.plan_init(
            _layout(root),
            "nuc",
            address="192.0.2.20",
            asking=False,
            shared=False,
            secret_file=False,
            vault_pass=str(root / ".vault_pass"),
            environ={"AGENT_WAREHOUSE_TOKEN_SECRET": "fake"},
            templates=REPO / "tools" / "templates" / "host",
        )


def test_switching_and_removing_are_decided_and_written_by_tools_host(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    layout = _layout(root)
    assert setup.plan_switch(layout, "nuc").name == "nuc" and not (root / "hosts" / "current").exists()
    assert setup.plan_remove(layout, "nuc") == root / "hosts" / "nuc" and (root / "hosts" / "nuc").is_dir()
    switched = _tool(root, "switch", "nuc")
    assert switched.returncode == 0, switched.stderr
    assert (root / "hosts" / "current").read_text() == "nuc\n"
    assert ((root / "hosts" / "current").stat().st_mode & 0o777) == 0o600
    with pytest.raises(Refused, match="is the current host"):
        setup.plan_remove(layout, "nuc")


# ------------------------------------------------------------------------- what the adapter keeps of today's ----


def test_stderr_says_the_host_warning_before_a_later_refusal(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    _aliases(root, beta="nuc")
    said = _refused(root, "--host=lab", "--names=beta")
    assert said.index("warning: beta is nuc's") < said.index("error: --host names lab")


def test_stderr_says_the_unopened_vault_before_an_address_refusal(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _host(root, "lab", hosts_yml=LAB_SPACED)
    said = _tool(root, "env", "--host=nuc", "--vault-pass=")
    assert said.returncode == 1
    assert said.stderr.index("could not be opened here") < said.stderr.index("error: lab's ansible_host")


def test_the_routing_note_is_said_only_once_every_check_passed_and_last(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, beta="nuc")
    said = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", "--names=beta")
    assert said.returncode == 0, said.stderr
    assert said.stderr.splitlines()[-1].startswith("beta is nuc's, as its SSH alias records: this run is against nuc")


@pytest.mark.parametrize(
    ("current", "args"),
    [
        ("", ()),
        ("nuc", ()),
        ("", ("--host=nuc",)),
        ("", ("--names=beta",)),
        ("", ("--names=gamma,newvm",)),
        ("nuc", ("--names=gamma",)),
        ("", ("--nested=nuc",)),
    ],
)
def test_the_adapter_prints_the_host_the_core_chose(tmp_path: Path, current: str, args: tuple[str, ...]) -> None:
    # Both call choose: this catches the adapter wired wrong, never a rule that drifted.
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, beta="nuc", gamma="pve")
    if current:
        (root / "hosts" / "current").write_text(f"{current}\n")
    given = dict(arg.lstrip("-").split("=", 1) for arg in args)
    request = _request(
        root,
        host=given.get("host", ""),
        nested=given.get("nested", ""),
        names=tuple(filter(None, given.get("names", "").split(","))),
    )
    chosen = choose.choose(_layout(root), request).host.name
    assert _resolve(root, *args)["host"] == chosen
    # And the read a UI or the MCP server asks, just host --json with the names and --host a run would be given.
    if "nested" not in given:
        listed = _tool(root, "list", "--json", *args)
        assert listed.returncode == 0, listed.stderr
        where = json.loads(listed.stdout)["where"]
        assert where is None if not args else where["host"] == chosen


def test_nothing_of_a_host_s_local_yml_leaves_but_what_just_host_shows(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc", local={**ALWAYS, "vm_ipconfig": "PRIVATE-NUC-VALUE"})
    for args in (("list", "--json"), ("every", f"--vault-pass={root / '.vault_pass'}")):
        said = _tool(root, *args)
        assert said.returncode == 0, said.stderr
        assert "PRIVATE-NUC-VALUE" not in said.stdout and "vm_ipconfig" not in said.stdout


# The JSON just host --json printed before the core made it (bfec029e, written by hand), byte for byte: the core's
# HostList, through wire.to_wire, prints the same, and only adds the keys it gained since.
def _two_current(root: Path) -> None:
    _checkout(root, local={"proxmox_node": "pve1"})
    _host(root, "nuc")
    (root / "hosts" / "current").write_text("nuc\n")


def _not_set_up(root: Path) -> None:
    _checkout(root)
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()


def _no_first(root: Path) -> None:
    _checkout(root, first=False)
    _host(root, "nuc")


def _current_gone(root: Path) -> None:
    _checkout(root)
    _host(root, "nuc")
    (root / "hosts" / "current").write_text("gone\n")


LISTINGS = {
    "one-host": _checkout,
    "two-hosts-nuc-current": _two_current,
    "a-host-not-set-up": _not_set_up,
    "no-first-host": _no_first,
    "current-names-no-host": _current_gone,
}
RECORDED = {
    "one-host": {
        "format_version": "1.0",
        "kind": "host_list",
        "current_host": "pve",
        "hosts": [
            {
                "name": "pve",
                "address": "192.0.2.10",
                "folder": "inventory",
                "settings": {"proxmox_node": "pve"},
                "host_kind": "proxmox",
                "first": True,
                "current": True,
                "problem": "",
            }
        ],
        "problem": "",
    },
    "two-hosts-nuc-current": {
        "format_version": "1.0",
        "kind": "host_list",
        "current_host": "nuc",
        "hosts": [
            {
                "name": "pve",
                "address": "192.0.2.10",
                "folder": "inventory",
                "settings": {"proxmox_node": "pve1"},
                "host_kind": "proxmox",
                "first": True,
                "current": False,
                "problem": "",
            },
            {
                "name": "nuc",
                "address": "192.0.2.20",
                "folder": "hosts/nuc",
                "settings": {"proxmox_node": "nuc"},
                "host_kind": "proxmox",
                "first": False,
                "current": True,
                "problem": "",
            },
        ],
        "problem": "",
    },
    "a-host-not-set-up": {
        "format_version": "1.0",
        "kind": "host_list",
        "current_host": "pve",
        "hosts": [
            {
                "name": "pve",
                "address": "192.0.2.10",
                "folder": "inventory",
                "settings": {"proxmox_node": "pve"},
                "host_kind": "proxmox",
                "first": True,
                "current": True,
                "problem": "",
            },
            {
                "name": "lab",
                "address": "",
                "folder": "hosts/lab",
                "settings": {},
                "host_kind": None,
                "first": False,
                "current": False,
                "problem": "not set up: just init --host lab",
            },
            {
                "name": "nuc",
                "address": "192.0.2.20",
                "folder": "hosts/nuc",
                "settings": {"proxmox_node": "nuc"},
                "host_kind": "proxmox",
                "first": False,
                "current": False,
                "problem": "",
            },
        ],
        "problem": "",
    },
    "no-first-host": {
        "format_version": "1.0",
        "kind": "host_list",
        "current_host": None,
        "hosts": [
            {
                "name": "nuc",
                "address": "192.0.2.20",
                "folder": "hosts/nuc",
                "settings": {"proxmox_node": "nuc"},
                "host_kind": "proxmox",
                "first": False,
                "current": False,
                "problem": "",
            }
        ],
        "problem": "inventory/hosts.yml names no host, and hosts/current none of nuc: choose one with just host <name>",
    },
    "current-names-no-host": {
        "format_version": "1.0",
        "kind": "host_list",
        "current_host": None,
        "hosts": [
            {
                "name": "pve",
                "address": "192.0.2.10",
                "folder": "inventory",
                "settings": {"proxmox_node": "pve"},
                "host_kind": "proxmox",
                "first": True,
                "current": False,
                "problem": "",
            },
            {
                "name": "nuc",
                "address": "192.0.2.20",
                "folder": "hosts/nuc",
                "settings": {"proxmox_node": "nuc"},
                "host_kind": "proxmox",
                "first": False,
                "current": False,
                "problem": "",
            },
        ],
        "problem": "hosts/current names gone, which is no host of this checkout's (pve, nuc): just init --host gone "
        "--address <address> sets one up",
    },
}


@pytest.mark.parametrize("name", list(LISTINGS))
def test_just_host_json_is_the_bytes_it_was_before_the_core(tmp_path: Path, name: str) -> None:
    root = tmp_path / "checkout"
    LISTINGS[name](root)
    said = _tool(root, "list", "--json")
    assert said.returncode == 0, said.stderr
    # Keys only added since: where, at the end, null unless a run was asked about.
    assert said.stdout == json.dumps({**RECORDED[name], "where": None}) + "\n"


def test_where_is_a_refusal_as_data_with_the_notes_said_before_it(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    _aliases(root, beta="nuc")
    where = choose.where(_layout(root), _request(root, host="lab", names=("beta",)))
    assert where.host == "" and "which cannot be used" in where.refusal
    assert len(where.notes) == 1 and where.notes[0].startswith("beta is nuc's")
    assert choose.where(_layout(root), _request(root, names=("beta",))).host == "nuc"


def test_to_wire_names_the_list_host_list_and_refuses_a_field_named_kind() -> None:
    assert wire.to_wire(HostList(current_host=None, hosts=(), problem=""))["kind"] == "host_list"

    @dataclass(frozen=True)
    class Clash:
        kind: str

    with pytest.raises(TypeError, match="kind"):
        wire.to_wire(Clash(kind="proxmox"))

    # A property is sent as a key too, after the envelope, so one named kind would replace the document's unseen.
    @dataclass(frozen=True)
    class Derived:
        name: str

        @property
        def kind(self) -> str:
            return "proxmox"

    with pytest.raises(TypeError, match="property named kind"):
        wire.to_wire(Derived(name="pve"))


def test_every_says_each_host_s_row_as_just_status_reads_it(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    said = _tool(root, "every", f"--vault-pass={root / '.vault_pass'}")
    assert said.returncode == 0, said.stderr
    rows = json.loads(said.stdout)
    assert [list(row) for row in rows] == [["name", "current", "sources", "vault_files", "extra", "problem"]] * 2
    assert rows[1]["extra"] == {"claude_hosts_other": [{"name": "pve", "address": "192.0.2.10"}]}
