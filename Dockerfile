FROM python:3.10-slim-buster

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Download Daytona CLI globally
RUN curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-amd64 -o /usr/local/bin/daytona \
    && chmod +x /usr/local/bin/daytona

# Setup non-root user (UID 1000) for Hugging Face Spaces compatibility
RUN useradd -m -u 1000 user
WORKDIR /app

# Install python dependencies
RUN pip install --no-cache-dir fastapi uvicorn requests pyngrok

# Copy application files
COPY --chown=user:user web/ /app/web/
COPY --chown=user:user controller.py /app/controller.py

# Create writable directories for Daytona configurations and connections
RUN mkdir -p /app/configs /app/connections \
    && chown -R user:user /app

# Switch to the non-root user
USER 1000

# Set environment variables
ENV WORKSPACE_DIR=/app
ENV PORT=7860

# Expose the default HF Spaces port
EXPOSE 7860

# Start uvicorn
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7860"]
