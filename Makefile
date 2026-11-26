# signals-backend
PORT ?= 8080
CONSOLE ?=            # path to a built console (dist/) to serve at /, optional

.PHONY: sim serve plan up
sim:            ## run the simulation (no radios); set CONSOLE=../signals-console/dist to serve the UI
	python3 -m backend.server --sim --port $(PORT) $(if $(CONSOLE),--web $(CONSOLE))
serve:          ## real mode: serve data/ + live/annotations (run collectors separately or via compose)
	python3 -m backend.server --port $(PORT) $(if $(CONSOLE),--web $(CONSOLE))
plan:           ## show which configured SDRs are present
	python3 -m backend.sdr plan --config config.json
up:             ## docker compose: collectors + correlator + web
	docker compose up --build
