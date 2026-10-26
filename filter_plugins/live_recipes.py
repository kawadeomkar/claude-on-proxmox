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


class Outcome:
    """What a VM's fact says of its outcome in a step: where the fact is set and true, the VM is marked state
    (done, skipped, failed). Only the fact's truth is read, never its text, so a VM can put no words on the screen
    through one."""

    def __init__(self, fact, state):
        self.fact = fact
        self.state = state


class Step:
    """A step of a stage, labelled as a person would name it, and what puts a task in it.

    plays: the playbook files (a file name) every task of whose plays is this step's, as each sign-in is. blocks: the
    named blocks every task inside which is this step's, whatever role or file it comes from, as destroy's release of
    each service. outcomes: the facts that say each VM's outcome in it, the first that holds winning over what its
    tasks' results say."""

    def __init__(self, label, plays=(), outcomes=(), blocks=()):
        self.label = label
        self.plays = tuple(plays)
        self.outcomes = tuple(outcomes)
        self.blocks = frozenset(blocks)


class Info:
    """What a stage's row says of it, from a fact a task of the run sets: the VMs it names (a list of names, or of
    mappings with a name), after word - "started beta"."""

    def __init__(self, fact, word):
        self.fact = fact
        self.word = word


class Stage:
    """A stage of a run, and the plays that are its: a playbook file (a file name), or one play of it (file:play
    name). Without steps of its own, its steps are the roles its tasks are in, and its tasks outside any role are the
    stage's own. info "vm" says the run's VM on its row, as the provision stage does, or an Info says what a fact
    names. A quiet stage's tasks are counted but never shown: its row appears only once one of them fails - a last
    play that only raises again what failed earlier. outcomes: as a step's, for each of its steps that has none of
    its own."""

    def __init__(self, key, plays=(), steps=(), info="", quiet=False, outcomes=()):
        self.key = key
        self.plays = tuple(plays) + tuple(play for step in steps for play in step.plays)
        self.steps = tuple(steps)
        self.info = info
        self.quiet = quiet
        self.outcomes = tuple(outcomes)


class Causes:
    """Where the failures a re-raise repeats were rescued: inside blocks of these names, on the same host, or with
    hosts "any" on every host - a last play on this machine that raises what failed on the VMs; and with
    unreachable, the host's unreachable results a task ignored, as the sign-ins' pings do."""

    def __init__(self, blocks=(), unreachable=False, hosts="same"):
        self.blocks = frozenset(blocks)
        self.unreachable = unreachable
        self.hosts = hosts


class Recipe:
    """A command's run: its stages, in order; the playbook files whose first play reports what the run will do; how
    many of the running step's last tasks are shown; the tasks that raise again failures a rescue took earlier, by
    name, each with where its causes are; the word for a step of a run of one stage (noun, its footer's and its
    summary's); the summary's columns; the playbook files whose add_host gives the summary a VM's address and ID,
    read from Proxmox by discovery; and the tasks whose failure is a person's answer, not a failure - each with the
    line said instead."""

    def __init__(
        self,
        stages=(),
        plan=(),
        window=5,
        raises=None,
        noun="step",
        columns=("time", "tasks", "changed"),
        addresses_from=(),
        declines=None,
    ):
        self.stages = tuple(stages)
        self.plan = frozenset(plan)
        self.window = window
        self.raises = dict(raises or {})
        self.noun = noun
        self.columns = tuple(columns)
        self.addresses_from = frozenset(addresses_from)
        self.declines = dict(declines or {})


# Each sign-in, as both just deploy and just login run it: its playbook, and each VM's outcome in it from the facts
# its role and play set - signed in now, skipped (Enter at Claude's prompt, a browser that never came back), failed,
# or the work after it failed.
SIGN_INS = (
    Step(
        "Claude",
        plays=["claude_login.yml"],
        outcomes=[
            Outcome("claude_vm_claude_login_failure", "failed"),
            Outcome("claude_code_login_skipped", "skipped"),
            Outcome("claude_code_login_done", "done"),
        ],
    ),
    Step(
        "GitHub",
        plays=["github_login.yml"],
        outcomes=[
            Outcome("claude_vm_github_login_failure", "failed"),
            Outcome("github_projects_login_done", "done"),
        ],
    ),
    Step(
        "Tailscale",
        plays=["tailscale_login.yml"],
        outcomes=[Outcome("claude_vm_tailscale_login_failure", "failed")],
    ),
)
# What a sign-in's play rescues, for its last play, or just login's, to raise: the sign-in itself, the work after it,
# and a VM its ping could not reach.
SIGN_IN_FAILURES = Causes(
    blocks=[
        "Sign this VM in",
        "Start Remote Control as just deploy --only configure would",
        "Configure the repositories now that gh is signed in",
    ],
    unreachable=True,
)

# What configuration and the sign-ins rescue and record in claude_vm_configure_failures, for a last play to raise:
# Tailscale's role in configure, and the work after a sign-in.
CONFIGURE_FAILURES = Causes(
    blocks=[
        "Install Tailscale and keep it joined",
        "Start Remote Control as just deploy --only configure would",
        "Configure the repositories now that gh is signed in",
    ]
)


RECIPES = {
    # just deploy, every form of it: the whole of deploy.yml, and each --only stage's playbook alone.
    "deploy": Recipe(
        stages=[
            Stage("template", plays=["template.yml"]),
            Stage("provision", plays=["provision.yml"], info="vm"),
            Stage("configure", plays=["configure.yml"]),
            Stage("SSH aliases", plays=["ssh_config.yml"]),
            Stage("sign in", steps=SIGN_INS),
            Stage("VS Code", plays=["code.yml"]),
            # deploy.yml's last play, which raises what failed after the sign-ins once VS Code has opened.
            Stage("finish", plays=["deploy.yml:Fail each VM whose work after a sign-in failed"], quiet=True),
        ],
        plan=["deploy.yml"],
        raises={
            "Fail where the work after a sign-in failed": CONFIGURE_FAILURES,
            "Fail where Tailscale failed": CONFIGURE_FAILURES,
        },
    ),
    # just login: the three sign-ins one VM after another, or each VM's state of them; one stage, so its sign-ins are
    # the rows. Discovery and each sign-in's own discovery, which login.yml turns off, are counted, never shown.
    "login": Recipe(
        stages=[
            Stage("discover", plays=["discover.yml"], quiet=True),
            Stage("sign in", steps=SIGN_INS),
            Stage("finish", plays=["login.yml:Fail each VM whose sign-in, or the work after one, failed"], quiet=True),
        ],
        window=3,
        raises={
            "Fail where a sign-in, or the work after one, failed": SIGN_IN_FAILURES,
            # just login --only <sign-in>: that playbook's own last play.
            "Fail where the sign-in failed": SIGN_IN_FAILURES,
            "Fail where the work after the sign-in failed": SIGN_IN_FAILURES,
        },
        noun="sign-in",
        columns=("time",),
        addresses_from=["discover.yml"],
    ),
    # just destroy: the check (and starting a stopped VM to release it), each VM's release, its deletion, and the
    # aliases and the report. Its release's steps are its blocks, since one role's entry points fall in two of them.
    "destroy": Recipe(
        stages=[
            Stage(
                "check",
                plays=["destroy.yml:Confirm and check what to destroy"],
                info=Info("claude_vm_release_started", "started"),
            ),
            Stage(
                "release",
                plays=["destroy.yml:Release each VM from what it signed in to"],
                steps=[
                    Step("sessions", blocks=["Save what of the VM's Claude Code sessions is only there"]),
                    Step("Claude", blocks=["Sign Claude Code out, and stop Remote Control"]),
                    Step("GitHub", blocks=["Sign gh out"]),
                    Step("Tailscale", blocks=["Take the VM off the tailnet"]),
                ],
            ),
            Stage(
                "delete",
                plays=["destroy.yml:Delete the VMs on Proxmox"],
                outcomes=[
                    Outcome("claude_vm_delete_error", "failed"),
                    Outcome("claude_vm_save_error", "skipped"),
                    Outcome("claude_vm_deleted", "done"),
                ],
            ),
            Stage("aliases", plays=["destroy.yml:Forget the SSH alias of each deleted VM"]),
        ],
        window=3,
        raises={
            "Fail for each VM that was not deleted": Causes(
                blocks=[
                    "Delete the VM, or remember why not",
                    "Save what of the VM's Claude Code sessions is only there",
                ],
                hosts="any",
            ),
        },
        declines={"Abort unless confirmed": "Nothing was deleted: the deletion was not confirmed."},
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
