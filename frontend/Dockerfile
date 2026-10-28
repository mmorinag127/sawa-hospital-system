FROM node:24-trixie-slim AS trusted-base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419+deb13u1' \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_OPTIONS=--use-openssl-ca \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs

FROM trusted-base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
COPY vendor ./vendor
RUN npm ci

FROM trusted-base AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=/api
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=
ARG NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED=false
ARG NEXT_PUBLIC_PORTAL_URL=
ARG API_PROXY_TARGET
ARG SHIFT_WEB_PROXY_TARGET
ARG SCHOOL_LUNCH_WEB_PROXY_TARGET
ENV API_PROXY_TARGET=${API_PROXY_TARGET}
ENV SHIFT_WEB_PROXY_TARGET=${SHIFT_WEB_PROXY_TARGET}
ENV SCHOOL_LUNCH_WEB_PROXY_TARGET=${SCHOOL_LUNCH_WEB_PROXY_TARGET}
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=${NEXT_PUBLIC_GOOGLE_CLIENT_ID}
ENV NEXT_PUBLIC_PORTAL_URL=${NEXT_PUBLIC_PORTAL_URL}
ENV NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED=${NEXT_PUBLIC_SAWA_ASSISTANT_TRIAL_ENABLED}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM trusted-base AS runner
WORKDIR /app
ARG NEXT_PUBLIC_GIT_SHA=unknown
ARG NEXT_PUBLIC_DEPLOYED_AT=unknown
ENV SAWA_GIT_SHA=${NEXT_PUBLIC_GIT_SHA}
ENV SAWA_DEPLOYED_AT=${NEXT_PUBLIC_DEPLOYED_AT}
ENV NODE_ENV=production
ENV PORT=8080
ENV HOSTNAME=0.0.0.0
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/node_modules/@sawa/ui ./node_modules/@sawa/ui
CMD ["node", "server.js"]
