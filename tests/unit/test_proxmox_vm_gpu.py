"""roles/proxmox_vm's GPU passthrough, held statically: what the scenario cannot show by running it.

The role scenario (roles/proxmox_vm/molecule/default/side_effect.yml) runs the
GPU's paths against the fake Proxmox API. What it cannot run: a CA trusted
through REQUESTS_CA_BUNDLE, which `uri` ignores, since its fake certificate is
made inside its container; and the rules that keep a value read from the
previous run of the role out of the GPU decisions.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "proxmox_vm"
VARS = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())


def _tasks():
    for path in sorted((ROLE / "tasks").glob("*.yml")):
        stack = list(yaml.safe_load(path.read_text()) or [])
        while stack:
            task = stack.pop(0)
            yield path.name, task
            for key in ("block", "rescue", "always"):
                stack[:0] = task.get(key) or []


def test_every_uri_call_of_the_role_passes_the_ca_and_hides_the_token():
    # The modules' HTTP client reads REQUESTS_CA_BUNDLE, through which the
    # wiki trusts the Proxmox CA; uri does not. Without ca_path, a host that
    # passes every proxmox_kvm call would fail every start of a VM with a GPU.
    calls = [(name, task) for name, task in _tasks() if "ansible.builtin.uri" in task]
    assert len(calls) == 2, [task["name"] for _, task in calls]
    for name, task in calls:
        uri = task["ansible.builtin.uri"]
        assert uri["ca_path"] == "{{ proxmox_vm_api_ca_path | default(omit, true) }}", (name, task["name"])
        assert uri["validate_certs"] == "{{ proxmox_vm_validate_certs }}", (name, task["name"])
        assert uri["headers"] == "{{ proxmox_vm_api_headers }}" and task["no_log"] is True, (name, task["name"])


def test_the_ca_path_is_wired_from_the_one_the_playbooks_use():
    defaults = (REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text()
    assert 'proxmox_vm_api_ca_path: "{{ proxmox_api_ca_path }}"' in defaults.splitlines()
    assert 'proxmox_vm_gpu_mapping: "{{ proxmox_gpu_mapping }}"' in defaults.splitlines()


def test_a_gpu_is_sent_by_its_mapping_alone():
    # Never host=<address>: root's alone, and it names the hardware the host's
    # owner maps. The mapping's name is checked before any call reaches Proxmox.
    settings = next(task for _, task in _tasks() if task["name"] == "present | Apply hardware and cloud-init settings")
    assert settings["community.proxmox.proxmox_kvm"]["hostpci"] == (
        "{{ {'hostpci0': 'mapping=' ~ proxmox_vm_gpu_mapping} if proxmox_vm_gpu_set | bool else omit }}"
    )
    check = next(task for _, task in _tasks() if task["name"] == "Validate the GPU mapping")
    assert check["ansible.builtin.assert"]["that"] == "proxmox_vm_gpu_mapping is match('^[A-Za-z][A-Za-z0-9_-]+$')"
    names = [task["name"] for name, task in _tasks() if name == "main.yml"]
    assert names.index("Validate the GPU mapping") < names.index("Manage the VM")


def test_the_gpu_decisions_read_this_runs_lookup_alone():
    # proxmox_vm_config is registered only after the start, so for a new VM it
    # still holds the previous run's VM - side_effect.yml runs the role back to
    # back - and proxmox_vm_found_config falls back to it.
    gpu = {name: value for name, value in VARS.items() if name.startswith(("proxmox_vm_gpu", "proxmox_vm_has_gpu"))}
    assert "proxmox_vm_gpu_config" in gpu
    for name, value in gpu.items():
        assert not re.search(r"proxmox_vm_config\b|proxmox_vm_found_config", str(value)), name
    assert "proxmox_vm_lookup" in VARS["proxmox_vm_gpu_config"]


def test_no_gpu_internal_is_a_fact():
    facts = set()
    for _, task in _tasks():
        facts |= set((task.get("ansible.builtin.set_fact") or {}).keys())
    assert facts == {"proxmox_vm_exists", "proxmox_vm_resolved_id", "proxmox_vm_stopped", "proxmox_vm_address"}


def test_a_vm_with_a_gpu_never_starts_through_proxmox_kvm():
    # proxmox_kvm waits out its whole timeout on a start that failed.
    start = next(task for _, task in _tasks() if task["name"] == "present | Start the VM")
    assert "not (proxmox_vm_has_gpu | bool)" in start["when"]
    by_api = next(task for _, task in _tasks() if task["name"] == "present | Start the VM with its GPU")
    assert by_api["when"] == "proxmox_vm_gpu_starts | bool"
    assert "proxmox_vm_has_gpu | bool" in VARS["proxmox_vm_gpu_starts"]
