FROM alpine:3.22

LABEL org.opencontainers.image.title="Synology DVD Bridge" \
      org.opencontainers.image.description="Read a CD/DVD on a Linux NAS, share its files, and convert unprotected DVD-Video to Jellyfin MP4." \
      org.opencontainers.image.source="https://github.com/yuliankanters2011-tech/dvd" \
      org.opencontainers.image.licenses="MIT"

# Alpine's util-linux is built with libmount support. Python uses only its
# standard library; there are no pip packages or runtime downloads.
RUN apk add --no-cache python3 util-linux ddrescue ffmpeg \
    && mkdir -p /media/dvd /srv/dvd /var/lib/dvd

WORKDIR /opt/dvd-bridge
COPY app/ ./app/
COPY web/ ./web/
COPY scripts/container-entrypoint.sh /usr/local/bin/dvd-entrypoint

RUN chmod 0755 /usr/local/bin/dvd-entrypoint \
    && python3 -m compileall -q /opt/dvd-bridge/app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEVICE=/dev/sr0 \
    SHARE_DIR=/srv/dvd \
    STATE_DIR=/var/lib/dvd \
    POLL_SECONDS=5 \
    WEB_PORT=8080 \
    FILMS_DIR=/volume1/video/Films \
    MUSIC_DIR=/volume1/video/Muziek \
    SLEEP_IDLE_SECONDS=600 \
    DRIVE_SPEED=0

EXPOSE 8080
VOLUME ["/var/lib/dvd"]

# No optical disc is required at startup. An absent disc is a normal state.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEB_PORT','8080')+'/healthz',timeout=3).read()" || exit 1

ENTRYPOINT ["/usr/local/bin/dvd-entrypoint"]
CMD ["python3", "-m", "app.main"]