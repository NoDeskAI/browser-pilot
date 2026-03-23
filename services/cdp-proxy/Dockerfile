FROM node:20-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    chromium \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY . .

EXPOSE 3100
CMD ["node", "index.js"]
