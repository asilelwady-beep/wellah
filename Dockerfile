FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html maps.js icon.svg icon-192.png icon-512.png sw.js ./
ENV WALLAHA_BIND=0.0.0.0 WALLAHA_PORT=8080 WALLAHA_DB_PATH=/data/wallaha.sqlite3
RUN mkdir -p /data
EXPOSE 8080
CMD ["python3", "server.py"]
