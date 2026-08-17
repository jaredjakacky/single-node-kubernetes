#!/usr/bin/env bash

set -Eeuo pipefail

readonly CONTAINERD_VERSION="2.3.3"
readonly CONTAINERD_ARCHIVE="containerd-${CONTAINERD_VERSION}-linux-amd64.tar.gz"
# Published in the matching upstream release asset: ${CONTAINERD_ARCHIVE}.sha256sum
readonly CONTAINERD_SHA256="ebf6e710056312628eaf6fb4a1c32f0a4ae5f812568321be4029389d66fc7c7c"
# Derived from the checksum-verified upstream archive.
readonly CONTAINERD_BINARY_SHA256="dcd1f9fcf26b93cd0f579c52ca96d82fe88067d42a5edb3f7c4e0c9f6e5c4e34"
readonly CONTAINERD_SHIM_SHA256="70183d6fbe17157e9fd20bbd6cbee8f0a9fd05d906762ecde88815b39d78d5e3"
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

runtime_changed=false
unit_changed=false

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

verify_cgroup_v2() {
  [[ "$(stat --file-system --format %T /sys/fs/cgroup)" == "cgroup2fs" ]] ||
    die "cgroup v2 is not active"
}

install_file_if_changed() {
  local source="$1"
  local destination="$2"
  local mode="$3"

  if [[ -f "${destination}" ]] &&
    [[ ! -L "${destination}" ]] &&
    cmp --silent "${source}" "${destination}" &&
    [[ "$(stat --format %a "${destination}")" == "${mode#0}" ]] &&
    [[ "$(stat --format %u:%g "${destination}")" == "0:0" ]]; then
    return 1
  fi

  install --owner root --group root --mode "${mode}" "${source}" "${destination}" ||
    die "failed to install ${destination}"
  return 0
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

  if ! sha256_matches "${expected}" "${artifact}"; then
    die "SHA-256 verification failed for ${artifact}"
  fi
}

sha256_matches() {
  local expected="$1"
  local artifact="$2"

  [[ -f "${artifact}" ]] &&
    printf '%s  %s\n' "${expected}" "${artifact}" | sha256sum --check --status -
}

containerd_is_current() {
  [[ -x /usr/local/bin/containerd ]] &&
    sha256_matches "${CONTAINERD_BINARY_SHA256}" /usr/local/bin/containerd &&
    [[ -x /usr/local/bin/containerd-shim-runc-v2 ]] &&
    sha256_matches "${CONTAINERD_SHIM_SHA256}" /usr/local/bin/containerd-shim-runc-v2
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
  runtime_changed=true
}

runc_is_current() {
  [[ -x /usr/local/sbin/runc ]] &&
    sha256_matches "${RUNC_SHA256}" /usr/local/sbin/runc
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
  runtime_changed=true
}

disable_swap() {
  local fstab="${WORK_DIR}/fstab"

  if [[ -n "$(swapon --show --noheadings)" ]]; then
    log "disabling active swap"
    swapoff --all
  fi

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
    log "disabled persistent swap entries in /etc/fstab"
  fi
}

configure_kernel() {
  log "configuring required kernel settings"

  printf '%s\n' 'overlay' >"${WORK_DIR}/containerd-modules.conf"
  install --directory --mode 0755 /etc/modules-load.d
  if install_file_if_changed \
    "${WORK_DIR}/containerd-modules.conf" \
    /etc/modules-load.d/containerd.conf \
    0644; then
    log "updated persistent OverlayFS module configuration"
  fi
  modprobe overlay

  # br_netfilter is intentionally omitted until the CNI is selected.
  printf '%s\n' 'net.ipv4.ip_forward = 1' >"${WORK_DIR}/kubernetes-sysctl.conf"
  install --directory --mode 0755 /etc/sysctl.d
  if install_file_if_changed \
    "${WORK_DIR}/kubernetes-sysctl.conf" \
    /etc/sysctl.d/99-single-node-kubernetes.conf \
    0644; then
    log "updated persistent IPv4 forwarding configuration"
  fi
  sysctl --load /etc/sysctl.d/99-single-node-kubernetes.conf >/dev/null
}

validate_containerd_config() {
  local config="$1"

  /usr/local/bin/containerd --config "${config}" config dump >"${WORK_DIR}/effective-containerd-config.toml"
  grep --extended-regexp --quiet \
    '^[[:space:]]*SystemdCgroup[[:space:]]*=[[:space:]]*true$' \
    "${WORK_DIR}/effective-containerd-config.toml" ||
    die "containerd effective configuration does not enable systemd cgroups"
}

configure_containerd() {
  log "configuring containerd"

  validate_containerd_config "${CONTAINERD_CONFIG_SOURCE}"

  install --directory --mode 0755 /usr/local/lib/systemd/system /etc/containerd
  if install_file_if_changed \
    "${CONTAINERD_SERVICE_SOURCE}" \
    /usr/local/lib/systemd/system/containerd.service \
    0644; then
    log "updated containerd systemd unit"
    unit_changed=true
    runtime_changed=true
  fi

  if install_file_if_changed \
    "${CONTAINERD_CONFIG_SOURCE}" \
    /etc/containerd/config.toml \
    0644; then
    log "updated containerd configuration"
    runtime_changed=true
  fi

  if [[ "${unit_changed}" == true ]]; then
    systemctl daemon-reload
  fi

  if ! systemctl is-enabled --quiet containerd.service; then
    systemctl enable containerd.service >/dev/null
  fi

  if ! systemctl is-active --quiet containerd.service; then
    systemctl start containerd.service
  elif [[ "${runtime_changed}" == true ]]; then
    systemctl restart containerd.service
  else
    log "containerd is already active and unchanged"
  fi
}

verify_postconditions() {
  log "verifying host postconditions"

  verify_cgroup_v2
  [[ -z "$(swapon --show --noheadings)" ]] || die "swap remains enabled"
  [[ "$(sysctl --values net.ipv4.ip_forward)" == "1" ]] || die "IPv4 forwarding is disabled"
  grep --word-regexp --quiet overlay /proc/filesystems || die "overlay filesystem support is unavailable"

  containerd_is_current || die "containerd ${CONTAINERD_VERSION} is not installed"
  runc_is_current || die "runc ${RUNC_VERSION} is not installed"
  systemctl is-enabled --quiet containerd.service || die "containerd is not enabled"
  systemctl is-active --quiet containerd.service || die "containerd is not active"

  validate_containerd_config /etc/containerd/config.toml
}

main() {
  require_root
  require_amd64
  require_sources
  verify_cgroup_v2
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
