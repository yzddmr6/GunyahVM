# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Control subprocesses must load the same private runtime as their live VM."""

from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from gunyahvm import core


@pytest.mark.parametrize("guest", ["linux", "windows11"])
def test_reconnect_loads_private_shared_library(tmp_path, monkeypatch, guest):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("a native C compiler is required for the loader regression")
    private = tmp_path / "private"
    private.mkdir()
    (tmp_path / "library.c").write_text("int value(void) { return 42; }\n")
    (tmp_path / "client.c").write_text(
        '#include <stdio.h>\nint value(void);\n'
        'int main(void) { if (value()!=42) return 1; puts("display_reconnected"); return 0; }\n')
    library = private / "libgvmfixture.so"
    binary = tmp_path / "control"
    subprocess.run([compiler, "-shared", "-fPIC", str(tmp_path / "library.c"), "-o", str(library)], check=True)
    subprocess.run([compiler, str(tmp_path / "client.c"), "-L" + str(private), "-lgvmfixture", "-o", str(binary)], check=True)
    assert subprocess.run([str(binary)], env={"PATH": "/usr/bin:/bin"}, capture_output=True).returncode == 127
    runtime = {"components": {
        "crosvm": {"features": ["wayland-reconnect"], "files": {"binary": str(binary)}},
        "renderer": {"files": {"library": str(library)}},
    }}
    endpoint = tmp_path / "wayland"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(endpoint))
        monkeypatch.setattr(core, "status", lambda *a: {"display_socket": str(endpoint), "run_dir": str(tmp_path)})
        monkeypatch.setattr(core, "vmm_active", lambda *a: True)
        monkeypatch.setattr(core.components, "load", lambda: runtime)
        core.reconnect_display(tmp_path, {"os": guest}, endpoint)
