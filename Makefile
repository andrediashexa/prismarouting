# BPA core tests. They need neither VPP nor root.
CFLAGS ?= -O2 -Wall -Wextra -Werror -DBPA_STANDALONE
BUILD  := .build

.PHONY: test test-appliance test-iso test-install iso release-gate \
        hw-up hw-lab hw-vpp hw-down \
        model vectors clean

test: vectors $(BUILD)/test_bpa $(BUILD)/test_bpa_ports $(BUILD)/test_bpa_rtsp \
      $(BUILD)/test_bpa_mss $(BUILD)/test_bpa_ftp
	@$(BUILD)/test_bpa
	@echo
	@$(BUILD)/test_bpa_ports
	@echo
	@$(BUILD)/test_bpa_rtsp
	@echo
	@$(BUILD)/test_bpa_mss
	@echo
	@$(BUILD)/test_bpa_ftp
	@echo
	@python3 appliance/test_tune.py
	@echo
	@python3 test/test_hexash_complete.py
	@echo
	@python3 test/test_hexash_config.py
	@echo
	@python3 test/test_context_map.py
	@echo
	@python3 test/test_routes.py
	@echo
	@sh test/test_version_stamp.sh
	@echo
	@python3 test/test_vppapi.py
	@echo
	@python3 test/test_hexash_apply.py
	@echo
	@sh test/test_hexash_login.sh
	@echo
	@sh test/test_installer.sh

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

# The RTSP parser is the half of the ALG that decides whether to touch a
# payload at all, so it is built standalone: the refusal cases are what keep
# it from corrupting a stream, and they must not need a running VPP to run.
$(BUILD)/test_bpa_rtsp: test/test_bpa_rtsp.c src/plugins/bpa/bpa_rtsp.h \
                        test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_rtsp.c

# The MSS clamp and the FTP sequence shift: arithmetic on a TCP header whose
# failure mode is silent - the peer drops the segment and the connection hangs
# exactly as it did before, with a counter claiming the fix was applied. Built
# standalone for the same reason the RTSP parser is.
$(BUILD)/test_bpa_mss: test/test_bpa_mss.c src/plugins/bpa/bpa_mss.h \
                       test/bpa_shim.h test/bpa_tcp_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_mss.c

$(BUILD)/test_bpa_ftp: test/test_bpa_ftp.c src/plugins/bpa/bpa_ftp.h \
                       test/bpa_shim.h test/bpa_tcp_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_ftp.c

# Tests that need VPP, so they run in the build container.
test-appliance:
	@sh tools/build.sh plugin >/dev/null
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_startup_conf.sh"
	@echo
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    hexarouting-dev:26.06 sh /src/appliance/test_tuned_conf.sh"

# The hardware lab keeps a container alive so that hexash and vppctl have a VPP
# to talk to. Long-lived means somebody has to stop it: hw-down exists so that
# "somebody" is a command rather than a memory.
hw-up:
	@sg docker -c "docker rm -f hexa-vpp >/dev/null 2>&1; \
	    docker run -d --name hexa-vpp --privileged --network=host \
	      -v $$(pwd):/src -v $$HOME/.ssh:/root/.ssh:ro \
	      -w /src hexarouting-dev:26.06 sleep infinity" >/dev/null
	@sg docker -c "docker exec hexa-vpp sh -c 'apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq iproute2 iptables openssh-client tcpdump \
	      >/dev/null 2>&1'"
	@echo "hexa-vpp is up. Remember: make hw-down when you are finished."

# The topology: addresses and routes on both machines. Separate from hw-vpp
# because it touches the PEER, and because getting it wrong is what made the
# lab look like a dataplane fault for a long time.
hw-lab:
	@sg docker -c "docker exec hexa-vpp sh /src/lab/hw-lab.sh"

hw-vpp:
	@sg docker -c "docker exec hexa-vpp sh /src/lab/hw-vpp.sh"

# Safe when it is not there, so it can be run without checking first.
hw-down:
	@sg docker -c "docker rm -f hexa-vpp >/dev/null 2>&1" || true
	@echo "hexa-vpp removed."

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

# Installs the ISO onto a blank disk and boots that disk with the ISO gone.
# The second boot is the assertion: a live image cannot prove it.
test-install: iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 qemu-utils squashfs-tools \
	      fdisk >/dev/null 2>&1; \
	    sh /src/lab/install-iso.sh)

# Boots the freshly built ISO and demands the self-test say "ready".
test-iso: iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 >/dev/null 2>&1; \
	    BOOT_SECS=$${BOOT_SECS:-240} sh /src/lab/boot-iso.sh)

# The release medium, built from the TAG and booted. CONTRIBUTING.md section 5
# requires both; this is that procedure as one command, because as a sequence
# somebody retypes it gets retyped wrong - three times in one evening, each
# caught by a rule rather than by attention. tools/release-gate.sh says which.
release-gate:
	@sh tools/release-gate.sh $(TAG)

# Validates the reference model itself.
model:
	@cd ref && python3 validate.py && python3 check_magic.py

clean:
	@rm -rf $(BUILD) test/bpa_vectors.h
