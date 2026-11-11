# syntax=docker/dockerfile:1
FROM node:26-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:26-alpine AS builder
WORKDIR /app
ARG API_INTERNAL_URL=http://biblioflow-api:8000
ENV API_INTERNAL_URL=${API_INTERNAL_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:26-alpine AS runner
ARG UID=1000
ARG GID=1000
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

# Security updates of the base packages, and no npm / corepack at runtime (the server is `node server.js`;
# npm's bundled dependencies only add findings to the image scan).
RUN apk upgrade --no-cache \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
# alpine の node イメージには UID/GID 1000 の node ユーザーが既にあるため先に削除する
RUN (deluser --remove-home node 2>/dev/null || true) \
 && (delgroup node 2>/dev/null || true) \
 && addgroup -g "${GID}" nextjs \
 && adduser -D -u "${UID}" -G nextjs -s /sbin/nologin nextjs \
 && mkdir -p /app/.next/cache \
 && chown -R nextjs:nextjs /app

COPY --from=builder --chown=nextjs:nextjs /app/public ./public
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
