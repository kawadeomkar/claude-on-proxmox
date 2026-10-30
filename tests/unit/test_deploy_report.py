"""What `just deploy` says before it starts, rendered by deploy.yml's own tasks.

The first play looks the template up through the API; here a canned lookup
stands in for it, set as a fact, which ranks with the register it replaces.
The tasks that follow run as deploy.yml has them.

What this guards: the opening message was built in a task var named like the
lookup's register, and a register outranks a task var, so the run opened by
printing the API's raw answer instead of the sentence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import ansible_config

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
LOOKUP = "Look up the template by name"
REPORT = "Report what this run will do"
WAITS = "opens a page in your browser, and the run waits for you there."


# The template as roles/proxmox_template builds it with its defaults, as Proxmox gives its config back.
IMAGE = "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
BUILT = {
    "name": "ubuntu-24.04-cloudinit",
    "agent": "enabled=1",
    "cpu": "host",
    "ostype": "l26",
    "net0": "virtio=BC:24:11:40:CD:BD,bridge=vmbr0",
    "scsi0": "local-lvm:base-9000-disk-0,discard=on,size=3584M",
    "scsihw": "virtio-scsi-pci",
    "serial0": "socket",
    "vga": "serial0",
    "template": 1,
    "description": f"Built by claude-on-proxmox: build=1; image={IMAGE}; packages=qemu-guest-agent; sha256=ab12",
}


def _said(tmp_path: Path, built: bool, config: dict | None = None, **extra) -> str:
    first = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    tasks = first["tasks"]
    assert [t["name"] for t in tasks][0] == LOOKUP
    lookup = tasks[0]["register"]
    template = {"name": "ubuntu-24.04-cloudinit", "node": "pve", "template": 1, "config": config or BUILT}
    canned = {"proxmox_vms": [template] if built else []}
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [{"name": "Stand in for the lookup", "ansible.builtin.set_fact": {lookup: canned}}, *tasks[1:]],
    }
    playbook = tmp_path / "deploy_report.yml"
    playbook.write_text(yaml.safe_dump([play], sort_keys=False))
    variables = {
        "proxmox_template_name": "ubuntu-24.04-cloudinit",
        "proxmox_node": "pve",
        "claude_vm_names": ["alpha"],
        "deploy_sign_in": True,
        "deploy_code": True,
        **extra,
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    # The template role's comparison, which the play includes, and its filter.
    env.update(
        ANSIBLE_CONFIG=ansible_config(tmp_path),
        ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
        ANSIBLE_ROLES_PATH=str(REPO / "roles"),
        ANSIBLE_FILTER_PLUGINS=str(REPO / "filter_plugins"),
    )
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-c", "local", "-e", json.dumps(variables), str(playbook)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    said = [
        host["msg"]
        for play in report["plays"]
        for task in play["tasks"]
        if task["task"]["name"] == REPORT
        for host in task["hosts"].values()
    ]
    assert len(said) == 1
    assert isinstance(said[0], str), said[0]
    return said[0]


def test_a_built_template_is_said_in_words(tmp_path):
    said = _said(tmp_path, built=True)
    assert said.startswith('Template "ubuntu-24.04-cloudinit" is already built, so this run needs no SSH'), said
    assert "proxmox_vms" not in said


def test_a_missing_template_is_said_to_be_built_first(tmp_path):
    said = _said(tmp_path, built=False)
    assert said.startswith('Template "ubuntu-24.04-cloudinit" does not exist yet; building it first'), said


def test_a_template_that_differs_from_its_settings_is_said_to_be_rebuilt_with_each_difference(tmp_path):
    config = {**BUILT, "net0": "virtio=BC:24:11:40:CD:BD,bridge=vmbr1", "cpu": "x86-64-v2-AES"}
    said = _said(tmp_path, built=True, config=config)
    assert said.startswith(
        'Template "ubuntu-24.04-cloudinit" differs from its settings (cpu x86-64-v2-AES → host; bridge vmbr1 → vmbr0),'
        " so this run rebuilds it first, over SSH as root@"
    ), said


def test_a_template_an_earlier_version_built_is_said_to_be_rebuilt(tmp_path):
    said = _said(tmp_path, built=True, config={k: v for k, v in BUILT.items() if k != "description"})
    assert said.startswith(
        'Template "ubuntu-24.04-cloudinit" differs from its settings (no record of how it was built, so it was built'
        " by an earlier version of this project), so this run rebuilds it first"
    ), said


def test_a_template_kept_as_it_is_is_said_to_differ_and_needs_no_ssh(tmp_path):
    config = {**BUILT, "vga": "std"}
    said = _said(tmp_path, built=True, config=config, proxmox_template_rebuild=False)
    assert said.startswith(
        'Template "ubuntu-24.04-cloudinit" differs from its settings (vga std → serial0), and is kept as it is since'
        " proxmox_template_rebuild is false, so this run needs no SSH to the Proxmox host."
    ), said


@pytest.mark.parametrize("sign_in", [True, False])
def test_the_stop_for_the_sign_ins_is_said_up_front(tmp_path, sign_in):
    said = _said(tmp_path, built=True, deploy_sign_in=sign_in)
    assert (WAITS in said) is sign_in, said
    assert "\n" not in said


# A sign-in is said only where it opens a page: Claude's without an API key,
# GitHub's for the device flow, Tailscale's where no auth key joins the VM. The
# list was built by appending " and Tailscale" to "Claude, GitHub", so with a key
# the run said "signing in to Claude, GitHub: each opens a page", and an API key
# or a GitHub token, which open none, never took a name off it.
KEY = "tskey-auth-kDeploy0CNTRL-TestOnly000000000000000"


@pytest.mark.parametrize(
    ("settings", "sign_ins"),
    [
        pytest.param({}, "Claude, GitHub and Tailscale", id="none-given"),
        pytest.param({"tailscale_auth_key": KEY}, "Claude and GitHub", id="tailscale-key"),
        pytest.param({"tailscale_enabled": False}, "Claude and GitHub", id="tailscale-off"),
        pytest.param(
            {"tailscale_auth_method": "web", "tailscale_auth_key": KEY},
            "Claude, GitHub and Tailscale",
            id="tailscale-web",
        ),
        pytest.param({"tailscale_auth_method": "key"}, "Claude and GitHub", id="tailscale-key-method"),
        pytest.param({"anthropic_api_key": "an-api-key"}, "GitHub and Tailscale", id="api-key"),
        pytest.param({"github_token": "a-token"}, "Claude and Tailscale", id="github-token"),
        pytest.param({"github_auth": False}, "Claude and Tailscale", id="github-off"),
        pytest.param(
            {"github_auth_method": "web", "github_token": "a-token"}, "Claude, GitHub and Tailscale", id="github-web"
        ),
        pytest.param({"github_auth_method": "token"}, "Claude and Tailscale", id="github-token-method"),
        pytest.param({"anthropic_api_key": "an-api-key", "github_auth": False}, "Tailscale", id="one"),
    ],
)
def test_only_the_sign_ins_that_open_a_page_are_said(tmp_path, settings, sign_ins):
    said = _said(tmp_path, built=True, **settings)
    assert f"several minutes, then signing in to {sign_ins}, and opening VS Code on alpha." in said, said
    each = "Each sign-in" if "," in sign_ins or " and " in sign_ins else "The sign-in"
    assert said.endswith(f" {each} {WAITS}"), said


def test_a_run_with_no_page_to_open_says_no_sign_in(tmp_path):
    settings = {"anthropic_api_key": "an-api-key", "github_token": "a-token", "tailscale_auth_key": KEY}
    said = _said(tmp_path, built=True, **settings)
    assert said.endswith(
        "Then provisioning and configuring alpha, which takes several minutes, and opening VS Code on alpha."
    ), said


# The VMs were joined with commas only, so two read as three things to do:
# "provisioning alpha, beta and configuring".
@pytest.mark.parametrize(
    ("names", "said_as"),
    [
        pytest.param(["alpha", "beta"], "alpha and beta", id="two"),
        pytest.param(["alpha", "beta", "gamma"], "alpha, beta and gamma", id="three"),
    ],
)
def test_several_vms_are_said_as_a_list_and_get_no_window(tmp_path, names, said_as):
    said = _said(tmp_path, built=True, claude_vm_names=names, tailscale_enabled=False)
    assert (
        f"Then provisioning and configuring {said_as}, which takes several minutes,"
        f" then signing in to Claude and GitHub. Each sign-in {WAITS}"
    ) in said, said
    assert "VS Code" not in said
