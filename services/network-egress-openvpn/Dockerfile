FROM alpine:3.20

RUN apk add --no-cache openvpn tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY start.sh /usr/local/bin/start-openvpn-egress
RUN chmod +x /usr/local/bin/start-openvpn-egress

EXPOSE 8888

CMD ["/usr/local/bin/start-openvpn-egress"]
