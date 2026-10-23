"""playbooks/tasks/remote_tmp.yml, run as make's runs run it: with pipelining, before any module needs remote_tmp.

With pipelining (ansible.cfg) a module that transfers no file gets no per-task
temporary directory, so the first one running as root that needs one makes
root's remote_tmp itself and warns "did not exist and was created": get_url
building the template on a new Proxmox host, deb822_repository in dev_tools on
a new VM. template.yml and configure.yml make it first. Molecule runs without
pipelining, so a temporary directory made for its first task makes remote_tmp
there and the task is always ok; only these runs show what it is for. Each is
a stand-in host whose HOME is a directory of its own, with no remote_tmp yet,
and get_url, fetching a file:// URL, stands in for the module that needs one.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from harness import LOGINS, REPO, _run, _stand_in_hosts

REMOTE_TMP = REPO / "playbooks" / "tasks" / "remote_tmp.yml"
TASK = "remote_tmp | Make the temporary directory Ansible's modules use"
FETCH = "Fetch a file, as a module that needs remote_tmp"
WARNING = "did not exist and was created"


def _fetch(root: Path, first: bool, **env: str) -> tuple[dict, Path]:
    """Run get_url with pipelining on a new stand-in host, after remote_tmp.yml when first; the report, and its HOME."""
    hosts = _stand_in_hosts(root, {"probe": LOGINS["claude-ai"]})
    source = root / "source.txt"
    source.write_text("fetched\n")
    tasks = [{"name": "Make it first", "ansible.builtin.import_tasks": str(REMOTE_TMP)}] if first else []
    tasks.append(
        {
            "name": FETCH,
            "ansible.builtin.get_url": {"url": source.as_uri(), "dest": str(root / "fetched.txt"), "mode": "0644"},
        }
    )
    playbook = root / "play.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "claude_vms", "gather_facts": False, "tasks": tasks}]))
    report = _run(root, playbook, hosts, env={"ANSIBLE_PIPELINING": "True", **env})
    assert report["rc"] == 0, report["stderr"]
    return report, root / "probe"


def _results(report: dict) -> dict:
    return {task["task"]["name"]: task["hosts"]["probe"] for play in report["plays"] for task in play["tasks"]}


def _warnings(report: dict) -> list:
    return [warning for result in _results(report).values() for warning in result.get("warnings") or []]


def test_a_module_that_makes_remote_tmp_itself_warns(tmp_path):
    # What the task is for, and so that this file sees it: the run pipelines,
    # the host's remote_tmp is new, and the module makes it.
    report, home = _fetch(tmp_path, first=False)
    assert any(WARNING in warning for warning in _warnings(report)), _warnings(report)
    assert (home / ".ansible" / "tmp").is_dir()


def test_made_first_no_module_warns(tmp_path):
    report, home = _fetch(tmp_path, first=True)
    results = _results(report)
    assert results[TASK]["changed"] is True, results[TASK]
    assert not _warnings(report), _warnings(report)
    made = home / ".ansible" / "tmp"
    assert made.is_dir()
    assert made.stat().st_mode & 0o777 == 0o700


def test_it_is_made_where_the_run_puts_remote_tmp(tmp_path):
    # ANSIBLE_REMOTE_TMP in the shell reaches every recipe; the task makes
    # that directory, not the default one.
    report, home = _fetch(tmp_path, first=True, ANSIBLE_REMOTE_TMP="~/elsewhere/tmp")
    assert not _warnings(report), _warnings(report)
    assert (home / "elsewhere" / "tmp").is_dir()
    assert not (home / ".ansible" / "tmp").exists()


def _pre_tasks(playbook: Path, play: str) -> list:
    return next(p for p in yaml.safe_load(playbook.read_text()) if p.get("name") == play).get("pre_tasks") or []


def test_both_plays_that_run_modules_as_root_make_it_before_their_roles():
    for playbook, play in (
        ("template.yml", "Build the cloud-init VM template"),
        ("configure.yml", "Configure Claude development VMs"),
    ):
        imports = [
            task
            for task in _pre_tasks(REPO / "playbooks" / playbook, play)
            if task.get("ansible.builtin.import_tasks") == "tasks/remote_tmp.yml"
        ]
        assert len(imports) == 1, playbook
    # A role run alone by TAGS can be a new VM's first.
    configure = _pre_tasks(REPO / "playbooks" / "configure.yml", "Configure Claude development VMs")
    assert next(t for t in configure if "ansible.builtin.import_tasks" in t)["tags"] == ["always"]
