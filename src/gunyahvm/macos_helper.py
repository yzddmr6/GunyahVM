# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Run the macOS assembly helper VM as its owner (after the fixed action).

The polkit action only raises the locked-memory limit and drops to the
calling user, as for an ordinary VM start. This process then validates the
work directory, boots the packaged guest kernel with the kit's helper
overlay, and waits for the guest to power off.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gunyahvm import components, host, storage  # noqa: E402

TIMEOUT = {"fill": 3 * 3600, "stage": 1800}
MEMORY_MIB = 2048
KIT = Path("/usr/lib/gunyahvm/macos/kit")


def owned_file(path, root):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != os.getuid() \
            or not path.resolve().is_relative_to(root):
        raise ValueError("helper disk is not an owned file in the VM library: " + str(path))
    if any(c in str(path) for c in ",:\n\0"):
        raise ValueError("helper disk paths may not contain crosvm separators")
    return path


def argv(runtime, work, spec, initrd, serial_log):
    crosvm = components.payload(runtime, "crosvm", "binary")
    kernel = components.payload(runtime, "guest_kernel", "image")
    a = [str(crosvm), "--no-syslog", "--log-level", "info", "--extended-status", "run",
         "--hypervisor", "gunyah", "--protected-vm-without-firmware",
         "--name", "gunyahvm-macos-helper", "--cpus", "4", "--mem", str(MEMORY_MIB), "--hugepages",
         "--no-pmu", "--no-smt", "--no-usb", "--swiotlb", "128",
         "--pivot-root", "/usr/share/gunyahvm/crosvm/empty"]
    if "balloon" in components.features(runtime, "crosvm"):
        a += ["--no-balloon"]
    for serial, path, ro in spec["disks"]:
        a += ["--block", f"path={path},id={serial}" + (",ro=true" if ro else "")]
    a += ["--serial", f"type=file,hardware=serial,num=1,path={serial_log},console,earlycon",
          "--initrd", str(initrd),
          "--params", "rdinit=/gvm-macos-helper console=ttyS0 panic=-1 loglevel=4",
          str(kernel)]
    return a


def main(workdir):
    if os.geteuid() == 0:
        raise ValueError("the helper VM runs as the VM owner")
    work = Path(workdir).resolve()
    store = work.parent.parent
    home = Path(os.path.expanduser("~")).resolve()
    if not store.is_relative_to(home) or work.parent.name != ".creating":
        raise ValueError("helper work directory must be a creation inside the VM library")
    spec = json.loads((work / "helper.json").read_text())
    if spec.get("phase") not in TIMEOUT:
        raise ValueError("unknown helper phase")
    for serial, path, ro in spec["disks"]:
        if not serial.startswith("gvm-") or len(serial) > 20 or type(ro) is not bool:
            raise ValueError("invalid helper disk")
        owned_file(path, store)
    runtime = components.load()
    kit = json.loads((KIT / "kit.json").read_text())
    if runtime["components"]["guest_kernel"].get("kernel_release") != kit.get("guest_kernel"):
        raise ValueError("the macOS helper's APFS module does not match the guest kernel; reinstall GunyahVM")
    initrd = work / "helper-initrd.img"
    with open(initrd, "wb") as out:
        with open(components.payload(runtime, "guest_kernel", "initrd"), "rb") as source:
            shutil.copyfileobj(source, out)
        # The kernel takes an uncompressed archive only at a 4-byte offset.
        out.write(b"\0" * (-out.tell() % 4))
        with open(KIT / "helper/overlay.cpio", "rb") as source:
            shutil.copyfileobj(source, out)
    serial_log = work / f"helper-{spec['phase']}-serial.log"
    serial_log.unlink(missing_ok=True)
    # Like a VM start: the helper runs beside other VMs when its RAM is free.
    with storage.admission_lock():
        shortage = host.memory_shortage(MEMORY_MIB, host.meminfo(), host.guest_commitments())
        if shortage:
            raise ValueError(shortage)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LD_")}
        env.update(components.library_environment(runtime))
        proc = subprocess.Popen(argv(runtime, work, spec, initrd, serial_log), env=env,
                                stdin=subprocess.DEVNULL)

    def stop(signum, frame):
        proc.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    deadline = time.monotonic() + TIMEOUT[spec["phase"]]
    while proc.poll() is None:
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            raise ValueError("the macOS assembly helper timed out")
        time.sleep(1)
    initrd.unlink(missing_ok=True)
    return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as error:
        print("gunyahvm macOS helper: " + str(error), file=sys.stderr)
        sys.exit(2)
