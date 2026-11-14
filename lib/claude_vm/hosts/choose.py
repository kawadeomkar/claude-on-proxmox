"""Which host: a run's, from --host, a nested recipe's host, the VMs named and hosts/current, in that order; and the
hosts as just host lists them (docs/architecture/custom-code.md §7.14). Reads the layout's files and the managed SSH
config, never a vault, never the environment and never a password script, so whoever asks runs nothing that may ask a
person. Writes nothing."""

from __future__ import annotations

from .layout import Layout
from .model import SHARED_HOSTS, Choice, Host, HostList, HostRow, Note, Refused, Request, Where


def pick(layout: Layout, given: str = "", lenient: bool = False) -> Host | None:
    """The host given (--host, or a nested recipe's host=), else the one hosts/current names, else the first. None for
    a checkout with no host at all - before just init - which runs as it always did."""
    if given:
        return layout.named(given, "--host names", lenient)
    current = layout.current_name()
    if current:
        return layout.named(current, f"{layout.hosts_rel}/current names", lenient)
    first = layout.first()
    if first is None and layout.many():
        raise Refused(
            f"{SHARED_HOSTS} names no host, and {layout.hosts_rel}/current none of {', '.join(layout.folders())}: "
            "choose one with just host <name>"
        )
    return first


def choose(layout: Layout, request: Request) -> Choice:
    """The host the request is against, and why; Refused, with any note said before it, where the request names two."""
    given = request.host or request.nested
    if request.host and request.nested and request.host != request.nested:
        raise Refused(f"--host {request.host} in a run against {request.nested}: one host per run")
    many = layout.many()
    routed = False
    names = list(request.names)
    origins: dict[str, str] = {}
    notes: list[Note] = []
    if many and names:
        # A VM name is one host's, and its alias records which: a named VM is found where it is.
        origins = layout.origins(names)
        if given:
            elsewhere = {name: origin for name, origin in origins.items() if origin != given}
            if elsewhere:
                # A name two hosts each have, made before a name was one host's: the run reaches the VM of the name on
                # the host given, the alias stays the other's, and a run that would write it is refused
                # (tasks/vm_names.yml).
                said = ", ".join(f"{name} is {origin}'s" for name, origin in sorted(elsewhere.items()))
                notes.append(
                    Note(
                        level="warning",
                        text=f"{said}, as its SSH alias records, and this run is against {given}: it reaches the VM "
                        f"of the name on {given}. A VM name belongs to one host, so the alias stays where it is, and a "
                        "run here that would write it - just deploy, just code - is refused: give one of them another "
                        "name",
                    )
                )
        else:
            on = sorted(set(origins.values()))
            if len(on) > 1:
                said = "; ".join(
                    f"{', '.join(sorted(n for n, o in origins.items() if o == origin))} on {origin}" for origin in on
                )
                raise Refused(
                    f"the VMs named are on {len(on)} hosts ({said}), as their SSH aliases record, and a run reaches "
                    "one host: run each host's on its own, with --host"
                )
            given, routed = (on[0], True) if on else ("", False)
    try:
        # One host, and none asked for: nothing to choose, and nothing read - the run is as it always was.
        host = pick(layout, given, request.lenient) if given or many or layout.current_name() else None
        current = ""
        if routed:
            # Said where it is not the host the person would take it for: the run's title and a deletion's question
            # name it too.
            try:
                found = pick(layout)
            except Refused:
                found = None
            current = found.name if found else ""
    except Refused as refused:
        raise Refused(str(refused), notes=tuple(notes)) from refused
    return Choice(
        host=host,
        many=many,
        routed=routed,
        current=current,
        origins=tuple((name, origins[name]) for name in names if name in origins),
        unplaced=tuple(name for name in names if name not in origins) if many else (),
        notes=tuple(notes),
    )


def where(layout: Layout, request: Request) -> Where:
    """Which host the run the request describes would reach, and what it would say on choosing it: choose, made
    public, no vault opened. A refusal is data here, as a layout's problem is, so a page can say why before a run."""
    try:
        choice = choose(layout, request)
    except Refused as refused:
        notes = tuple(note.text for note in refused.notes)
        return Where(host="", routed=False, unplaced=(), notes=notes, refusal=str(refused))
    return Where(
        host=choice.host.name if choice.host else "",
        routed=choice.routed,
        unplaced=choice.unplaced,
        notes=tuple(note.text for note in choice.notes),
        refusal="",
    )


def listing(layout: Layout, asked: Where | None = None) -> HostList:
    """Every host from its files alone, no vault opened: what just host shows, and its --json, with ``asked``, the run
    a caller asked about."""
    try:
        every = layout.every()
        current = pick(layout) if every else None
    except Refused as refused:
        try:
            every = layout.every()
        except Refused:
            every = []
        current = None
        said = str(refused)
    else:
        said = ""
    defaults = layout.defaults()
    rows = []
    for host in every:
        settings = host.shown()
        for _, setting in host.kind.shown if host.kind else ():
            if settings.get(setting) in (None, ""):
                settings[setting] = defaults.get(setting) if host.first else None
        rows.append(
            HostRow(
                name=host.name,
                address=host.address,
                folder=host.folder,
                settings=settings,
                host_kind=host.kind.key if host.kind else None,
                first=host.first,
                current=current is not None and host.name == current.name,
                problem=host.problem,
            )
        )
    return HostList(current_host=current.name if current else None, hosts=tuple(rows), problem=said, where=asked)
