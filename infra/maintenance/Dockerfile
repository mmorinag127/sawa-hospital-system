ARG FRONTEND_IMAGE
FROM ${FRONTEND_IMAGE}
WORKDIR /sawa-maintenance
COPY server.cjs ./server.cjs
ENTRYPOINT ["node", "/sawa-maintenance/server.cjs"]
