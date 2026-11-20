# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# The macOS kit's build host: Ubuntu 26.04 with the packages of macOS-DroidVM's
# configs/env.lock.json (LLVM 21, Python 3.14, meson 1.10), from a fixed archive snapshot,
# and rustup for the lock's dated nightly (the toolchain itself goes to the workspace).
FROM ubuntu@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
ARG SNAPSHOT=20261119T000000Z
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ca-certificates >/dev/null \
 && sed -i "s#http://archive.ubuntu.com/ubuntu/#https://snapshot.ubuntu.com/ubuntu/${SNAPSHOT}/#; s#http://security.ubuntu.com/ubuntu/#https://snapshot.ubuntu.com/ubuntu/${SNAPSHOT}/#" /etc/apt/sources.list.d/ubuntu.sources \
 && rm -rf /var/lib/apt/lists/* && apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends --reinstall ca-certificates >/dev/null \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    build-essential autoconf automake libtool pkg-config git python3 python3-mako python3-yaml python3-packaging \
    meson ninja-build cmake bison flex m4 glslang-tools mtools unifdef tcsh pax libbsd-dev uuid-dev libssl-dev \
    zlib1g-dev cpio xz-utils zstd ccache llvm-21 llvm-21-dev clang-21 lld-21 7zip gcc-aarch64-linux-gnu \
    docker-cli curl file patch rsync gcc-13 g++-13 gcc-13-aarch64-linux-gnu >/dev/null \
 && rm -rf /var/lib/apt/lists/*
# The EDK II firmware's ACPI tables need iasl.
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends acpica-tools >/dev/null \
 && rm -rf /var/lib/apt/lists/*
# GCC 13, as on the ubuntu-24.04 runners of macOS-DroidVM's CI: EDK II's BaseTools at the
# locked commit do not compile with GCC 15, and DarwinBoot.efi builds with the GCC toolchain.
RUN for t in gcc:gcc-13 g++:g++-13 cc:gcc-13 c++:g++-13 cpp:cpp-13 \
      aarch64-linux-gnu-gcc:aarch64-linux-gnu-gcc-13 aarch64-linux-gnu-cpp:aarch64-linux-gnu-cpp-13; do \
      ln -s "/usr/bin/${t#*:}" "/usr/local/bin/${t%%:*}"; done
ARG RUSTUP=1.28.2
ARG RUSTUP_SHA256=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c
RUN curl -sSfL -o /usr/local/bin/rustup-init \
      https://static.rust-lang.org/rustup/archive/${RUSTUP}/x86_64-unknown-linux-gnu/rustup-init \
 && echo "${RUSTUP_SHA256}  /usr/local/bin/rustup-init" | sha256sum -c - && chmod 755 /usr/local/bin/rustup-init
ENV PATH=/usr/lib/llvm-21/bin:$PATH
