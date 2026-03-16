FROM kasmweb/chromium:1.16.0
USER root
RUN apt-get update && apt-get install -y xdotool && rm -rf /var/lib/apt/lists/*
RUN sed -i 's/-sslOnly//g' /dockerstartup/vnc_startup.sh
COPY kasmvnc.yaml /etc/kasmvnc/kasmvnc.yaml
USER 1000
