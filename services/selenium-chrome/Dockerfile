FROM seleniarm/standalone-chromium:latest
USER root
ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y xdotool xclip chromium-l10n && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/chromium/policies/managed && \
    echo '{"RestoreOnStartup":4,"RestoreOnStartupURLs":["https://www.baidu.com"]}' \
    > /etc/chromium/policies/managed/default_homepage.json
COPY start-browser.sh /opt/bin/start-browser.sh
RUN chmod +x /opt/bin/start-browser.sh
COPY browser.conf /etc/supervisor/conf.d/browser.conf
RUN mkdir -p /home/seluser/.fluxbox && \
    printf '[app] (name=chromium-browser)\n  [Maximized] {yes}\n[end]\n' > /home/seluser/.fluxbox/apps && \
    chown -R seluser:seluser /home/seluser/.fluxbox
USER seluser
