#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
#
# The macOS VM's EDK II firmware, run by tools/build-macos-kit-inputs.py in the build container
# with this directory at /build/fw, so no build path differs between machines:
#   build-firmware.sh EDK2_GUNYAH_URL COMMIT EDK2_URL COMMIT FIRMWARE_VER
# edk2-gunyah (Droid-VM) on TianoCore EDK II with edk2-gunyah's own patches to it, plus
# edk2-gunyah-virtio-gpu-queue-size.patch; edk2-reproducible.patch makes EDK II take PE time
# stamps and stack cookies from SOURCE_DATE_EPOCH, so the image is the same on every build.
set -euo pipefail
cd /build/fw
pinned() {  # DIR URL COMMIT: a checkout of COMMIT (made once, checked afterwards)
	if [ ! -d "$1/.git" ]; then
		git init -q "$1" && git -C "$1" remote add origin "$2"
		git -C "$1" fetch -q --depth 1 origin "$3" && git -C "$1" checkout -q --detach FETCH_HEAD
		return 1
	fi
	[ "$(git -C "$1" rev-parse HEAD)" = "$3" ] || { echo "$1 is not at $3" >&2; exit 1; }
}
pinned edk2-gunyah "$1" "$2" || git -C edk2-gunyah apply ../edk2-gunyah-virtio-gpu-queue-size.patch
if ! pinned edk2 "$3" "$4"; then
	git -C edk2 submodule update -q --init --recursive --depth 1
	for patch in edk2-gunyah/patches/edk2/*.patch; do patch -d edk2 -s -tNp1 < "$patch"; done
	git -C edk2 apply ../edk2-reproducible.patch
fi
export WORKSPACE=/build/fw/workspace EDK_TOOLS_PATH=/build/fw/edk2/BaseTools PYTHON_COMMAND=python3 \
	PACKAGES_PATH=/build/fw/edk2-gunyah:/build/fw/edk2 GCC_AARCH64_PREFIX=aarch64-linux-gnu-
# Serially: VfrCompile's makefile runs one antlr per generated file in a parallel make.
[ -x edk2/BaseTools/Source/C/bin/GenFw ] || make -C edk2/BaseTools/Source/C -j1 >/dev/null
rm -rf "$WORKSPACE"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"
set +u
source /build/fw/edk2/edksetup.sh BaseTools >/dev/null
set -u
build -p GunyahPkg/GunyahKernel.dsc -a AARCH64 -b RELEASE -t GCC -n "${JOBS:-8}" -q \
	-D DISABLE_NEW_DEPRECATED_INTERFACES=TRUE -D FIRMWARE_VER="$5" -D PCI_CAM_MODE=FALSE
cp Build/GunyahKernel-AARCH64/RELEASE_GCC/FV/GUNYAH_EFI.fd /build/fw/edk2-gunyah.fd
