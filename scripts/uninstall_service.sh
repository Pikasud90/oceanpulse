#!/usr/bin/env bash
# Remove the OceanPulse background service.
set -euo pipefail
LABEL="com.oceanpulse.daemon"

case "$(uname -s)" in
Darwin)
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed launchd agent $LABEL"
    ;;
Linux)
    systemctl --user disable --now oceanpulse.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/oceanpulse.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed systemd user unit oceanpulse.service"
    ;;
*)
    echo "Unsupported platform: $(uname -s)"
    exit 1
    ;;
esac
