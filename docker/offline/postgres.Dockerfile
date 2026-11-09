FROM acc/ubuntu-base:noble
ARG https_proxy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-16 && rm -rf /var/lib/apt/lists/*
COPY postgres-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PATH=/usr/lib/postgresql/16/bin:$PATH PGDATA=/var/lib/postgresql/data
ENTRYPOINT ["/entrypoint.sh"]
