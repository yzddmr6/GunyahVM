# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Authorization negative cases and the privileged launcher's drop boundary."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


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
    from gunyahvm import privileged as m

    calls = []
    monkeypatch.setattr(
        m.sys,
        "argv",
        ["gunyahvm-helper", "run", "/home/vmuser/.local/share/gunyahvm", "linux", ""],
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
            pw_name="vmuser", pw_dir="/home/vmuser", pw_gid=1234, pw_uid=1234
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
        assert args[2:4] == ["/usr/lib/gunyahvm/python/gunyahvm/supervisor.py", "start"]
        calls.append(("exec", args))

    monkeypatch.setattr(m.os, "execve", execve)
    m.main()
    assert calls[0] == ("limit", (4096 * 1024**2,) * 2)
    assert calls[-1][0] == "exec"


@pytest.mark.parametrize("caller", ["", "0", "-1", "nobody"])
def test_run_helper_rejects_missing_or_root_caller(monkeypatch, caller):
    from gunyahvm import privileged as m

    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        m.sys, "argv", ["gunyahvm-helper", "run", "/tmp/store", "linux", ""]
    )
    monkeypatch.setenv("PKEXEC_UID", caller)
    with pytest.raises(ValueError, match="ordinary"):
        m.main()


def test_network_helper_rejects_public_subnet():
    from gunyahvm import privileged

    with pytest.raises(ValueError, match="RFC1918"):
        privileged.network_commands(
            1234, "vmuser", "11111111-1111-4111-8111-111111111111", "8.8.8.0/24"
        )


def test_cleanup_is_not_skipped_after_network_was_disabled(monkeypatch):
    from gunyahvm import privilege

    calls = []
    monkeypatch.setattr(privilege, "call", lambda *args: calls.append(args))
    cfg = {
        "network_mode": "off",
        "id": "11111111-1111-4111-8111-111111111111",
        "subnet": "10.203.1.0/24",
        "vhost_net": False,
    }
    privilege.network_action("up", cfg)
    assert not calls
    privilege.network_action("down", cfg)
    assert calls == [("net-down", cfg["id"], cfg["subnet"], "userspace")]
