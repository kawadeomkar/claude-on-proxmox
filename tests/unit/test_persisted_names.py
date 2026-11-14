"""The names runs have left on VMs, on the Proxmox host and on this machine, which change only with a migration.

Each is data the project finds again on a later run: a tag on a VM, a marker file, a drop-in it owns, a block in the
managed SSH config, a path inside a workspace file VS Code reopens. Renamed in the code, every VM and machine already
deployed would be stranded - a VM no longer found as this project's, a drop-in written twice under two names, a marker
read where none was ever written. So each is listed here as the code spells it, and a change to one fails this test,
which is the place to stop and write its migration (docs/architecture/decisions.md §15.4; docs/guides/upgrading.md).
2.0, which named the project agent-warehouse, moved them so: each by its new name, and the 1.x name its migration still
reads through 2.x beside it, so that neither half can go unseen.

A name is held whole: a variable's value as YAML reads it, or the exact line that spells it. A substring would pass a
new name that contains the old one (gh-login-v2 for gh-login), which strands as surely as any other rename.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

# (file, variable, its value): a variable of a vars, defaults or inventory file, compared as YAML reads it.
VALUES = [
    # On every VM, in Proxmox: the ownership tag, the mark of a VM whose provisioning did not finish, the default name.
    ("inventory/group_vars/all/defaults.yml", "claude_vm_tag", "agent-warehouse"),
    # Its 1.x name, owned beside it while it is the default, and every tag made from either: what each reader takes as
    # the project's, the Tailscale record above all, and what provisioning writes under the new one (2.x).
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_vm_tag_1x",
        "{{ 'claude-on-proxmox' if claude_vm_tag | lower == 'agent-warehouse' else '' }}",
    ),
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_vm_tags_owned",
        "{{ [claude_vm_tag | lower] + ([claude_vm_tag_1x] if claude_vm_tag_1x | length > 0 else []) }}",
    ),
    ("inventory/group_vars/all/defaults.yml", "proxmox_vm_tags_1x", "{{ claude_vm_tags_owned[1:] }}"),
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_vm_tag_pattern",
        "(?i)(^|[;,])({{ claude_vm_tags_owned | map('regex_escape') | join('|') }})([;,]|$)",
    ),
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_vm_tailscale_tag_pattern",
        "(?i)(^|[;,])({{ claude_vm_tags_owned | map('regex_escape') | join('|') }})-tailscale([;,]|$)",
    ),
    (
        "roles/proxmox_vm/vars/main.yml",
        "proxmox_vm_tags_now",
        "{{ proxmox_vm_tags_given | proxmox_tags_moved(proxmox_vm_sent_tags[0], proxmox_vm_tags_1x) }}",
    ),
    ("inventory/group_vars/all/defaults.yml", "vm_default_name", "agent-warehouse-default"),
    # Its 1.x name, which a bare deploy or destroy refuses to miss (tasks/default_name_1x.yml, 2.x), and the default it
    # is refused beside.
    ("inventory/group_vars/all/defaults.yml", "claude_vm_default_name_1x", "claude-on-proxmox-default"),
    ("inventory/group_vars/all/defaults.yml", "claude_vm_default_name_2_0", "agent-warehouse-default"),
    ("roles/proxmox_vm/vars/main.yml", "proxmox_vm_unfinished_tag", "{{ proxmox_vm_sent_tags[0] }}-unfinished"),
    # The same tag as the role spells it when no inventory gives it one, and the record of a VM's Tailscale join made
    # from it, as the inventory and each role spell it: the record that keeps the vault's auth key from a VM again.
    ("roles/proxmox_vm/defaults/main.yml", "proxmox_vm_tags", ["agent-warehouse"]),
    ("roles/proxmox_vm/defaults/main.yml", "proxmox_vm_tags_1x", ["claude-on-proxmox"]),
    ("inventory/group_vars/all/defaults.yml", "claude_vm_tailscale_tag", "{{ claude_vm_tag | lower }}-tailscale"),
    ("roles/proxmox_vm/vars/main.yml", "proxmox_vm_tailscale_tag", "{{ proxmox_vm_sent_tags[0] }}-tailscale"),
    ("roles/tailscale/defaults/main.yml", "tailscale_joined_tag", "agent-warehouse-tailscale"),
    # On the Proxmox host: where the template's image is built.
    ("roles/proxmox_template/defaults/main.yml", "proxmox_template_image_dir", "/var/lib/vz/agent-warehouse"),
    # And by its 1.x name, which the build moves it from (2.x), beside the default it moves to.
    ("roles/proxmox_template/vars/main.yml", "proxmox_template_image_dir_1x", "/var/lib/vz/claude-on-proxmox"),
    ("roles/proxmox_template/vars/main.yml", "proxmox_template_image_dir_default", "/var/lib/vz/agent-warehouse"),
    # On a VM: the roles' markers and drop-ins, by their names from 2.0 and through 1.x, which each role's names.yml
    # moves them from (the directory while it is the default) and ownership.yml reads until then (2.x).
    ("roles/tailscale/defaults/main.yml", "tailscale_marker_dir", "/var/lib/agent-warehouse"),
    ("roles/tailscale/vars/main.yml", "tailscale_marker_dir_default", "/var/lib/agent-warehouse"),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_marker_dir_1x",
        "{{ '/var/lib/claude-on-proxmox' if tailscale_marker_dir == tailscale_marker_dir_default else '' }}",
    ),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_dropin_path_1x",
        "/etc/systemd/system/tailscaled.service.d/10-claude-on-proxmox.conf",
    ),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_installed_marker_path",
        "{{ tailscale_marker_dir }}/tailscale-installed",
    ),
    ("roles/tailscale/vars/main.yml", "tailscale_joined_marker_path", "{{ tailscale_marker_dir }}/tailscale-joined"),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_dropin_path",
        "/etc/systemd/system/tailscaled.service.d/10-agent-warehouse.conf",
    ),
    # The sign-in state's directory, github_projects' and claude_code's, by its name from 2.0 and through 1.x, which
    # each role's names.yml moves it from and just status and the release read until then (2.x).
    ("roles/github_projects/vars/main.yml", "github_projects_marker_file", "{{ github_projects_state_dir }}/gh-login"),
    (
        "roles/github_projects/vars/main.yml",
        "github_projects_device_marker_file",
        "{{ github_projects_state_dir }}/gh-device-login",
    ),
    (
        "roles/github_projects/vars/main.yml",
        "github_projects_state_dir",
        "{{ github_projects_home_dir }}/.config/agent-warehouse",
    ),
    (
        "roles/github_projects/vars/main.yml",
        "github_projects_state_dir_1x",
        "{{ github_projects_home_dir }}/.config/claude-on-proxmox",
    ),
    ("roles/claude_code/vars/main.yml", "claude_code_login_dir", "{{ claude_code_home }}/.config/agent-warehouse"),
    ("roles/claude_code/vars/main.yml", "claude_code_login_dir_1x", "{{ claude_code_home }}/.config/claude-on-proxmox"),
    # common's drop-ins and files, by their names from 2.0, and each by its 1.x name, which names.yml moves it from:
    # the cloud-init file among them, written with passwordless sudo off and removed when it is turned on - under a new
    # name the old one would stay, and take the sudo rule away again at the VM's next cloud-init instance.
    ("roles/common/vars/main.yml", "common_ssh_path_dropin", "/etc/ssh/sshd_config.d/15-agent-warehouse-path.conf"),
    ("roles/common/vars/main.yml", "common_cloud_init_user_cfg", "/etc/cloud/cloud.cfg.d/99-agent-warehouse-user.cfg"),
    ("roles/common/vars/main.yml", "common_sshd_hardening", "/etc/ssh/sshd_config.d/10-agent-warehouse.conf"),
    ("roles/common/vars/main.yml", "common_apt_downloads", "/etc/apt/apt.conf.d/90agent-warehouse-downloads"),
    (
        "roles/common/vars/main.yml",
        "common_names_1x",
        [
            {"old": "/etc/ssh/sshd_config.d/10-claude-on-proxmox.conf", "new": "{{ common_sshd_hardening }}"},
            {"old": "/etc/ssh/sshd_config.d/15-claude-on-proxmox-path.conf", "new": "{{ common_ssh_path_dropin }}"},
            {"old": "/etc/apt/apt.conf.d/90claude-on-proxmox-downloads", "new": "{{ common_apt_downloads }}"},
            {"old": "/etc/cloud/cloud.cfg.d/99-claude-on-proxmox-user.cfg", "new": "{{ common_cloud_init_user_cfg }}"},
        ],
    ),
    ("roles/common/vars/main.yml", "common_tmux_marker_1x", "# {mark} claude-on-proxmox: common_tmux_settings"),
    # Inside every workspace file VS Code reopens from its recent list: VS Code's helpers, by path (their directory,
    # code_helpers_dir, is code.yml's, below).
    ("playbooks/vars/vscode.yml", "code_helpers", ["claude-terminal.sh", "session-notes.sh", "session_notes.py"]),
    # On this machine: the managed SSH config, by its name from 2.0, and by its 1.x name beside it, which the first
    # alias write moves it from and the prune and just status read until then (through 2.x).
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_ssh_config_file",
        "{{ lookup('ansible.builtin.env', 'HOME') }}/.ssh/agent-warehouse.conf",
    ),
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_ssh_config_legacy_file",
        "{{ (claude_ssh_config_file | dirname) ~ '/claude-on-proxmox.conf'\n"
        "   if claude_ssh_config_file | basename == 'agent-warehouse.conf' else '' }}",
    ),
]

# (file, the line as it is there, stripped): where the name is a task's argument, a play's variable, code or text.
LINES = [
    # On a VM: common's and vscode_server's files, the latter VS Code's name on any editor's VM through 1.x.
    ("roles/common/tasks/sshd.yml", 'dest: "{{ common_sshd_hardening }}"'),
    ("roles/common/tasks/apt.yml", 'dest: "{{ common_apt_downloads }}"'),
    ("roles/vscode_server/tasks/main.yml", "dest: /etc/ssh/sshd_config.d/20-vscode-server.conf"),
    ("roles/vscode_server/tasks/main.yml", "sysctl_file: /etc/sysctl.d/60-vscode-server.conf"),
    # The settings' block in /etc/tmux.conf, written and taken out by its marker: under a new one, blockinfile would
    # add a second block and never take the first out.
    ("roles/common/tasks/tmux.yml", 'marker: "# {mark} agent-warehouse: common_tmux_settings"'),
    ("roles/common/tasks/tmux.yml", 'marker: "{{ common_tmux_marker_1x }}"'),
    # just code's own directory on a VM, and what is in it: the sessions' mirror and manifest, written by just code and
    # read by destroy's save; the notes; the workspace files.
    ("lib/claude_vm/sessions.py", 'path = str(given or "") or f"{str(projects).rstrip(\'/\')}/.agent-warehouse"'),
    # By its 1.x name, which code.yml moves it from and destroy's save reads the manifest from until then (2.x).
    (
        "lib/claude_vm/sessions.py",
        'return "" if str(given or "") else f"{str(projects).rstrip(\'/\')}/.claude-on-proxmox"',
    ),
    (
        "playbooks/code.yml",
        "{{ code_root_given | just_code_dir_1x(editor_files_dir | default(vscode_workspaces_dir"
        " | default('', true))) }}",
    ),
    ("playbooks/code.yml", 'argv: [mv, -n, "{{ code_ws_dir_1x }}", "{{ code_ws_dir }}"]'),
    ("playbooks/code.yml", 'code_session_dir: "{{ code_ws_dir }}/claude-sessions"'),
    ("playbooks/tasks/session_write.yml", 'mirror: "{{ code_session_dir }}/mirror"'),
    ("playbooks/tasks/session_write.yml", 'manifest: "{{ code_session_dir }}/manifest.json"'),
    (
        "playbooks/tasks/session_save.yml",
        "else claude_vm_save_projects | just_code_dir(claude_vm_save_code_given)) }}/claude-sessions/manifest.json",
    ),
    (
        "playbooks/tasks/session_save.yml",
        'claude_vm_save_code_1x: "{{ claude_vm_save_projects | just_code_dir_1x(claude_vm_save_code_given) }}"',
    ),
    ("playbooks/code.yml", "code_notes_dir: \"{{ code_ws_dir_given ~ '/session' }}\""),
    (
        "playbooks/code.yml",
        "code_notes_file: \"{{ code_ws_dir_given ~ '/session/' ~ claude_vm_names[0] ~ '-session.md' }}\"",
    ),
    # VS Code's workspace files, one project's in project/, and the helpers its tasks run, by path.
    ("lib/claude_vm/editors/vscode.py", 'WORKSPACE_SUFFIX = ".code-workspace"'),
    ("lib/claude_vm/editors/vscode.py", 'return f"project/{inputs.project}{WORKSPACE_SUFFIX}"'),
    (
        "lib/claude_vm/editors/vscode.py",
        "return f\"{posixpath.basename(inputs.root) or 'projects'}{WORKSPACE_SUFFIX}\"",
    ),
    ("lib/claude_vm/editors/vscode.py", 'f"{helpers}/claude-terminal.sh",'),
    ("lib/claude_vm/editors/vscode.py", 'notes_helper = f"{helpers}/session-notes.sh"'),
    # Zed's tasks file in just code's directory, which Zed reads from the window's tree.
    ("lib/claude_vm/editors/zed.py", 'TASKS: Final = ".zed/tasks.json"'),
    # The helpers' directory, which every editor's helpers are in, and the shared helper, whose path is in the
    # tasks file just code writes and in a task of the user's own (docs/guides/zed.md): <projects>/.agent-warehouse/
    # helpers/claude-start.sh by default, and through 2.x the link at its 1.x path, written where the directory moves.
    ("playbooks/code.yml", 'code_helpers_dir: "{{ code_ws_dir }}/helpers"'),
    ("playbooks/code.yml", "code_shared_helpers: [claude-start.sh]"),
    ("playbooks/code.yml", 'code_start_helper: "{{ code_helpers_dir }}/claude-start.sh"'),
    # The same two names as the editors' plans write them: the directory they make, and the helper Zed's task runs.
    ("lib/claude_vm/editors/common.py", 'HELPERS: Final = "helpers"'),
    ("lib/claude_vm/editors/zed.py", 'HELPER: Final = "claude-start.sh"'),
    # The first line of every file in JSON with comments just code writes for an editor to read from 1.1 on
    # (.zed/tasks.json today), by which a later run tells its own from a file that is someone else's.
    (
        "lib/claude_vm/editors/common.py",
        'MARKER: Final = "// Written by just code (agent-warehouse); edits here are overwritten."',
    ),
    (
        "lib/claude_vm/editors/common.py",
        'MARKER_1X: Final = "// Written by just code (claude-on-proxmox); edits here are overwritten."',
    ),
    ("lib/claude_vm/editors/zed.py", "foreign = head is not None and head not in (common.MARKER, common.MARKER_1X)"),
    # The notes' live section, as the template writes it and the helper finds it, and as 1.x wrote it, which the helper
    # and code.yml's carry-over read and write again with 2.0's.
    ("playbooks/templates/vm_session.md.j2", "<!-- agent-warehouse:live -->"),
    ("playbooks/templates/vm_session.md.j2", "<!-- /agent-warehouse:live -->"),
    (
        "playbooks/files/session_notes.py",
        'START, END = "<!-- agent-warehouse:live -->", "<!-- /agent-warehouse:live -->"',
    ),
    (
        "playbooks/files/session_notes.py",
        'START_1X, END_1X = "<!-- claude-on-proxmox:live -->", "<!-- /claude-on-proxmox:live -->"',
    ),
    # In Proxmox, beside the ownership tag: the mark of a VM whose Tailscale has held a login, <tag>-tailscale.
    ("lib/claude_vm/vpn/common.py", 'TAILSCALE: Final = "tailscale"'),
    # On the Proxmox host: the record of the template's build in its notes.
    ("lib/claude_vm/proxmox.py", 'TEMPLATE_RECORD = "Built by agent-warehouse:"'),
    ("lib/claude_vm/proxmox.py", 'TEMPLATE_RECORD_1X = "Built by claude-on-proxmox:"'),
    # On this machine: the managed SSH config's blocks, written with 2.0's markers, read with either, and the 1.x
    # markers of a moved file rewritten; the checkout's own folder by both names; the git hook's text, both known.
    ("playbooks/tasks/ssh_config.yml", 'marker: "# {mark} agent-warehouse: {{ claude_ssh_vm.name }}"'),
    ("playbooks/tasks/ssh_config.yml", "regexp: '^# (BEGIN|END) claude-on-proxmox: (\\S+)$'"),
    (
        "lib/claude_vm/proxmox.py",
        'r"^# BEGIN (?P<by>agent-warehouse|claude-on-proxmox): (?P<name>\\S+)$\\n(?P<body>.*?)'
        '^# END (?P=by): (?P=name)$",',
    ),
    # And the host each block records its VM was found on (2.5), which the prune, destroy and the next write go by:
    # written as a comment line of the block, read by one pattern.
    ("playbooks/tasks/ssh_config.yml", "# origin: {{ claude_ssh_origin }}"),
    ("lib/claude_vm/proxmox.py", 'ORIGIN_RE = re.compile(r"^# origin: (?P<origin>\\S+)$", re.M)'),
    ("tools/env-run", 'checkout=$(basename "$here")-$(printf %s "$here" | cksum | cut -d \' \' -f 1)'),
    ("tools/env-run", "data=${XDG_DATA_HOME:-$HOME/.local/share}/agent-warehouse/checkouts/$checkout"),
    ("tools/env-run", "legacy=${XDG_DATA_HOME:-$HOME/.local/share}/claude-on-proxmox/checkouts/$checkout"),
    # The git hook just init copies into a clone's hooks folder, which a later just init knows as its own by its text,
    # 2.0's or 1.x's: by neither, it would take an installed hook for someone else's, keep it as pre-commit.legacy, and
    # pre-commit would run that one too.
    ("tools/git-hook", "# agent-warehouse's pre-commit hook, which just init copies into git's hooks"),
    (
        "justfile",
        "if grep -q -e 'File generated by pre-commit' -e \"agent-warehouse's pre-commit hook\""
        ' -e "claude-on-proxmox\'s pre-commit hook" "$hook" 2>/dev/null; then',
    ),
]


def _value(where: str, name: str):
    data = yaml.safe_load((REPO / where).read_text())
    assert isinstance(data, dict) and name in data, f"{where} no longer sets {name}"
    return data[name]


def _spells(where: str, line: str) -> bool:
    return line in {each.strip() for each in (REPO / where).read_text().splitlines()}


@pytest.mark.parametrize(("where", "name", "value"), VALUES, ids=[f"{w}: {n}" for w, n, _ in VALUES])
def test_a_name_a_run_left_somewhere_is_the_value_it_was(where: str, name: str, value) -> None:
    assert _value(where, name) == value, f"{where}: {name} changed, and deployed VMs and machines have the old one"


@pytest.mark.parametrize(("where", "line"), LINES, ids=[f"{w}: {line[:48]}" for w, line in LINES])
def test_a_name_a_run_left_somewhere_is_spelled_on_its_line_as_it_was(where: str, line: str) -> None:
    assert _spells(where, line), f"{where} no longer has the line {line!r}: deployed VMs and machines have it"


def test_a_name_kept_inside_a_longer_one_is_a_rename_too(tmp_path, monkeypatch) -> None:
    # What a substring would pass: the old name still inside the new.
    (tmp_path / "vars.yml").write_text('marker: "{{ home }}/.config/claude-on-proxmox/gh-login-v2"\n')
    (tmp_path / "tasks.yml").write_text("    dest: /etc/ssh/sshd_config.d/10-claude-on-proxmox.conf.d\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert _value("vars.yml", "marker") != "{{ home }}/.config/claude-on-proxmox/gh-login"
    assert not _spells("tasks.yml", "dest: /etc/ssh/sshd_config.d/10-claude-on-proxmox.conf")
