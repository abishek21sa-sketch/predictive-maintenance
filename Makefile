.PHONY: install test diagnostics audit benchmark demo run wheel

install:
	python -m pip install -e '.[dev]'

test:
	python -m pytest -W error

diagnostics:
	python scripts/phase1_diagnostics.py
	python scripts/phase2_diagnostics.py
	python scripts/phase2b_diagnostics.py
	python scripts/phase2c_diagnostics.py
	python scripts/phase3_diagnostics.py
	python scripts/phase4_diagnostics.py
	python scripts/v1_diagnostics.py

audit:
	python scripts/release_audit.py

benchmark:
	python scripts/v1_benchmark.py

demo:
	pdm-intelligence demo

run:
	uvicorn pdm_intelligence.api.main:app --reload

wheel:
	python -m pip wheel . --no-deps --no-build-isolation -w dist
