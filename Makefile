VENV_PATH?=venv

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
	rm -rf dist
	rm -rf .pytest_cache
	rm -rf "$(VENV_PATH)"

venv:
	python3 -m venv "$(VENV_PATH)"
	"$(VENV_PATH)"/bin/pip install --upgrade wheel pytest asynctest maxminddb docutils
	"$(VENV_PATH)"/bin/pip install --editable .

test: venv
	. "$(VENV_PATH)"/bin/activate ; \
	"$(VENV_PATH)"/bin/pytest --exitfirst tests
	# Check if README.org converts correctly to rst for PyPI
	python3 setup.py check -r -s >/dev/null

release:
	pyrelease CHANGELOG ./stig/__version__.py
