FROM acc/ubuntu-base:noble
ARG https_proxy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv git && rm -rf /var/lib/apt/lists/*
COPY docker/offline/uv /usr/local/bin/uv
COPY docker/offline/ca.crt /usr/local/share/ca-certificates/proxy.crt
RUN update-ca-certificates >/dev/null
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_SYSTEM_CERTS=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/bin/python3
COPY backend/ .
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 acc && mkdir -p /data/workspaces && chown acc /data/workspaces
USER acc
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
