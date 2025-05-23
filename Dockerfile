# Use Python 3.10 as the base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -U -r requirements.txt

# Install Playwright and its dependencies
RUN playwright install-deps firefox

# Fetch Camoufox browser
RUN camoufox fetch

# Create necessary directories
RUN mkdir -p /app/auth_profiles/active /app/logs /app/errors_py

# Copy application code
COPY . .

# Create entrypoint script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Check if authentication files exist\n\
if [ -z "$(ls -A /app/auth_profiles/active/*.json 2>/dev/null)" ]; then\n\
    echo "ERROR: No authentication files found in /app/auth_profiles/active/"\n\
    echo "Please mount a volume with your authentication files to /app/auth_profiles/active/"\n\
    echo "Example: docker run -v /path/to/your/auth_files:/app/auth_profiles/active ..."\n\
    exit 1\n\
fi\n\
\n\
# Print startup message\n\
echo "Starting AI Studio Proxy API..."\n\
echo "Port: 2048"\n\
echo "Log level: $SERVER_LOG_LEVEL"\n\
echo "Launch mode: $LAUNCH_MODE"\n\
\n\
# Start the application\n\
exec python launch_camoufox.py --headless "$@"\n\
' > /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Note: Running as root to avoid permission issues with mounted volumes
# If needed, the user can be overridden in docker-compose.yml

# Define volumes for persistent data
VOLUME ["/app/auth_profiles", "/app/logs", "/app/errors_py"]

# Expose the port the app runs on
EXPOSE 2048

# Set environment variables
ENV LAUNCH_MODE=direct_debug_no_browser
ENV SERVER_REDIRECT_PRINT=false
ENV SERVER_LOG_LEVEL=INFO
ENV DEBUG_LOGS_ENABLED=false
ENV TRACE_LOGS_ENABLED=false

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:2048/ || exit 1

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command (can be overridden)
CMD []
