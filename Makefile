# BPA core tests. They need neither VPP nor root.
CFLAGS ?= -O2 -Wall -Wextra -Werror -DBPA_STANDALONE
BUILD  := .build

.PHONY: test test-appliance test-iso iso model vectors clean

test: vectors $(BUILD)/test_bpa $(BUILD)/test_bpa_ports
	@$(BUILD)/test_bpa
	@echo
	@$(BUILD)/test_bpa_ports
	@echo
	@python3 appliance/test_tune.py
	@echo
	@python3 test/test_hexash_complete.py
	@echo
	@sh test/test_version_stamp.sh
	@echo
	@python3 test/test_vppapi.py
	@echo
	@python3 test/test_hexash_apply.py

vectors:
	@cd ref && python3 gen_vectors.py

$(BUILD)/test_bpa: test/test_bpa_pool.c src/plugins/bpa/bpa_pool.c \
                   src/plugins/bpa/bpa_pool.h test/bpa_shim.h test/bpa_vectors.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_pool.c src/plugins/bpa/bpa_pool.c

$(BUILD)/test_bpa_ports: test/test_bpa_ports.c src/plugins/bpa/bpa_ports.h \
                         test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_ports.c

# Tests that need VPP, so they run in the build container.
test-appliance:
	@sh tools/build.sh plugin >/dev/null
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_startup_conf.sh"
	@echo
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_tuned_conf.sh"

# ISO: needs root and the image tools, so it runs in the container.
# The dependencies live here on purpose - build-iso.sh only checks them.
ISO_DEPS := debootstrap xorriso squashfs-tools dosfstools mtools \
              grub-common grub-pc-bin grub-efi-amd64-bin
ISO_RUN = sg docker -c "docker run --rm --privileged --device=/dev/kvm \
	    -v $$(pwd):/src -w /src hexarouting-dev:26.06 sh -c '$(1)'"

iso:
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq $(ISO_DEPS) >/dev/null 2>&1; \
	    sh /src/appliance/build-iso.sh)

# Boots the freshly built ISO and demands the self-test say "ready".
test-iso: iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 >/dev/null 2>&1; \
	    BOOT_SECS=$${BOOT_SECS:-240} sh /src/lab/boot-iso.sh)

# Validates the reference model itself.
model:
	@cd ref && python3 validate.py && python3 check_magic.py

clean:
	@rm -rf $(BUILD) test/bpa_vectors.h
