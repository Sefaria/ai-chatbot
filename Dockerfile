FROM node:22 AS script

WORKDIR /build
COPY . .
RUN npm install
RUN npm run build


FROM python:3.11-alpine3.23 AS server

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

USER 1001

EXPOSE 8080

ENTRYPOINT ["gunicorn", "chatbot_server.wsgi:application", "--bind", "0.0.0.0:8080", "--worker-class", "gthread", "--threads", "4"]
