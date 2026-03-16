FROM ghcr.io/m1k1o/neko/chromium:latest
RUN mkdir -p /etc/chromium/policies/managed && \
    echo '{"RestoreOnStartup":4,"RestoreOnStartupURLs":["https://www.baidu.com"]}' \
    > /etc/chromium/policies/managed/default_homepage.json
COPY chromium.conf /etc/neko/supervisord/chromium.conf
