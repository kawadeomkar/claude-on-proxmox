#!/usr/bin/env bash
# Stateful stand-in for Proxmox's `qm` so roles/proxmox_template can be tested
# in a container. Stores one config file per VMID under $FAKE_QM_STATE in the
# same "key: value" format as `qm config`, and appends every invocation to
# $FAKE_QM_STATE/calls.log.
set -euo pipefail
STATE="${FAKE_QM_STATE:-/var/lib/fake-qm}"
mkdir -p "$STATE"
printf '%q ' "$@" >> "$STATE/calls.log"; echo >> "$STATE/calls.log"

cmd="${1:-}"; vmid="${2:-}"
write_opts() { # --key value ... -> key: value lines
  while [ $# -gt 0 ]; do
    key="${1#--}"; val="${2:-}"; shift 2 || break
    printf '%s: %s\n' "$key" "$val" >> "$STATE/$vmid.conf"
  done
}
case "$cmd" in
  config)
    [ -f "$STATE/$vmid.conf" ] || { echo "Configuration file 'nodes/pve/qemu-server/$vmid.conf' does not exist" >&2; exit 2; }
    cat "$STATE/$vmid.conf" ;;
  create)
    [ ! -f "$STATE/$vmid.conf" ] || { echo "unable to create VM $vmid: config file already exists" >&2; exit 2; }
    shift 2; : > "$STATE/$vmid.conf"; write_opts "$@" ;;
  set)
    [ -f "$STATE/$vmid.conf" ] || { echo "Configuration file for $vmid does not exist" >&2; exit 2; }
    shift 2; write_opts "$@" ;;
  template)
    [ -f "$STATE/$vmid.conf" ] || { echo "Configuration file for $vmid does not exist" >&2; exit 2; }
    echo "template: 1" >> "$STATE/$vmid.conf" ;;
  *)
    echo "fake qm: unsupported command '$cmd'" >&2; exit 1 ;;
esac
