# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Creation must reject missing access before any image or VM preparation."""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from gunyahvm import cli


@pytest.fixture
def permitted_host(monkeypatch):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1234)
    monkeypatch.setattr(cli.os, "getuid", lambda: 1234)
    original = Path.is_char_device
    monkeypatch.setattr(
        Path,
        "is_char_device",
        lambda p: True if p == Path("/dev/gunyah") else original(p),
    )
    monkeypatch.setattr(
        cli.os,
        "access",
        lambda path, mode: (
            str(path) == "/dev/gunyah" and mode == (cli.os.R_OK | cli.os.W_OK)
        ),
    )
    pid = cli.os.getpid()
    started = (
        Path(f"/proc/{pid}/task/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    )
    calls = []

    def checker(argv, **kwargs):
        assert argv[0] == "pkcheck"
        assert argv[-2:] == ["--process", f"{pid},{started},1234"]
        assert "--allow-user-interaction" not in argv
        assert kwargs["timeout"] == 10
        calls.append(argv[2])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", checker)
    return calls


def forbid_preparation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized create reached preparation")

    monkeypatch.setattr(cli, "select_key", forbidden)
    monkeypatch.setattr(cli, "output", forbidden)
    monkeypatch.setattr(cli, "create_windows", forbidden)
    monkeypatch.setattr(cli.host, "defaults", forbidden)


@pytest.mark.parametrize("guest", ["linux", "windows11"])
@pytest.mark.parametrize("node,access", [(False, False), (True, False)])
def test_device_denial_precedes_polkit_and_all_preparation(
    tmp_path, monkeypatch, permitted_host, guest, node, access
):
    monkeypatch.setattr(Path, "is_char_device", lambda p: node)
    monkeypatch.setattr(cli.os, "access", lambda *a: access)
    forbid_preparation(monkeypatch)
    with pytest.raises(cli.Failure, match="/dev/gunyah"):
        cli.create(SimpleNamespace(os=guest, store=tmp_path / "store"))
    assert not permitted_host
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("guest", ["linux", "windows11"])
@pytest.mark.parametrize("denied", ["run", "net"])
def test_polkit_denial_precedes_all_preparation(
    tmp_path, monkeypatch, permitted_host, guest, denied
):
    seen = []

    def checker(argv, **kwargs):
        seen.append(argv[2])
        return subprocess.CompletedProcess(
            argv, int(argv[2].endswith("." + denied)), "", ""
        )

    monkeypatch.setattr(cli.subprocess, "run", checker)
    forbid_preparation(monkeypatch)
    with pytest.raises(cli.Failure, match="polkit did not authorize"):
        cli.create(SimpleNamespace(os=guest, store=tmp_path / "store"))
    assert seen[-1].endswith("." + denied)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("pkcheck missing"), subprocess.TimeoutExpired("pkcheck", 10)],
)
def test_checker_failure_does_not_prepare_images(
    tmp_path, monkeypatch, permitted_host, error
):
    def checker(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli.subprocess, "run", checker)
    forbid_preparation(monkeypatch)
    with pytest.raises(cli.Failure, match="cannot check polkit"):
        cli.create(SimpleNamespace(os="linux", store=tmp_path / "store"))
    assert not list(tmp_path.iterdir())


def test_missing_process_identity_stops_before_polkit(monkeypatch, permitted_host):
    original = Path.read_text

    def read(path, *args, **kwargs):
        if str(path).startswith("/proc/") and path.name == "stat":
            raise FileNotFoundError("no procfs")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(cli.Failure, match="procfs"):
        cli.create_preflight()
    assert not permitted_host


def test_authorized_linux_reaches_download_after_both_actions(
    tmp_path, monkeypatch, permitted_host
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "select_key",
        lambda path: (tmp_path / "key.pub", tmp_path / "key", "ssh-ed25519 example"),
    )
    args = SimpleNamespace(
        os="linux",
        cpus=2,
        memory_mib=1024,
        disk_gib=8,
        boot_dir=None,
        image=None,
        ssh_key=None,
        store=tmp_path / "store",
        name="guest",
        subnet=None,
        username="ubuntu",
        vhost_net=False,
        release="20261016",
    )

    class ReachedDownload(Exception):
        pass

    def image_command(argv):
        assert Path(argv[1]).name == "image.py" and argv[2] == "create"
        assert permitted_host == [
            "io.github.yzddmr6.gunyahvm.run",
            "io.github.yzddmr6.gunyahvm.net",
        ]
        raise ReachedDownload

    monkeypatch.setattr(cli, "output", image_command)
    with pytest.raises(ReachedDownload):
        cli.create(args)


def test_authorized_windows_preflight_precedes_dispatch(monkeypatch, permitted_host):
    def prepare(args):
        assert permitted_host == [
            "io.github.yzddmr6.gunyahvm.run",
            "io.github.yzddmr6.gunyahvm.net",
        ]
        return "prepared"

    monkeypatch.setattr(cli, "create_windows", prepare)
    assert cli.create(SimpleNamespace(os="windows11")) == "prepared"
