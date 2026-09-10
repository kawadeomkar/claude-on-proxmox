#!/bin/sh
# Stand-in for virt-customize: records its arguments so the Molecule verify
# stage can assert what would have been installed into the image.
set -eu
LOG=/var/lib/fake-virt-customize.log
mkdir -p "$(dirname "$LOG")"
printf '%s\n' "$*" >>"$LOG"
exit 0
