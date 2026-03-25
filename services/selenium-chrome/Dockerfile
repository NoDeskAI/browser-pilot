FROM seleniarm/standalone-chromium:latest
USER root
RUN apt-get update && apt-get install -y xdotool && rm -rf /var/lib/apt/lists/*

# Hide fluxbox taskbar
RUN mkdir -p /home/seluser/.fluxbox && \
    echo 'session.screen0.toolbar.visible: false' >> /home/seluser/.fluxbox/init && \
    chown -R seluser:seluser /home/seluser/.fluxbox

USER seluser
