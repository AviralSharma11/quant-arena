# The public entry point: the built frontend baked into Caddy (Task 3.3).
#
# Built in CI and pulled by the VM, like every other image — a 1 GB VM should never run a Vite
# build. Context is the repository root; `web.Dockerfile.dockerignore` beside this file replaces
# the root `.dockerignore`, which excludes `web/` for the Python image.

FROM node:24-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM caddy:2.10-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
