# Enhanced Chat System - Optimized Dockerfile with Uvicorn
# Supports both development (hot reload) and production modes

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install essential system dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Database client
    postgresql-client \
    # Cache client
    redis-tools \
    # Network tools
    curl \
    wget \
    ca-certificates \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements file
COPY requirements.txt .

# Debug: Show requirements.txt content
RUN echo "=== Requirements.txt content ===" && cat requirements.txt

# Install Python dependencies step by step
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install authentication libraries explicitly first
RUN echo "Installing authentication libraries..." && \
    pip install --no-cache-dir \
    python-jose[cryptography]==3.3.0 \
    passlib==1.7.4 \
    bcrypt==4.1.2 \
    cryptography==41.0.7

# Verify passlib is installed
RUN echo "Verifying passlib..." && \
    python -c "from passlib.context import CryptContext; print('✅ passlib works!')" && \
    pip show passlib

# Install rest of requirements
RUN echo "Installing remaining requirements..." && \
    pip install --no-cache-dir -r requirements.txt

# Final verification
RUN echo "=== Final package list ===" && pip list | grep -E "(passlib|bcrypt|jose)"

# Copy application code
COPY chat_system.py .

# Create upload directory for files
RUN mkdir -p /app/uploads /app/logs && \
    chmod 755 /app/uploads /app/logs

# Create non-root user for security (production)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "chat_system.py"]
# Production: Run with uvicorn (can be overridden by docker-compose)
# CMD ["uvicorn", "chat_system:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]