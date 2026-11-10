# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parents[2] / "src/gunyahvm/guest-control.py"


def agent():
    spec = importlib.util.spec_from_file_location("guest_control", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_run(report, code):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        output = report if isinstance(report, str) else json.dumps(report)
        return SimpleNamespace(returncode=code, stdout=output if "--format" in argv else "")

    return run, calls


def test_failed_setup_is_reported_instead_of_blocking_readiness():
    run, calls = fake_run({
        "status": "error", "extended_status": "error - done",
        "errors": ["('package_update_upgrade_install', ProcessExecutionError('dpkg returned 1'))"],
        "recoverable_errors": {"WARNING": ["snapd: system does not fully support snapd"]},
    }, 1)
    report = agent().setup_report(run)
    assert calls[0] == ["cloud-init", "status", "--wait"]
    assert report["status"] == "error - done"
    assert report["problems"][0].startswith("('package_update_upgrade_install'")
    assert report["problems"][1] == "WARNING: snapd: system does not fully support snapd"


def test_clean_and_unreadable_results():
    run, _calls = fake_run({"status": "done", "extended_status": "done", "errors": [],
                            "recoverable_errors": {}}, 0)
    assert agent().setup_report(run) == {"status": "done", "problems": []}
    run, _calls = fake_run("not json", 1)
    assert agent().setup_report(run) == {"status": "error", "problems": []}
    run, _calls = fake_run({"errors": ["x" * 2000] * 20}, 1)
    report = agent().setup_report(run)
    assert len(report["problems"]) == 8 and all(len(p) == 400 for p in report["problems"])
