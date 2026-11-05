# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim-bookworm AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
# Supply a combined CA bundle only when the build environment uses a TLS proxy.
# The secret mount and its trust variables never enter the runtime image.
RUN --mount=type=secret,id=combined_ca,required=false \
    --mount=type=cache,target=/root/.cache/uv \
    if [ -f /run/secrets/combined_ca ]; then \
        export SSL_CERT_FILE=/run/secrets/combined_ca \
               REQUESTS_CA_BUNDLE=/run/secrets/combined_ca \
               PIP_CERT=/run/secrets/combined_ca; \
    fi; \
    uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PA_WEB_DATA_DIR=/tmp/pa-agent
RUN groupadd --gid 10001 pa-agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/pa-agent pa-agent
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
COPY app.py pyproject.toml README.md LICENSE ./
COPY pa_agent ./pa_agent
COPY public ./public
COPY prompt_engineering ./prompt_engineering
COPY third_party ./third_party
# Managed checkouts may have owner-only source permissions; retain root ownership
# while allowing the runtime user to read files and traverse source directories.
RUN chmod -R a+rX /app
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
