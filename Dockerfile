FROM node:22 AS script

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build


FROM python:3.11-alpine3.23 AS server

# Install Node.js for Claude Code CLI (required by claude-agent-sdk)
RUN apk add --no-cache nodejs npm \
    && npm install -g @anthropic-ai/claude-code@2.1.37

# Create managed settings for Claude Code CLI (system-level config)
RUN mkdir -p /etc/claude-code \
    && echo '{}' > /etc/claude-code/managed-settings.json

# set user as non-root with a known UID for Kubernetes
RUN adduser -D -u 1001 appuser \
    && mkdir -p /tmp \
    && chmod 1777 /tmp

COPY /server /server
WORKDIR /server
RUN pip install -U -r requirements.txt
RUN mkdir -p static/js
COPY --from=script /build/dist/lc-chatbot.umd.cjs static/js/
RUN python manage.py collectstatic --noinput

# Entrypoint: pre-warm Claude CLI to initialize ~/.claude/ before first request
RUN printf '#!/bin/sh\nclaude --version > /dev/null 2>&1 || true\nexec "$@"\n' \
    > /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

USER 1001

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh", "gunicorn", "chatbot_server.wsgi:application", "--bind", "0.0.0.0:8080", "--worker-class", "gthread", "--threads", "4"]
