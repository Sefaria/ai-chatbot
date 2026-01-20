FROM node:22 AS script

WORKDIR /build
COPY . .
RUN npm install


FROM python:3.11-alpine3.23 AS server

# set user as non-root with a known UID for Kubernetes
RUN adduser -D -u 1001 appuser

COPY /server /server
WORKDIR /server
RUN pip install -U -r requirements.txt
COPY --from=script /build/node_modules/svelte/src/internal/client/dom/elements/custom-element.js static/js/

USER 1001

EXPOSE 8080

ENTRYPOINT ["gunicorn", "chatbot_server.wsgi:application", "--bind", "0.0.0.0:8080"] 
