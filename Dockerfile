FROM node:22 AS script

WORKDIR /build
COPY . .
RUN npm install
RUN npm run build


FROM python:3.11-alpine3.23 AS server

# Install Node.js for Claude Code CLI (required by claude-agent-sdk)
RUN apk add --no-cache nodejs npm \
    && npm install -g @anthropic-ai/claude-code

# set user as non-root with a known UID for Kubernetes
RUN adduser -D -u 1001 appuser \
    && mkdir -p /home/appuser/.claude \
    && printf '{}' > /home/appuser/.claude/remote-settings.json \
    && chown -R appuser:appuser /home/appuser \
    && chmod 700 /home/appuser/.claude \
    && chmod 600 /home/appuser/.claude/remote-settings.json \
    && mkdir -p /tmp \
    && chmod 1777 /tmp

COPY /server /server
WORKDIR /server
RUN pip install -U -r requirements.txt
RUN mkdir -p static/js
COPY --from=script /build/dist/lc-chatbot.umd.cjs static/js/
RUN python manage.py collectstatic --noinput

ENV HOME=/home/appuser
USER 1001

EXPOSE 8080

ENTRYPOINT ["gunicorn", "chatbot_server.wsgi:application", "--bind", "0.0.0.0:8080", "--worker-class", "gthread", "--threads", "4"]
