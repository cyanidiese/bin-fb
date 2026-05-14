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

# Source
COPY . .

# Ensure writable state dirs exist in the image
RUN mkdir -p /app/data /app/logs

# Build Next.js
RUN cd dashboard && npm run build

EXPOSE 3000

# dashboard runs from /app/dashboard so BOT_ROOT resolves to /app
CMD ["sh", "-c", "cd /app/dashboard && node_modules/.bin/next start -p 3000"]
