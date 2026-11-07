#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build only the small project-owned ARM64 PE helper with locked LLVM tools."""

import argparse
from pathlib import Path
import subprocess


def build(tool_root, source, output, work):
    work.mkdir(parents=True, exist_ok=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "1791617089",
        "LD_LIBRARY_PATH": str(tool_root / "usr/lib/x86_64-linux-gnu"),
    }
    exports = {
        "kernel32": [
            "ExitProcess",
            "GetLastError",
            "GetStdHandle",
            "WriteFile",
            "CreateFileW",
            "ReadFile",
            "CloseHandle",
            "GetCommandLineW",
            "LoadLibraryW",
            "GetProcAddress",
        ],
        "crypt32": [
            "CertOpenStore",
            "CertAddEncodedCertificateToStore",
            "CertCloseStore",
        ],
        "setupapi": [
            "SetupDiCreateDeviceInfoList",
            "SetupDiCreateDeviceInfoW",
            "SetupDiSetDeviceRegistryPropertyW",
            "SetupDiCallClassInstaller",
            "SetupDiDestroyDeviceInfoList",
            "SetupDiGetClassDevsW",
            "SetupDiEnumDeviceInterfaces",
        ],
    }

    def run(argv):
        subprocess.run([str(x) for x in argv], env=env, check=True)

    for dll, names in exports.items():
        definition = work / (dll + ".def")
        definition.write_text(
            "LIBRARY " + dll + ".dll\nEXPORTS\n" + "\n".join(names) + "\n"
        )
        run(
            [
                tool_root / "usr/bin/llvm-dlltool-18",
                "-m",
                "arm64",
                "-d",
                definition,
                "-l",
                work / (dll + ".lib"),
            ]
        )
    run(
        [
            tool_root / "usr/bin/clang-18",
            "--target=aarch64-pc-windows-msvc",
            "-O2",
            "-fno-stack-protector",
            "-ffreestanding",
            "-fshort-wchar",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
            source,
            "-o",
            work / "winpe-helper.obj",
        ]
    )
    run(
        [
            tool_root / "usr/bin/lld-link-18",
            "/machine:arm64",
            "/subsystem:console",
            "/entry:mainCRTStartup",
            "/nodefaultlib",
            "/timestamp:0",
            "/out:" + str(output),
            work / "winpe-helper.obj",
            *[work / (name + ".lib") for name in exports],
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "src/gunyahvm/windows/winpe-helper.c"
    build(args.tool_root.resolve(), source, args.output.resolve(), args.work.resolve())


if __name__ == "__main__":
    main()
