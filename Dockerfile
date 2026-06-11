# Use a lightweight official Python image
FROM python:3.12-slim

# Set environment variables to prevent Python from writing pyc files and to buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/bot.db

# Set work directory
WORKDIR /app

# Create a non-root system user/group and the persistent data directory
RUN groupadd -r botgroup && useradd -r -g botgroup botuser && \
    mkdir -p /data && \
    chown -R botuser:botgroup /data /app

# Copy only requirements to leverage Docker cache
COPY --chown=botuser:botgroup requirements.txt .

# Install Python dependencies (installed globally as root so non-root can access)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY --chown=botuser:botgroup . .

# Expose volume for persistent database
VOLUME ["/data"]

# Switch to the non-root user
USER botuser

# Run the bot application
CMD ["python", "bot.py"]
