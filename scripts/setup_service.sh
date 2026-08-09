#!/usr/bin/env bash
# Install OceanPulse ingestion as a background service.
#   macOS  -> launchd user agent
#   Linux  -> systemd user unit
#
# Both are user-level. A personal data-collection daemon has no business
# running as root, so neither path needs sudo.

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -x ".venv/bin/python" ]; then
    echo "ERROR: no virtual environment found."
    echo "Run ./run.sh once first — the service needs the environment it creates."
    exit 1
fi

PYTHON="$PROJECT_ROOT/.venv/bin/python"
LABEL="com.oceanpulse.daemon"

case "$(uname -s)" in
Darwin)
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT_ROOT/run.py</string>
        <string>daemon</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT_ROOT</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$PROJECT_ROOT/logs/service.log</string>
    <key>StandardErrorPath</key><string>$PROJECT_ROOT/logs/service_error.log</string>
</dict>
</plist>
PLISTEOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Installed launchd agent: $LABEL"
    echo "Check it with:  launchctl list | grep oceanpulse"
    ;;
Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/oceanpulse.service" <<UNITEOF
[Unit]
Description=OceanPulse marine data ingestion daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
ExecStart=$PYTHON $PROJECT_ROOT/run.py daemon
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload
    systemctl --user enable --now oceanpulse.service
    echo "Installed systemd user unit: oceanpulse.service"
    echo "Check it with:  systemctl --user status oceanpulse"
    echo
    echo "A user service stops at logout. On a headless machine, run:"
    echo "  sudo loginctl enable-linger \$USER"
    ;;
*)
    echo "Unsupported platform: $(uname -s)"
    exit 1
    ;;
esac

echo
echo "Ingestion now runs in the background. Start the dashboard with:"
echo "  ./run.sh --no-daemon"
echo "so you do not end up with two pollers competing."
