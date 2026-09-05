# Shared lab setup. Sourced, not executed.

# Puts the vat2 plugin where vat2 looks for it, which is where install.sh puts
# it on a real box.
#
# The labs used to pass "vat2 -p /vat2_plugins" instead. That proves the client
# works in a configuration no appliance ever runs, and this project has already
# shipped one defect of exactly that shape: hexash had never been started from
# its installed location, so a missing config.py reached every ISO. Any lab that
# drives the binary API calls this.
use_binary_api() {
  install -D -m 0644 /vat2_plugins/bpa_test_plugin_bpa_plugin.so \
    /usr/lib/x86_64-linux-gnu/vat2_plugins/bpa_test_plugin_bpa_plugin.so
}
