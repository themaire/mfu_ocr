PYTHON := .venv/bin/python
PIP := .venv/bin/pip

venv:
	/usr/bin/python -m venv .venv

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -e .[dev]

check-env:
	PYTHONPATH=src $(PYTHON) scripts/check_env.py

help:
	PYTHONPATH=src $(PYTHON) -m ocr_hailo.cli --help
