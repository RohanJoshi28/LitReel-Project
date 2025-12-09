.PHONY: install start test lint

install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

start:
	flask --app app run --debug

test:
	pytest -q
