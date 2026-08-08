# Shortcuts for the things you actually run. `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install test lint typecheck robot demo bench docker-bench docker-test clean

PY ?= python

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## install the package with dev + VISA extras
	$(PY) -m pip install -e ".[dev,visa]"

test:  ## unit + integration tests (starts its own emulator; no hardware)
	$(PY) -m pytest -q

lint:  ## ruff
	$(PY) -m ruff check .

typecheck:  ## mypy
	$(PY) -m mypy hiltf

demo:  ## pure-Python end-to-end run against the in-process bench
	$(PY) examples/run_all.py

robot:  ## Robot suites against the in-process bench
	$(PY) -m robot --outputdir reports/local hiltf/layer1_suites

bench:  ## run the bench emulator in the foreground (Ctrl-C to stop)
	$(PY) -m hiltf.emulators

robot-socket:  ## Robot suites over raw TCP/SCPI + binary UDP (needs `make bench`)
	$(PY) -m robot --outputdir reports/local-socket \
		--variable CONFIG:config/bench_socket.yaml hiltf/layer1_suites

robot-visa:  ## Robot suites over PyVISA/pyvisa-py (needs `make bench`)
	$(PY) -m robot --outputdir reports/local-visa \
		--variable CONFIG:config/bench_visa.yaml hiltf/layer1_suites

docker-bench:  ## containerised bench: run every suite over sockets and over VISA
	docker compose run --rm socket-runner
	docker compose run --rm visa-runner
	docker compose down

docker-test:  ## the test suite inside the image
	docker compose run --rm tests

clean:  ## remove caches and generated output
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__ \
		reports/local reports/local-socket reports/local-visa \
		reports/docker-socket reports/docker-visa reports/ci
