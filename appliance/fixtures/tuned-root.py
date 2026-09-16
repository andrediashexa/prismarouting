# A synthetic box the tuner will judge TUNED: isolated CPUs the kernel really
# has, hugepages really reserved, and a card. Only what the tuner reads.
import os, sys
R = sys.argv[1]
def w(p, c):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(str(c) + "\n")
for n in range(8):
    b = f"{R}/sys/devices/system/cpu/cpu{n}/topology"
    w(f"{b}/physical_package_id", 0)
    w(f"{b}/core_id", n)
    w(f"{b}/thread_siblings_list", n)
w(f"{R}/sys/devices/system/cpu/isolated", "1-7")
w(f"{R}/sys/devices/system/cpu/online", "0-7")
w(f"{R}/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages", 5)
w(f"{R}/proc/meminfo", "MemTotal:       16777216 kB")
# One virtio card, the way a guest presents it: the netdev's `device` points
# at the virtio bus device whose parent is the PCI one.
pci = f"{R}/sys/devices/pci0000:00/0000:00:12.0"
os.makedirs(f"{pci}/virtio3", exist_ok=True)
w(f"{pci}/class", "0x020000")
os.makedirs(f"{R}/sys/class/net", exist_ok=True)
os.makedirs(f"{R}/sys/class/net/ens18", exist_ok=True)
if not os.path.exists(f"{R}/sys/class/net/ens18/device"):
    os.symlink(f"{pci}/virtio3", f"{R}/sys/class/net/ens18/device")
w(f"{pci}/numa_node", 0)
print(R)

# The card's maximum MTU is a netlink attribute, not a file under `root`, so
# the tuner asks `ip`. HEXA_IP is the seam it reads it through, and without a
# stub here the fixture would ask the machine running the test about a card
# that only exists in this tree - which is how the first #330 check came to be
# one that could not fail.
bin_ = os.path.join(R, "bin")
os.makedirs(bin_, exist_ok=True)
ip = os.path.join(bin_, "ip")
open(ip, "w").write(
    "#!/bin/sh\n"
    "echo '2: ens18: <BROADCAST,MULTICAST,UP> mtu 1500 qdisc fq_codel'\n"
    "echo '    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff "
    "minmtu 68 maxmtu 1500'\n")
os.chmod(ip, 0o755)
