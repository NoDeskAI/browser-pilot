FROM seleniarm/standalone-chromium:latest
USER root
RUN apt-get update && apt-get install -y xdotool && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/chromium/policies/managed && \
    echo '{"RestoreOnStartup":4,"RestoreOnStartupURLs":["https://www.baidu.com"]}' \
    > /etc/chromium/policies/managed/default_homepage.json
COPY start-browser.sh /opt/bin/start-browser.sh
RUN chmod +x /opt/bin/start-browser.sh
COPY browser.conf /etc/supervisor/conf.d/browser.conf
USER seluser
