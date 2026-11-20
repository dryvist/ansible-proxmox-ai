ARG SUPERGATEWAY_IMAGE
FROM ${SUPERGATEWAY_IMAGE}

USER root
ARG MCP_PACKAGE
RUN npm install --global --prefix /opt/next-ai-drawio-mcp --ignore-scripts \
      --cache /tmp/npm-cache "${MCP_PACKAGE}" \
    && rm -rf /tmp/npm-cache

USER 1000:1000
