FROM python:3.12-slim-trixie AS build

# Compile Pillow from the locked source distribution, not its bundled decoder.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential pkg-config libjpeg62-turbo-dev zlib1g-dev libfreetype6-dev \
        libbrotli-dev \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libbrotli1)" ge 1.1.0-2+deb13u1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/asset-alerts-matplotlib
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo libfreetype6 libbrotli1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libbrotli1)" ge 1.1.0-2+deb13u1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home app \
    && mkdir /app/data \
    && chown app:app /app/data
COPY --from=build /app/.venv /app/.venv
RUN /app/.venv/bin/python -c 'import ctypes; from PIL import ImageFont; lib = ctypes.CDLL("libbrotlidec.so.1"); lib.BrotliDecoderErrorString.restype = ctypes.c_char_p; assert b"FORMAT_BLOCK_SWITCH" in lib.BrotliDecoderErrorString(-17)'
USER app

ENTRYPOINT ["/app/.venv/bin/asset-alerts"]
CMD ["run"]
