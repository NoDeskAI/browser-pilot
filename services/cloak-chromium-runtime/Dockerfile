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

RUN mkdir -p /etc/chromium/policies/managed /etc/opt/chrome/policies/managed && \
    printf '%s\n' '{"CommandLineFlagSecurityWarningsEnabled":false}' \
    | tee /etc/chromium/policies/managed/browser-pilot.json /etc/opt/chrome/policies/managed/browser-pilot.json >/dev/null

RUN mkdir -p /root/.fluxbox /home/seluser/.fluxbox && \
    printf '[app] (class=Chromium-browser)\n  [Maximized] {yes}\n[end]\n[app] (class=Google-chrome)\n  [Maximized] {yes}\n[end]\n[app] (name=chromium-browser)\n  [Maximized] {yes}\n[end]\n[app] (name=google-chrome)\n  [Maximized] {yes}\n[end]\n' \
      | tee /root/.fluxbox/apps /home/seluser/.fluxbox/apps >/dev/null && \
    printf 'session.screen0.toolbar.visible: false\n' \
      | tee /root/.fluxbox/init /home/seluser/.fluxbox/init >/dev/null && \
    if id seluser >/dev/null 2>&1; then chown -R seluser:seluser /home/seluser/.fluxbox; fi

WORKDIR /app

COPY bp_cloak_driver.py /usr/local/bin/bp-cloak-driver
COPY start-cloak-runtime.sh /usr/local/bin/start-cloak-runtime
RUN chmod +x /usr/local/bin/bp-cloak-driver /usr/local/bin/start-cloak-runtime

EXPOSE 4444 7900

ENTRYPOINT ["/usr/local/bin/start-cloak-runtime"]
