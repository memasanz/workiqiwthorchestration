# syntax=docker/dockerfile:1.6
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund --loglevel=error
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

# MSAL build-time configuration. Must be passed in at `docker build` time
# (Vite inlines them into the bundle). Defaults are non-secret tenant +
# app-reg IDs from the Iter5 Entra setup.
ARG VITE_TENANT_ID=""
ARG VITE_SPA_CLIENT_ID=""
ARG VITE_API_CLIENT_ID=""
ENV VITE_TENANT_ID=$VITE_TENANT_ID \
    VITE_SPA_CLIENT_ID=$VITE_SPA_CLIENT_ID \
    VITE_API_CLIENT_ID=$VITE_API_CLIENT_ID

RUN npm run build

FROM nginx:1.27-alpine AS runtime
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV BACKEND_BASE_URL="https://ca-mpwflow-dev-chat-api.icyground-4e2c6fde.eastus2.azurecontainerapps.io" \
    PORT=8080 \
    NGINX_ENVSUBST_FILTER="^(BACKEND_BASE_URL|PORT)$"

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
