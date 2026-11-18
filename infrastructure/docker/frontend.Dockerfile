# Build the SPA, then serve it
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 80
HEALTHCHECK CMD wget -qO- http://127.0.0.1:80/ >/dev/null || exit 1
CMD ["serve", "-s", "dist", "-l", "80"]
