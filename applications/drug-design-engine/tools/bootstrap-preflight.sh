#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Bootstrap preflight — can this container build the dde tools
# environment, and if not, exactly what is it missing?
#
# Run this BEFORE ./install.sh. It writes nothing, installs nothing,
# and needs no privilege. It exists because install.sh is a long
# operation that fails in the middle: pip runs for minutes, then fpocket
# fails to link because a static library nobody thought about is absent,
# and the environment left behind is a partial one that still looks
# provisioned to anything that does not read exit codes.
#
#   ./bootstrap-preflight.sh
#
# Exit codes, and they are the point of the script:
#   0  ready — install.sh can run to completion here
#   1  missing prerequisites — the apt-get line is printed; act on it
#   2  cannot check — unsupported platform, or a check itself broke
#
# 2 is not 1. "This container is not Debian, so I do not know what to
# tell you" is a different answer from "this container is Debian and is
# missing gcc", and a bootstrapper that treats them the same will
# improvise a package list for a distribution nobody tested.

set -uo pipefail   # deliberately NOT -e: this script's job is to keep
                   # checking after something fails, and report all of
                   # it at once. A preflight that stops at the first
                   # missing package makes the operator run it, install
                   # one thing, run it again — which is the loop the
                   # script exists to remove.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Known C-extension prerequisites (science stack)
# ---------------------------------------------------------------------------
#
# The science stack (prody, numpy, scipy, biopython, etc.) compiles C
# extensions.  These OS packages must be present for those compilations:
#
#   python3-dev        Python.h and the CPython development headers
#   build-essential    gcc, g++, make, and the standard toolchain
#   libc6-dev          static libc archive (libc.a) for -static linking
#   libstdc++-N-dev    static C++ runtime for the vendored molfile plugin
#
# fpocket also builds from source and requires the same toolchain plus
# the static archives.  Without these, install.sh produces exit 3
# (science stack failure) or exit 4 (missing binary).
#
# Standard C/C++ headers needed by specific packages:
#   Python.h           prody (no CPython 3.11 wheel), numpy, scipy
#   stdio.h etc.       fpocket (compiled from source, linked -static)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

REMEDIATE=true

while [ $# -gt 0 ]; do
    case "$1" in
        --remediate)    REMEDIATE=true;  shift ;;
        --no-remediate) REMEDIATE=false; shift ;;
        -h|--help)
            printf 'Usage: %s [--remediate|--no-remediate]\n' "${0##*/}"
            printf '\n'
            printf 'Options:\n'
            printf '  --remediate      auto-install missing OS packages when\n'
            printf '                   passwordless sudo (or root) is available (default)\n'
            printf '  --no-remediate   detect and report only; install nothing\n'
            exit 0
            ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done

ok()   { printf '  \033[1;32mok\033[0m      %s\n' "$*"; }
miss() { printf '  \033[1;31mMISSING\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33mwarn\033[0m    %s\n' "$*"; }
info() { printf '  \033[1;34m—\033[0m       %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

MISSING_PKGS=""
FAILED=0
UNKNOWN=0

need_pkg() { MISSING_PKGS="${MISSING_PKGS} $1"; FAILED=1; }

# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------
#
# The package names below are Debian's. On anything else they are wrong,
# and a wrong package name is worse than no package name because it gets
# pasted into a shell.

head_ "Platform"

DEBIAN=false
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    info "${PRETTY_NAME:-unknown}"
    case "${ID:-}:${ID_LIKE:-}" in
        debian:*|ubuntu:*|*:*debian*)
            DEBIAN=true ;;
        *)
            warn "not Debian-family: the package names below are Debian's and"
            warn "        will not be correct here. The REQUIREMENTS are still"
            warn "        accurate; translate them, do not paste them."
            UNKNOWN=1 ;;
    esac
else
    warn "no /etc/os-release; cannot identify the distribution"
    UNKNOWN=1
fi

if [ "$(uname -m)" != "x86_64" ]; then
    miss "architecture is $(uname -m), not x86_64"
    info "        The vina binary is published for linux_x86_64 only, and"
    info "        fpocket's vendored molfile plugin is a prebuilt LINUXAMD64"
    info "        static archive. Neither has an arm64 path in install.sh."
    FAILED=1
else
    ok "x86_64"
fi

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

head_ "Python"

if ! command -v python3 >/dev/null 2>&1; then
    miss "python3"
    need_pkg python3
else
    PYV="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        ok "python3 ${PYV}"
        # Say what was actually run, not what is theoretically supported.
        # 3.10 is the floor because the CLI uses `X | None` in evaluated
        # annotations. 3.11.2 is the only version it has been run on.
        [ "${PYV%.*}" = "3.11" ] || info "        floor is 3.10 (PEP 604 annotations); tested only on 3.11.2"
    else
        miss "python3 is ${PYV}; need 3.10 or newer"
        FAILED=1
    fi

    # The real test, not the proxy. `import venv` succeeds on Debian even
    # when python3-venv is absent; the failure only appears when you try
    # to create one, because it is ensurepip that is missing. Checking
    # the import would report ready and then install.sh would fail on its
    # first command.
    VENV_PROBE="$(mktemp -d)"
    if python3 -m venv "${VENV_PROBE}/v" >/dev/null 2>&1 && [ -x "${VENV_PROBE}/v/bin/pip" ]; then
        ok "python3 -m venv (created a working venv with pip)"
    else
        miss "python3 -m venv cannot create a venv with pip"
        info "        This is the ensurepip split: the venv module imports"
        info "        fine and creating one still fails."
        need_pkg "python3-venv"
    fi
    rm -rf "$VENV_PROBE"

    # Python.h — needed by the science stack, not by the CLI. prody has
    # no CPython 3.11 wheel and compiles C extensions.
    PYINC="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null)"
    if [ -n "$PYINC" ] && [ -f "${PYINC}/Python.h" ]; then
        ok "Python.h (${PYINC})"
    else
        miss "Python.h — the science stack will not build"
        info "        install.sh survives this: core deps install, the science"
        info "        stack fails loudly, and it exits 3. The CLI works; rdkit,"
        info "        biopython, prody, scipy, pandas and matplotlib do not."
        need_pkg "python3-dev"
    fi
fi

# ---------------------------------------------------------------------------
# Fetch and verify
# ---------------------------------------------------------------------------

head_ "Fetch and verify"

for c in curl tar sha256sum install; do
    if command -v "$c" >/dev/null 2>&1; then
        ok "$c"
    else
        miss "$c"
        case "$c" in
            curl) need_pkg curl ;;
            tar) need_pkg tar ;;
            *) need_pkg coreutils ;;
        esac
    fi
done

# ca-certificates is the one that bites minimal images. curl exists,
# every https URL fails, and the error is a TLS message rather than a
# missing-package message.
if [ -e /etc/ssl/certs/ca-certificates.crt ] || [ -d /etc/ssl/certs ]; then
    ok "CA certificates present"
else
    miss "no CA certificate store — every https download will fail"
    need_pkg ca-certificates
fi

if command -v git >/dev/null 2>&1; then
    ok "git ($(git --version 2>/dev/null | awk '{print $3}'))"
else
    miss "git"
    info "        Not needed to BUILD the environment. Needed for it to be"
    info "        accountable: dde env stamp records the commit the"
    info "        environment was provisioned from, and doctor checks that"
    info "        commit is reachable from a remote. Without git every"
    info "        artifact records an environment nobody can reproduce."
    need_pkg git
fi

# Hypex's Go modules declare this minimum toolchain. The source and module
# dependencies are vendored in DDE, so this check does not access the network.
HYPEX_GO_VERSION="1.26.1"
if command -v go >/dev/null 2>&1; then
    GOV="$(GOTOOLCHAIN=local go env GOVERSION 2>/dev/null)"
    GOV_NUMBER="${GOV#go}"
    LOWEST="$(printf '%s\n' "$HYPEX_GO_VERSION" "$GOV_NUMBER" | sort -V | head -n 1)"
    if [ "$LOWEST" = "$HYPEX_GO_VERSION" ]; then
        ok "go ${GOV_NUMBER} (Hypex requires ${HYPEX_GO_VERSION} or newer)"
    else
        miss "go is ${GOV:-unknown}; vendored Hypex requires go${HYPEX_GO_VERSION} or newer"
        info "        The deployment image supplies Go; install.sh does not replace it."
        FAILED=1
    fi
else
    miss "go${HYPEX_GO_VERSION} — required to build vendored Hypex tools"
    info "        The deployment image must provide the Go toolchain."
    FAILED=1
fi

# ---------------------------------------------------------------------------
# Node.js / npm — KaTeX is installed from npm at provision time
# ---------------------------------------------------------------------------

head_ "Node.js / npm (KaTeX)"

if command -v node >/dev/null 2>&1; then
    NODEV="$(node --version 2>/dev/null)"
    NODEV_NUMBER="${NODEV#v}"
    NODEV_MAJOR="${NODEV_NUMBER%%.*}"
    if [ "$NODEV_MAJOR" -ge 18 ] 2>/dev/null; then
        ok "node ${NODEV} (>= v18 required)"
    else
        miss "node is ${NODEV}; need v18 or newer"
        FAILED=1
    fi
else
    miss "node — required for npm to install KaTeX"
    FAILED=1
fi

if command -v npm >/dev/null 2>&1; then
    ok "npm $(npm --version 2>/dev/null)"
else
    miss "npm — required to install KaTeX for site math rendering"
    FAILED=1
fi

# ---------------------------------------------------------------------------
# C toolchain — fpocket is compiled here, not downloaded
# ---------------------------------------------------------------------------

head_ "C toolchain (fpocket builds from source)"

TOOLCHAIN_OK=true
for c in gcc g++ make ldd; do
    if command -v "$c" >/dev/null 2>&1; then
        ok "$c"
    else
        miss "$c"
        TOOLCHAIN_OK=false
        [ "$c" = "ldd" ] && need_pkg libc-bin || need_pkg build-essential
    fi
done

# The two checks nobody thinks to make, and the reason this script has a
# section instead of a line. gcc and make being present does not mean a
# STATIC link will succeed — that needs the .a archives, which live in
# -dev packages that a container can easily lack while compiling
# dynamically just fine. install.sh links fpocket static on purpose, and
# refuses to install a dynamically linked one, so a container missing
# these produces a build that succeeds and an install that declines.
if [ "$TOOLCHAIN_OK" = true ]; then
    LIBC_A="$(gcc -print-file-name=libc.a 2>/dev/null)"
    if [ "$LIBC_A" != "libc.a" ] && [ -f "$LIBC_A" ]; then
        ok "libc.a (static libc)"
    else
        miss "libc.a — fpocket is linked -static and cannot be"
        need_pkg libc6-dev
    fi

    LIBSTDCXX_A="$(gcc -print-file-name=libstdc++.a 2>/dev/null)"
    if [ "$LIBSTDCXX_A" != "libstdc++.a" ] && [ -f "$LIBSTDCXX_A" ]; then
        ok "libstdc++.a (static C++ runtime, for the vendored molfile plugin)"
    else
        miss "libstdc++.a — the static link will fail at the molfile plugin"
        # Version-suffixed on Debian and the suffix tracks gcc, so it is
        # derived rather than hard-coded. A hard-coded libstdc++-12-dev
        # is correct on bookworm and wrong on the next release, and it is
        # wrong silently, in a package name a human will paste.
        GCCV="$(gcc -dumpversion 2>/dev/null | cut -d. -f1)"
        need_pkg "libstdc++-${GCCV:-12}-dev"
    fi
fi

# ---------------------------------------------------------------------------
# Privilege — can this container install any of the above?
# ---------------------------------------------------------------------------
#
# Checked even when nothing is missing, because the answer determines
# what a bootstrapper should DO about a gap, and "report blocked" is a
# legitimate outcome. An agent that cannot install packages and does not
# know it will try to work around the gap — build fpocket dynamically,
# skip the science stack, install a wheel from somewhere else — and each
# of those produces an environment that is not the one the ENV_VERSION
# describes.

head_ "Privilege"

CAN_INSTALL=false
if [ "$(id -u)" = "0" ]; then
    ok "running as root"
    CAN_INSTALL=true
    APT_PREFIX=""
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    ok "passwordless sudo as $(whoami)"
    CAN_INSTALL=true
    APT_PREFIX="sudo "
else
    warn "not root and no passwordless sudo (running as $(whoami))"
    APT_PREFIX="sudo "
fi

# ---------------------------------------------------------------------------
# Network egress
# ---------------------------------------------------------------------------
#
# Three hosts, named separately, because they fail separately and the
# consequences differ. An install with pypi blocked has no CLI at all; an
# install with github blocked has a working CLI and no binaries, exits 4,
# and doctor names the missing tool.

head_ "Network egress"

probe() {
    local url="$1" what="$2"
    if ! command -v curl >/dev/null 2>&1; then
        warn "cannot probe ${what}: no curl"; UNKNOWN=1; return
    fi
    if curl -fsS --max-time 15 -o /dev/null "$url" 2>/dev/null; then
        ok "$what"
    else
        miss "$what unreachable ($url)"
        FAILED=1
    fi
}

probe "https://pypi.org/simple/click/" "pypi.org (pip packages — no CLI without it)"
probe "https://files.pythonhosted.org/" "files.pythonhosted.org (wheel downloads)"
probe "https://github.com/" "github.com (vina release, fpocket source tarball)"

# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

head_ "Disk"

TARGET="${DDE_TOOLS_HOME:-/scion-volumes/tools}"
PROBE_DIR="$TARGET"
while [ ! -d "$PROBE_DIR" ] && [ "$PROBE_DIR" != "/" ]; do
    PROBE_DIR="$(dirname "$PROBE_DIR")"
done
AVAIL_MB="$(df -Pm "$PROBE_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -z "$AVAIL_MB" ]; then
    warn "could not measure free space at ${PROBE_DIR}"
    UNKNOWN=1
elif [ "$AVAIL_MB" -lt 2500 ]; then
    miss "${AVAIL_MB} MB free at ${PROBE_DIR}; need ~2.5 GB"
    info "        The venv with the science stack is ~1.8 GB — rdkit, scipy,"
    info "        pandas and matplotlib dominate it. The fpocket build tree is"
    info "        transient but wants a few hundred MB while it runs."
    FAILED=1
else
    ok "${AVAIL_MB} MB free at ${PROBE_DIR}"
fi

# ---------------------------------------------------------------------------
# Optional
# ---------------------------------------------------------------------------

head_ "Optional"

if command -v obabel >/dev/null 2>&1; then
    ok "obabel ($(command -v obabel))"
else
    warn "obabel not found — install.sh notes this and continues"
    info "        Used by computational-chemist skills for format conversion."
    info "        Not required for install.sh to exit 0. apt package: openbabel"
fi

# ---------------------------------------------------------------------------
# Auto-remediation
# ---------------------------------------------------------------------------
#
# Rule: fail-stop applies to conditions the agent CANNOT remediate, not
# to conditions with a documented remedy in hand.  When the preflight
# knows exactly which packages are missing AND can install them (root or
# passwordless sudo on a Debian-family system), it does so automatically
# and re-runs itself with --no-remediate to verify.
#
# It reports blocked only when:
#   - remediation is impossible (no root, no passwordless sudo), or
#   - the re-check still fails after remediation, or
#   - --no-remediate was passed explicitly.

if [ "$FAILED" = 1 ] && [ -n "$MISSING_PKGS" ] && \
   [ "$CAN_INSTALL" = true ] && [ "$REMEDIATE" = true ]; then
    if [ "$DEBIAN" = true ]; then
        head_ "Auto-remediation"
        info "Missing packages:$MISSING_PKGS"
        info "Attempting install (passwordless sudo / root available)..."
        printf '\n'
        # shellcheck disable=SC2086
        if ${APT_PREFIX}apt-get update -qq 2>&1 && \
           ${APT_PREFIX}apt-get install -y -qq $MISSING_PKGS 2>&1; then
            printf '\n'
            INSTALLED_PKGS="$MISSING_PKGS"
            info "Installed:$INSTALLED_PKGS"
            info "Re-running preflight to verify..."
            printf '\n'
            exec "$SCRIPT_DIR/bootstrap-preflight.sh" --no-remediate
        else
            printf '\n'
            miss "apt-get failed — manual intervention required"
            info "The packages could not be installed automatically."
            info "Install them manually and re-run this preflight."
        fi
    else
        head_ "Auto-remediation"
        info "Auto-remediation is available only on Debian-family systems."
        info "Translate the package names below for this distribution and"
        info "install them manually."
    fi
fi

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

printf '\n'
if [ "$FAILED" = 0 ] && [ -z "$MISSING_PKGS" ]; then
    if [ "$UNKNOWN" = 1 ]; then
        printf '\033[1;33mINCOMPLETE\033[0m — nothing failed, but some checks could not run\n'
        printf 'and are listed as warn above. Read those before running install.sh.\n'
        exit 2
    fi
    printf '\033[1;32mREADY\033[0m — run:  ./install.sh\n'
    printf 'Then verify with `dde doctor` and read its exit code.\n'
    exit 0
fi

printf '\033[1;31mNOT READY\033[0m\n'
if [ -n "$MISSING_PKGS" ]; then
    printf '\nInstall the missing packages:\n\n'
    if [ "$DEBIAN" = true ]; then
        printf '    %sapt-get update && %sapt-get install -y%s\n\n' \
            "$APT_PREFIX" "$APT_PREFIX" "$MISSING_PKGS"
    else
        printf '    (Debian names — translate for this distribution)\n'
        printf '   %s\n\n' "$MISSING_PKGS"
    fi
fi

if [ "$CAN_INSTALL" = false ]; then
    printf 'You cannot install these: not root, and sudo is unavailable or\n'
    printf 'wants a password. This is a BLOCKED task, not a problem to work\n'
    printf 'around. Report it and name the packages above. Do not build a\n'
    printf 'degraded environment — install.sh already refuses a dynamically\n'
    printf 'linked fpocket, and the ENV_VERSION of a hand-patched environment\n'
    printf 'describes an environment nobody else can reproduce.\n\n'
fi

# Anything not fixable by a package — network, disk, architecture — is a
# separate exit reason from a missing package but the same exit code,
# because the action is the same: do not run install.sh yet.
exit 1
