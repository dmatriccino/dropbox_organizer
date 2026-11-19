# Read-only Dropbox duplicate-media report, dockerized.
# `make help` lists targets. Nothing here can modify your Dropbox.

IMAGE       ?= dropbox-organizer
DEV_IMAGE   ?= $(IMAGE)-dev
TOKEN_DIR   ?= $(HOME)/.config/dropbox_organizer
DATA_DIR    ?= $(CURDIR)/data
REPORTS_DIR ?= $(CURDIR)/reports

# Interactive TTY only when we have one (so `make scan` also works from cron/nohup).
TTY      := $(shell [ -t 0 ] && echo -it || echo -i)
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

DOCKER_RUN = docker run --rm --init $(TTY) \
	--user $(shell id -u):$(shell id -g) \
	$(ENV_FILE) -e DROPBOX_APP_KEY \
	-v "$(TOKEN_DIR)":/home/app/.config/dropbox_organizer \
	-v "$(DATA_DIR)":/work/data \
	-v "$(REPORTS_DIR)":/work/reports \
	$(IMAGE)

.DEFAULT_GOAL := help
.PHONY: help build build-dev dirs auth scan scan-fresh report run test lint shell clean-scan clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build: ## Build the runtime image
	docker build --target runtime -t $(IMAGE) .

build-dev: ## Build the test/lint image
	docker build --target dev -t $(DEV_IMAGE) .

dirs:
	@mkdir -p "$(TOKEN_DIR)" "$(DATA_DIR)" "$(REPORTS_DIR)"
	@chmod 700 "$(TOKEN_DIR)"

auth: build dirs ## One-time PKCE login; saves refresh token to ~/.config/dropbox_organizer/token
	$(DOCKER_RUN) auth

scan: build dirs ## Resumable metadata scan into data/ (Ctrl+C is safe; re-run to resume)
	$(DOCKER_RUN) scan $(ARGS)

scan-fresh: build dirs ## Throw away the checkpoint and rescan from scratch
	$(DOCKER_RUN) scan --fresh $(ARGS)

report: build dirs ## Write reports/duplicates_*.csv and reports/summary_*.txt
	$(DOCKER_RUN) report $(ARGS)

run: scan report ## scan (or resume) then report

test: build-dev ## Run ruff + pytest inside Docker (no network, no Dropbox)
	docker run --rm --network none $(DEV_IMAGE)

lint: build-dev ## Run ruff only
	docker run --rm --network none $(DEV_IMAGE) sh -c "ruff check --no-cache . && ruff format --no-cache --check ."

shell: build dirs ## Shell inside the runtime container
	docker run --rm -it --user $(shell id -u):$(shell id -g) $(ENV_FILE) -e DROPBOX_APP_KEY \
		-v "$(TOKEN_DIR)":/home/app/.config/dropbox_organizer \
		-v "$(DATA_DIR)":/work/data -v "$(REPORTS_DIR)":/work/reports \
		--entrypoint sh $(IMAGE)

clean-scan: ## Delete the local scan checkpoint (data/). Your Dropbox is untouched.
	rm -rf "$(DATA_DIR)"

clean: ## Remove the Docker images
	-docker rmi $(IMAGE) $(DEV_IMAGE)
