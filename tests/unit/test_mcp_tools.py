"""The MCP server's tools are the justfile's recipes: each tool's schema made from its recipe's parameters, and every
recipe and option either a tool's or left out for a reason named in lib/claude_vm/mcp/recipes.py.

A recipe or an option added to the justfile without a decision fails here, as test_docs.py fails on a stale reference
page. So does a justfile setting read with env() that the server does not give on just's command line, where the
environment the server was started in - a project's settings reach it - would otherwise set it.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from claude_vm.mcp import recipes, server
from claude_vm.mcp.config import Config, parse

REPO = Path(__file__).resolve().parents[2]
JUST = shutil.which("just")
CONFIG = Config(checkout=REPO, python=sys.executable, just=JUST, uv="uv", path="/usr/bin:/bin", home="/")


@pytest.fixture(scope="module")
def dumped() -> dict:
    return recipes.dump(CONFIG, {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")})


def test_every_recipe_has_a_tool_or_a_reason_and_not_both(dumped):
    tools = {tool.recipe for tool in recipes.TOOLS.values()}
    assert tools & set(recipes.NO_TOOL) == set()
    assert tools | set(recipes.NO_TOOL) == set(dumped["recipes"])
    assert "vault" in recipes.NO_TOOL


def test_every_option_of_a_tool_s_recipe_is_a_property_fixed_or_left_out_for_a_reason(dumped):
    for name, tool in recipes.TOOLS.items():
        recipe = dumped["recipes"][tool.recipe]
        properties = recipes.schema(recipe, name)["properties"]
        for parameter in recipe["parameters"]:
            prop = recipes.prop(parameter)
            taken = prop in properties, prop in tool.fixed, (name, prop) in recipes.LEFT_OUT
            assert sum(taken) == 1, (name, prop, taken)
    assert {tool for tool, _ in recipes.LEFT_OUT} <= set(recipes.TOOLS)


def test_each_schema_is_valid_its_patterns_just_s_anchored_and_its_descriptions_the_help(dumped):
    for name, tool in recipes.TOOLS.items():
        recipe = dumped["recipes"][tool.recipe]
        schema = recipes.schema(recipe, name)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        for parameter in recipe["parameters"]:
            prop = schema["properties"].get(recipes.prop(parameter))
            if prop is None:
                continue
            assert prop["description"] == recipes.HELP.get((name, recipes.prop(parameter)), parameter["help"])
            where = prop["items"] if prop["type"] == "array" else prop
            for pattern in parameter.get("pattern") or []:
                assert where.get("pattern") == f"^(?:{pattern})$"
                re.compile(where["pattern"])


def test_deploy_takes_a_list_of_repositories_as_just_does(dumped):
    validator = Draft202012Validator(recipes.schema(dumped["recipes"]["deploy"], "deploy"))
    assert validator.is_valid({"names": ["a"], "repos": "one,two.2,three-3"})
    for bad in ("owner/name", ".claude", "a,,b", "a b", ",a"):
        assert not validator.is_valid({"names": ["a"], "repos": bad}), bad


def test_no_property_offers_the_default_vm_the_server_refuses(dumped):
    for (name, prop), _ in recipes.HELP.items():
        assert prop in recipes.schema(dumped["recipes"][recipes.TOOLS[name].recipe], name)["properties"], (name, prop)
    for name, tool in recipes.TOOLS.items():
        for prop, spec in recipes.schema(dumped["recipes"][tool.recipe], name)["properties"].items():
            assert "agent-warehouse-default" not in spec["description"], (name, prop)


def test_a_schema_takes_what_just_takes_and_refuses_what_its_pattern_refuses(dumped):
    status = Draft202012Validator(recipes.schema(dumped["recipes"]["status"], "status"))
    assert status.is_valid({"names": ["alpha", "beta"], "only": "vms,work"})
    assert not status.is_valid({"names": ["-x"]})
    assert not status.is_valid({"only": "vms;id"})
    assert not status.is_valid({"json": True})


def test_no_setting_is_a_property(dumped):
    assignments = {name.lower() for name in dumped["assignments"]}
    for name, tool in recipes.TOOLS.items():
        assert set(recipes.schema(dumped["recipes"][tool.recipe], name)["properties"]) & assignments == set()


def test_every_setting_the_environment_could_set_is_given_on_the_command_line_at_the_justfile_s_default(dumped):
    # A setting just reads with env() takes its value from the environment unless the command line gives one.
    read = {name for name, value in dumped["assignments"].items() if value["value"][:2] == ["call", "env"]}
    assert read == set(recipes.SETTINGS)
    given = recipes.settings(CONFIG)
    for name in recipes.SETTINGS:
        if name in ("PYTHON", "UV"):
            continue
        default = subprocess.run(
            [JUST, "--justfile", str(REPO / "justfile"), "--evaluate", name],
            cwd=REPO,
            env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert given[name] == default, name
    assert given["PYTHON"] == CONFIG.python and given["UV"] == CONFIG.uv


def test_argv_is_options_in_their_long_form_then_the_words_each_one_word(dumped):
    recipe = dumped["recipes"]["status"]
    argv = recipes.command(CONFIG, recipe, "status", {"names": ["alpha", "$(id)", "a b"], "only": "-vvv"})
    assert argv[:3] == [JUST, "--justfile", str(REPO / "justfile")]
    assert argv[3 : 3 + len(recipes.SETTINGS)] == [f"{k}={v}" for k, v in recipes.settings(CONFIG).items()]
    assert argv[3 + len(recipes.SETTINGS) :] == ["status", "--only=-vvv", "--json", "alpha", "$(id)", "a b"]
    # A word that starts with - would be an option to just, whatever its pattern: refused here.
    with pytest.raises(ValueError, match="never start with -"):
        recipes.command(CONFIG, recipe, "status", {"names": ["alpha", "--only=configure"]})
    # Nothing given, nothing said; the server's fixed options still are.
    assert recipes.command(CONFIG, recipe, "status", {"names": [], "only": ""})[-2:] == ["status", "--json"]


def test_just_refuses_a_value_its_pattern_refuses_as_at_a_terminal(dumped, tmp_path):
    for arguments in ({"only": "-vvv"}, {"names": ["$(id)"]}, {"names": ["a b"]}):
        argv = recipes.command(CONFIG, dumped["recipes"]["status"], "status", arguments)
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
        out = subprocess.run(argv, cwd=REPO, env=env, capture_output=True)
        assert out.returncode != 0 and b"does not match pattern" in out.stderr, arguments


def test_each_tool_s_function_takes_exactly_its_schema_s_properties(dumped):
    srv = server.Server(CONFIG, "", {})
    srv._dumped = dumped
    for tool in server.tools(srv):
        fn_params = set(tool.fn_metadata.arg_model.model_fields)
        assert fn_params == set(tool.parameters["properties"]), tool.name


def test_the_instructions_stay_short():
    # Every Claude Code session on the machine carries them.
    assert len(server.INSTRUCTIONS) < 800


def test_a_command_line_just_mcp_never_writes_is_a_refusal_not_an_exit():
    for argv in ([], ["--checkout=/x"], ["--help"], [f"--checkout={REPO}", "--unknown=1"]):
        config, refusal = parse(argv)
        assert config is None and refusal
    given = [f"--checkout={REPO}", f"--python={sys.executable}", f"--just={JUST}", "--uv=uv", "--path=/usr/bin"]
    assert parse(given)[0] is None
    config, refusal = parse([*given, "--home=/home/dev"])
    assert refusal == "" and config.checkout == REPO and config.attended == "" and config.data_home == ""


def test_the_server_reads_nothing_of_its_environment_but_through_the_allow_list():
    # Its configuration is its arguments: the environment is read once, by main, and handed to environment.py's allow
    # list for each run; nothing else in the package reads a variable.
    package = REPO / "lib" / "claude_vm" / "mcp"
    readers = sorted(p.name for p in package.glob("*.py") if re.search(r"os\.environ|getenv", p.read_text()))
    assert readers == ["environment.py", "server.py"]
    source = (package / "server.py").read_text()
    assert source.count("os.environ") == 1 and "os.environ" in inspect.getsource(server.main)


# ------------------------------------------------------------------------------------------- more than one host ----
# The current host is the terminal's, as the default VM is: no tool changes it, a deploy that may create a VM names the
# host it goes on, and a named VM that exists is found where its alias says it is, as just host --json says.


def test_the_hosts_tool_lists_and_never_switches(dumped):
    assert recipes.TOOLS["hosts"].recipe == "host" and recipes.TOOLS["hosts"].fixed == {"json": True}
    assert recipes.schema(recipes.recipe_of(dumped, "hosts"), "hosts")["properties"] == {}


def test_with_more_than_one_host_a_deploy_that_may_create_a_vm_names_its_host():
    assert server.deploy_refusal(["newvm"], "", False, "", unplaced=True).startswith(
        "this checkout has more than one Proxmox host, and deploy may create a VM: name the host"
    )
    assert server.deploy_refusal(["newvm"], "", False, "nuc", unplaced=True) == ""
    # A VM its alias places, configuration, a dry run: no host to name.
    assert server.deploy_refusal(["alpha"], "", False, "", unplaced=False) == ""
    assert server.deploy_refusal(["newvm"], "configure", False, "", unplaced=True) == ""
    assert server.deploy_refusal(["newvm"], "", True, "", unplaced=True) == ""
    # Without names, the default VM, which is never implied here, whatever the hosts.
    assert server.deploy_refusal([], "", False, "nuc").startswith("deploy creates the VMs named, and none were")


def test_a_run_is_against_the_host_the_read_names_else_the_current_one_and_none_with_one_host():
    # The server asks just host --json, --for the names and --host the call gives (test_mcp_hosts.py runs it): this is
    # what it takes from the answer.
    pve, nuc = {"name": "pve", "first": True}, {"name": "nuc", "first": False}
    one = {"current_host": "pve", "hosts": [pve], "where": {"host": "pve", "refusal": ""}}
    assert server.run_host(one) == "" and not server.many_hosts(one)
    two = {"current_host": "nuc", "hosts": [pve, nuc]}
    assert server.many_hosts(two)
    assert server.run_host({**two, "where": None}) == "nuc"
    assert server.run_host({**two, "where": {"host": "pve", "routed": True, "refusal": ""}}) == "pve"
    # Names on two hosts: none, and the run refuses them itself.
    assert server.run_host({**two, "where": {"host": "", "refusal": "the VMs named are on 2 hosts"}}) == ""
    # Unread: none, a word in a title or a dialog.
    assert server.run_host({}) == ""
    # A second host without a first is more than one host too.
    assert server.many_hosts({"current_host": None, "hosts": [nuc]})
