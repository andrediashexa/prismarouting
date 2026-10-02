/*
 * logd_ipfix_udp.c - the RFC 8158 export over UDP, from the log container
 * (#550 step 3). The same messages VPP's vnet/ipfix-export writes for this
 * plugin's two flow reports, made here so that no part of log export runs in
 * the dataplane:
 *
 *   message header  version 10, length, export time (s), sequence number,
 *                   observation domain - VPP's stream's domain (1)
 *   template set    set id 2; one template per message, as VPP sends them:
 *                   the block report (RFC 8158 Table 21, 7 fields) and the
 *                   boot marker's (2 fields), with the template ids VPP's
 *                   flow report assigned, every template_interval seconds and
 *                   before the first data record
 *   data set        set id = the template id; block records packed until the
 *                   next one would not fit the path MTU (IP and UDP headers
 *                   counted, as VPP counts them); a marker in a message of its
 *                   own, sent at once
 *
 * Sent from a kernel UDP socket bound to the configured source address and
 * source port 4739 - VPP's stream's - in the host's network namespace: the
 * datagrams leave through the linux_cp pair and the dataplane's FIB, as the
 * TCP streams' always have, with the source the collector already knows.
 * IP_FREEBIND, because the address is the dataplane's and the kernel may not
 * have it on any interface of its own. Only the default table: a VRF exporter
 * stays in VPP (logd_udp_takes() says so).
 *
 * THE SEQUENCE NUMBER is VPP's: the plugin's records never advance VPP's
 * stream counter (bpa_log_block_event() writes records without counting them
 * in n_data_records), so every message VPP sends carries the stream's
 * sequence number as it stands - and so does this. A collector sees the same
 * number from either sender.
 *
 * Plain C over POSIX; test/test_prisma_logd.c receives every message on
 * 127.0.0.1 and reads it field by field.
 */
#include <errno.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define LOGD_IE_OBSERVATION_TIME_MS 323
#define LOGD_IE_NAT_EVENT	    230
#define LOGD_IE_SOURCE_IPV4	    8
#define LOGD_IE_POST_NAT_SOURCE_IPV4 225
#define LOGD_IE_PORT_RANGE_START    361
#define LOGD_IE_PORT_RANGE_END	    362
#define LOGD_IE_NAT_POOL_ID	    283
#define LOGD_IE_SYSTEM_INIT_TIME_MS 160
#define LOGD_IPFIX_SRC_PORT	    4739
#define LOGD_IP_UDP_HDR		    28 /* IPv4 + UDP: in VPP's path MTU */
#define LOGD_IPFIX_HDR		    16
#define LOGD_SET_HDR		    4
#define LOGD_BLOCK_RECORD	    25
#define LOGD_MARKER_RECORD	    16

typedef struct
{
  int fd;
  bpa_log_seg_udp_cfg_t cfg;	/* the one the socket was made for */
  u8 buf[1500];
  u32 len;			/* bytes in buf: 0 = no message open */
  u32 n_in_buf;
  f64 last_template;		/* 0 = never sent on this socket */
  bpa_log_seg_udp_stats_t st;
} logd_udp_t;

static void
logd_put16 (u8 *p, u16 v)
{
  p[0] = v >> 8;
  p[1] = v;
}

static void
logd_put32 (u8 *p, u32 v)
{
  p[0] = v >> 24;
  p[1] = v >> 16;
  p[2] = v >> 8;
  p[3] = v;
}

static void
logd_put64 (u8 *p, u64 v)
{
  logd_put32 (p, (u32) (v >> 32));
  logd_put32 (p + 4, (u32) v);
}

/* Does the container carry this UDP export? Enabled, with a collector and a
 * source, in the default table: a VRF exporter stays in VPP. */
int
logd_udp_takes (const bpa_log_seg_udp_cfg_t *c)
{
  return bpa_log_seg_udp_takes (c);
}

/* Writes the message and set headers in front of a message of `len` bytes
 * whose set is `set_id`. */
void
logd_udp_headers (u8 *m, u32 len, u16 set_id, u32 export_time, u32 seq,
		  u32 domain)
{
  logd_put16 (m, 10);
  logd_put16 (m + 2, (u16) len);
  logd_put32 (m + 4, export_time);
  logd_put32 (m + 8, seq);
  logd_put32 (m + 12, domain);
  logd_put16 (m + 16, set_id);
  logd_put16 (m + 18, (u16) (len - LOGD_IPFIX_HDR));
}

/* A template message for the block report (marker = 0) or the marker's
 * (marker = 1), into m; returns its length. */
u32
logd_udp_template (const bpa_log_seg_udp_cfg_t *c, int marker, u32 now, u8 *m)
{
  static const u16 block[][2] = {
    { LOGD_IE_OBSERVATION_TIME_MS, 8 }, { LOGD_IE_NAT_EVENT, 1 },
    { LOGD_IE_SOURCE_IPV4, 4 },		{ LOGD_IE_POST_NAT_SOURCE_IPV4, 4 },
    { LOGD_IE_PORT_RANGE_START, 2 },	{ LOGD_IE_PORT_RANGE_END, 2 },
    { LOGD_IE_NAT_POOL_ID, 4 },
  };
  static const u16 mark[][2] = { { LOGD_IE_OBSERVATION_TIME_MS, 8 },
				 { LOGD_IE_SYSTEM_INIT_TIME_MS, 8 } };
  const u16 (*f)[2] = marker ? mark : block;
  u32 n = marker ? 2 : 7, i, len;
  u8 *p = m + LOGD_IPFIX_HDR + LOGD_SET_HDR;

  logd_put16 (p, marker ? c->marker_template_id : c->block_template_id);
  logd_put16 (p + 2, (u16) n);
  p += 4;
  for (i = 0; i < n; i++, p += 4)
    {
      logd_put16 (p, f[i][0]); /* enterprise bit 0: IANA's elements */
      logd_put16 (p + 2, f[i][1]);
    }
  len = (u32) (p - m);
  logd_udp_headers (m, len, 2, now, c->seq, c->domain_id);
  return len;
}

/* One block record, in VPP's byte order and field order, at p. */
void
logd_udp_block_record (u8 *p, f64 wall, u8 event, u32 ip_priv, u32 ip_pub,
		       u16 port_lo, u16 port_hi, u32 pool_id)
{
  logd_put64 (p, (u64) (wall * 1e3));
  p[8] = event;
  logd_put32 (p + 9, ip_priv);
  logd_put32 (p + 13, ip_pub);
  logd_put16 (p + 17, port_lo);
  logd_put16 (p + 19, port_hi);
  logd_put32 (p + 21, pool_id);
}

static int
logd_udp_send (logd_udp_t *u, const u8 *m, u32 len)
{
  struct sockaddr_in to = { .sin_family = AF_INET };

  to.sin_addr.s_addr = u->cfg.collector;
  to.sin_port = htons (u->cfg.port ? u->cfg.port : 4739);
  if (sendto (u->fd, m, len, 0, (struct sockaddr *) &to, sizeof (to))
      == (ssize_t) len)
    {
      u->st.n_packets++;
      return 0;
    }
  u->st.n_send_fail++;
  u->st.last_errno = errno;
  return -1;
}

/* Closes the socket; what was in an open message is dropped and counted. */
void
logd_udp_close (logd_udp_t *u)
{
  if (u->n_in_buf)
    u->st.n_dropped += u->n_in_buf;
  u->len = u->n_in_buf = 0;
  if (u->fd >= 0)
    close (u->fd);
  u->fd = -1;
  u->st.bound = 0;
  u->last_template = 0;
}

/* Makes (or remakes, when the configuration moved) the socket: bound to the
 * source and 4739. 0, or -1 with the errno counted. */
int
logd_udp_open (logd_udp_t *u, const bpa_log_seg_udp_cfg_t *c)
{
  struct sockaddr_in a = { .sin_family = AF_INET };
  int one = 1;

  if (u->fd >= 0 && u->cfg.collector == c->collector && u->cfg.src == c->src
      && u->cfg.port == c->port)
    {
      u->cfg = *c;
      return 0;
    }
  logd_udp_close (u);
  u->fd = socket (AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
  if (u->fd < 0)
    {
      u->st.last_errno = errno;
      return -1;
    }
  setsockopt (u->fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof (one));
  setsockopt (u->fd, IPPROTO_IP, IP_FREEBIND, &one, sizeof (one));
  a.sin_addr.s_addr = c->src;
  a.sin_port = htons (LOGD_IPFIX_SRC_PORT);
  if (bind (u->fd, (struct sockaddr *) &a, sizeof (a)))
    {
      u->st.last_errno = errno;
      close (u->fd);
      u->fd = -1;
      return -1;
    }
  u->cfg = *c;
  u->st.bound = 1;
  return 0;
}

/* Sends the open data message, if any. */
void
logd_udp_flush (logd_udp_t *u, u32 now)
{
  if (!u->n_in_buf)
    return;
  logd_udp_headers (u->buf, u->len, u->cfg.block_template_id, now,
		    u->cfg.seq, u->cfg.domain_id);
  if (logd_udp_send (u, u->buf, u->len))
    u->st.n_dropped += u->n_in_buf;
  u->len = u->n_in_buf = 0;
}

/* Both templates, when they are due: before the first data record on this
 * socket, and every template_interval seconds after. */
void
logd_udp_templates (logd_udp_t *u, f64 now)
{
  u8 m[128];
  u32 n;
  f64 ivl = u->cfg.template_interval ? u->cfg.template_interval : 20;

  if (u->last_template && now - u->last_template < ivl)
    return;
  n = logd_udp_template (&u->cfg, 0, (u32) now, m);
  if (!logd_udp_send (u, m, n))
    u->st.n_templates++;
  n = logd_udp_template (&u->cfg, 1, (u32) now, m);
  if (!logd_udp_send (u, m, n))
    u->st.n_templates++;
  u->last_template = now;
}

/* One block event: into the open message, which is sent first when the
 * record would not fit the path MTU. */
void
logd_udp_record (logd_udp_t *u, f64 now, f64 wall, u8 event, u32 ip_priv,
		 u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id)
{
  u32 room = u->cfg.path_mtu - LOGD_IP_UDP_HDR;

  if (u->len && u->len + LOGD_BLOCK_RECORD > room)
    logd_udp_flush (u, (u32) now);
  if (!u->len)
    u->len = LOGD_IPFIX_HDR + LOGD_SET_HDR;
  logd_udp_block_record (u->buf + u->len, wall, event, ip_priv, ip_pub,
			 port_lo, port_hi, pool_id);
  u->len += LOGD_BLOCK_RECORD;
  u->n_in_buf++;
  u->st.n_records++;
}

/* The boot marker: a message of its own, sent at once, as VPP sends it. */
void
logd_udp_marker (logd_udp_t *u, f64 now, f64 wall, f64 boot_wall)
{
  u8 m[LOGD_IPFIX_HDR + LOGD_SET_HDR + LOGD_MARKER_RECORD];
  u32 len = sizeof (m);

  logd_put64 (m + LOGD_IPFIX_HDR + LOGD_SET_HDR, (u64) (wall * 1e3));
  logd_put64 (m + LOGD_IPFIX_HDR + LOGD_SET_HDR + 8, (u64) (boot_wall * 1e3));
  logd_udp_headers (m, len, u->cfg.marker_template_id, (u32) now, u->cfg.seq,
		    u->cfg.domain_id);
  if (!logd_udp_send (u, m, len))
    u->st.n_markers++;
}
