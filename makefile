# Windows + Linux Compatible Makefile
# Requirements:
# 	- GNU Make
# 	- Python 3.11
#
# Quick start:
#   make setup      venv + dependencies + simulator download
#   make run-sim    launch the simulator (leave it on the track menu)
#   make test       run test_sim.py against the running sim
	
# OS Detection
ifeq ($(OS),Windows_NT)
	SHELL 			:= cmd.exe
	.SHELLFLAGS 	:= /c
	PY_BOOTSTRAP 	?= py -3.11
else
	PY_BOOTSTRAP 	?= python3.11
endif

# Settings
VENV      ?= venv
SIM_DIR   ?= DonkeySim
DL_DIR    ?= downloads
SIM_HOST  ?= 127.0.0.1
SIM_PORT  ?= 9091
# Optional direct URL to the sim zip; skips the GitHub latest-release lookup
SIM_URL   ?=
# Zip install avoids needing git. Alternative: git+https://github.com/tawnkramer/gym-donkeycar
GYM_PKG   ?= https://github.com/tawnkramer/gym-donkeycar/archive/refs/heads/master.zip

# Venv interpreter (backslashes for cmd.exe recipes, forward slashes for make targets)
ifeq ($(OS),Windows_NT)
  VPY_FILE := $(VENV)/Scripts/python.exe
  VPY      := $(subst /,\,$(VPY_FILE))
else
  VPY_FILE := $(VENV)/bin/python
  VPY      := $(VPY_FILE)
endif

HELPER := $(PY_BOOTSTRAP) tools/donkeycar_setup.py
DEPS_STAMP := $(VENV)/.deps-installed
SIM_STAMP := $(VENV)/.sim-installed
REQUIREMENTS := requirements.txt
BACKEND_DEPS_STAMP := $(VENV)/.backend-deps-installed
RL_REQUIREMENTS := requirements-rl.txt
RL_DEPS_STAMP := $(VENV)/.rl-deps-installed

# Menus
help: ## Show this help
	@$(HELPER) help makefile

setup: backend-deps deps download-sim check ## Venv, dependencies and simulator download ## Venv, dependencies and simulator download
	
venv: $(VPY_FILE) ## Create the virtual environment only
 
$(VPY_FILE):
	$(PY_BOOTSTRAP) -m venv $(VENV)

deps: $(DEPS_STAMP) ## Install gym-donkeycar (GitHub), gymnasium, pillow

$(DEPS_STAMP): $(VPY_FILE)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip uninstall -y gym-donkeycar gym
	$(VPY) -m pip install $(GYM_PKG) gymnasium pillow
	@$(HELPER) stamp $(DEPS_STAMP)

frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-dev: ## Run the Vite dev server (http://localhost:5173)
	cd frontend && npm run dev

frontend-build: ## Build the frontend into frontend/dist
	cd frontend && npm run build

backend-deps: $(BACKEND_DEPS_STAMP) ## Install backend dependencies
$(BACKEND_DEPS_STAMP): $(VPY_FILE) $(REQUIREMENTS)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -r $(REQUIREMENTS)
	@$(HELPER) stamp $(BACKEND_DEPS_STAMP)

backend-run: backend-deps ## Run the FastAPI dev server
	$(VPY) -m uvicorn backend.main:app --reload

db-migrate: ## Run Alembic migrations
	$(VPY) -m alembic upgrade head

db-run: ## Start the database
	docker-compose up -d db

db-stop: ## Stop the database
	docker-compose stop db

download-sim: $(SIM_STAMP) ## Download the simulator unless one is already in the project
	
$(SIM_STAMP):
	$(HELPER) download-sim --sim-dir $(SIM_DIR) --dl-dir $(DL_DIR) $(if $(SIM_URL),--url $(SIM_URL))
	@$(HELPER) stamp $(SIM_STAMP)

download-sim-force: ## Re-download the simulator even if one already exists
	$(HELPER) download-sim --sim-dir $(SIM_DIR) --dl-dir $(DL_DIR) --force $(if $(SIM_URL),--url $(SIM_URL))
	@$(HELPER) stamp $(SIM_STAMP)

check: $(DEPS_STAMP) ## Verify donkey envs are registered in Gymnasium
	@$(VPY) tools/donkeycar_setup.py check

sim-run: $(SIM_STAMP) ## Launch the simulator
	@$(HELPER) run-sim --sim-dir $(SIM_DIR)

sim-stop: ## Kill any running simulator
	@$(HELPER) stop-sim

driver-run-test: $(DEPS_STAMP) ## Run test_sim.py (sim must be running)
	@$(HELPER) wait-sim --host $(SIM_HOST) --port $(SIM_PORT)
	$(VPY) tools/test_sim_data_telemetry.py

broker-run: ## Start the Redpanda stream broker
	docker compose up -d redpanda

broker-stop: ## Stop the Redpanda stream broker
	docker compose stop redpanda

broker-peek: ## Peek at the telemetry topic (from the beginning)
	docker exec -it sopirlab-redpanda rpk topic consume telemetry --offset start

broker-clean: ## Delete the telemetry topic (auto-recreated on next produce)
	docker exec -it sopirlab-redpanda rpk topic delete telemetry

worker-run: backend-deps ## Run the worker ingest consumer (host)
	$(VPY) -u -m worker

test-aggregates-kpi: backend-deps ## Run the KPI unit tests
	$(VPY) -m pytest tests -v

rl-deps: $(RL_DEPS_STAMP) ## Install CPU-only torch and stable-baselines3
$(RL_DEPS_STAMP): $(VPY_FILE) $(RL_REQUIREMENTS)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install torch --index-url https://download.pytorch.org/whl/cpu
	$(VPY) -m pip install -r $(RL_REQUIREMENTS)
	@$(HELPER) stamp $(RL_DEPS_STAMP)

train-lka: rl-deps ## Train the LKA PPO agent (resumes automatically)
	$(VPY) tools/train_lka.py $(ARGS)

test-rl: backend-deps deps ## Run the LKA unit tests (no sim needed)
	$(VPY) -m pytest tests/sdk -v

# clean: stop-sim ## Remove downloads and the simulator folder
# 	@$(HELPER) clean $(DL_DIR) $(SIM_DIR)

# distclean: clean ## clean + remove the virtual environment
# 	@$(HELPER) clean $(VENV)