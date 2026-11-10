FROM acc/ubuntu-base:noble
ARG https_proxy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends redis-server && rm -rf /var/lib/apt/lists/*
USER redis
CMD ["redis-server", "--save", "", "--appendonly", "no", "--bind", "0.0.0.0", "--protected-mode", "no"]
