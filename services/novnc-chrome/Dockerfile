FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:1

RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    chromium-browser \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    git \
    python3 \
    python3-numpy \
    xdotool \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc && \
    git clone --depth 1 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify

COPY start.sh /start.sh
COPY nav-api.py /nav-api.py
RUN chmod +x /start.sh /nav-api.py

EXPOSE 6080 6081

CMD ["/start.sh"]
