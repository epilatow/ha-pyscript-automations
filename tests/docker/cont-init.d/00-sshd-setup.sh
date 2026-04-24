#!/usr/bin/env bash
# This is AI generated code
# One-shot sshd setup: generate host keys, install the
# test-fixture-provided public key into authorized_keys,
# and allow root login with keys only. Runs before any
# s6 service starts.

set -euo pipefail

# Generate host keys if not already present. Keys live
# under /etc/ssh and persist for the life of the container.
ssh-keygen -A

# Authorize the test key if the fixture has bind-mounted
# one at /test/authorized_keys. Absence is fine when the
# container runs for manual exploration.
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ -f /test/authorized_keys ]; then
    cat /test/authorized_keys >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi

# sshd config: disable password auth, allow root via key.
# Do not overwrite an existing customized config; only
# fill in the defaults the harness needs.
cat > /etc/ssh/sshd_config.d/01-test-harness.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
UsePAM no
EOF
