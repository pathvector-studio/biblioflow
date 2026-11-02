# syntax=docker/dockerfile:1
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ARG API_INTERNAL_URL=http://biblioflow-api:8000
ENV API_INTERNAL_URL=${API_INTERNAL_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
ARG UID=1000
ARG GID=1000
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

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
