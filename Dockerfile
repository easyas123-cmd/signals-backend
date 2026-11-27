# Reference image: Python plus the RTL-SDR tools the collectors shell out to.
FROM debian:stable-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 rtl-sdr rtl-433 aircrack-ng bluez \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend ./backend
COPY config.example.json ./config.json
ENTRYPOINT ["python3"]
