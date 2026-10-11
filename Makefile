SERVICE := nrkup.service
UNIT_DIR := $(HOME)/.config/systemd/user

.PHONY: install
install:
	uv tool install --force --reinstall .
	mkdir -p $(UNIT_DIR)
	cp $(SERVICE) $(UNIT_DIR)/
	systemctl --user daemon-reload
	systemctl --user enable $(SERVICE)
	systemctl --user restart $(SERVICE)
