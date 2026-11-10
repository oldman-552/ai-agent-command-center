# Adds the -updates and -security suites to the debootstrapped base (debootstrap only configures "noble") and
# applies every published update, so the offline images are patched like the official ones.
#   docker build --network host --build-arg https_proxy --build-arg http_proxy \
#     -t acc/ubuntu-base:noble -f docker/offline/base.Dockerfile docker/offline
FROM acc/ubuntu-base:noble
ARG https_proxy
ARG http_proxy
RUN printf '%s\n' \
      "deb http://archive.ubuntu.com/ubuntu noble main universe" \
      "deb http://archive.ubuntu.com/ubuntu noble-updates main universe" \
      "deb http://security.ubuntu.com/ubuntu noble-security main universe" > /etc/apt/sources.list \
 && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
