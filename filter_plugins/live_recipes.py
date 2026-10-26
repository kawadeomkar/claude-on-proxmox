"""Every `just` command drawn live, declared: what each recipe's run is made of, and nothing about how it is drawn.

A recipe that is drawn live exports CLAUDE_ON_PROXMOX_LIVE, its title, whose first word names its declaration here
(RECIPES). A declaration says what the run is made of - its stages, each stage's steps and how a play or a task is
matched to them - and filter_plugins/run_view.py and filter_plugins/live_ansible.py draw any run from it alone. A
command extends the shared engine only through a declaration: anything a declaration cannot say becomes a new field
here and new code in the engine, never code of one command's own (docs/architecture/custom-code.md §7.8).

Pure data: nothing here reads, writes or imports anything of the project's. Loaded by path as
claude_on_proxmox_live_recipes, as every module here loads another; it has no filters of its own. A test holds every
name a declaration matches to the playbook it names (tests/unit/test_live_recipes.py).
"""

from __future__ import annotations


class Step:
    """A step of a stage, labelled as a person would name it, and what puts a task in it.

    plays: the playbook files (a file name) every task of whose plays is this step's, as each sign-in is."""

    def __init__(self, label, plays=()):
        self.label = label
        self.plays = tuple(plays)


class Stage:
    """A stage of a run, and the plays that are its: by playbook file (a file name). Without steps of its own, its
    steps are the roles its tasks are in, and its tasks outside any role are the stage's own. info "vm" says the run's
    VM on its row, as the provision stage does."""

    def __init__(self, key, plays=(), steps=(), info=""):
        self.key = key
        self.plays = tuple(plays) + tuple(play for step in steps for play in step.plays)
        self.steps = tuple(steps)
        self.info = info


class Recipe:
    """A command's run: its stages, in order, and the playbook files whose first play reports what the run will do."""

    def __init__(self, stages=(), plan=()):
        self.stages = tuple(stages)
        self.plan = frozenset(plan)


RECIPES = {
    # just deploy, every form of it: the whole of deploy.yml, and each --only stage's playbook alone.
    "deploy": Recipe(
        stages=[
            Stage("template", plays=["template.yml"]),
            Stage("provision", plays=["provision.yml"], info="vm"),
            Stage("configure", plays=["configure.yml"]),
            Stage("SSH aliases", plays=["ssh_config.yml"]),
            Stage(
                "sign in",
                steps=[
                    Step("Claude", plays=["claude_login.yml"]),
                    Step("GitHub", plays=["github_login.yml"]),
                    Step("Tailscale", plays=["tailscale_login.yml"]),
                ],
            ),
            Stage("VS Code", plays=["code.yml"]),
        ],
        plan=["deploy.yml"],
    ),
}

# A run whose title names no declaration is drawn as one, as deploy's is.
DEFAULT = "deploy"


def recipe(title):
    """The declaration a run's title names: its first word, the recipe's name."""
    words = str(title or "").split()
    return RECIPES.get(words[0] if words else "", RECIPES[DEFAULT])


class FilterModule:
    """No filters: loaded by path. Here so the filter loader takes the file as a plugin."""

    def filters(self):
        return {}
