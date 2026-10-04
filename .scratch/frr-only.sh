mkdir -p /var/run/frr /etc/frr
for d in zebra bfdd bgpd; do printf 'hostname box\n' > /etc/frr/$d.conf; done
chown -R frr:frr /var/run/frr /etc/frr
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf
/usr/lib/frr/mgmtd -d -A 127.0.0.1
/usr/lib/frr/staticd -d -A 127.0.0.1
/usr/lib/frr/bfdd -d -A 127.0.0.1 -f /etc/frr/bfdd.conf
/usr/lib/frr/bgpd -d -A 127.0.0.1 -f /etc/frr/bgpd.conf
sleep 3
ip link add dum0 type dummy; ip link set dum0 up; ip addr add 10.9.0.1/24 dev dum0
