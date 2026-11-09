FROM acc/ubuntu-base:noble
ARG https_proxy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends rabbitmq-server && rm -rf /var/lib/apt/lists/* \
 && rabbitmq-plugins enable --offline rabbitmq_management
USER rabbitmq
CMD ["rabbitmq-server"]
