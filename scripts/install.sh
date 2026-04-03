#!/usr/bin/env bash
#
# Install ha-pyscript-automations into a Home Assistant
# configuration directory.
#
# Usage: ./scripts/install.sh [/path/to/ha/config]
#
# Creates relative symlinks for individual files (not
# whole directories) to avoid conflicts with other
# pyscript deployments.
#
# Symlinks use relative paths so they resolve correctly
# across Docker containers that may mount the config
# directory at different absolute paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
HA_CONFIG="${1:-/config}"

if [ ! -d "$HA_CONFIG" ]; then
    echo "Error: HA config dir not found: $HA_CONFIG"
    echo "Usage: $0 [/path/to/ha/config]"
    exit 1
fi

# Resolve to real path so the prefix check works even
# when HA_CONFIG or REPO_DIR traverse symlinks
# (e.g., /config -> /root/config in HA containers).
HA_CONFIG="$(cd "$HA_CONFIG" && pwd -P)"

# Repo must be inside the HA config directory for
# relative symlinks to work.
case "$REPO_DIR" in
    "$HA_CONFIG"/*)
        REPO_REL="${REPO_DIR#"$HA_CONFIG/"}"
        ;;
    *)
        echo "Error: repo must be inside HA config dir"
        echo "  repo:   $REPO_DIR"
        echo "  config: $HA_CONFIG"
        exit 1
        ;;
esac

echo "Installing to: $HA_CONFIG"
echo "  Repo: $REPO_REL"

# Ensure repo files are readable by all containers
chmod -R a+rX "$REPO_DIR"

# ── Files to install ────────────────────────────────
# Paths are relative to both the repo and the HA config
# directory (repo layout mirrors the HA config layout).
#
# Add new files here as automations are added.
FILES=(
    "pyscript/ha_pyscript_automations.py"
    "pyscript/modules/sensor_threshold_switch_controller.py"
    "blueprints/automation/ha_pyscript_automations/sensor_threshold_switch_controller.yaml"
)

# ── Helpers ─────────────────────────────────────────

# Compute relative symlink target for a file.
# The path is relative to both the repo and HA config.
#
# Example: file_rel="pyscript/modules/foo.py" (depth 2)
#   => "../../$REPO_REL/pyscript/modules/foo.py"
relative_target() {
    local file_rel="$1"
    local file_dir
    file_dir="$(dirname "$file_rel")"

    local depth=0
    local d="$file_dir"
    while [ "$d" != "." ]; do
        depth=$((depth + 1))
        d="$(dirname "$d")"
    done

    local prefix=""
    local i
    for ((i = 0; i < depth; i++)); do
        prefix="../$prefix"
    done

    echo "${prefix}${REPO_REL}/${file_rel}"
}

# ── Install loop ────────────────────────────────────

errors=0

for file_rel in "${FILES[@]}"; do
    src_abs="$REPO_DIR/$file_rel"
    dst_abs="$HA_CONFIG/$file_rel"

    if [ ! -f "$src_abs" ]; then
        echo "Error: source not found: $src_abs"
        errors=$((errors + 1))
        continue
    fi

    mkdir -p "$(dirname "$dst_abs")"

    target="$(relative_target "$file_rel")"

    if [ -e "$dst_abs" ] || [ -L "$dst_abs" ]; then
        if [ ! -L "$dst_abs" ]; then
            echo "Error: $file_rel exists but is" \
                "not a symlink"
            errors=$((errors + 1))
            continue
        fi
        existing="$(readlink "$dst_abs")"
        if [ "$existing" != "$target" ]; then
            echo "Error: $file_rel links to" \
                "'$existing', expected '$target'"
            errors=$((errors + 1))
            continue
        fi
        echo "  $file_rel (already linked)"
    else
        ln -s "$target" "$dst_abs"
        echo "  $file_rel (linked)"
    fi
done

if [ "$errors" -gt 0 ]; then
    echo ""
    echo "Failed: $errors error(s). Fix the above" \
        "issues and re-run."
    exit 1
fi

echo ""
echo "Done. Next steps:"
echo "  1. Restart Home Assistant or reload PyScript"
echo "  2. Go to Settings > Automations > Blueprints"
echo "  3. Create automations from installed blueprints"
