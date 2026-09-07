"""Shared fixtures for testing custom Ansible modules without a live API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from ansible.module_utils import basic
from ansible.module_utils.testing import patch_module_args

ROOT = Path(__file__).resolve().parents[2]


class AnsibleExitJson(Exception):
    """Raised instead of sys.exit() when a module calls exit_json."""


class AnsibleFailJson(Exception):
    """Raised instead of sys.exit() when a module calls fail_json."""


def _exit_json(*args, **kwargs):
    kwargs.setdefault("changed", False)
    raise AnsibleExitJson(kwargs)


def _fail_json(*args, **kwargs):
    kwargs["failed"] = True
    raise AnsibleFailJson(kwargs)


@pytest.fixture
def patch_module(monkeypatch):
    """Make AnsibleModule raise instead of exiting so tests can inspect results."""
    monkeypatch.setattr(basic.AnsibleModule, "exit_json", _exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", _fail_json)


def run_main(main, args):
    """Run a module's main() with the given args; return its exit_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleExitJson) as exc:
        main()
    return exc.value.args[0]


def fail_main(main, args):
    """Run a module's main() with the given args; return its fail_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleFailJson) as exc:
        main()
    return exc.value.args[0]


def load_module(relative_path):
    """Import a module file from the repo by path (roles/*/library/*.py)."""
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module
