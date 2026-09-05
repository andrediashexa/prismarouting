# Testes do nucleo BPA. Nao precisam de VPP nem de root.
CFLAGS ?= -O2 -Wall -Wextra -Werror -DBPA_STANDALONE
BUILD  := .build

.PHONY: test test-appliance model vectors clean

test: vectors $(BUILD)/test_bpa $(BUILD)/test_bpa_ports
	@$(BUILD)/test_bpa
	@echo
	@$(BUILD)/test_bpa_ports
	@echo
	@python3 appliance/test_tune.py

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

# Valida o proprio modelo de referencia.
model:
	@cd ref && python3 validate.py && python3 check_magic.py

clean:
	@rm -rf $(BUILD) test/bpa_vectors.h
