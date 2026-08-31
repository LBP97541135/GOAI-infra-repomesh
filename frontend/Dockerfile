# Delivery console image for `docker compose --profile console up` (batch S-2).
#
# Stage 1 builds the SPA; stage 2 serves it with nginx, which also reverse
# proxies the API. Serving the bundle from nginx rather than mounting it into
# the FastAPI app keeps the API image and src/ untouched, and gives the browser
# a single origin (no CORS, no dev proxy).

FROM node:22-alpine AS build
WORKDIR /app

# registry.npmjs.org is unreliably slow from this team's network; default to a
# fast regional mirror and allow per-build override with
# `docker compose build --build-arg NPM_REGISTRY=...`.
ARG NPM_REGISTRY=https://registry.npmmirror.com
COPY package.json package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}

COPY . .

# Runtime credentials are written by the nginx entrypoint. They are never part
# of the image build command, layer history, or frontend cache.
RUN npm run build

FROM nginx:1.27-alpine
ARG API_UPSTREAM=console-api
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN sed -i "s/__REPOMESH_API_UPSTREAM__/${API_UPSTREAM}/g" /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh
EXPOSE 80
