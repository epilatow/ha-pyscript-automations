# This is AI generated code
# Docker image used by the blueprint_toolkit docker
# test harness. Extends the upstream Home Assistant image
# with openssh-server, git, and bash so the in-repo test
# suite can exercise scripts/install.sh and
# scripts/dev-deploy.py against a running HA instance.

ARG HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable
FROM ${HA_IMAGE}

# openssh-server: dev-deploy.py ssh's into the HA host to
# run install.sh + reload services. In tests we ssh
# root@localhost from inside this container to itself.
# git: dev-deploy.py requires a git repo on the source
# host to diff its working tree.
# bash: install.sh is a bash script; busybox's sh is
# not fully bash-compatible.
RUN apk add --no-cache openssh-server openssh-keygen git bash

# s6 cont-init hook: runs once at container start, before
# any services. Generates sshd host keys and installs the
# test-fixture-provided public key into
# /root/.ssh/authorized_keys.
COPY cont-init.d/ /etc/cont-init.d/
RUN chmod +x /etc/cont-init.d/*

# s6 service: long-running sshd, supervised alongside HA.
COPY services.d/ /etc/services.d/
RUN chmod +x /etc/services.d/sshd/run
