.PHONY: setup
setup: install-deps refresh-bin user-setup

.PHONY: install-deps
install-deps:
	@echo "Ensuring pip is up to date (needed for editable pyproject.toml installs)..."; \
	python3 -m pip install -q --upgrade pip --break-system-packages || true
	@if [ -f requirements.txt ]; then \
		echo "Installing Python dependencies..."; \
		python3 -m pip install -q -r requirements.txt --break-system-packages; \
	fi
	@if [ -d libs/ppui ]; then \
		echo "Installing internal ppui library in editable mode..."; \
		python3 -m pip install -q -e ./libs/ppui --break-system-packages; \
	fi

.PHONY: refresh-bin
refresh-bin:
	@chmod +x scripts/refresh-bin
	@./scripts/refresh-bin

.PHONY: user-setup
user-setup:
	ln -sfn $(CURDIR)/bin ~/bin_pas
	@chmod +x scripts/setup-path
	@./scripts/setup-path

.PHONY: version
version:
	@cat VERSION
