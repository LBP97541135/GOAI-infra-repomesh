# Delivery console image for `docker compose --profile console up` (batch S-2).
#
# Stage 1 builds the SPA; stage 2 serves it with nginx, which also reverse
# proxies the API. Serving the bundle from nginx rather than mounting it into
# the FastAPI app keeps the API image and src/ untouched, and gives the browser
# a single origin (no CORS, no dev proxy).

FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The read-model endpoints authenticate with a Bearer agent-action token, which
# is baked into the bundle at build time. It must match REPOMESH_AGENT_ACTION_TOKEN
# on the API container. This is a local/demo credential, not a secret.
ARG VITE_API_TOKEN=console-dev-token
# Written as .env.production because `vite build` runs in production mode and
# never loads .env.development. Empty VITE_API_BASE = same origin.
RUN printf 'VITE_API_BASE=\nVITE_API_TOKEN=%s\n' "$VITE_API_TOKEN" > .env.production \
    && npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
