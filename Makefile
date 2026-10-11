# BPA core tests. They need neither VPP nor root.
CFLAGS ?= -O2 -Wall -Wextra -Werror -DBPA_STANDALONE
BUILD  := .build

.PHONY: kernel test test-appliance test-iso test-install test-mirror iso \
        upgrade-artifact release-gate \
        hw-up hw-lab hw-vpp hw-down \
        model vectors clean

test: vectors $(BUILD)/test_bpa $(BUILD)/test_bpa_ports $(BUILD)/test_bpa_rtsp \
      $(BUILD)/test_bpa_mss $(BUILD)/test_bpa_ftp $(BUILD)/test_bpa_ftp64 \
      $(BUILD)/test_bpa_icmp \
      $(BUILD)/test_bpa_sip $(BUILD)/test_bpa_rewrite $(BUILD)/test_bpa_l4 \
      $(BUILD)/test_bpa_auto_mss $(BUILD)/test_bpa_dyn \
      $(BUILD)/test_bpa_pptp $(BUILD)/test_bpa_log_tcp \
      $(BUILD)/test_bpa_audit $(BUILD)/test_bpa_log_syslog \
      $(BUILD)/test_bpa_identity $(BUILD)/test_bpa_md5 \
      $(BUILD)/test_bpa_block_ident \
      $(BUILD)/test_bpa_rebalance \
      $(BUILD)/test_bpa_lent \
      $(BUILD)/test_bpa_radius \
      $(BUILD)/test_bpa_static \
      $(BUILD)/test_bpa_top \
      $(BUILD)/test_bpa_xcvr \
      $(BUILD)/test_bpa_linkcount \
      $(BUILD)/test_bpa_rate \
      $(BUILD)/test_bpa_keys $(BUILD)/test_bpa_nat64 \
      $(BUILD)/test_bpa_subblock \
      $(BUILD)/test_bpa_workers \
      $(BUILD)/test_bpa_local \
      $(BUILD)/test_bpa_frame $(BUILD)/test_bpa_exhaust \
      $(BUILD)/test_bpa_log_ring $(BUILD)/test_prisma_logd \
      $(BUILD)/test_bpa_sock
	@sh tools/run-tests.sh

vectors:
	@cd ref && python3 gen_vectors.py && python3 gen_nat64_vectors.py

# bpa_pool.c is #included by the harness rather than compiled beside it, and
# bpa_dyn.c below the same way: four of the functions in each are static, and
# a static function is reachable from no other translation unit. The reader
# in test/test_every_function.py names a function only when a harness calls
# it, and the comparator qsort sorts an address set by, the two tests that
# decide whether a divide is a shift, and the four operations that move the
# free stack were named by nothing until #390. The file stays a prerequisite
# so that a change to it rebuilds the binary, which is the property the
# bpa_seq.h comment below is about.
$(BUILD)/test_bpa: test/test_bpa_pool.c src/plugins/bpa/bpa_pool.c \
                   src/plugins/bpa/bpa_pool.h src/plugins/bpa/bpa_nat64.h test/bpa_shim.h test/bpa_vectors.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_pool.c

# The arithmetic over a pool's static forwards, which took a pool - and so a
# VPP vector - until #390's next batch gave it (v, n). The vector belongs to
# the one caller in bpa.c that has one; what decides where an operator's
# forward lands is a header function now, and this drives it over arrays.
$(BUILD)/test_bpa_static: test/test_bpa_static.c \
                   src/plugins/bpa/bpa_pool.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_static.c

# The order "show bpa top" reads its rows in. bpa_cli.c's own header says the
# arithmetic in that file is deliberately not excused because it is waiting
# for a harness; this is the harness, and the arithmetic needs nothing but
# two rows.
# The session table's keys and a session's two rules (#472): bpa_keys.h,
# which compiles here as well as with VPP.
$(BUILD)/test_bpa_keys: test/test_bpa_keys.c \
                   src/plugins/bpa/bpa_keys.h src/plugins/bpa/bpa_pool.h \
                   test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_keys.c

# NAT64's arithmetic (#485): bpa_nat64.h against ref/bpa_model.py.
$(BUILD)/test_bpa_nat64: test/test_bpa_nat64.c \
                   src/plugins/bpa/bpa_nat64.h src/plugins/bpa/bpa_pool.h \
                   test/bpa_shim.h test/bpa_vec_shim.h test/bpa_nat64_vectors.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_nat64.c

# A subscriber's blocks (#472): bpa_subblock.h.
$(BUILD)/test_bpa_subblock: test/test_bpa_subblock.c \
                   src/plugins/bpa/bpa_subblock.h src/plugins/bpa/bpa_ports.h \
                   src/plugins/bpa/bpa_pool.h test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_subblock.c

# Which worker owns a subscriber and which thread that is (#466, #472).
$(BUILD)/test_bpa_workers: test/test_bpa_workers.c \
                   src/plugins/bpa/bpa_workers.h src/plugins/bpa/bpa_pool.c \
                   src/plugins/bpa/bpa_pool.h test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_workers.c

# The box's own addresses as a set, asked before the FIB (#489).
$(BUILD)/test_bpa_local: test/test_bpa_local.c \
                   src/plugins/bpa/bpa_local.h test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_local.c

# The block-event rings' shared segment (#550): layout, drop, wrap, two
# threads, and a reader in another process through /dev/shm.
$(BUILD)/test_bpa_log_ring: test/test_bpa_log_ring.c \
                   src/plugins/bpa/bpa_log_ring.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_log_ring.c -lpthread -lrt

# The log container's process (#550): it includes bpa_log_tcp.c as the TCP
# exporter's own harness does, and is driven here against a segment, a
# collector on 127.0.0.1 and a spill directory of its own.
$(BUILD)/test_prisma_logd: test/test_prisma_logd.c src/logger/prisma_logd.c src/logger/logd_ipfix_udp.c \
                   src/plugins/bpa/bpa_log_tcp.c src/plugins/bpa/bpa_log_ring.h \
                   src/plugins/bpa/bpa_log_syslog.c src/plugins/bpa/bpa_identity.c \
                   src/plugins/bpa/bpa_log.h test/bpa_log_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_prisma_logd.c \
	    src/plugins/bpa/bpa_log_syslog.c src/plugins/bpa/bpa_identity.c -lrt

# One exhaustion record per subscriber per interval (#549).
$(BUILD)/test_bpa_exhaust: test/test_bpa_exhaust.c \
                   src/plugins/bpa/bpa_exhaust.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_exhaust.c

# The length of the message at the front of an exporter's buffer (#492).
$(BUILD)/test_bpa_frame: test/test_bpa_frame.c \
                   src/plugins/bpa/bpa_frame.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_frame.c

# Whether the exporter sees its collector close (#493).
$(BUILD)/test_bpa_sock: test/test_bpa_sock.c \
                   src/plugins/bpa/bpa_sock.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_sock.c

$(BUILD)/test_bpa_top: test/test_bpa_top.c \
                   src/plugins/bpa/bpa_top.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_top.c

# A transceiver read's ranges and the build-id it compares (#590): bytes
# only, no VPP and no DPDK.
$(BUILD)/test_bpa_xcvr: test/test_bpa_xcvr.c \
                   src/plugins/bpa/bpa_xcvr.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_xcvr.c

# A port's link changes, counted (#588): two calls and a struct.
$(BUILD)/test_bpa_linkcount: test/test_bpa_linkcount.c \
                   src/plugins/bpa/bpa_linkcount.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_linkcount.c

# The subscriber's creation-rate bucket: two fields and a clock, which is
# what `max-rate` on a pool buys. It was inside bpa_session.c and nothing
# drove it.
$(BUILD)/test_bpa_rate: test/test_bpa_rate.c \
                   src/plugins/bpa/bpa_rate.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_rate.c

# The dynamic block allocator, driven rather than evaluated. Standalone
# because paired address pooling (RFC 4787 REQ-2, the required default under
# RFC 6888 REQ-2) had its dynamic half measured only by a docker lab, and by
# one written to pin what the box did WRONG - so the conformance statement in
# four documents had nothing in `make test` behind it. test/bpa_vec_shim.h is
# what makes it buildable: this file's behaviour IS what its VPP vectors do.
$(BUILD)/test_bpa_dyn: test/test_bpa_dyn.c src/plugins/bpa/bpa_dyn.c \
                       src/plugins/bpa/bpa_dyn.h src/plugins/bpa/bpa_pool.c \
                       src/plugins/bpa/bpa_pool.h test/bpa_shim.h \
                       test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_dyn.c src/plugins/bpa/bpa_pool.c

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

# src/plugins/bpa/bpa_seq.h is named by the five recipes that reach it, and
# it is reached through test/bpa_tcp_shim.h rather than directly - so naming
# only the shim left `make test` reporting green on a binary built before the
# change. Measured: the signed comparison in bpa_seq_delta_at cut to a plain
# `>` and `make test_bpa_ftp` answering "is up to date", all five suites green
# with the boundary arithmetic broken. That is TESTING.md's "a mutation that
# does not reach the compiler" as a standing property of the build rather
# than as an accident of one campaign.
# The MSS clamp and the FTP sequence shift: arithmetic on a TCP header whose
# failure mode is silent - the peer drops the segment and the connection hangs
# exactly as it did before, with a counter claiming the fix was applied. Built
# standalone for the same reason the RTSP parser is.
$(BUILD)/test_bpa_mss: test/test_bpa_mss.c src/plugins/bpa/bpa_mss.h \
                       test/bpa_shim.h test/bpa_tcp_shim.h \
                       src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_mss.c

# The outbound rewrite: two checksum corrections per session, applied per
# packet. Standalone because this exact optimisation has already been written,
# measured at a 28% saving and shipped WRONG once (#63) - the arithmetic was
# off by a sign, every translated packet left with both checksums bad, and the
# only thing that said so was three ALG labs at the end of the change. A
# checksum this cheap to check does not belong behind a container.
$(BUILD)/test_bpa_rewrite: test/test_bpa_rewrite.c \
                           src/plugins/bpa/bpa_rewrite.h \
                           test/bpa_shim.h test/bpa_tcp_shim.h \
                           src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_rewrite.c

# The L4 id a packet is keyed on and the destination rewrite's checksums,
# checked the way a receiver checks them (#472).
$(BUILD)/test_bpa_l4: test/test_bpa_l4.c src/plugins/bpa/bpa_l4_wire.h \
                      src/plugins/bpa/bpa_gre.h \
                      test/bpa_shim.h test/bpa_tcp_shim.h \
                      src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_l4.c

# The "tcp-mss auto" minimum: which MTU the clamp is derived from as interfaces
# join and leave. Standalone because the bookkeeping is INCREMENTAL (#287) and
# incremental bookkeeping is wrong in ways that depend on the order of the
# operations, which is a thing to enumerate rather than to hope a lab meets.
# It is also where the COST is asserted - by counting the slots an attach
# reads, not by timing it, because a stopwatch on a loaded machine measures
# the machine.
$(BUILD)/test_bpa_auto_mss: test/test_bpa_auto_mss.c \
                            src/plugins/bpa/bpa_auto_mss.h \
                            src/plugins/bpa/bpa_mss.h \
                            test/bpa_shim.h test/bpa_tcp_shim.h \
                            src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_auto_mss.c

# bpa_ftp.c is #included by the harness, the way bpa_pool.c is above: the
# nine functions of the ALG's text half are static, so nothing compiled beside
# the file can call one, and until #390 batch I the parse that decides whether
# a subscriber's payload is touched at all was named by no test.
$(BUILD)/test_bpa_ftp: test/test_bpa_ftp.c src/plugins/bpa/bpa_ftp.h \
                       src/plugins/bpa/bpa_ftp.c \
                       test/bpa_shim.h test/bpa_tcp_shim.h \
                       src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_ftp.c

# FTP64 (RFC 6384, #485 phase 5): the text half of a NAT64 pool's FTP ALG,
# in bpa_ftp.c beside the IPv4 one whose static parsers it stands on, so
# included the same way. -Wno-unused-function because the IPv4 half's static
# parsers are test_bpa_ftp.c's to call, and are compiled here unused.
$(BUILD)/test_bpa_ftp64: test/test_bpa_ftp64.c src/plugins/bpa/bpa_ftp.h \
                         src/plugins/bpa/bpa_ftp.c \
                         test/bpa_shim.h test/bpa_tcp_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Wno-unused-function -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_ftp64.c

# The ICMP error parse: the only walk this plugin does through headers a remote
# wrote inside another packet. Standalone because its REFUSALS are the half
# that matters and a lab cannot see them - a packet dropped by a bounds check
# and a packet dropped after reading past the buffer look identical from
# outside. This one puts an unmapped page behind every packet so the difference
# is a crash instead of a judgement call.
$(BUILD)/test_bpa_icmp: test/test_bpa_icmp.c src/plugins/bpa/bpa_icmp.h \
              src/plugins/bpa/bpa_gre.h \
                        test/bpa_shim.h test/bpa_tcp_shim.h \
                        src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_icmp.c
# The SIP parser and the edit list it feeds. Standalone for the reason the RTSP
# parser is, and for one more: a SIP message is rewritten in several places at
# once, so the offsets of every edit after the first depend on the ones before
# it. That arithmetic is exercised here against whole messages, where a wrong
# answer is a message that still parses and names the wrong things.
$(BUILD)/test_bpa_sip: test/test_bpa_sip.c src/plugins/bpa/bpa_sip.h \
                       test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_sip.c

# The PPTP ALG's three segment-local functions: the worst-outcome fold, the
# checksum-preserving Call ID replacement and the payload bounds. Included by
# the harness for the reason bpa_pool.c is - all three are static - and
# standalone because a wrong checksum correction has the peer dropping a
# segment the counter says was rewritten, which is #63's failure mode in a
# second ALG.
$(BUILD)/test_bpa_pptp: test/test_bpa_pptp.c src/plugins/bpa/bpa_pptp.c \
                        src/plugins/bpa/bpa_pptp.h \
                        test/bpa_shim.h test/bpa_tcp_shim.h \
                        src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_pptp.c

# The IPFIX-over-TCP exporter: the message it assembles, the spill file and
# the socket. Standalone because what this transport promises - order, and no
# loss - is kept by arithmetic on one buffer, and every way of getting that
# arithmetic wrong is silent: #219 spilled 82917 bytes, replayed 82917, and
# the collector decoded one record out of them. The one socket it opens is on
# 127.0.0.1 and connects to itself, so this needs no network.
# bpa_log_syslog.c is compiled BESIDE it since #20: the stream carries either
# wire, so the exporter's own harness links the line formatter the syslog wire
# calls. Included files elsewhere in this Makefile, compiled here, because
# this one is reached by two harnesses and including it twice would be two
# copies of the same function in one program.
$(BUILD)/test_bpa_log_tcp: test/test_bpa_log_tcp.c \
                           src/plugins/bpa/bpa_log_tcp.c \
                           src/plugins/bpa/bpa_frame.h \
                           src/plugins/bpa/bpa_sock.h \
                           src/plugins/bpa/bpa_log_syslog.c \
                           src/plugins/bpa/bpa_identity.c \
                           src/plugins/bpa/bpa_log.h \
                           test/bpa_shim.h test/bpa_vec_shim.h \
                           test/bpa_tcp_shim.h test/bpa_log_shim.h \
                           src/plugins/bpa/bpa_seq.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_log_tcp.c src/plugins/bpa/bpa_log_syslog.c \
	    src/plugins/bpa/bpa_identity.c -lpthread

# The configuration trail's hex reader, which is the one function in
# bpa_audit.c that touches neither format(), nor vppinfra's SHA-2, nor the
# CLI. Standalone because its refusals decide whether the chain is verified
# against bytes or against uninitialised memory, and both readings print a
# verdict.
# MD5, against RFC 1321's own vectors. This product carries its own because
# vppinfra ships no MD5 and the image no OpenSSL headers, and RADIUS asks for
# it by name - see bpa_md5.c for why that is not a security choice.
$(BUILD)/test_bpa_md5: test/test_bpa_md5.c src/plugins/bpa/bpa_md5.c \
                       src/plugins/bpa/bpa_md5.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_md5.c

# The accounting packet a BNG already sends. Standalone because the parse and
# the digest read their arguments and nothing else; the socket is the plugin's
# half and is not in this harness.
$(BUILD)/test_bpa_radius: test/test_bpa_radius.c \
                          src/plugins/bpa/bpa_radius.c \
                          src/plugins/bpa/bpa_radius.h \
                          src/plugins/bpa/bpa_md5.c test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_radius.c

# Who held a private address at the instant a block was allocated to it. The
# parse, the search and the unknown marker are standalone because none of them
# reads a packet, a session or a pool - and because a wrong one is invisible in
# a log that parses.
# bpa_identity_table.c IS A PREREQUISITE, and was not: the harness includes
# it, so editing the file under test left the binary "up to date" and the
# suite measured the build before the change. Found by mutating three
# decisions in that file and watching all three stay green.
$(BUILD)/test_bpa_identity: test/test_bpa_identity.c \
                            src/plugins/bpa/bpa_identity.c \
                            src/plugins/bpa/bpa_identity_table.c \
                            src/plugins/bpa/bpa_identity.h test/bpa_shim.h \
                            test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_identity.c

# The syslog line, which is the half of a second transport that decides what a
# court-ordered lookup reads. Standalone because none of it needs a socket, a
# collector or a VPP - and a wrong field is invisible in a capture that parses.
# bpa_identity.c is compiled beside it since #21: the line carries the
# subscriber's identity, and the unknown marker it falls back to lives there -
# one holder, so the wire and the table cannot disagree about what "nobody
# knows" looks like.
# What a RELEASE record names when the identity table changed after the
# allocation (#397). Standalone because none of it needs a packet, a pool or a
# VPP: it is a store, and every way of getting it wrong - the holder now, the
# holder before a block was reused, the subscriber of a deleted pool - produces
# a record that PARSES and attributes traffic to the wrong person.
# Which blocks move between workers' slices when a subscriber is refused with
# blocks free next door (#210). Standalone because none of it needs a pool, a
# packet or a VPP: counters in, moves out - and every way of getting it wrong
# is quiet, from a lender that gives away what it needs to a plan that holds
# the barrier for as long as the counters say.
# The loan book the return path reads: which worker is holding a block it does
# not own (#210). Standalone because it is a table and a counter, and the
# counter IS the fast path - the handoff tests it before it looks at anything
# else, so a count that drifts above zero turns a comparison into a hash
# lookup on every packet of every box.
$(BUILD)/test_bpa_lent: test/test_bpa_lent.c \
                        src/plugins/bpa/bpa_lent.c \
                        test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_lent.c

$(BUILD)/test_bpa_rebalance: test/test_bpa_rebalance.c \
                             src/plugins/bpa/bpa_rebalance.c \
                             src/plugins/bpa/bpa_rebalance.h \
                             test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_rebalance.c

$(BUILD)/test_bpa_block_ident: test/test_bpa_block_ident.c \
                               src/plugins/bpa/bpa_block_ident.c \
                               src/plugins/bpa/bpa_block_ident.h \
                               src/plugins/bpa/bpa_identity.c \
                               src/plugins/bpa/bpa_identity.h \
                               test/bpa_shim.h test/bpa_vec_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_block_ident.c

$(BUILD)/test_bpa_log_syslog: test/test_bpa_log_syslog.c \
                              src/plugins/bpa/bpa_log_syslog.c \
                              src/plugins/bpa/bpa_identity.c \
                              src/plugins/bpa/bpa_identity.h \
                              src/plugins/bpa/bpa_log.h test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ \
	    test/test_bpa_log_syslog.c src/plugins/bpa/bpa_identity.c

$(BUILD)/test_bpa_audit: test/test_bpa_audit.c src/plugins/bpa/bpa_audit.c \
                         test/bpa_shim.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I test -I src/plugins/bpa -o $@ test/test_bpa_audit.c

# Tests that need VPP, so they run in the build container.
test-appliance:
	@sh tools/build.sh plugin >/dev/null
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    prisma-routing-dev:26.06 sh /src/appliance/test_startup_conf.sh"
	@echo
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    prisma-routing-dev:26.06 sh /src/appliance/test_tuned_conf.sh"
	@echo
# The third profile is not a third box, it is every box: the two above start
# VPP on ONE generated file each, and the tuner has nine branches. #377 shipped
# in the branch neither of them renders - a card whose maximum MTU is below
# 9000 - and VPP answered "unknown input `mtu 1500'" on an installed box.
	@sg docker -c "docker run --rm --privileged -v $$(pwd):/src -w /src \
	    prisma-routing-dev:26.06 sh /src/appliance/test_startup_conf_shapes.sh"

# The hardware lab keeps a container alive so that prismash and vppctl have a VPP
# to talk to. Long-lived means somebody has to stop it: hw-down exists so that
# "somebody" is a command rather than a memory.
hw-up:
	@sg docker -c "docker rm -f prisma-vpp >/dev/null 2>&1; \
	    docker run -d --name prisma-vpp --privileged --network=host \
	      -v $$(pwd):/src -v $$HOME/.ssh:/root/.ssh:ro \
	      -w /src prisma-routing-dev:26.06 sleep infinity" >/dev/null
	@sg docker -c "docker exec prisma-vpp sh -c 'apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq iproute2 iptables openssh-client tcpdump \
	      >/dev/null 2>&1'"
	@echo "prisma-vpp is up. Remember: make hw-down when you are finished."

# The topology: addresses and routes on both machines. Separate from hw-vpp
# because it touches the PEER, and because getting it wrong is what made the
# lab look like a dataplane fault for a long time.
hw-lab:
	@sg docker -c "docker exec prisma-vpp sh /src/lab/hw-lab.sh"

hw-vpp:
	@sg docker -c "docker exec prisma-vpp sh /src/lab/hw-vpp.sh"

# Safe when it is not there, so it can be run without checking first.
hw-down:
	@sg docker -c "docker rm -f prisma-vpp >/dev/null 2>&1" || true
	@echo "prisma-vpp removed."

# ISO: needs root and the image tools, so it runs in the container.
# The dependencies live here on purpose - build-iso.sh only checks them.
ISO_DEPS := debootstrap xorriso squashfs-tools dosfstools mtools \
              grub-common grub-pc-bin grub-efi-amd64-bin
ISO_RUN = sg docker -c "docker run --rm --privileged --device=/dev/kvm \
	    -v $$(pwd):/src -w /src prisma-routing-dev:26.06 sh -c '$(1)'"

# THE IMAGES FIRST, ON THIS HOST. build-iso.sh runs inside a privileged
# container that has no docker daemon, so the images cannot be built from
# there - and a medium built without them carries none, quietly, which is
# what happened the first time (#396).
images:
	sh tools/build-images.sh save

# THE KERNEL, the same way and for the same reason as the images (#449): built
# on this host, in a container of its own, cached by Debian source version
# under /var/cache/prisma/kernel, and put at the top of the tree for
# build-iso.sh to install. A medium built without it would carry no kernel
# at all, so build-iso.sh refuses rather than falls back.
kernel:
	sh tools/build-kernel.sh
	. appliance/kernel.env; cp /var/cache/prisma/kernel/$$KERNEL_DEB_VERSION/linux-image-*-prisma_*.deb prisma-kernel.deb

iso: images kernel
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq $(ISO_DEPS) >/dev/null 2>&1; \
	    sh /src/appliance/build-iso.sh)

# Installs the ISO onto a blank disk and boots that disk with the ISO gone.
# The second boot is the assertion: a live image cannot prove it.
# The wrong-password run goes WITH it, in the same container, and is not a
# separate target anybody can forget. TESTING.md calls it "how the driver is
# kept honest" and nothing invoked it: not the suite, which excludes
# install-iso, not this file, and not the release gate (#256). A control that
# is optional is a control nobody runs, and what it controls is the thing that
# would otherwise let this lab run a whole session against a login prompt and
# report a pass.
#
# Three conditions, because two of them are easy to write in a way that cannot
# go red: the run must FAIL, it must fail AT THE LOGIN, and this recipe must
# fail if it ever starts passing.
# NOT `test-install: iso`. The medium this installs has to be the medium the
# gate booted, and a phony prerequisite rebuilt it: tools/release-gate.sh ran
# `make iso`, booted that image, and this target then built a second one over
# the same path and installed that (#307). tools/iso-ready.sh answers whether
# there is already an image and whether the tree has moved since it was built,
# so a developer with no image still gets one and nobody gets yesterday's.
test-install:
	@sh tools/iso-ready.sh || $(MAKE) iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 qemu-utils squashfs-tools \
	      fdisk mdadm >/dev/null 2>&1; \
	    sh /src/tools/test-install.sh)

# The same install onto TWO disks, then the same box booted with one of them
# pulled out. It is a separate target and not a flag on the one above because
# they answer different questions and the one above is the one that says a
# box with a single disk is unchanged (#83).
test-mirror:
	@sh tools/iso-ready.sh || $(MAKE) iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 qemu-utils squashfs-tools \
	      fdisk mdadm >/dev/null 2>&1; \
	    sh /src/lab/install-iso.sh mirror)

# Boots the freshly built ISO and demands the self-test say "ready".
# Same as test-install below, and for the same reason.
test-iso:
	@sh tools/iso-ready.sh || $(MAKE) iso
	@$(call ISO_RUN,set -e; apt-get update -qq >/dev/null 2>&1; \
	    apt-get install -y -qq qemu-system-x86 >/dev/null 2>&1; \
	    BOOT_SECS=$${BOOT_SECS:-240} sh /src/lab/boot-iso.sh)

# The upgrade artifact: what an operator who already has a box installs a new
# version from. No root, no container, no debootstrap - it is the tree plus the
# plugins in a tarball, and appliance/build-upgrade.sh proves it by installing
# it into a throwaway directory before it will hand it over.
#
# NOT a prerequisite of anything and not dependent on `iso`. The medium and the
# artifact are two builds of one tree that must agree about which tree, and
# tools/version-stamp.sh is what makes them: the artifact's name is the ISO's
# with the extension swapped, so a build that may not be called a release
# cannot produce an artifact that may.
#
# The plugin is NOT built here, for the same reason `iso` does not build it:
# tools/release-gate.sh builds it once and both read what is there. On a
# developer's machine with no build, build-upgrade.sh refuses and says which
# command makes one.
upgrade-artifact:
	@sh appliance/build-upgrade.sh

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
	@rm -rf $(BUILD) test/bpa_vectors.h test/bpa_nat64_vectors.h
