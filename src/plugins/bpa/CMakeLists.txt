add_vpp_plugin(bpa
  SOURCES
  bpa_api.c
  bpa.c
  bpa_pool.c
  bpa_dyn.c
  bpa_lent.c
  bpa_rebalance.c
  bpa_log.c
  bpa_log_tcp.c
  bpa_log_syslog.c
  bpa_identity.c
  bpa_identity_table.c
  bpa_block_ident.c
  bpa_md5.c
  bpa_radius.c
  bpa_audit.c
  bpa_session.c
  bpa_in2out.c
  bpa_handoff.c
  bpa_out2in.c
  bpa_nat64.c
  bpa_ftp.c
  bpa_pptp.c
  bpa_cli.c
  bpa_ra.c
  bpa_ip6_bind.c
  bpa_stats.c
  bpa_transceiver.c
  bpa_linkcount.c

  API_FILES
  bpa.api
)

# The build stamp the loaded plugin carries (#291).
#
# A custom TARGET rather than a custom command on a file, and it runs on every
# build on purpose: what the stamp says changes when HEAD moves or the tree
# goes dirty, and neither of those touches a file this build already depends
# on. The script writes the header only when its content changed, so a build
# that is genuinely the same build does not relink.
#
# The dependency is on the target and not on the header, because the header
# does not exist on the first configure - the compiler's own depfile is what
# recompiles bpa.c afterwards, which is exactly right: only the objects that
# include it.
set(BPA_VERSION_H ${CMAKE_CURRENT_BINARY_DIR}/bpa_version.h)
add_custom_target(bpa_version_h ALL
  COMMAND sh ${CMAKE_SOURCE_DIR}/tools/plugin-version-h.sh
              ${CMAKE_SOURCE_DIR} ${BPA_VERSION_H}
  BYPRODUCTS ${BPA_VERSION_H}
  COMMENT "stamping the plugin with this tree's version"
  VERBATIM)
add_dependencies(bpa_plugin bpa_version_h)
