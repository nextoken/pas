# Chain ensures install-deps always runs before refresh-bin / user-setup (also safe with make -j).
.PHONY: setup
setup: user-setup

# Used for system/Homebrew Python (PEP 668). Use empty for venv: `make setup PIP_ARGS=`
PIP_ARGS ?= --break-system-packages

.PHONY: install-deps
install-deps:
	@chmod +x scripts/install-deps.sh
	@PIP_ARGS='$(PIP_ARGS)' ./scripts/install-deps.sh

.PHONY: refresh-bin
refresh-bin: install-deps
	@chmod +x scripts/refresh-bin
	@./scripts/refresh-bin

.PHONY: user-setup
user-setup: refresh-bin
	ln -sfn $(CURDIR)/bin ~/bin_pas
	@chmod +x scripts/setup-path
	@./scripts/setup-path

.PHONY: version
version:
	@cat VERSION
