FROM seleniarm/standalone-chromium:latest
USER root
ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y xdotool xclip chromium-l10n python3-websocket && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/chromium/policies/managed && \
    echo '{"RestoreOnStartup":5}' \
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
