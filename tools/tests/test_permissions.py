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
    code += "\nfor (const action of ['load','net','run','firmware']) console.log(rule({id:'io.github.yzddmr6.gunyahvm.'+action},subject));"
    code += "\nconsole.log(rule({id:'org.freedesktop.policykit.exec'},subject));"
    assert subprocess.check_output(["node", "-e", code], text=True).splitlines() == [
        expected
    ] * 4 + ["other"]


def driver_state(monkeypatch, m, loaded, calls):
    """/dev/gunyah exists when loaded; load() records itself and creates it."""
    original = Path.is_char_device
    state = {"loaded": loaded}
    monkeypatch.setattr(
        m.Path, "is_char_device",
        lambda p: state["loaded"] if p == Path("/dev/gunyah") else original(p),
    )

    def load():
        assert m.os.geteuid() == 0, "drivers load before root is dropped"
        calls.append(("load",))
        state["loaded"] = True

    monkeypatch.setattr(m, "load", load)


@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("guest", ["other", "shared"])
def test_run_helper_drops_all_ids_before_fixed_exec(monkeypatch, guest, loaded):
    from gunyahvm import privileged as m

    calls = []
    monkeypatch.setattr(
        m.sys,
        "argv",
        ["gunyahvm-helper", "run", "/home/vmuser/.local/share/gunyahvm", "linux", "", guest],
    )
    driver_state(monkeypatch, m, loaded, calls)
    monkeypatch.setattr(m, "prepare_shared_memory", lambda: calls.append(("memory",)))
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
    # The first start after boot loads the drivers; a later start does not load again.
    loads = [("load",)] if not loaded else []
    assert calls[:len(loads)] == loads
    rest = calls[len(loads):]
    # Only a shared-memory guest's start prepares host memory, and it does so as root.
    assert (rest[0] == ("memory",)) == (guest == "shared")
    assert rest[1 if guest == "shared" else 0] == ("limit", (4096 * 1024**2,) * 2)
    assert ("load",) not in rest
    assert calls[-1][0] == "exec"


@pytest.mark.parametrize("loaded", [False, True])
def test_macos_assembly_loads_absent_drivers_before_dropping_root(monkeypatch, tmp_path, loaded):
    from gunyahvm import privileged as m

    calls = []
    home = tmp_path / "home"
    work = home / ".local/share/gunyahvm/.creating/macos"
    work.mkdir(parents=True)
    account = SimpleNamespace(pw_name="vmuser", pw_dir=str(home), pw_gid=1234, pw_uid=1234)
    driver_state(monkeypatch, m, loaded, calls)
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    fw = SimpleNamespace(summary=lambda: {"hypervisor": True})
    fw.require = lambda: fw
    monkeypatch.setattr(m.host, "firmware", lambda: fw)
    monkeypatch.setattr(m.host, "memory_budget", lambda: {"memlock_mib": 4096})
    monkeypatch.setattr(m.resource, "setrlimit", lambda key, value: calls.append(("limit",)))
    for name in ("chdir", "umask", "initgroups", "setresgid", "setresuid"):
        monkeypatch.setattr(m.os, name, lambda *a, n=name: calls.append((n,)))
    monkeypatch.setattr(m.os, "execve", lambda path, args, env: calls.append(("exec", args[-1])))
    m.assemble(account, str(work.resolve()))
    assert calls.count(("load",)) == (0 if loaded else 1)
    if not loaded:
        assert calls[0] == ("load",)
    assert calls.index(("limit",)) < calls.index(("setresuid",))
    assert calls[-1] == ("exec", str(work.resolve()))


def test_shared_memory_drops_clean_cache_then_compacts(monkeypatch):
    from gunyahvm import privileged as m

    written = []
    monkeypatch.setattr(m.Path, "write_text", lambda self, value: written.append((str(self), value)))
    m.prepare_shared_memory()
    assert written == [("/proc/sys/vm/drop_caches", "1"), ("/proc/sys/vm/compact_memory", "1")]


def test_run_helper_rejects_unknown_guest_kind(monkeypatch):
    from gunyahvm import privileged as m

    monkeypatch.setattr(m, "caller", lambda: SimpleNamespace(pw_uid=1234))
    with pytest.raises(ValueError, match="guest kind"):
        m.launch(SimpleNamespace(pw_uid=1234), "/tmp/store", "linux", "", "windows")


@pytest.mark.parametrize("caller", ["", "0", "-1", "nobody"])
def test_run_helper_rejects_missing_or_root_caller(monkeypatch, caller):
    from gunyahvm import privileged as m

    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        m.sys, "argv", ["gunyahvm-helper", "run", "/tmp/store", "linux", "", "other"]
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
