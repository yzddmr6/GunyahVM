#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Run inside the pinned Ubuntu arm64 build root with the guest modules installed
# at /usr/lib/modules/7.2.15-gvm63 as a real directory, never a build-tree symlink.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$#" -eq 2 ] || { echo 'Usage: build-guest-initrd.sh IMAGE OUTPUT' >&2; exit 2; }
[ ! -L /usr/lib/modules/7.2.15-gvm63 ]
empty=$(mktemp -d "$(dirname -- "$2")/.dracut-conf-XXXXXX")
trap 'rmdir "$empty"' EXIT
export SOURCE_DATE_EPOCH=1794009600
dracut --force --reproducible --no-hostonly --no-hostonly-cmdline \
    --conf /dev/null --confdir "$empty" \
    --modules 'systemd dracut-systemd base rootfs-block fs-lib kernel-modules udev-rules shutdown' \
    --filesystems 'ext4 vfat iso9660' \
    --remove '/etc/ld.so.conf.d /etc/passwd- /etc/group- /etc/shadow /etc/gshadow /etc/.pwd.lock' \
    --include "$repo/third_party/runtime/initrd/passwd" /etc/passwd \
    --include "$repo/third_party/runtime/initrd/group" /etc/group \
    --kver 7.2.15-gvm63 --kernel-image "$1" --gzip "$2"
test -s "$2"
