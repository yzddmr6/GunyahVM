# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""The Windows VNC port check ignores closing connections but not listeners.

Run: python3 -m pytest tools/tests/test_windows_vm_display.py
"""

import socket

import pytest

from gunyahvm import windows_vm

def listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server


def test_active_listener_is_rejected():
    with listener() as server:
        with pytest.raises(ValueError):
            windows_vm.display_available({"vnc_port": server.getsockname()[1]})


def test_closing_connection_from_previous_run_is_ignored():
    server = listener()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    # The VMM exits first: its side closes while the viewer still holds the
    # connection open, leaving the server port in FIN-WAIT-2.
    accepted.close()
    server.close()
    try:
        windows_vm.display_available({"vnc_port": port})
    finally:
        client.close()
