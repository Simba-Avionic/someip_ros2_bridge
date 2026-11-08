PYTHON ?= python3

.PHONY: check test lint format-check type-check config

check: test lint format-check type-check config

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check .

type-check:
	$(PYTHON) -m mypy someip_ros2_bridge tools

config:
	$(PYTHON) -m someip_ros2_bridge.node --config config/bridge.yaml --check
