"""Write tests/unit/durations.json, how long each unit test took, from a run's JUnit report: what --shard balances by.

A run with a report, then this, from the checkout:

    pytest tests/unit -n auto --dist loadgroup --junitxml=report.xml -o junit_family=xunit1
    python3 tools/unit_durations.py report.xml

A parallel run's times are inflated by its workers' contention, but in proportion, which is all a split needs; a
module-scoped fixture's playbook run is counted in the first test that uses it, which its whole module goes with. A
test the file lacks counts as the mean (tests/unit/conftest.py), so a new one never breaks a shard; write it again when
the shards drift apart, as each CI shard's time shows.
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
    if len(sys.argv) != 2:
        sys.exit("usage: tools/unit_durations.py <a run's JUnit report, written with -o junit_family=xunit1>")
    found = durations(Path(sys.argv[1]))
    if not found:
        sys.exit(f"no test cases in {sys.argv[1]}")
    DURATIONS.write_text(json.dumps(found, indent=0, sort_keys=True) + "\n")
    print(f"{len(found)} tests, {sum(found.values()) / 60:.1f} minutes, into {DURATIONS}")


if __name__ == "__main__":
    main()
