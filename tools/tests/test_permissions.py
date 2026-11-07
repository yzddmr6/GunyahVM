# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Authorization negative cases and the privileged launcher's drop boundary."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


def load(name):
    loader = importlib.machinery.SourceFileLoader(
        "test_" + name.replace("-", "_"), str(REPO / "libexec" / name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "local,active,seat,group,expected",
    [
        (True, True, "seat0", False, "yes"),
        (False, True, "", False, "no"),
        (True, False, "seat0", False, "no"),
        (True, True, "", False, "no"),
        (False, False, "", True, "yes"),
    ],
)
def test_polkit_seat_or_explicit_group(local, active, seat, group, expected):
    policy = (
        REPO / "packaging/usr/share/polkit-1/rules.d/50-gunyahvm.rules"
    ).read_text()
    code = (
        "let rule; const polkit={Result:{YES:'yes',NO:'no',NOT_HANDLED:'other'},addRule:f=>rule=f};\n"
        + policy
    )
    code += (
        "\nconst subject="
        + json.dumps(dict(user="vmuser", local=local, active=active, seat=seat))
        + "; subject.isInGroup=g=>g==='gunyah' && "
        + json.dumps(group)
        + ";"
    )
    code += "\nfor (const action of ['load','net','run']) console.log(rule({id:'io.github.yzddmr6.gunyahvm.'+action},subject));"
    code += "\nconsole.log(rule({id:'org.freedesktop.policykit.exec'},subject));"
    assert subprocess.check_output(["node", "-e", code], text=True).splitlines() == [
        expected
    ] * 3 + ["other"]


def test_run_helper_drops_all_ids_before_fixed_exec(monkeypatch):
    m = load("gunyahvm-run")
    calls = []
    monkeypatch.setattr(
        m.sys, "argv", ["gunyahvm-run", "/home/vmuser/.local/share/gunyahvm", "linux"]
    )
    monkeypatch.setenv("PKEXEC_UID", "1234")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted.so")
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.os, "readlink", lambda p: "mnt:[123]")
    monkeypatch.setattr(
        m.pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(
            pw_name="vmuser", pw_dir="/home/vmuser", pw_gid=1234
        ),
    )
    fw = SimpleNamespace(
        compatible=["vendor,board"], summary=lambda: {"hypervisor": True}
    )
    fw.require = lambda: fw
    monkeypatch.setattr(m.host, "firmware", lambda: fw)
    monkeypatch.setattr(m.host, "device_options", lambda c: {})
    monkeypatch.setattr(m.host, "memory_budget", lambda: {"memlock_mib": 4096})
    monkeypatch.setattr(
        m.resource, "setrlimit", lambda key, value: calls.append(("limit", value))
    )
    for name in ("chdir", "umask", "initgroups", "setresgid", "setresuid"):
        monkeypatch.setattr(m.os, name, lambda *a, n=name: calls.append((n, a)))

    def execve(path, args, env):
        assert calls[-2:] == [
            ("setresgid", (1234, 1234, 1234)),
            ("setresuid", (1234, 1234, 1234)),
        ]
        assert "PYTHONPATH" not in env and "LD_PRELOAD" not in env
        assert path == "/usr/bin/python3" and args[1] == "-I"
        assert args[2:4] == ["/usr/lib/gunyahvm/python/gunyahvm/cli.py", "_start"]
        calls.append(("exec", args))

    monkeypatch.setattr(m.os, "execve", execve)
    m.main()
    assert calls[0] == ("limit", (4096 * 1024**2,) * 2)
    assert calls[-1][0] == "exec"


@pytest.mark.parametrize("caller", ["", "0", "-1", "nobody"])
def test_run_helper_rejects_missing_or_root_caller(monkeypatch, caller):
    m = load("gunyahvm-run")
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.sys, "argv", ["gunyahvm-run", "/tmp/store", "linux"])
    monkeypatch.setenv("PKEXEC_UID", caller)
    with pytest.raises(ValueError, match="ordinary"):
        m.main()


def test_network_helper_rejects_public_subnet():
    result = subprocess.run(
        [
            "python3",
            str(REPO / "libexec/gunyahvm-net"),
            "up",
            "--vm-id",
            "11111111-1111-4111-8111-111111111111",
            "--subnet",
            "8.8.8.0/24",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "RFC1918" in result.stderr
