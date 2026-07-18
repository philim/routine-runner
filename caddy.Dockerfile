# Caddy with the Cloudflare DNS plugin for DNS-01 issuance (spec §9.1).
# There is no official prebuilt image with DNS plugins, so we build one.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
