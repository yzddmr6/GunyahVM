#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Ubuntu adaptation of iceland-gvm 7ae498c2 gvm/gvm.sh; see NOTICE.
"""Shared ordinary-user VM API and supervisor. No frontend or GTK dependencies."""

import argparse
from dataclasses import dataclass
from .errors import Failure
from .storage import atomic_json, read_json
from .seed import seed
from . import config, capabilities, crosvmargs, lifecycle, portproxy
from .privilege import network_action
from .sleepguard import SleepGuard
from .console import replay

import fcntl
import ipaddress
import json
import os
import pwd
import re
import resource
import select
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
THP = Path("/sys/kernel/mm/transparent_hugepage/shmem_enabled")
from gunyahvm import windows_vm
from gunyahvm import host


def dma_pool_mib(cfg):
    return 256 if cfg.get("os") == "windows11" else 128


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def output(argv):
    return run(argv, capture_output=True, text=True).stdout.strip()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def vm_name(value):
    if not re.fullmatch("[a-z][a-z0-9-]{0,31}", value):
        raise argparse.ArgumentTypeError(
            "name: lowercase letter, then at most 31 letters/digits/hyphens"
        )
    return value


def guest_username(value):
    if value == "root" or not re.fullmatch("[a-z_][a-z0-9_-]{0,31}", value):
        raise argparse.ArgumentTypeError("use a valid non-root guest username")
    return value


def owned_vm(store, name, uid, inputs=True):
    home = Path(pwd.getpwuid(uid).pw_dir).resolve()
    store = store.resolve()
    vm = store / "vms" / vm_name(name)
    if not store.is_relative_to(home):
        raise Failure("VM storage must be inside the owner home directory")
    if vm.is_symlink() or not vm.is_dir() or vm.stat().st_uid != uid:
        raise Failure("VM missing, symlinked, or owned by another user")
    cfg = config.validate(read_json(vm / "config.json"))
    if inputs and cfg["os"] == "linux" and not cfg.get("ssh_public_key"):
        raise Failure("VM is missing its cloud-init public key")
    if cfg["uid"] != uid or cfg["name"] != name:
        raise Failure("VM identity mismatch")
    required = ["disk.raw", "seed.iso"]
    if cfg["guest_payload"] or not cfg.get("boot_managed"):
        required.append("boot/cmdline")
    if not cfg["guest_payload"] and not cfg.get("boot_managed"):
        required += ["boot/Image", "boot/initrd.img"]
    if cfg.get("os") == "windows11":
        required = ["disk.raw", "firmware.fd"]
        if cfg["install_media"]:
            required += [
                "install/boot.raw",
                "install/install.raw",
                "install/drivers.raw",
            ]
    for name in required if inputs else []:
        p = vm / name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(vm):
            raise Failure("invalid VM input " + name)
    return vm, cfg


def process_ticks(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def spawn_vmm(argv, out, err, env):
    # When device processes outlive a stop, crosvm's exit cleanup SIGKILLs its
    # whole process group. Give it its own group so that cannot reach the
    # supervisor, which must still record how the VM stopped.
    return subprocess.Popen(
        argv, stdout=out, stderr=err, env=env, close_fds=True, start_new_session=True
    )


def monitor_active(state):
    pid = state.get("supervisor_pid")
    return bool(
        pid
        and state.get("host_boot_id") == boot_id()
        and bool(state.get("supervisor_ticks"))
        and process_ticks(pid) == state.get("supervisor_ticks")
    )


def vmm_active(state):
    pid = state.get("pid")
    return bool(
        pid
        and state.get("host_boot_id") == boot_id()
        and bool(state.get("vmm_ticks"))
        and process_ticks(pid) == state.get("vmm_ticks")
    )


def select_key(explicit):
    candidates = (
        [Path(explicit).expanduser()]
        if explicit
        else [
            Path.home() / ".ssh" / n
            for n in ["id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"]
        ]
    )
    key = next((p.resolve() for p in candidates if p.is_file()), None)
    if not key:
        raise Failure("no SSH public key: use --ssh-key ~/.ssh/your_key.pub")
    run(["ssh-keygen", "-l", "-f", key], stdout=subprocess.DEVNULL)
    lines = [s for s in key.read_text().splitlines() if s.strip()]
    if len(lines) != 1 or not re.match(r"^(ssh-|ecdsa-sha2-)", lines[0]):
        raise Failure("supply one OpenSSH public key")
    identity = (
        key.with_suffix("")
        if key.suffix == ".pub" and key.with_suffix("").is_file()
        else key
    )
    return key, identity, lines[0]


def create(args):
    create_preflight()
    vm_name(args.name)
    if args.os == "windows11":
        return create_windows(args)
    args.cpus = host.defaults("linux")["cpus"] if args.cpus is None else args.cpus
    args.memory_mib = (
        host.defaults("linux")["memory_mib"]
        if args.memory_mib is None
        else args.memory_mib
    )
    args.disk_gib = 8 if args.disk_gib is None else args.disk_gib
    if os.geteuid() == 0:
        raise Failure("create as the ordinary owner, not root")
    if args.boot_dir and not args.image:
        raise Failure("--boot-dir requires an explicit matching --image")
    if args.ssh_key:
        key, identity, keytext = select_key(args.ssh_key)
    else:
        keydir = args.store.expanduser().resolve() / "keys"
        if not keydir.is_relative_to(Path.home().resolve()):
            raise Failure("VM storage must be inside the owner home directory")
        keydir.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = keydir / args.name
        if not identity.exists():
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", identity])
        key, identity, keytext = select_key(str(identity) + ".pub")
    store = args.store.expanduser().resolve()
    if not store.is_relative_to(Path.home().resolve()):
        raise Failure("VM storage must be inside the owner home directory")
    store.mkdir(parents=True, exist_ok=True)
    (store / "vms").mkdir(exist_ok=True)
    vm = store / "vms" / args.name
    if vm.exists():
        raise Failure("VM already exists")
    image = Path(args.image).expanduser().resolve() if args.image else None
    size = args.disk_gib * 1024**3
    if (
        not 1 <= args.cpus <= 64
        or not 512 <= args.memory_mib <= host.memory_budget()["guest_max_mib"]
        or args.disk_gib < 1
    ):
        raise Failure("invalid resource size")
    info = (
        json.loads(output(["qemu-img", "info", "--output=json", image]))
        if image
        else None
    )
    if info and info.get("backing-filename"):
        raise Failure("use an independent cloud image, not a backing-chain overlay")
    if info and size < info["virtual-size"]:
        raise Failure("requested disk is smaller than the source image")
    ident = str(uuid.uuid4())
    subnet = ipaddress.ip_network(
        args.subnet or f"10.203.{int(ident[:8], 16) % 250 + 1}.0/24"
    )
    if subnet.version != 4 or subnet.prefixlen != 24 or not subnet.is_private:
        raise Failure("subnet must be a private IPv4 /24")
    cfg = {
        "schema": config.SCHEMA,
        "id": ident,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "name": args.name,
        "username": args.username,
        "cpus": args.cpus,
        "memory_mib": args.memory_mib,
        "disk_gib": args.disk_gib,
        "source_image": str(image) if image else "Ubuntu 26.04 release-" + args.release,
        "boot_managed": not bool(args.boot_dir),
        "ssh_identity": str(identity),
        "ssh_public_key": keytext,
        "subnet": str(subnet),
        "vhost_net": args.vhost_net,
    }
    cfg = new_config(cfg, args)
    try:
        if image:
            vm.mkdir(mode=0o700)
            run(
                [
                    "qemu-img",
                    "convert",
                    "-f",
                    info["format"],
                    "-O",
                    "raw",
                    "-S",
                    "4k",
                    image,
                    vm / "disk.raw",
                ]
            )
            run(["qemu-img", "resize", "-f", "raw", vm / "disk.raw", str(size)])
        else:
            output(
                [
                    sys.executable,
                    BUNDLE / "image.py",
                    "create",
                    "--output",
                    vm,
                    "--size",
                    str(size),
                    "--username",
                    args.username,
                    "--ssh-public-key",
                    key,
                    "--cache-dir",
                    store / "cache",
                    "--release",
                    args.release,
                    "--mirror",
                    getattr(args, "mirror", "official"),
                    "--instance-id",
                    ident,
                    *(["--runtime-kernel"] if cfg["guest_payload"] else []),
                ]
            )
            shutil.rmtree(vm / "seed")
            (vm / "seed.iso").unlink()
        if args.boot_dir:
            source = Path(args.boot_dir).expanduser().resolve()
            (vm / "boot").mkdir()
            for dest, pattern in [("Image", "Image*"), ("initrd.img", "initrd*")]:
                matches = [p for p in source.glob(pattern) if p.is_file()]
                if len(matches) != 1:
                    raise Failure(f"{source}: need exactly one {pattern}")
                shutil.copyfile(matches[0], vm / "boot" / dest)
            cmdline = (
                (source / "cmdline").read_text().strip()
                if (source / "cmdline").exists()
                else "root=LABEL=cloudimg-rootfs ro console=tty1 console=ttyAMA0"
            )
            (vm / "boot/cmdline").write_text(cmdline + "\n")
        elif image:
            output(
                [
                    sys.executable,
                    BUNDLE / "image.py",
                    "refresh",
                    vm,
                    *(["--runtime-kernel"] if cfg["guest_payload"] else []),
                ]
            )
        if not cfg["guest_payload"]:
            with (vm / "boot/Image").open("rb") as kernel:
                if kernel.read(64)[56:60] != b"ARM\x64":
                    raise Failure("guest Image is not an unwrapped ARM64 Image")
        seed(vm, cfg, keytext)
        atomic_json(vm / "config.json", cfg)
        atomic_json(
            vm / "state.json",
            {
                "state": "created",
                "exit_code": None,
                "exit_reason": None,
                "clean_shutdown": None,
            },
        )
    except BaseException:
        if vm.exists():
            shutil.rmtree(vm)
        raise
    print("created " + str(vm))
    if args.start:
        start_vm(store, args.name)


def require_device_access():
    if not Path("/dev/gunyah").is_char_device():
        raise Failure("/dev/gunyah absent; run gunyahvm load separately")
    if not os.access("/dev/gunyah", os.R_OK | os.W_OK):
        raise Failure(
            "no read/write access to /dev/gunyah; require an active local seat ACL or gunyah group membership"
        )


def create_preflight():
    """Reject unauthorized creation before keys, storage or media preparation."""
    if os.geteuid() == 0:
        raise Failure("create as the ordinary owner, not root")
    require_device_access()
    pid = os.getpid()
    try:
        # The thread-group leader's kernel start time also stays authoritative
        # under user-mode emulators that synthesize /proc/PID/stat.
        fields = (
            Path(f"/proc/{pid}/task/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        )
        started = int(fields[19])
    except (OSError, ValueError, IndexError) as exc:
        raise Failure(
            "cannot identify the current process for polkit; check procfs before creating a VM"
        ) from exc
    subject = f"{pid},{started},{os.getuid()}"
    for operation in ("run", "net"):
        action = "io.github.yzddmr6.gunyahvm." + operation
        try:
            result = subprocess.run(
                ["pkcheck", "--action-id", action, "--process", subject],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Failure(
                f"cannot check polkit authorization for {action}: {exc}; no image preparation started"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or f"pkcheck exit {result.returncode}"
            raise Failure(
                f"polkit did not authorize {action}: {detail}; require an active local seat "
                "or gunyah group membership and a new login; check the polkit service and installed rules. "
                "No image preparation started."
            )


def access_limits(cfg, check_vhost=True):
    if os.geteuid() == 0:
        raise Failure("VMM must run as the ordinary VM owner, never root")
    capabilities = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if any(
        int(capabilities.get(k, "0").strip(), 16) & (1 << 12)
        for k in ["CapEff", "CapPrm", "CapAmb"]
    ):
        raise Failure("VMM launcher must not hold CAP_NET_ADMIN")
    require_device_access()
    required = (cfg["memory_mib"] + dma_pool_mib(cfg)) * 1024**2
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if soft != resource.RLIM_INFINITY and soft < required:
        raise Failure(
            f"RLIMIT_MEMLOCK soft={soft} bytes < required {required}; "
            "run gunyahvm start so the authorized launcher supplies its finite limit"
        )
    if (
        check_vhost
        and cfg["vhost_net"]
        and not os.access("/dev/vhost-net", os.R_OK | os.W_OK)
    ):
        raise Failure(
            "no user read/write access to /dev/vhost-net; optional acceleration needs its own device rule/session"
        )
    return soft, hard


def host_mem():
    return {
        k: int(v.split()[0])
        for line in Path("/proc/meminfo").read_text().splitlines()
        for k, v in [line.split(":", 1)]
    }


def send_poweroff(rd):
    fd = os.open(rd / "agent.in", os.O_WRONLY | os.O_NONBLOCK)
    try:
        # Record intent before the guest can acknowledge and exit.
        (rd / "poweroff.requested").touch(mode=0o600)
        os.write(fd, b"poweroff\n")
    finally:
        os.close(fd)


def exit_reason(returncode):
    # crosvm src/main.rs CommandStatus, with --extended-status enabled.
    if returncode < 0:
        return "signal:" + signal.Signals(-returncode).name
    return {
        0: "stop",
        32: "reset",
        33: "crash",
        34: "guest_panic",
        35: "invalid_args",
        36: "watchdog_reset",
    }.get(returncode, "error")


def host_snapshot():
    snapshot = capabilities.probe()
    if not snapshot.firmware.get("capability_ids") and os.environ.get(
        "GUNYAHVM_HOST_INFO"
    ):
        from . import devices

        snapshot.firmware = json.loads(os.environ["GUNYAHVM_HOST_INFO"])
        snapshot.profile = devices.match(snapshot.firmware.get("compatible", []))
        snapshot.readings = capabilities.sensors(snapshot.profile)
    return snapshot


def supervise_run(vm, cfg, lock_fd):
    from . import windows_agent

    lock = os.fdopen(lock_fd, "a")
    rd = vm / "runs" / str(time.time_ns())
    rd.mkdir(parents=True)
    state = lifecycle.transition(
        {"state": "created"},
        "starting",
        started_at=time.time(),
        host_boot_id=boot_id(),
        supervisor_pid=os.getpid(),
        supervisor_ticks=process_ticks(os.getpid()),
        network_cleanup_needed=cfg["network_mode"] == "nat",
        run_dir=str(rd),
        exit_code=None,
        exit_reason=None,
        clean_shutdown=False,
        stop_requested=False,
        poweroff_acknowledged=False,
        forced_stop=False,
        ipv4=None,
    )
    proc, fifo_fds, forwards = None, [], []
    requested, agent_offset, agent_buffer = False, 0, ""
    next_guard, next_windows = 0, 0
    guard_deadline = None

    def record():
        atomic_json(vm / "state.json", state)

    def terminate(signum, frame):
        (vm / "guard.requested").write_text("Supervisor termination")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    record()
    try:
        soft, hard = access_limits(cfg)
        state.update(vmm_uid=os.getuid(), memlock_soft=soft, memlock_hard=hard)
        snapshot = host_snapshot()
        snapshot.require(cfg)
        if cfg.get("boot_managed") and not cfg["guest_payload"]:
            prepared = json.loads(
                output([sys.executable, BUNDLE / "image.py", "refresh", vm])
            )
            state["kernel_release"] = prepared["kernel_release"]
        if cfg["os"] == "linux":
            seed(vm, cfg, cfg["ssh_public_key"])
        for name in ("serial.log", "agent.log", "crosvm.stdout", "crosvm.stderr"):
            (rd / name).touch(mode=0o600)
        for name in ("agent.in", "console.in"):
            os.mkfifo(rd / name, 0o600)
            fifo_fds.append(os.open(rd / name, os.O_RDWR | os.O_NONBLOCK))
        argv, extra_env = crosvmargs.build(
            vm, cfg, rd, snapshot, os.environ.get("WAYLAND_DISPLAY")
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("LD_", "CROSVM_", "GUNYAHVM_"))
        }
        env.update(extra_env)
        if cfg["display"]["vnc"]:
            windows_vm.display_available({"vnc_port": cfg["display"]["vnc_port"]})
        if (vm / "guard.requested").exists():
            raise Failure(
                "Start cancelled because the host is preparing to sleep or stop"
            )
        with (
            (rd / "crosvm.stdout").open("w") as out,
            (rd / "crosvm.stderr").open("w") as err,
        ):
            proc = spawn_vmm(argv, out, err, env)
        state = lifecycle.transition(
            state, "running", pid=proc.pid, vmm_ticks=process_ticks(proc.pid)
        )
        record()

        def read_agent():
            nonlocal agent_offset, agent_buffer, forwards, requested
            with (rd / "agent.log").open() as stream:
                stream.seek(agent_offset)
                agent_buffer += stream.read(65536)
                agent_offset = stream.tell()
            if len(agent_buffer) > 65536:
                raise Failure("oversized guest control message")
            while "\n" in agent_buffer:
                line, agent_buffer = agent_buffer.split("\n", 1)
                if line.startswith("GUNYAHVM_READY "):
                    guest = json.loads(line.split(" ", 1)[1])
                    net = ipaddress.ip_network(cfg["subnet"])
                    ip = next(
                        (
                            address
                            for address in guest["ipv4"]
                            if ipaddress.ip_address(address) in net
                            and ipaddress.ip_address(address)
                            not in (
                                net.network_address,
                                net.network_address + 1,
                                net.broadcast_address,
                            )
                        ),
                        None,
                    )
                    if (
                        not state.get("guest_ready")
                        and (vm / "guard.requested").exists()
                    ):
                        requested = (
                            False  # retry on a readiness transition, not on a timer
                        )
                    state.update(
                        guest_ready=True, guest_boot_id=guest["boot_id"], ipv4=ip
                    )
                    if ip and cfg["forwards"] and not forwards:
                        forwards = portproxy.start(cfg["forwards"], ip)
                    record()
                elif line.startswith("GUNYAHVM_ERROR "):
                    state.update(guest_ready=False, error=line.split(" ", 1)[1])
                    record()
                elif line.strip() == "GUNYAHVM_POWEROFF_REQUEST":
                    state["poweroff_acknowledged"] = True
                    record()

        while proc.poll() is None:
            now = time.monotonic()
            read_agent()
            if now >= next_guard:
                why = capabilities.guard_reasons(
                    snapshot.profile,
                    capabilities.sensors(snapshot.profile),
                    running=True,
                )
                if why:
                    (vm / "guard.requested").write_text("; ".join(why))
                next_guard = now + 5
            if (
                cfg["os"] == "windows11"
                and not cfg["install_media"]
                and now >= next_windows
            ):
                try:
                    ip = windows_agent.guest_ip(cfg)
                    if ip:
                        ready = windows_agent.command(
                            vm, cfg, ip, "echo GUNYAHVM_READY"
                        )
                        if (
                            not state.get("guest_ready")
                            and ready.returncode == 0
                            and "GUNYAHVM_READY" in ready.stdout
                            and (vm / "guard.requested").exists()
                        ):
                            requested = False
                        state.update(
                            ipv4=ip,
                            guest_ready=ready.returncode == 0
                            and "GUNYAHVM_READY" in ready.stdout,
                        )
                        if cfg["forwards"] and not forwards:
                            forwards = portproxy.start(cfg["forwards"], ip)
                        record()
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    state["agent_error"] = str(exc)
                next_windows = time.monotonic() + 10
            guard = (vm / "guard.requested").exists()
            if guard and guard_deadline is None:
                guard_deadline = now + (180 if cfg["os"] == "windows11" else 120)
                state["guard_reason"] = (vm / "guard.requested").read_text()
            force = (vm / "force-stop.requested").exists() or (
                guard_deadline is not None and now >= guard_deadline
            )
            if force:
                state.update(forced_stop=True, clean_shutdown=False)
                state = lifecycle.transition(state, "stopping")
                record()
                # A bounded crash stop is necessary before the logind deadline.
                # Preserve failed status; never label it a clean shutdown.
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                break
            if not requested and ((vm / "stop.requested").exists() or guard):
                requested = True
                state["stop_requested"] = True
                state.pop("stop_error", None)
                try:
                    if cfg["os"] == "windows11":
                        state["poweroff_acknowledged"] = windows_agent.poweroff(
                            vm, cfg, state.get("ipv4")
                        )
                    else:
                        if not state.get("guest_ready"):
                            raise Failure(
                                "Guest agent is not ready; inspect console or use stop --force"
                            )
                        send_poweroff(rd)
                    state = lifecycle.transition(state, "stopping")
                except (
                    OSError,
                    ValueError,
                    Failure,
                    subprocess.SubprocessError,
                ) as exc:
                    state["stop_error"] = str(exc)
                    if not guard:
                        (vm / "stop.requested").unlink(missing_ok=True)
                        requested = False
                record()
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        read_agent()
        state.update(
            lifecycle.outcome(
                proc.returncode,
                state["forced_stop"],
                state["stop_requested"],
                state["poweroff_acknowledged"],
            )
        )
        state["exit_reason"] = exit_reason(proc.returncode)
    except Exception as exc:
        state.update(state="failed", error=str(exc), clean_shutdown=False)
        if proc is not None and proc.poll() is None:
            # Supervisor failure must not strand a VMM with no guard/inhibitor.
            state["forced_stop"] = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        if proc is not None:
            state.update(
                exit_code=proc.returncode, exit_reason=exit_reason(proc.returncode)
            )
    finally:
        portproxy.stop(forwards)
        for fd in fifo_fds:
            os.close(fd)
        for name in ("agent.in", "console.in", "control.sock"):
            (rd / name).unlink(missing_ok=True)
        state["finished_at"] = time.time()
        record()
        lock.close()
    return 0 if state["state"] == "stopped" else 1


def start_vm(store, name):
    from .privilege import start

    return start(store, name)


def start_vm_authorized(store, name):
    vm, cfg = owned_vm(store, name, os.getuid())
    snapshot = host_snapshot()
    snapshot.require(cfg)
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if wayland:
        socket_path = Path(wayland)
        if not socket_path.is_socket() or socket_path.stat().st_uid != os.getuid():
            raise Failure("Wayland socket is absent or belongs to another user")
    existing = read_json(vm / "state.json")
    if monitor_active(existing) or vmm_active(existing):
        raise Failure(
            "VM is already running or its VMM survived an interrupted supervisor"
        )
    if os.uname().machine != "aarch64":
        raise Failure("start requires the aarch64 Gunyah host")
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            if (
                entry.stat().st_uid == os.getuid()
                and (entry / "exe").resolve(strict=True).name == "crosvm"
            ):
                raise Failure("another crosvm owned by this user is still active")
        except (OSError, RuntimeError) as ex:
            if isinstance(ex, Failure):
                raise
    access_limits(cfg, check_vhost=False)
    if cfg["cpus"] > len(os.sched_getaffinity(0)):
        raise Failure("requested vCPUs exceed online CPUs")
    if THP.exists() and re.search(r"\[(never|deny)\]", THP.read_text()):
        raise Failure(
            "host shmem THP is disabled; administrator/test setup must enable it"
        )
    required = (
        cfg["memory_mib"] + dma_pool_mib(cfg) + host.memory_budget()["host_reserve_mib"]
    )
    if host_mem()["MemAvailable"] // 1024 < required:
        raise Failure(f"need MemAvailable >= {required} MiB")
    runtime = Path(f"/run/user/{os.getuid()}") / "gunyahvm"
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / "host.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("another VM owned by this user is running")
        (vm / "force-stop.requested").unlink(missing_ok=True)
        (vm / "stop.requested").unlink(missing_ok=True)
        (vm / "guard.requested").unlink(missing_ok=True)
        previous = read_json(vm / "state.json")
        if previous.get("network_cleanup_needed"):
            network_action("down", cfg)
        previous["network_cleanup_needed"] = cfg["network_mode"] == "nat"
        atomic_json(vm / "state.json", previous)
        network_action("up", cfg)
        try:
            access_limits(cfg)
            with (vm / "supervisor.log").open("a") as log:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        str(BUNDLE / "supervisor.py"),
                        "run",
                        str(store.resolve()),
                        name,
                        str(lock.fileno()),
                    ],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    pass_fds=(lock.fileno(),),
                )
        except BaseException:
            network_action("down", cfg)
            raise
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = read_json(vm / "state.json")
            if (
                state.get("supervisor_pid") == proc.pid
                and state["state"] == "running"
                and proc.poll() is None
            ):
                print(
                    "started "
                    + name
                    + " as uid "
                    + str(os.getuid())
                    + (
                        "; use view for Windows installation/OOBE"
                        if cfg.get("os") == "windows11"
                        else "; use list/console for cloud-init"
                    )
                )
                return
            if proc.poll() is not None:
                network_action("down", cfg)
                raise Failure("startup failed; see state.json and supervisor.log")
            time.sleep(0.1)
        print("VM is still starting as the ordinary user; inspect list/console")


def stop_vm(vm, cfg, force=False):
    state = read_json(vm / "state.json")
    if monitor_active(state):
        (vm / ("force-stop.requested" if force else "stop.requested")).touch(mode=0o600)
        deadline = time.monotonic() + (225 if cfg["os"] == "windows11" else 165)
        while monitor_active(state) and time.monotonic() < deadline:
            time.sleep(0.2)
            state = read_json(vm / "state.json")
            if state.get("stop_error") and not force:
                raise Failure(state["stop_error"])
        if monitor_active(state):
            raise Failure(
                "Guest did not stop; resources retained. Use stop --force explicitly if needed"
            )
    elif vmm_active(state):
        if not force:
            raise Failure(
                "Supervisor interrupted; VMM is still alive. Use stop --force"
            )
        os.killpg(state["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while vmm_active(state) and time.monotonic() < deadline:
            time.sleep(0.1)
        if vmm_active(state):
            os.killpg(state["pid"], signal.SIGKILL)
        deadline = time.monotonic() + 5
        while vmm_active(state) and time.monotonic() < deadline:
            time.sleep(0.1)
        if vmm_active(state):
            raise Failure("VMM remains alive; network retained")
        state.update(state="failed", forced_stop=True, clean_shutdown=False)
    if state.get("network_cleanup_needed"):
        network_action("down", cfg)
        state["network_cleanup_needed"] = False
    if state["state"] in ("starting", "running", "stopping", "restarting"):
        state.update(state="interrupted", clean_shutdown=False)
    atomic_json(vm / "state.json", state)
    if not force and state.get("supervisor_pid") and not state.get("clean_shutdown"):
        raise Failure("VMM exited without confirmed clean shutdown")
    return state


def status(vm, cfg):
    state = read_json(vm / "state.json")
    if cfg.get("os") == "windows11":
        state["display_uri"] = f"vnc://127.0.0.1:{cfg['display']['vnc_port']}"
        state["install_media"] = cfg["install_media"]
    if state["state"] in (
        "starting",
        "running",
        "stopping",
        "restarting",
    ) and not monitor_active(state):
        state["state"] = "interrupted"
    return state


def console(vm, cfg, interactive):
    state = status(vm, cfg)
    rd = Path(state.get("run_dir", ""))
    if not state.get("run_dir") or not (rd / "serial.log").exists():
        raise Failure("no serial log yet")
    if not interactive:
        os.execvp("tail", ["tail", "-n", "80", "-F", str(rd / "serial.log")])
    import termios
    import tty

    if not sys.stdin.isatty():
        raise Failure("interactive console needs a terminal")
    old = termios.tcgetattr(sys.stdin)
    fd = os.open(rd / "console.in", os.O_WRONLY | os.O_NONBLOCK)
    print(
        "Ctrl-] detaches; guest serial login remains password-locked until configured from SSH."
    )
    try:
        tty.setraw(sys.stdin)
        with (rd / "serial.log").open("rb") as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 256 * 1024))
            while True:
                data = log.read()
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    data = os.read(sys.stdin.fileno(), 1024)
                    if not data or b"\x1d" in data:
                        break
                    os.write(fd, data)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        os.close(fd)


def create_windows(args):
    if os.geteuid() == 0:
        raise Failure("create as the ordinary owner")
    if args.image or args.boot_dir or args.ssh_key or args.vhost_net:
        raise Failure("Windows uses --iso and userspace virtio-net")
    memory = (
        host.defaults("windows11")["memory_mib"]
        if args.memory_mib is None
        else args.memory_mib
    )
    cpus = host.defaults("windows11")["cpus"] if args.cpus is None else args.cpus
    disk = 64 if args.disk_gib is None else args.disk_gib
    if (
        not 4608 <= memory <= host.memory_budget()["guest_max_mib"]
        or not 1 <= cpus <= 64
        or disk < 64
    ):
        raise Failure(
            "Windows requires at least 4608 MiB RAM within the host budget, 1..64 vCPUs and a 64 GiB disk"
        )
    if not 1024 <= args.vnc_port <= 65535:
        raise Failure("VNC needs an unprivileged TCP port")
    store = args.store.expanduser().resolve()
    if not store.is_relative_to(Path.home().resolve()):
        raise Failure("VM storage must be inside the owner home directory")
    ident = str(uuid.uuid4())
    subnet = ipaddress.ip_network(
        args.subnet or f"10.203.{int(ident[:8], 16) % 250 + 1}.0/24"
    )
    if subnet.version != 4 or subnet.prefixlen != 24 or not subnet.is_private:
        raise Failure("subnet must be a private IPv4 /24")
    cfg = {
        "schema": config.SCHEMA,
        "os": "windows11",
        "id": ident,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "name": args.name,
        "cpus": cpus,
        "memory_mib": memory,
        "disk_gib": disk,
        "boot_managed": False,
        "install_media": True,
        "subnet": str(subnet),
        "vhost_net": False,
    }
    cfg = new_config(cfg, args)
    if (
        args.local_user
        and args.local_user.lower() == cfg["windows"]["ssh_user"].lower()
    ):
        raise Failure("Interactive and dedicated Windows control accounts must differ")
    vm = store / "vms" / args.name
    vm.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        windows_vm.prepare(vm, cfg, args, BUNDLE)
        atomic_json(vm / "config.json", cfg)
        atomic_json(
            vm / "state.json",
            {
                "state": "created",
                "exit_code": None,
                "exit_reason": None,
                "clean_shutdown": None,
            },
        )
    except BaseException:
        shutil.rmtree(vm)
        raise
    print("created " + str(vm))
    if args.start:
        start_vm(store, args.name)


def supervise(vm, cfg, lock_fd):
    guard = None
    try:
        guard = SleepGuard(
            lambda: (vm / "guard.requested").write_text("Host preparing for sleep")
        )
        with os.fdopen(lock_fd, "a") as lock:
            while True:
                result = supervise_run(vm, cfg, os.dup(lock.fileno()))
                state = read_json(vm / "state.json")
                if (
                    state.get("error")
                    or state.get("forced_stop")
                    or (vm / "guard.requested").exists()
                    or (vm / "stop.requested").exists()
                ):
                    return result
                if (
                    cfg["os"] == "windows11"
                    and cfg["install_media"]
                    and state.get("clean_shutdown")
                ):
                    if not windows_vm.deployment_complete(vm, cfg):
                        state.update(
                            state="failed",
                            clean_shutdown=False,
                            error="Windows deployment did not finish",
                        )
                        atomic_json(vm / "state.json", state)
                        return 1
                    windows_vm.save_deployment_logs(vm)
                    cfg["install_media"] = False
                    atomic_json(vm / "config.json", cfg)
                    shutil.rmtree(vm / "install")
                elif state.get("exit_reason") != "reset":
                    return result
                state.update(state="restarting", clean_shutdown=False)
                atomic_json(vm / "state.json", state)
    except Exception as exc:
        state = read_json(vm / "state.json")
        state.update(state="failed", clean_shutdown=False, error=str(exc))
        atomic_json(vm / "state.json", state)
        return 1
    finally:
        state = read_json(vm / "state.json")
        if not vmm_active(state) and state.get("network_cleanup_needed"):
            try:
                network_action("down", cfg)
                state["network_cleanup_needed"] = False
            except Exception as exc:
                state["cleanup_error"] = str(exc)
            atomic_json(vm / "state.json", state)
        if guard:
            guard.close()


def new_config(cfg, args):
    cfg = config.complete_new(cfg)
    cfg["memory_mode"] = getattr(args, "memory_mode", "protected")
    cfg["network_mode"] = getattr(args, "network", "nat")
    cfg["display"]["gpu"] = getattr(args, "gpu", None) or cfg["display"]["gpu"]
    cfg["display"]["vnc"] = getattr(args, "vnc", False) or cfg["os"] == "windows11"
    cfg["display"]["vnc_port"] = getattr(args, "vnc_port", 5900)
    if cfg["display"]["vnc"] and not getattr(args, "gpu", None):
        cfg["display"]["gpu"] = "simplefb"
    cfg["display"]["input_mode"] = getattr(args, "input_mode", "tablet")
    cfg["guest_payload"] = getattr(args, "guest_payload", False)
    cfg["guest_distribution"] = (
        "ubuntu" if cfg["os"] == "linux" and not getattr(args, "image", None) else ""
    )
    cfg["shares"] = getattr(args, "shares", [])
    cfg["forwards"] = getattr(args, "forwards", [])
    cfg["windows"]["ssh_user"] = getattr(args, "windows_user", "gunyahvm-agent")
    config.validate(cfg)
    snapshot = capabilities.probe()
    cfg["display"].update(snapshot.profile["display"])
    snapshot.require_selection(cfg)
    return cfg


def list_vms(store):
    result = []
    for vm in sorted((Path(store) / "vms").glob("*")):
        if not (vm / "config.json").exists():
            continue
        try:
            vm, cfg = owned_vm(store, vm.name, os.getuid(), inputs=False)
            result.append({"config": cfg, "status": status(vm, cfg)})
        except (ValueError, OSError, Failure, KeyError, TypeError) as exc:
            result.append(
                {
                    "config": {"name": vm.name},
                    "status": {"state": "invalid", "error": str(exc)},
                }
            )
    return result


def save_config(store, name, cfg):
    vm, old = owned_vm(store, name, os.getuid(), inputs=False)
    state = status(vm, old)
    if monitor_active(state) or vmm_active(state):
        raise Failure("Stop the VM before changing its settings")
    if any(
        cfg.get(k) != old.get(k) for k in ("id", "name", "uid", "gid", "os", "disk_gib")
    ):
        raise Failure("VM identity and disk size cannot be changed here")
    if old["os"] == "windows11" and cfg["subnet"] != old["subnet"]:
        raise Failure("Windows subnet is fixed when its guest firewall is provisioned")
    atomic_json(vm / "config.json", config.validate(cfg))


def delete_vm(store, name):
    vm, cfg = owned_vm(store, name, os.getuid(), inputs=False)
    state = read_json(vm / "state.json")
    if monitor_active(state) or vmm_active(state):
        raise Failure("Stop the VM before deleting it")
    if state.get("network_cleanup_needed"):
        network_action("down", cfg)
    shutil.rmtree(vm)


def restart_vm(store, name):
    vm, cfg = owned_vm(store, name, os.getuid())
    stop_vm(vm, cfg)
    return start_vm(store, name)


def console_text(store, name):
    vm, cfg = owned_vm(store, name, os.getuid(), inputs=False)
    state = status(vm, cfg)
    return replay(Path(state["run_dir"]) / "serial.log") if state.get("run_dir") else ""


def crosvm_argv(vm, cfg, rd):
    return crosvmargs.build(
        vm, cfg, rd, capabilities.probe(), os.environ.get("WAYLAND_DISPLAY")
    )[0]


@dataclass
class CreateRequest:
    store: Path
    name: str
    os: str = "linux"
    image: str | None = None
    release: str = "20261016"
    mirror: str = "official"
    boot_dir: str | None = None
    ssh_key: str | None = None
    username: str = "ubuntu"
    disk_gib: int | None = None
    memory_mib: int | None = None
    cpus: int | None = None
    subnet: str | None = None
    vhost_net: bool = False
    start: bool = False
    iso: Path | None = None
    edition: str | None = None
    local_user: str | None = None
    password_fd: int | None = None
    windows_user: str = "gunyahvm-agent"
    vnc_port: int = 5900
    vnc: bool = False
    gpu: str | None = None
    memory_mode: str = "protected"
    network: str = "nat"
    input_mode: str = "tablet"
    guest_payload: bool = False
