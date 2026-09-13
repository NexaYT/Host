# ============================================================
#  Bot Hosting System — Dockerfile
#  Base: Python 3.11 slim (small image, fast build)
# ============================================================

FROM python:3.11-slim

# ── System dependencies ──────────────────────────────────────
# gcc / libffi / libssl are needed by some bot packages (e.g. cryptography, cffi)
# pip is upgraded so package installs inside the container work reliably
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libssl-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ──────────────────────────────────────
# Copy requirements first so Docker can cache this layer separately.
# If only host28_updated.py changes, pip install is NOT re-run.
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── App source ───────────────────────────────────────────────
COPY host28_updated.py .

# ── Persistent data directories ──────────────────────────────
# hosted_bots/  → uploaded bot .py files
# data/         → config JSON, logs
# Mount these as Docker volumes so data survives container restarts.
RUN mkdir -p /app/hosted_bots /app/data

# ── Environment variables (override at runtime) ───────────────
# BOT_TOKEN       — your Telegram bot token (required)
# ADMIN_ID        — your Telegram user ID  (required)
# BACKUP_CHANNEL  — channel ID for cloud backup (optional)
# PORT            — HTTP health-check port (default 8080)
ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Health-check ──────────────────────────────────────────────
# Docker / Render will probe /ping every 30 s.
# If the bot hangs, the container is restarted automatically.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ping || exit 1

# ── Expose port ───────────────────────────────────────────────
EXPOSE ${PORT}

# ── Entry point ───────────────────────────────────────────────
CMD ["python", "-u", "main.py"]
