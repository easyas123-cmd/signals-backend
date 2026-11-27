# signals-backend

A small, generic backend for the [signals-console](https://github.com/easyas123-cmd/signals-console)
dashboard. It turns one or more SDRs, a Wi-Fi adapter, and a Bluetooth adapter into the JSON feeds
the console reads: RF devices, a spectrum sweep, ADS-B aircraft, Wi-Fi/BLE, and correlated
incidents. It is standard-library Python with no runtime dependencies, and it runs in a simulation
mode with no hardware at all.

Design notes are in [docs/backend.md](https://github.com/easyas123-cmd/signals-console/blob/main/docs/backend.md)
in the console repo.

## Try it with no hardware

```sh
# serves the API and a live, simulated dataset on http://localhost:8080
python3 -m backend.server --sim

# to also serve the dashboard UI, build the console and point --web at its dist/
make sim CONSOLE=../signals-console/dist
```

Open `http://localhost:8080/`. The simulation drives the whole pipeline: it generates evolving
devices, Wi-Fi activity, aircraft, and an RF sweep, runs them through the correlator, and streams
events over `/live`, so the dashboard shows a working system (the "live" indicator goes green)
without a single radio attached.

## Run it for real

The backend is a handful of small processes that share a `data/` directory:

- **collectors** write one feed each: `rf433` (rtl_433 decodes), `sweep` (rtl_power occupancy),
  `adsb` (dump1090), `wifi` (airodump-ng), and `ble` (BlueZ). `wifi` and `ble` share `wifi.json`
  through a lock, each owning its slice.
- the **correlator** turns collector events into `incidents.json`.
- the **web** layer serves `data/`, the `/live` SSE stream, the `/annotations` store, and `/jobs/*`.

You need the RTL-SDR tools on PATH (`rtl_433`, `rtl_power`) and, for ADS-B, a running
`dump1090`/`readsb`. Wi-Fi capture needs a monitor-mode adapter and the aircrack-ng suite
(`sudo airmon-ng start wlan0`); BLE needs a Bluetooth adapter and BlueZ (`bluetoothctl`). Docker
Compose wires it together:

```sh
cp config.example.json config.json      # edit the SDR serials for your dongles
docker compose up --build
```

Or run the pieces directly:

```sh
python3 -m backend.server --port 8080 &                 # web + live + annotations
python3 -m backend.correlator_service &                 # events.jsonl -> incidents.json
python3 -m backend.collectors.rf433 --serial ISM433 --freq 433.92M &
python3 -m backend.collectors.sweep --serial SCAN --range 24M:1750M:1M &
```

## Multiple SDRs, always on

`rtl_433` and `dump1090` can't share one dongle at the same time, so continuous coverage of several
bands needs several radios. Give each dongle a stable serial (`rtl_eeprom -s ISM433`) and map
serials to roles in `config.json`:

```json
{
  "scheduler": { "mode": "dedicated" },
  "sdrs": [
    { "serial": "ISM433",   "role": "rf433", "freq": "433.92M" },
    { "serial": "ADSB1090", "role": "adsb" },
    { "serial": "SCAN",     "role": "sweep", "range": "24M:1750M:1M" },
    { "serial": "SUB1G",    "role": "multi", "rotate": ["fobs315", "ism915", "meters"] }
  ]
}
```

- **dedicated** mode runs each role's collector as its own always-on process, so the 433 listener,
  ADS-B, and the sweep all run at once.
- a `multi` role time-shares one dongle across several sub-GHz jobs on a rotation, which is also the
  single-dongle fallback.

Address dongles by serial, never by index (indices reorder across reboots). Check what's present:

```sh
python3 -m backend.sdr list                # discovered dongles + serials
python3 -m backend.sdr plan --config config.json
```

Use a powered USB hub for several dongles, and watch USB bandwidth and front-end overload.

## Layout

```
backend/
  server.py              web layer: feeds, /annotations (rev/409), /live SSE, /jobs
  model.py correlate.py  simulation world model and the reference rule engine
  correlator_service.py  real-mode correlator (tails events.jsonl)
  sdr.py                 discover by serial, plan role->serial, per-serial lock
  feeds.py               atomic feed writes + the event log
  collectors/            rf433, sweep, adsb, wifi, ble (hardware path)
schema/                  JSON schema for the data contract
config.example.json  docker-compose.yml  Dockerfile  Makefile
```

## A note on use

Receive only. The backend never transmits. Decoding or capturing some signals is regulated in some
places, so know your local law, and don't publish captures that identify people or devices.

## License

MIT. See [LICENSE](LICENSE).
