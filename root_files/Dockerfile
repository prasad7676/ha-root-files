ARG BUILD_FROM
FROM debian:bookworm-slim

ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    python3 \
    util-linux \
    ntfs-3g \
    exfatprogs \
    exfat-fuse \
    e2fsprogs \
    dosfstools \
    fuse3 \
  && rm -rf /var/lib/apt/lists/*

COPY app /app
COPY run.sh /run.sh
RUN chmod 755 /run.sh

EXPOSE 8099
ENTRYPOINT []
CMD ["/run.sh"]
