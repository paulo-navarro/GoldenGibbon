
MAKE_CMD_DIR := $(dir $(lastword $(MAKEFILE_LIST)))

PHONY: help menu

help: ## Show help message
	@echo "$(BLUE)GoldenGibbon - Available Commands$(NC)"
	@echo ""
	@awk 'BEGIN { \
		FS = ":.*##"; \
		printf "Usage: make $(BLUE)<target>$(NC)\n\nTargets:\n" \
	} \
	/^[a-zA-Z_-]+:.*?##/ { \
		printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2 \
	}' \
	$(MAKEFILE_LIST)

menu: ## Interactive menu to select and run make targets
	@bash $(MAKE_CMD_DIR)make_menu.sh $(MAKEFILE_LIST)
