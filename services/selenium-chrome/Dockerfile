ARG BASE_IMAGE=selenium/standalone-chrome:latest
ARG CHROME_VERSION=
FROM ${BASE_IMAGE}
USER root
ENV LANG=C.UTF-8

ARG CHROME_VERSION
RUN if [ -n "${CHROME_VERSION}" ]; then \
      INSTALLED=$(dpkg -s chromium 2>/dev/null | sed -n 's/^Version: //p' | grep -oP '^\d+' || echo "0"); \
      TARGET=$(echo "${CHROME_VERSION}" | grep -oP '^\d+'); \
      if [ "$INSTALLED" != "$TARGET" ]; then \
        echo ">>> Upgrading Chromium: ${INSTALLED} -> ${CHROME_VERSION}" \
        && echo "deb http://deb.debian.org/debian sid main" > /etc/apt/sources.list.d/sid.list \
        && apt-get update \
        && (apt-get install -y --allow-downgrades -t sid "chromium=${CHROME_VERSION}*" "chromium-common=${CHROME_VERSION}*" 2>/dev/null \
            || apt-get install -y --allow-downgrades -t sid chromium chromium-common) \
        && rm -f /etc/apt/sources.list.d/sid.list \
        && rm -rf /var/lib/apt/lists/*; \
      fi; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       xdotool xclip python3-websocket \
       libegl-mesa0 libegl1 \
       fonts-croscore fonts-crosextra-carlito fonts-crosextra-caladea \
       fonts-liberation fontconfig \
    && rm -rf /var/lib/apt/lists/*
COPY fonts-local.conf /etc/fonts/local.conf
RUN fc-cache -f
RUN if [ ! -f /usr/lib/chromium/locales/zh-CN.pak ]; then \
        CHROME_VER=$(dpkg -s chromium 2>/dev/null | sed -n 's/^Version: //p'); \
        if [ -n "$CHROME_VER" ]; then \
            HASH=$(wget -q -O- "https://snapshot.debian.org/mr/binary/chromium-l10n/$CHROME_VER/binfiles" \
                | python3 -c "import sys,json; r=json.load(sys.stdin).get('result',[]); print(r[0]['hash'] if r else '')" 2>/dev/null) \
            && [ -n "$HASH" ] \
            && wget -q -O /tmp/l10n.deb "https://snapshot.debian.org/file/$HASH" \
            && dpkg -i --force-depends /tmp/l10n.deb \
            && rm -f /tmp/l10n.deb \
            || echo "WARN: chromium-l10n not installed, browser UI will be English"; \
        fi; \
    fi
RUN mkdir -p /etc/chromium/policies/managed && \
    echo '{"RestoreOnStartup":5,"CommandLineFlagSecurityWarningsEnabled":false}' \
    > /etc/chromium/policies/managed/default_homepage.json
COPY start-browser.sh /opt/bin/start-browser.sh
RUN chmod +x /opt/bin/start-browser.sh
COPY browser.conf /etc/supervisor/conf.d/browser.conf
COPY stealth-ext /opt/stealth-ext
RUN mkdir -p /home/seluser/.fluxbox && \
    printf '[app] (name=chromium-browser)\n  [Maximized] {yes}\n[end]\n' > /home/seluser/.fluxbox/apps && \
    printf 'session.screen0.toolbar.visible: false\n' > /home/seluser/.fluxbox/init && \
    chown -R seluser:seluser /home/seluser/.fluxbox
RUN mkdir -p /home/seluser/chrome-data/webdriver /home/seluser/chrome-data/manual \
    && chown -R seluser:seluser /home/seluser/chrome-data
RUN chown -R seluser:seluser /opt/stealth-ext
COPY cdp-logger.py /opt/bin/cdp-logger.py
COPY cdp-logger.conf /etc/supervisor/conf.d/cdp-logger.conf
USER seluser
