# Testes do nucleo BPA. Nao precisam de VPP nem de root.
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

# Testes que precisam do VPP, entao rodam no container de build.
test-appliance:
	@sh tools/build.sh plugin >/dev/null
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_startup_conf.sh"
	@echo
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_tuned_conf.sh"

# ISO: precisa de root e das ferramentas de imagem, entao roda no container.
# As dependencias vao aqui de proposito - build-iso.sh so as confere.
ISO_DEPS := debootstrap xorriso squashfs-tools dosfstools mtools \
              grub-common grub-pc-bin grub-efi-amd64-bin
ISO_RUN = sg docker -c "docker run --rm --privileged --device=/dev/kvm \
	    -v $$(pwd):/src -w /src hexarouting-dev:26.06 sh -c '$(1)'"

iso:
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq $(ISO_DEPS) >/dev/null 2>&1; \
	    sh /src/appliance/build-iso.sh)

# Boota a ISO recem-construida e exige que o autoteste diga "ready".
test-iso: iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 >/dev/null 2>&1; \
	    BOOT_SECS=$${BOOT_SECS:-240} sh /src/lab/boot-iso.sh)

# Valida o proprio modelo de referencia.
model:
	@cd ref && python3 validate.py && python3 check_magic.py

clean:
	@rm -rf $(BUILD) test/bpa_vectors.h
