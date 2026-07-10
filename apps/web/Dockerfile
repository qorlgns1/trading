# syntax=docker/dockerfile:1.7
FROM node:24-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile

FROM base AS builder
COPY --from=deps /app/node_modules /app/node_modules
COPY --from=deps /app/apps/web/node_modules /app/apps/web/node_modules
COPY . .
RUN pnpm --filter web build

FROM node:24-slim AS runner
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN groupadd --gid 10001 nodeapp && useradd --uid 10001 --gid nodeapp --create-home nodeapp
COPY --from=builder --chown=nodeapp:nodeapp /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nodeapp:nodeapp /app/apps/web/.next/static ./apps/web/.next/static
USER nodeapp
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
