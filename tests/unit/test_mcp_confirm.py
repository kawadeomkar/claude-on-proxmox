"""What the person confirms before an agent deletes a VM or changes a setting (lib/claude_vm/mcp/confirm.py).

destroy, settings_set and init carry requiresUserInteraction: Claude Code's own prompt, which no hook of the calling
project's answers. destroy and settings_set then ask in a dialog that shows what is at stake and wants a code that
changes with what is asked: a decline, a cancel, a generic hook's answer, other words - nothing runs. Each runs only
for Claude Code 2.1.309 or later.

As test_mcp_jobs.py, the server runs a copy of this checkout through the real just with stand-ins: ansible-playbook
records what it was given; python is the real one, but for tools/status, which prints a report with the VMs a dialog
shows, and tools/settings, whose run it records and answers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_mcp_server import SERVER, _args, _copy, _env, _just, _text

from claude_vm.mcp import confirm

REPO = Path(__file__).resolve().parents[2]
REPORT = {
    "exit": 0,
    "vms": [
        {
            "name": "alpha",
            "vmid": 105,
            "node": "pve",
            "status": "running",
            "vcpus": 4,
            "maxmem": 8 * 1024**3,
            "maxdisk": 32 * 1024**3,
            "work": {"repositories": [{"name": "demo", "commits_on_no_remote": 2, "branches_on_no_remote": 1}]},
        }
    ],
}
SETTING = {"target": "vm_cores", "settings": [{"name": "vm_cores", "value": 2}], "notes": []}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("confirm")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    runs = root / "runs"
    runs.mkdir()
    # The real Python, but for tools/settings, whose run is recorded and answered with SETTING, and for just status's
    # engine, which prints REPORT alone.
    (bin_dir / "python").write_text(
        "#!/bin/sh\n"
        'case "${1:-}" in tools/settings)\n'
        f'  printf "%s\\n" "$@" > "{runs}/settings.$$"\n'
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
    return {
        "root": root,
        "runs": runs,
        "checkout": _copy(root),
        "bin": bin_dir,
        "just": _just(root, bin_dir),
        "home": home,
        "path": f"{Path(shutil.which('just')).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }


def _ran(world: dict, kind: str) -> list[list[str]]:
    paths = sorted(world["runs"].glob(f"{kind}.*"), key=lambda path: path.stat().st_mtime)
    return [path.read_text().splitlines() for path in paths]


def _clear(world: dict) -> None:
    for path in world["runs"].iterdir():
        path.unlink()


async def _session(world, steps, answer, client_name="claude-code", client_version="2.1.319"):
    """One client, its dialogs answered by answer(message) -> (action, content); what each dialog said, and each
    call's result."""
    from mcp import Client, StdioServerParameters
    from mcp_types import ElicitResult, Implementation

    asked: list[str] = []

    async def elicit(context, params):
        asked.append(params.message)
        action, content = answer(params.message)
        return ElicitResult(action=action, content=content)

    params = StdioServerParameters(command=sys.executable, args=["-I", str(SERVER), *_args(world)], env=_env(world))
    info = Implementation(name=client_name, version=client_version)
    callback = elicit if answer is not None else None
    async with Client(params, client_info=info, elicitation_callback=callback) as client:
        listed = await client.list_tools()
        results = [await client.call_tool(name, arguments) for name, arguments in steps]
    return {tool.name: tool for tool in listed.tools}, asked, results


def _talk(world, steps, answer=None, **client):
    return asyncio.run(asyncio.wait_for(_session(world, steps, answer, **client), 180))


def _typed(message: str) -> str:
    return re.search(r"type: (.+)$", message, re.MULTILINE).group(1)


def test_the_confirmed_tools_carry_claude_code_s_prompt_and_destroy_says_it_deletes(world):
    tools, _, _ = _talk(world, [])
    for name in ("destroy", "settings_set", "init", "install"):
        assert tools[name].meta["anthropic/requiresUserInteraction"] is True, name
    assert tools["destroy"].annotations.destructive_hint is True
    assert tools["destroy"].input_schema["required"] == ["names"]
    assert "confirm" not in tools["destroy"].input_schema["properties"]
    assert "token_secret_file" not in tools["init"].input_schema["properties"]


def test_destroy_runs_with_confirm_only_once_the_person_typed_the_code_and_the_names(world):
    _clear(world)
    _, asked, (result,) = _talk(
        world, [("destroy", {"names": ["alpha"]})], lambda m: ("accept", {"confirm": _typed(m)})
    )
    message = asked[0]
    assert message.startswith("This deletes alpha and their disks")
    assert "- alpha: VM 105 on pve, running, 4 cores, 8 GiB memory, 32 GiB disk" in message
    assert "    demo: 2 commits on 1 branch, on this VM alone" in message
    # What to type is among the first lines, which Claude Code shows unfolded.
    assert message.splitlines()[1].startswith("To delete, type: ")
    assert not result.is_error, _text(result)
    destroy = _ran(world, "playbook")[-1]
    assert destroy[-1] == "playbooks/destroy.yml"
    assert '{"confirm_destroy": "yes"}' in destroy and '{"vm_name": "alpha"}' in destroy


@pytest.mark.parametrize(
    "answer",
    [
        lambda m: ("decline", None),
        lambda m: ("cancel", None),
        # A generic hook that accepts every dialog with the names, as one could be written: no code.
        lambda m: ("accept", {"confirm": "alpha"}),
        lambda m: ("accept", {"confirm": _typed(m).replace("alpha", "beta")}),
        lambda m: ("accept", {"confirm": "ZZZZ alpha"}),
    ],
)
def test_nothing_is_deleted_without_exactly_what_the_dialog_asked(world, answer):
    _clear(world)
    _, asked, (result,) = _talk(world, [("destroy", {"names": ["alpha"]})], answer)
    assert asked and result.is_error and "Nothing was deleted" in _text(result)
    assert "just destroy alpha" in _text(result)
    assert not [run for run in _ran(world, "playbook") if run[-1] == "playbooks/destroy.yml"]


def test_no_other_client_and_no_client_without_the_dialog_is_asked_anything(world):
    _clear(world)
    accept = lambda m: ("accept", {"confirm": _typed(m)})  # noqa: E731
    for client in ({"client_name": "claude-ai", "client_version": "1.0"}, {"client_version": "2.1.308"}):
        _, asked, (result,) = _talk(world, [("destroy", {"names": ["alpha"]})], accept, **client)
        assert result.is_error and asked == [] and "Ask the user to run it at a terminal" in _text(result)
    # Claude Code, but with no dialog to show: the server answers that the dialog is missing (the SDK's client raises
    # it), and nothing is deleted.
    try:
        _, _, (result,) = _talk(world, [("destroy", {"names": ["alpha"]})], None)
    except Exception:  # noqa: BLE001
        result = None
    assert result is None or result.is_error
    assert not [run for run in _ran(world, "playbook") if run[-1] == "playbooks/destroy.yml"]


def test_a_held_lock_is_said_before_anything_is_asked(world):
    holder = subprocess.Popen(["sleep", "60"])
    lock = world["checkout"] / ".cache" / "run.lock"
    try:
        # Taken as a terminal's run takes it, by tools/check in another zone than the server's: on macOS ps pads the
        # start it writes, which the server once read stripped, and never matched.
        lock.parent.mkdir(parents=True, exist_ok=True)
        check = [str(world["checkout"] / "tools" / "check"), "lock", str(lock), str(holder.pid), "deploy beta"]
        subprocess.run(check, env={**os.environ, "TZ": "XYZ-5:30"}, check=True)
        _, asked, (result,) = _talk(world, [("destroy", {"names": ["alpha"]})], lambda m: ("accept", {}))
    finally:
        holder.kill()
        holder.wait()
        lock.unlink()
    assert asked == [] and result.is_error and "just deploy beta is running" in _text(result)


def test_settings_set_changes_an_allowed_setting_once_confirmed_and_never_another(world):
    _clear(world)
    accept = lambda m: ("accept", {"confirm": _typed(m)})  # noqa: E731
    _, asked, (result,) = _talk(world, [("settings_set", {"name": "vm_cores", "value": "4"})], accept)
    assert asked[0].startswith("This changes vm_cores in local.yml, for every run from now on: now 2, new 4.")
    assert not result.is_error, _text(result)
    written = _ran(world, "settings")[-1]
    assert "--value=4" in written and written[-1] == "vm_cores"
    _clear(world)
    for name in ("github_projects_api_url", "claude_code_installer_url", "claude_ssh_options", "vm_ssh_public_keys"):
        _, asked, (result,) = _talk(world, [("settings_set", {"name": name, "value": "x"})], accept)
        assert result.is_error and asked == [] and f"just settings {name} --value=" in _text(result)
    assert _ran(world, "settings") == []


def test_claude_code_s_version_is_a_version_and_never_another_package(world):
    _clear(world)
    accept = lambda m: ("accept", {"confirm": _typed(m)})  # noqa: E731
    for value in ("npm:other-package", "github:someone/pkg", "https://example.com/pkg.tgz", "2.1.319 || x"):
        _, asked, (result,) = _talk(world, [("settings_set", {"name": "claude_code_version", "value": value})], accept)
        assert result.is_error and asked == [] and "a package npm installs as root" in _text(result)
        assert value not in _text(result)
    assert _ran(world, "settings") == []
    for value in ("stable", "latest", "2.1.319"):
        assert confirm.value_refusal("claude_code_version", value) == "", value
    assert confirm.value_refusal("vm_cores", "4") == "" and confirm.value_refusal("claude_code_version", "") == ""


def test_init_is_claude_code_s_alone_and_never_takes_a_secret(world):
    _, _, (result,) = _talk(world, [("init", {"host": "192.0.2.10"})], client_name="claude-ai", client_version="1.0")
    assert result.is_error and "just init" in _text(result)


def test_every_setting_of_the_example_is_allowed_or_refused_and_never_both():
    example = (REPO / "inventory" / "group_vars" / "all" / "local.yml.example").read_text()
    names = {m.group(1) for m in re.finditer(r"(?m)^#? ?([a-z][a-z0-9_]*):", example)}
    assert set(confirm.ALLOWED) & confirm.REFUSED == set()
    assert names == set(confirm.ALLOWED) | confirm.REFUSED, "classify each new setting in lib/claude_vm/mcp/confirm.py"
    # Where a credential goes, or what runs on every VM: never an agent's to change, in an example or not.
    for name in ("github_projects_api_url", "tailscale_api_url", "claude_code_installer_url"):
        assert name not in confirm.ALLOWED
    specs = {}
    for path in (REPO / "roles").glob("*/meta/argument_specs.yml"):
        for entry in (yaml.safe_load(path.read_text()) or {}).get("argument_specs", {}).values():
            specs |= entry.get("options") or {}
    assert set(confirm.ALLOWED) <= names | set(specs)


def test_the_code_changes_with_what_is_asked_and_reads_unambiguously():
    first, second = confirm.code("destroy", "alpha"), confirm.code("destroy", "beta")
    assert first != second and first == confirm.code("destroy", "alpha")
    assert len(first) == 4 and not set(first) & set("01IOL")


def test_with_more_than_one_host_the_deletion_names_its_host_and_its_code_holds_it():
    said = confirm.destroy_message(["alpha"], {"vms": []}, "WXYZ alpha", "nuc")
    assert said.startswith("This deletes alpha and their disks on the Proxmox host nuc. It cannot be undone.")
    # An answer for one host's alpha is none for another's.
    assert confirm.code("destroy", "nuc", "alpha") != confirm.code("destroy", "pve", "alpha")
    assert confirm.destroy_message(["alpha"], {"vms": []}, "WXYZ alpha").startswith(
        "This deletes alpha and their disks on Proxmox."
    )
