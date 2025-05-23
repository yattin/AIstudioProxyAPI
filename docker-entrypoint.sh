#!/bin/bash
set -e

# Check if authentication files exist
if [ -z "$(ls -A /app/auth_profiles/active/*.json 2>/dev/null)" ]; then
    echo "ERROR: No authentication files found in /app/auth_profiles/active/"
    echo "Please mount a volume with your authentication files to /app/auth_profiles/active/"
    echo "Example: docker run -v /path/to/your/auth_files:/app/auth_profiles/active ..."
    exit 1
fi

# Print startup message
echo "Starting AI Studio Proxy API..."
echo "Port: 2048"
echo "Log level: $SERVER_LOG_LEVEL"
echo "Launch mode: $LAUNCH_MODE"

# Start the application
exec python launch_camoufox.py --headless "$@"
