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

# Menus
help: ## Show this help
	@$(HELPER) help makefile

setup: deps download-sim check ## Venv, dependencies and simulator download
	
venv: $(VPY_FILE) ## Create the virtual environment only
 
$(VPY_FILE):
	$(PY_BOOTSTRAP) -m venv $(VENV)

deps: $(DEPS_STAMP) ## Install gym-donkeycar (GitHub), gymnasium, pillow

$(DEPS_STAMP): $(VPY_FILE)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip uninstall -y gym-donkeycar gym
	$(VPY) -m pip install $(GYM_PKG) gymnasium pillow
	@$(HELPER) stamp $(DEPS_STAMP)

download-sim: $(SIM_STAMP) ## Download the simulator unless one is already in the project
	
$(SIM_STAMP):
	$(HELPER) download-sim --sim-dir $(SIM_DIR) --dl-dir $(DL_DIR) $(if $(SIM_URL),--url $(SIM_URL))
	@$(HELPER) stamp $(SIM_STAMP)

sim-force: ## Re-download the simulator even if one already exists
	$(HELPER) download-sim --sim-dir $(SIM_DIR) --dl-dir $(DL_DIR) --force $(if $(SIM_URL),--url $(SIM_URL))
	@$(HELPER) stamp $(SIM_STAMP)

check: $(DEPS_STAMP) ## Verify donkey envs are registered in Gymnasium
	@$(VPY) tools/donkeycar_setup.py check

run-sim: $(SIM_STAMP) ## Launch the simulator
	@$(HELPER) run-sim --sim-dir $(SIM_DIR)

stop-sim: ## Kill any running simulator
	@$(HELPER) stop-sim

test: $(DEPS_STAMP) ## Run test_sim.py (sim must be running)
	@$(HELPER) wait-sim --host $(SIM_HOST) --port $(SIM_PORT)
	$(VPY) tools/test_sim_data_telemetry.py

# clean: stop-sim ## Remove downloads and the simulator folder
# 	@$(HELPER) clean $(DL_DIR) $(SIM_DIR)

# distclean: clean ## clean + remove the virtual environment
# 	@$(HELPER) clean $(VENV)