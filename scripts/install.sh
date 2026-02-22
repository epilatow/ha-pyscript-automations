#!/usr/bin/env bash
#
# Install ha-pyscript-automations into a Home Assistant
# configuration directory.
#
# Usage: ./scripts/install.sh [/path/to/ha/config]
#
# Creates relative symlinks for individual files (not
# whole directories) to avoid conflicts with other
# pyscript deployments:
#   - pyscript/ha_pyscript_automations.py
#   - pyscript/modules/*.py (each file individually)
#   - blueprints/automation/ha_pyscript_automations/*.yaml
#
# Symlinks use relative paths so they resolve correctly
# across Docker containers that may mount the config
# directory at different absolute paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HA_CONFIG="${1:-/config}"

if [ ! -d "$HA_CONFIG" ]; then
    echo "Error: HA config dir not found: $HA_CONFIG"
    echo "Usage: $0 [/path/to/ha/config]"
    exit 1
fi

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

# Ensure pyscript directories exist
mkdir -p "$HA_CONFIG/pyscript/modules"

# Symlink service file (from pyscript/, ../$REPO_REL/...)
SVC="ha_pyscript_automations.py"
if [ -e "$HA_CONFIG/pyscript/$SVC" ]; then
    echo "  $SVC already exists, skipping"
else
    ln -s "../$REPO_REL/pyscript/$SVC" \
        "$HA_CONFIG/pyscript/$SVC"
    echo "  Linked pyscript/$SVC"
fi

# Symlink individual module files
# (from pyscript/modules/, ../../$REPO_REL/...)
for mod in "$REPO_DIR"/pyscript/modules/*.py; do
    [ -f "$mod" ] || continue
    name="$(basename "$mod")"
    [ "$name" = "__init__.py" ] && continue
    if [ -e "$HA_CONFIG/pyscript/modules/$name" ]; then
        echo "  modules/$name already exists, skipping"
    else
        ln -s "../../$REPO_REL/pyscript/modules/$name" \
            "$HA_CONFIG/pyscript/modules/$name"
        echo "  Linked pyscript/modules/$name"
    fi
done

# Symlink individual blueprint files
# (from blueprints/automation/ha_pyscript_automations/,
#  ../../../$REPO_REL/...)
BP_DIR="$HA_CONFIG/blueprints/automation"
BP_DIR="$BP_DIR/ha_pyscript_automations"
mkdir -p "$BP_DIR"

for bp in "$REPO_DIR"/blueprints/*.yaml; do
    [ -f "$bp" ] || continue
    name="$(basename "$bp")"
    if [ -e "$BP_DIR/$name" ]; then
        echo "  blueprints/$name already exists, skipping"
    else
        ln -s "../../../$REPO_REL/blueprints/$name" \
            "$BP_DIR/$name"
        echo "  Linked blueprints/$name"
    fi
done

echo ""
echo "Done. Next steps:"
echo "  1. Restart Home Assistant or reload PyScript"
echo "  2. Go to Settings > Automations > Blueprints"
echo "  3. Create automation from"
echo "     'Sensor Threshold Switch Controller'"
