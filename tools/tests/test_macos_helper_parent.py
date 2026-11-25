# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS helper VM ends with the creation or upgrade that started it."""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
from gunyahvm import macos_helper


def test_a_helper_whose_starter_is_gone_refuses_to_run():
    with pytest.raises(ValueError, match="has ended"):
        macos_helper.die_with_parent(os.getppid() + 1)


def test_the_helper_and_its_vmm_die_when_the_starter_is_killed(tmp_path):
    """starter -> helper (die_with_parent) -> vmm (preexec die_with_parent), as in macos_helper.main."""
    pids = tmp_path / "pids"
    helper = textwrap.dedent(f"""
        import os, subprocess, sys, time
        sys.path.insert(0, {str(macos_helper.__file__.rsplit('/gunyahvm/', 1)[0])!r})
        from gunyahvm import macos_helper
        macos_helper.die_with_parent(int(sys.argv[1]))
        me = os.getpid()
        vmm = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               preexec_fn=lambda: macos_helper.die_with_parent(me))
        open({str(pids)!r}, "w").write(f"{{me}} {{vmm.pid}}")
        time.sleep(60)
    """)
    starter = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", {helper!r}, str(os.getpid())], start_new_session=True)
        time.sleep(60)
    """)])
    deadline = time.monotonic() + 20
    while not pids.exists() or not pids.read_text():
        assert time.monotonic() < deadline
        time.sleep(0.1)
    alive = [int(p) for p in pids.read_text().split()]
    starter.send_signal(signal.SIGKILL)
    starter.wait()
    deadline = time.monotonic() + 10
    while alive and time.monotonic() < deadline:
        alive = [p for p in alive if os.path.exists(f"/proc/{p}") and
                 open(f"/proc/{p}/stat").read().split(") ")[1][0] != "Z"]
        time.sleep(0.1)
    assert not alive
