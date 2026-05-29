FROM cloakhq/cloakbrowser:latest

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    fluxbox \
    hsetroot \
    novnc \
    websockify \
    x11-utils \
    x11vnc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir aiohttp

WORKDIR /app

COPY bp_cloak_driver.py /usr/local/bin/bp-cloak-driver
COPY start-cloak-runtime.sh /usr/local/bin/start-cloak-runtime
RUN chmod +x /usr/local/bin/bp-cloak-driver /usr/local/bin/start-cloak-runtime

EXPOSE 4444 7900

ENTRYPOINT ["/usr/local/bin/start-cloak-runtime"]
