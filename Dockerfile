FROM python:3.12-slim

# Node.js 22
RUN apt-get update && apt-get install -y curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python virtualenv — cached layer
COPY requirements.txt ./
RUN python3 -m venv .venv && \
    .venv/bin/pip install --no-cache-dir -r requirements.txt

# Node deps — cached layer
COPY dashboard/package.json dashboard/package-lock.json ./dashboard/
RUN cd dashboard && npm ci

# ── Dashboard source and build ──────────────────────────────────────────────
# The dashboard's own sources are copied FIRST and built here, before the Python
# source. `COPY . .` used to sit above `npm run build`, so any Python change
# invalidated that layer and rebuilt the whole Next.js app — about 40s of every
# bot-only deploy, for an artifact the `bot` service never serves.
COPY dashboard/ ./dashboard/
RUN cd dashboard && npm run build

# ── Python source ───────────────────────────────────────────────────────────
# Last, so editing main.py or bot/ leaves the Next build cached.
COPY . .

# Ensure writable state dirs exist in the image
RUN mkdir -p /app/data /app/logs

EXPOSE 3000

# dashboard runs from /app/dashboard so BOT_ROOT resolves to /app
CMD ["sh", "-c", "cd /app/dashboard && node_modules/.bin/next start -p 3000"]
