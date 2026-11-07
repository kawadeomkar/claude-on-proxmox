"""Write tests/unit/durations.json, how long each unit test took, from runs' JUnit reports: what --shard balances by.

Each CI shard's run with a report, on a machine of CI's four vCPUs (or `taskset -c` to four), then this over them
all, from the checkout:

    export PYTEST_XDIST_AUTO_NUM_WORKERS=4
    pytest tests/unit -n auto --dist loadgroup --shard <i>/6 --junitxml=shard-<i>.xml -o junit_family=xunit1
    python3 tools/unit_durations.py shard-*.xml earlier-*.xml

A test's time depends on what runs beside it - a playbook run over many stand-in hosts slows most beside others like
it, by half or more - so the file is written from the shards as CI runs them, under the grouping it balances, and
from more than one run of them, each test's longest time counting: from one whole run's times, a shard once took the
four longest runs at once, and ran 40% longer than the rest (2026-11). A module-scoped
fixture's playbook run is counted in the first test that uses it, which every test sharing it goes with. A test the
file lacks counts as the mean (tests/unit/conftest.py), so a new one never breaks a shard; write it again when the
shards drift apart, as each CI shard's time shows.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DURATIONS = Path(__file__).resolve().parents[1] / "tests" / "unit" / "durations.json"


def durations(report: Path) -> dict[str, float]:
    """Each test's time by its pytest node ID. xdist's loadgroup adds @<group> to a grouped test's name."""
    out = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        path = case.get("file")
        module = path.removesuffix(".py").replace("/", ".")
        classname = case.get("classname", "")
        owner = classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
        name = re.sub(r"@\w+$", "", case.get("name"))
        out["::".join(part for part in (path, owner, name) if part)] = round(float(case.get("time")), 2)
    return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: tools/unit_durations.py <each shard's JUnit report, written with -o junit_family=xunit1>...")
    found: dict[str, float] = {}
    for report in sys.argv[1:]:
        each = durations(Path(report))
        if not each:
            sys.exit(f"no test cases in {report}")
        # Each test's longest time of the reports: a run's time is mostly what ran beside it.
        for test, seconds in each.items():
            found[test] = max(found.get(test, 0.0), seconds)
    DURATIONS.write_text(json.dumps(found, indent=0, sort_keys=True) + "\n")
    print(f"{len(found)} tests, {sum(found.values()) / 60:.1f} minutes, into {DURATIONS}")


if __name__ == "__main__":
    main()
