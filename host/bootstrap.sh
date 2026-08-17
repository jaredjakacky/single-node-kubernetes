#!/usr/bin/env bash

set -Eeuo pipefail

readonly CONTAINERD_VERSION="2.3.3"
readonly CONTAINERD_ARCHIVE="containerd-${CONTAINERD_VERSION}-linux-amd64.tar.gz"
# Published in the matching upstream release asset: ${CONTAINERD_ARCHIVE}.sha256sum
readonly CONTAINERD_SHA256="ebf6e710056312628eaf6fb4a1c32f0a4ae5f812568321be4029389d66fc7c7c"
readonly CONTAINERD_URL="https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/${CONTAINERD_ARCHIVE}"

readonly RUNC_VERSION="1.5.1"
readonly RUNC_ARTIFACT="runc.amd64"
# Published in the matching upstream release asset: runc.sha256sum
readonly RUNC_SHA256="177df879d50c913eb205e898d5c1c05a18f574053c0ce5524c471208eaf06f6f"
readonly RUNC_URL="https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/${RUNC_ARTIFACT}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly CONTAINERD_SERVICE_SOURCE="${SCRIPT_DIR}/containerd.service"
readonly CONTAINERD_CONFIG_SOURCE="${SCRIPT_DIR}/containerd-config.toml"
WORK_DIR="$(mktemp -d)"
readonly WORK_DIR

cleanup() {
  rm -rf -- "${WORK_DIR}"
}

trap cleanup EXIT

log() {
  printf '[host-bootstrap] %s\n' "$*"
}

die() {
  printf '[host-bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run this script as root"
}

require_amd64() {
  [[ "$(uname -m)" == "x86_64" ]] || die "this bootstrap supports x86-64 hosts only"
}

require_sources() {
  [[ -f "${CONTAINERD_SERVICE_SOURCE}" ]] || die "missing ${CONTAINERD_SERVICE_SOURCE}"
  [[ -f "${CONTAINERD_CONFIG_SOURCE}" ]] || die "missing ${CONTAINERD_CONFIG_SOURCE}"
}

install_download_requirements() {
  if command -v curl >/dev/null && [[ -f /etc/ssl/certs/ca-certificates.crt ]]; then
    return
  fi

  log "installing download requirements"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends ca-certificates curl
}

download() {
  local url="$1"
  local destination="$2"

  curl \
    --fail \
    --location \
    --proto '=https' \
    --retry 5 \
    --retry-all-errors \
    --show-error \
    --silent \
    --tlsv1.2 \
    --output "${destination}" \
    "${url}"
}

verify_sha256() {
  local expected="$1"
  local artifact="$2"

  if ! printf '%s  %s\n' "${expected}" "${artifact}" | sha256sum --check --status -; then
    die "SHA-256 verification failed for ${artifact}"
  fi
}

containerd_is_current() {
  [[ -x /usr/local/bin/containerd ]] &&
    /usr/local/bin/containerd --version | grep --fixed-strings --quiet " v${CONTAINERD_VERSION} "
}

install_containerd() {
  local archive="${WORK_DIR}/${CONTAINERD_ARCHIVE}"

  if containerd_is_current; then
    log "containerd ${CONTAINERD_VERSION} is already installed"
    return
  fi

  log "downloading containerd ${CONTAINERD_VERSION}"
  download "${CONTAINERD_URL}" "${archive}"
  verify_sha256 "${CONTAINERD_SHA256}" "${archive}"

  log "installing containerd under /usr/local"
  tar --extract --gzip --file "${archive}" --directory /usr/local
}

runc_is_current() {
  [[ -x /usr/local/sbin/runc ]] &&
    /usr/local/sbin/runc --version | grep --fixed-strings --quiet "runc version ${RUNC_VERSION}"
}

install_runc() {
  local artifact="${WORK_DIR}/${RUNC_ARTIFACT}"

  if runc_is_current; then
    log "runc ${RUNC_VERSION} is already installed"
    return
  fi

  log "downloading runc ${RUNC_VERSION}"
  download "${RUNC_URL}" "${artifact}"
  verify_sha256 "${RUNC_SHA256}" "${artifact}"

  log "installing runc under /usr/local/sbin"
  install --directory --mode 0755 /usr/local/sbin
  install --mode 0755 "${artifact}" /usr/local/sbin/runc
}

disable_swap() {
  local fstab="${WORK_DIR}/fstab"

  log "disabling swap"
  swapoff --all

  awk '
    /^[[:space:]]*#/ { print; next }
    NF >= 3 && $3 == "swap" {
      print "# disabled by single-node-kubernetes bootstrap: " $0
      next
    }
    { print }
  ' /etc/fstab >"${fstab}"

  if ! cmp --silent "${fstab}" /etc/fstab; then
    install --mode 0644 "${fstab}" /etc/fstab
  fi
}

configure_kernel() {
  log "configuring required kernel settings"

  printf '%s\n' 'overlay' >"${WORK_DIR}/containerd-modules.conf"
  install --directory --mode 0755 /etc/modules-load.d
  install --mode 0644 "${WORK_DIR}/containerd-modules.conf" /etc/modules-load.d/containerd.conf
  modprobe overlay

  # br_netfilter is intentionally omitted until the CNI is selected.
  printf '%s\n' 'net.ipv4.ip_forward = 1' >"${WORK_DIR}/kubernetes-sysctl.conf"
  install --directory --mode 0755 /etc/sysctl.d
  install --mode 0644 "${WORK_DIR}/kubernetes-sysctl.conf" /etc/sysctl.d/99-single-node-kubernetes.conf
  sysctl --load /etc/sysctl.d/99-single-node-kubernetes.conf >/dev/null
}

configure_containerd() {
  log "installing containerd service and configuration"

  install --directory --mode 0755 /usr/local/lib/systemd/system /etc/containerd
  install --mode 0644 "${CONTAINERD_SERVICE_SOURCE}" /usr/local/lib/systemd/system/containerd.service
  install --mode 0644 "${CONTAINERD_CONFIG_SOURCE}" /etc/containerd/config.toml

  /usr/local/bin/containerd --config /etc/containerd/config.toml config dump >"${WORK_DIR}/effective-containerd-config.toml"
  grep --extended-regexp --quiet \
    '^[[:space:]]*SystemdCgroup[[:space:]]*=[[:space:]]*true$' \
    "${WORK_DIR}/effective-containerd-config.toml" ||
    die "containerd effective configuration does not enable systemd cgroups"

  systemctl daemon-reload
  systemctl enable containerd.service >/dev/null
  systemctl restart containerd.service
}

verify_postconditions() {
  log "verifying host postconditions"

  [[ "$(stat --file-system --format %T /sys/fs/cgroup)" == "cgroup2fs" ]] ||
    die "cgroup v2 is not active"
  [[ -z "$(swapon --show --noheadings)" ]] || die "swap remains enabled"
  [[ "$(sysctl --values net.ipv4.ip_forward)" == "1" ]] || die "IPv4 forwarding is disabled"
  grep --word-regexp --quiet overlay /proc/filesystems || die "overlay filesystem support is unavailable"

  containerd_is_current || die "containerd ${CONTAINERD_VERSION} is not installed"
  runc_is_current || die "runc ${RUNC_VERSION} is not installed"
  systemctl is-enabled --quiet containerd.service || die "containerd is not enabled"
  systemctl is-active --quiet containerd.service || die "containerd is not active"

  /usr/local/bin/containerd --config /etc/containerd/config.toml config dump >"${WORK_DIR}/effective-containerd-config.toml"
  grep --extended-regexp --quiet \
    '^[[:space:]]*SystemdCgroup[[:space:]]*=[[:space:]]*true$' \
    "${WORK_DIR}/effective-containerd-config.toml" ||
    die "containerd effective configuration does not enable systemd cgroups"
}

main() {
  require_root
  require_amd64
  require_sources
  install_download_requirements
  disable_swap
  configure_kernel
  install_containerd
  install_runc
  configure_containerd
  verify_postconditions
  log "bootstrap completed successfully"
}

main "$@"
