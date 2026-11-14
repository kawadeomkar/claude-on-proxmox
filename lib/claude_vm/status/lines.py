"""What the roles say of a VM's sign-ins, as functions of what is known of it: the line, and whether it leaves something
to do.

One definition for both who say it: the roles' own reports (roles/github_projects/tasks/login_report.yml and
roles/tailscale/tasks/report.yml, through filter_plugins/status.py), and just status, which reads the same state its
own way (claude_vm.status.found) and shows the same line. NEXT STEPS and the report never say two things of one VM.

Nothing here reads or writes anything, and no token or key reaches it: each takes whether one is set.
"""

from __future__ import annotations

from ..text import one_line, plain_text


def github_too_old_said(auth):
    """What follows gh's own words when it could not report its state: a gh too old for --json, the only one that
    fails it."""
    fix = (
        "Install gh from GitHub's apt repository (dev_tools_install_gh: true), or set github_auth: false to leave gh"
        " alone."
        if auth
        else "This project leaves gh alone there (github_auth: false)."
    )
    return f"`gh auth status --json` needs gh 2.81.0 or newer. {fix}"


def github_login_report(facts):
    """How gh is signed in, as {"line", "left"}: the line login_report.yml says, and whether NEXT STEPS records it - a
    gh too old to say where gh is the role's business, a device-flow login without the workflow scope, a sign-out that
    nothing in this run ends. A command that ends the line has no full stop after it: one would be pasted along."""
    auth = facts["auth"]
    how = {
        "pat": "with the personal access token this project put there"
        if facts["is_ours"]
        else "with a personal access token of your own",
        "oauth": "through a device-flow login",
        "env": "through a GH_TOKEN or GITHUB_TOKEN in its environment, which this role leaves alone",
        "other": "in a way this role does not manage",
    }
    out = "not signed in to GitHub, so only public repositories are cloned and git push fails there."
    both = facts["token_wanted"] and facts["token_set"]
    if not facts["gh_found"]:
        line = "gh is not installed (dev_tools_install_gh), so there is nothing to sign in."
    elif facts["status_rc"] != 0:
        line = f"gh could not report its login state ({facts['status_said'] or 'no output'}); " + github_too_old_said(
            auth
        )
    elif facts["signed_in"]:
        line = (
            f"{'signed in' if facts['login_done'] else 'already signed in'} to GitHub as {facts['account'] or '?'}"
            f" {how[facts['kind']]}."
        )
        if facts["lacks_workflow"]:
            line += (
                " Its token lacks the workflow scope, so a push that changes .github/workflows/ is refused; add it"
                f" with: ssh -t {facts['user']}@{facts['host']} gh auth refresh -h github.com -s workflow"
            )
    elif not auth:
        line = "not signed in to GitHub, and this project leaves gh alone there (github_auth: false)."
    elif both and facts["role_follows"]:
        line = f"{out} vault_github_token is set, and the role signs gh in with it next, in this run."
    elif both:
        line = (
            f"{out} vault_github_token is set, so configuration signs gh in with it, or says what stops it: "
            + facts["configure_command"]
        )
    elif facts["auth_method"] == "token":
        line = f"{out} github_auth_method is token, so set vault_github_token, then run {facts['configure_command']}"
    else:
        line = (
            f"{out} Run {facts['login_command']} to sign in by the device flow; that run clones the private"
            " repositories too."
        )
    left = facts["gh_found"] and (
        (facts["status_rc"] != 0 and auth)
        or facts["lacks_workflow"]
        or (not facts["signed_in"] and auth and not (both and facts["role_follows"]))
    )
    return {"line": line, "left": bool(left)}


# The cases that leave something to do whatever else holds.
TAILSCALE_OPEN = frozenset(
    {
        "check-no-package",
        "check-daemon-down",
        "connecting",
        "would-join",
        "page-pending",
        "expired",
        "refused",
        "NeedsMachineAuth",
        "Stopped",
    }
)


def _tailscale_case(facts):
    """Which of the report's lines applies: a case of its own, else the backend's state."""
    backend, check = facts["backend"], facts["check_mode"]
    needs_login = backend == "NeedsLogin"
    if not facts["ours"]:
        return "not-ours"
    if facts["check_without_package"]:
        return "check-no-package"
    if check and not facts["daemon_up"]:
        return "check-daemon-down"
    if backend in ("NoState", "Starting"):
        return "connecting"
    if check and facts["first_join"] and facts["key_wanted"] and not facts["key_refused_locally"]:
        return "would-join"
    if needs_login and facts["auth_url"]:
        return "page-pending"
    if needs_login and facts["joined"]:
        return "expired"
    if (
        needs_login
        and facts["key_wanted"]
        and not facts["key_held_back"]
        and (facts["join_ran"] or facts["key_refused_locally"])
    ):
        return "refused"
    return backend


def _tailscale_refused(facts):
    configure, or_browser = facts["configure_command"], ""
    if facts["auth_method"] != "key":
        or_browser = f", or sign in by the browser: {facts['login_command']}"
    why = facts["refused_why"]
    if facts["key_refused_locally"]:
        return (
            f"Tailscale refused vault_tailscale_auth_key ({why}): an OAuth client secret needs the tags it may apply"
            f" in tailscale_advertise_tags. Set them and run {configure}"
        )
    if why:
        return (
            f"Tailscale refused vault_tailscale_auth_key ({why}) - it has expired, been used up or been revoked. Put"
            f" a new one in the vault (reusable, pre-approved, tagged) and run {configure}{or_browser}"
        )
    said = one_line(facts["join_stderr"], None) or "no reason given"
    return f"Tailscale did not join with vault_tailscale_auth_key ({said}). {configure} tries again{or_browser}"


def _tailscale_expired(facts):
    if facts["expired_elsewhere"]:
        return (
            "signed out of Tailscale (its key expired). It was signed in outside this project, so it is left as it"
            " is: sign it in again on the VM, the way it was signed in."
        )
    if facts["auth_method"] != "key":
        return f"signed out of Tailscale (its key expired). {facts['login_command']} signs it in again."
    tag = f", remove its {facts['joined_tag']} tag in Proxmox" if facts["joined_before"] else ""
    return (
        "signed out of Tailscale (its key expired), and tailscale_auth_method key never signs a VM in by the browser"
        f" nor sends the key to a node that had a login: run sudo tailscale logout on it{tag}, then"
        f" {facts['configure_command']} joins it with the key."
    )


def _tailscale_needs_login(facts):
    configure, login, method = facts["configure_command"], facts["login_command"], facts["auth_method"]
    if facts["sign_in_follows"] and method != "key":
        return "Tailscale is installed; just deploy signs it in next, in this run."
    if method == "key" and facts["key_held_back"]:
        return (
            "Tailscale is installed but not signed in, and vault_tailscale_auth_key is not sent again to a VM that"
            f" joined before; with tailscale_auth_method auto, {login} signs it in from your browser."
        )
    if method == "key":
        return (
            f"Tailscale is installed but not signed in. {configure} joins it with vault_tailscale_auth_key"
            " (tailscale_auth_method: key)."
        )
    after = "."
    if facts["key_held_back"]:
        after = "; vault_tailscale_auth_key is not sent again to a VM that joined before."
    elif method == "auto":
        after = "; vault_tailscale_auth_key joins it on the next just deploy --only configure."
    return f"Tailscale is installed but not signed in. {login} signs it in from your browser{after}"


def _tailscale_line(case, facts):
    """One line: how the VM is on Tailscale, and the command that does the next thing where there is one. What the VM
    reports - its name, its tailnet - goes through plain_text, since the VM's user chooses it. The tailnet is said so
    that a login someone else approved first shows by a name the user knows; not under a control server of the user's
    own, where only its admin approves a login, and Headscale sends its own constant ("headscale")."""
    vm, backend = facts["name"], facts["backend"]
    shown = plain_text(facts["dns_name"] or facts["self_name"] or facts["hostname"])
    tailnet = (
        f"in the tailnet {plain_text(facts['tailnet'])} " if facts["tailnet"] and not facts["login_server"] else ""
    )
    where = f"{tailnet}as {shown} ({facts['ipv4'] or 'no IPv4 address yet'})"
    configure, login, admin = facts["configure_command"], facts["login_command"], facts["admin_page"]
    # Check mode's alone: just status reads no run in check mode, and names no such command.
    again = facts.get("reconfigure_command") or f"just deploy {vm} --only configure"
    if case == "check-no-package":
        return f"Tailscale is not installed, and check mode installs nothing; {again} installs it."
    if case == "check-daemon-down":
        return f"tailscaled is not running, and check mode starts nothing; {again} starts it."
    if case == "not-ours":
        said = (
            f"on Tailscale {where}; installed"
            if backend == "Running"
            else f"Tailscale is installed ({backend if facts['daemon_up'] else 'its daemon is not running'})"
        )
        return f"{said} outside this project, so it is left as it is."
    if case == "connecting":
        return (
            f"Tailscale is still connecting ({backend}) after {facts['settle_seconds']} seconds, so nothing was"
            f" changed. {configure} reads it again."
        )
    if case == "Running":
        parts = [
            ("joined Tailscale " if facts["joined_now"] else "on Tailscale ")
            + where
            + (
                "."
                if facts["login_ours"]
                else "; signed in outside this project, so its settings are left as they are."
            )
        ]
        if facts["name_taken"]:
            parts.append(
                f"A device named {facts['hostname']} is already in your tailnet, likely the VM this one replaced:"
                f" delete it at {admin}, then rename this one to {facts['hostname']} there."
            )
        if facts["set_changed"] and not facts["from_login"]:
            parts.append(f"Changed: tailscale {' '.join(facts['set_cmd'])}.")
        elif facts["check_mode"] and facts["login_ours"] and facts["set_flags"]:
            parts.append(f"Check mode: tailscale set {' '.join(facts['set_flags'])} would run.")
        if facts["tags_missing"]:
            parts.append(
                f"It lacks {', '.join(facts['tags_missing'])}, which tailscale_advertise_tags asks for: set its tags"
                f" in the admin console (Machines, {shown.split('.')[0]}, Edit ACL tags)."
            )
        return " ".join(parts)
    if case == "NeedsMachineAuth":
        return (
            f"signed in to Tailscale and waiting for an admin to approve it at {admin}; it connects by itself once"
            " approved."
        )
    if case == "Stopped":
        return "Tailscale is down (tailscale down on the VM). sudo tailscale up there brings it back."
    if case == "expired":
        return _tailscale_expired(facts)
    if case == "refused":
        return _tailscale_refused(facts)
    if case == "would-join":
        return f"Tailscale would join with vault_tailscale_auth_key; check mode sends nothing. {configure} joins it."
    if case == "page-pending":
        return (
            f"not signed in to Tailscale yet - the page stays valid for 7 days and {vm} joins by itself once you"
            f" approve it; {login} gives a fresh page."
        )
    if case == "NeedsLogin":
        return _tailscale_needs_login(facts)
    return f"Tailscale is {backend}."


def tailscale_report(facts):
    """How the VM is on Tailscale, as {"case", "line", "left"}. ``left`` is whether NEXT STEPS records the line: a page
    waiting, a login expired, a key refused, a node stopped or still connecting, an approval or a sign-in to come, a
    name taken or tags missing; never a node that runs as it should, or a Tailscale this role leaves alone."""
    case = _tailscale_case(facts)
    left = (
        case in TAILSCALE_OPEN
        or (case == "NeedsLogin" and not (facts["sign_in_follows"] and facts["auth_method"] != "key"))
        or (case == "Running" and bool(facts["name_taken"] or facts["tags_missing"]))
    )
    return {"case": case, "line": _tailscale_line(case, facts), "left": left}
