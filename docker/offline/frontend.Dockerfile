FROM acc/ubuntu-base:noble
COPY docker/offline/node /usr/local/bin/node
COPY docker/offline/ca.crt /usr/local/share/ca-certificates/proxy.crt
RUN update-ca-certificates >/dev/null
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# Built on the host with the same API_INTERNAL_URL the official Dockerfile uses
COPY frontend/.next/standalone ./
COPY frontend/.next/static ./.next/static
RUN useradd --create-home app
USER app
CMD ["node", "server.js"]
