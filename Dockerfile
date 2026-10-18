FROM python:3.12-slim
WORKDIR /app
COPY verified-price-supplement-20261013.json.gz catalog_expansion.py catalog-expansion-20261013.json.gz expanded-market-catalog.json verified-market-prices-20261013.json.gz server.py account_support.py wallet_controls.py index.html maps.js leaflet.js leaflet.css features.js icon.svg icon-192.png icon-512.png sw.js ./
COPY product-photo-*.jpg market-catalog.zip health-beauty-catalog.zip ./
ENV WALLAHA_BIND=0.0.0.0 WALLAHA_PORT=8080 WALLAHA_DB_PATH=/data/wallaha.sqlite3
RUN mkdir -p /data
EXPOSE 8080
CMD ["python3", "server.py"]
