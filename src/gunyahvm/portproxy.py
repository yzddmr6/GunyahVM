# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Per-VM loopback-only userspace port forwards."""

import select
import socket
import threading
import time


def _pipe(a, b, done):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        s.close()
    done(a, b)


class TcpForward:
    def __init__(self, port, target, log=print, bind="127.0.0.1"):
        self.target, self.log, self.stop = target, log, False
        self.connections = set()
        self.lock = threading.Lock()
        fam = socket.AF_INET6 if ":" in bind else socket.AF_INET
        self.ls = socket.socket(fam, socket.SOCK_STREAM)
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if fam == socket.AF_INET6:
            self.ls.setsockopt(
                socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0
            )  # v4 and v6 clients
        self.ls.bind((bind, port))
        self.ls.listen(16)
        self.ls.settimeout(0.5)
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def loop(self):
        while not self.stop:
            try:
                c, peer = self.ls.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                self.connections.add(c)
            try:
                up = socket.create_connection(self.target, timeout=10)
                up.settimeout(None)
                c.settimeout(None)
            except OSError as e:
                self.log(
                    "forward %s -> %s:%d: %s"
                    % (peer[0], self.target[0], self.target[1], e)
                )
                c.close()
                self.finished(c)
                continue
            with self.lock:
                if self.stop:
                    c.close()
                    up.close()
                    break
                self.connections.add(up)
            threading.Thread(
                target=_pipe, args=(c, up, self.finished), daemon=True
            ).start()
            threading.Thread(
                target=_pipe, args=(up, c, self.finished), daemon=True
            ).start()

    def finished(self, *sockets):
        with self.lock:
            for sock in sockets:
                self.connections.discard(sock)

    def close(self):
        self.stop = True
        self.ls.close()
        with self.lock:
            for sock in self.connections:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            self.connections.clear()


class UdpForward:
    IDLE = 60

    def __init__(self, port, target, log=print, bind="127.0.0.1"):
        self.target, self.log, self.stop = target, log, False
        fam = socket.AF_INET6 if ":" in bind else socket.AF_INET
        self.ls = socket.socket(fam, socket.SOCK_DGRAM)
        if fam == socket.AF_INET6:
            self.ls.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        self.ls.bind((bind, port))
        self.peers = {}  # client address -> [upstream socket, last use]
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def loop(self):
        while not self.stop:
            ups = {v[0]: k for k, v in self.peers.items()}
            try:
                r, _, _ = select.select([self.ls] + list(ups), [], [], 0.5)
            except (OSError, ValueError):
                break
            now = time.time()
            for s in r:
                if s is self.ls:
                    try:
                        d, peer = self.ls.recvfrom(65536)
                    except OSError:
                        return
                    if peer not in self.peers:
                        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        u.connect(self.target)
                        self.peers[peer] = [u, now]
                    self.peers[peer][1] = now
                    try:
                        self.peers[peer][0].send(d)
                    except OSError:
                        pass
                else:
                    peer = ups[s]
                    try:
                        d = s.recv(65536)
                        self.ls.sendto(d, peer)
                        self.peers[peer][1] = now
                    except OSError:
                        pass
            for peer, (u, last) in list(self.peers.items()):
                if now - last > self.IDLE:
                    u.close()
                    del self.peers[peer]

    def close(self):
        self.stop = True
        self.ls.close()
        self.t.join(timeout=1)
        for u, _ in list(self.peers.values()):
            u.close()
        self.peers.clear()


def start(forwards, guest_ip, log=print):
    """Start one forward per entry; returns the list of running forwards (failed ones are logged)."""
    out = []
    for f in forwards:
        cls = TcpForward if f["proto"] == "tcp" else UdpForward
        try:
            out.append(cls(f["host"], (guest_ip, f["guest"]), log))
            log(
                "forward %s %d -> %s:%d" % (f["proto"], f["host"], guest_ip, f["guest"])
            )
        except OSError as e:
            log("forward %s %d: %s" % (f["proto"], f["host"], e))
    return out


def stop(running):
    for p in running:
        p.close()
