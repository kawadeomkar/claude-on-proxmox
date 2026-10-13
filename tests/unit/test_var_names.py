"""No task var shares its name with a register or a fact set in the same place.

A register or a set_fact outranks a task var, so a task var named like one
reads the fact as soon as one exists: deploy.yml built its opening message in
a task var named like the template lookup's register, and printed the API's
raw answer instead. A playbook is one place; a role's task files are one place
together, since its tasks share their host's facts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SET_FACT = ("ansible.builtin.set_fact", "set_fact")


def _tasks(node):
    """Every task in a play list or task list, blocks opened."""
    for item in node if isinstance(node, list) else []:
        if not isinstance(item, dict):
            continue
        if "tasks" in item or "pre_tasks" in item or "post_tasks" in item or "handlers" in item:
            for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                yield from _tasks(item.get(key))
            continue
        yield item
        for key in ("block", "rescue", "always"):
            yield from _tasks(item.get(key))


def _names(files: list[Path]) -> tuple[dict, dict]:
    """Names set (register, set_fact) and task vars, each to the files that use them."""
    facts: dict[str, set] = {}
    task_vars: dict[str, set] = {}
    for path in files:
        for task in _tasks(yaml.safe_load(path.read_text())):
            where = str(path.relative_to(REPO))
            if isinstance(task.get("register"), str):
                facts.setdefault(task["register"], set()).add(where)
            for key in SET_FACT:
                if isinstance(task.get(key), dict):
                    for name in task[key]:
                        if name != "cacheable":
                            facts.setdefault(name, set()).add(where)
            if isinstance(task.get("vars"), dict):
                for name in task["vars"]:
                    task_vars.setdefault(name, set()).add(where)
    return facts, task_vars


PLACES = {
    **{str(p.relative_to(REPO)): [p] for p in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]},
    **{
        f"roles/{role.name}": sorted((role / "tasks").glob("*.yml"))
        for role in sorted((REPO / "roles").iterdir())
        if (role / "tasks").is_dir()
    },
}


@pytest.mark.parametrize("place", sorted(PLACES))
def test_no_task_var_is_named_like_a_register_or_a_fact(place):
    facts, task_vars = _names(PLACES[place])
    clashes = {name: sorted(task_vars[name] | facts[name]) for name in set(facts) & set(task_vars)}
    assert not clashes, clashes


def test_the_scan_sees_the_places_it_guards():
    facts, task_vars = _names(PLACES["deploy.yml"])
    assert "claude_deploy_template" in facts and "claude_deploy_template_said" in task_vars
    facts, task_vars = _names(PLACES["roles/claude_code"])
    assert "claude_code_login_prompt" in facts and "claude_code_login_why" in task_vars
