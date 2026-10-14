/*
 * build/prisma_trail.c (#597): the PPPoE session trail - who held which
 * inside address from when to when - as the box itself saw it.
 *
 * WHY IT EXISTS (docs/bng.md 3.1). A BNG hands the inside address out at
 * session time, in order of arrival, so the CGNAT's record of a block
 * (the BPA log, natEvent 16/17) names an inside address and no longer a
 * subscriber: answering "who held 200.150.0.19:2048 at 02:51" needs a second
 * retained record of which session held that inside address at 02:51. That
 * record was the operator's RADIUS accounting, which this product does not
 * run, retain or vouch for, and which is not written at all while every
 * accounting server is down. This is the box's own, written by the control
 * plane at the moment it binds and unbinds the address - whatever RADIUS got.
 *
 * WHAT A LINE IS, one per event, appended, fsynced:
 *
 *   <time> start session <sid> user <u> mac <m> interface <i> address <a>
 *                [prefix <IPv6 WAN prefix>]
 *   <time> delegate session ... delegated <p> delegated-since <time>
 *                since <time>
 *   <time> stop  session ... address <a> since <time> cause <c>
 *   <time> carry session ... address <a> since <time>
 *   <time> restart closed <n>
 *
 * each followed by " seq <n> sha256 <64 hex>": the digest over the previous
 * entry's digest (32 bytes, zero before the first) and this line up to and
 * including its sequence number - the configuration trail's chain
 * (src/plugins/bpa/bpa_audit.c), byte for byte the same rule, so one way of
 * reading a chain serves both. The time is UTC with milliseconds, the BPA
 * log's own resolution, so a record of one is compared with the other
 * without a conversion.
 *
 * A stop repeats what its start said, `since` included, so either line
 * answers alone - the rule the BPA log's release follows (#397). `carry`
 * restates a session still held when the file is rotated (monthly), so a
 * month's file answers for any instant in that month on its own;
 * `delegate` restates a session the moment it is delegated an IPv6 prefix
 * by DHCPv6 (#609), which its stop and carry then repeat; `restart`
 * is the control plane starting, and the stops written just before it, cause
 * Control-Plane-Lost, close what a control plane that died left open.
 *
 * Standalone C with no library but libc: the module that includes it
 * (build/accel-ppp-ng.patch, accel-pppd/extra/prisma_trail_mod.c) adds the
 * events and the lock, and test/test_prisma_trail.c drives everything here
 * alone. SHA-256 is written out rather than taken from libcrypto so that the
 * unit test needs nothing the host may not have; its vectors are FIPS 180-2's.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define PRISMA_TRAIL_LINE 1024
/* An escaped field longer than this is carried in part, marked "%+" - not an
 * escape this file ever writes, so a reader can tell a cut name from a whole
 * one. */
#define PRISMA_TRAIL_FIELD 192

/* ------------------------------------------------------------- SHA-256 */

struct prisma_sha256 {
	uint32_t h[8];
	uint64_t len;
	unsigned char buf[64];
	size_t n;
};

static const uint32_t prisma_sha256_k[64] = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
	0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
	0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
	0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
	0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
	0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
	0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
	0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
	0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
	0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
	0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define PRISMA_ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void prisma_sha256_block(struct prisma_sha256 *c, const unsigned char *p)
{
	uint32_t w[64], a, b, d, e, f, g, h, cc, t1, t2;
	int i;

	for (i = 0; i < 16; i++)
		w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 |
		       (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
	for (i = 16; i < 64; i++) {
		uint32_t s0 = PRISMA_ROR(w[i - 15], 7) ^ PRISMA_ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
		uint32_t s1 = PRISMA_ROR(w[i - 2], 17) ^ PRISMA_ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
		w[i] = w[i - 16] + s0 + w[i - 7] + s1;
	}
	a = c->h[0]; b = c->h[1]; cc = c->h[2]; d = c->h[3];
	e = c->h[4]; f = c->h[5]; g = c->h[6]; h = c->h[7];
	for (i = 0; i < 64; i++) {
		t1 = h + (PRISMA_ROR(e, 6) ^ PRISMA_ROR(e, 11) ^ PRISMA_ROR(e, 25)) +
		     ((e & f) ^ (~e & g)) + prisma_sha256_k[i] + w[i];
		t2 = (PRISMA_ROR(a, 2) ^ PRISMA_ROR(a, 13) ^ PRISMA_ROR(a, 22)) +
		     ((a & b) ^ (a & cc) ^ (b & cc));
		h = g; g = f; f = e; e = d + t1;
		d = cc; cc = b; b = a; a = t1 + t2;
	}
	c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d;
	c->h[4] += e; c->h[5] += f; c->h[6] += g; c->h[7] += h;
}

static void prisma_sha256_init(struct prisma_sha256 *c)
{
	static const uint32_t iv[8] = {
		0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
		0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
	};
	memcpy(c->h, iv, sizeof(iv));
	c->len = 0;
	c->n = 0;
}

static void prisma_sha256_update(struct prisma_sha256 *c, const void *data, size_t len)
{
	const unsigned char *p = data;

	c->len += len;
	while (len) {
		size_t take = 64 - c->n < len ? 64 - c->n : len;
		memcpy(c->buf + c->n, p, take);
		c->n += take;
		p += take;
		len -= take;
		if (c->n == 64) {
			prisma_sha256_block(c, c->buf);
			c->n = 0;
		}
	}
}

static void prisma_sha256_final(struct prisma_sha256 *c, unsigned char out[32])
{
	uint64_t bits = c->len * 8;
	unsigned char pad = 0x80, zero = 0, lenb[8];
	int i;

	prisma_sha256_update(c, &pad, 1);
	while (c->n != 56)
		prisma_sha256_update(c, &zero, 1);
	for (i = 0; i < 8; i++)
		lenb[i] = (unsigned char)(bits >> (56 - 8 * i));
	prisma_sha256_update(c, lenb, 8);
	for (i = 0; i < 8; i++) {
		out[4 * i] = (unsigned char)(c->h[i] >> 24);
		out[4 * i + 1] = (unsigned char)(c->h[i] >> 16);
		out[4 * i + 2] = (unsigned char)(c->h[i] >> 8);
		out[4 * i + 3] = (unsigned char)c->h[i];
	}
}

/* SHA-256 of `a` followed by `b`: the chain's digest is the previous digest
 * and the entry's text, one after the other. */
static void prisma_sha256(const void *a, size_t alen, const void *b, size_t blen,
			  unsigned char out[32])
{
	struct prisma_sha256 c;

	prisma_sha256_init(&c);
	prisma_sha256_update(&c, a, alen);
	prisma_sha256_update(&c, b, blen);
	prisma_sha256_final(&c, out);
}

/* ------------------------------------------------------------- fields */

struct prisma_trail {
	unsigned long long seq;		/* the last entry's sequence number */
	unsigned char head[32];		/* and its digest; zero before the first */
	char month[8];			/* "YYYY-MM" of the file's entries, "" when none */
};

/*
 * `in` as one word of a line: every byte that is a space, a control, not
 * ASCII, or '%' written %XX, so a user name with a space in it cannot shift
 * the fields after it. An empty value is "-". Past PRISMA_TRAIL_FIELD the
 * value is cut and ends in "%+". Returns 1 when it was cut, 0 otherwise.
 */
static int prisma_trail_escape(const char *in, char *out, size_t n)
{
	static const char hex[] = "0123456789ABCDEF";
	size_t o = 0, most = n - 1 < PRISMA_TRAIL_FIELD ? n - 1 : PRISMA_TRAIL_FIELD;
	const unsigned char *p = (const unsigned char *)(in ? in : "");

	if (!*p) {
		snprintf(out, n, "-");
		return 0;
	}
	for (; *p; p++) {
		int plain = *p > 0x20 && *p < 0x7f && *p != '%';
		if (o + (plain ? 1 : 3) > most - 2) {
			out[o++] = '%';
			out[o++] = '+';
			out[o] = 0;
			return 1;
		}
		if (plain)
			out[o++] = (char)*p;
		else {
			out[o++] = '%';
			out[o++] = hex[*p >> 4];
			out[o++] = hex[*p & 15];
		}
	}
	out[o] = 0;
	return 0;
}

/* `ts` as the BPA log writes an instant: UTC, milliseconds, a Z. */
static void prisma_trail_stamp(const struct timespec *ts, char out[32])
{
	struct tm tm;
	time_t s = ts->tv_sec;
	unsigned ms = (unsigned)(ts->tv_nsec / 1000000) % 1000u;

	gmtime_r(&s, &tm);
	if (strftime(out, 32, "%Y-%m-%dT%H:%M:%S", &tm) != 19) {
		snprintf(out, 32, "1970-01-01T00:00:00.000Z");
		return;
	}
	out[19] = '.';
	out[20] = (char)('0' + ms / 100);
	out[21] = (char)('0' + ms / 10 % 10);
	out[22] = (char)('0' + ms % 10);
	out[23] = 'Z';
	out[24] = 0;
}

/*
 * accel-ppp's terminate cause (include/ap_session.h, TERM_*) as the
 * Acct-Terminate-Cause accel-ppp sends RADIUS for it (radius/acct.c), word
 * for word: the two records of one stop say the same thing. A stop no
 * control plane wrote - closed by the next one - is Control-Plane-Lost,
 * which is the trail's own.
 */
static const char *prisma_trail_cause(int term)
{
	static const char *names[] = {
		NULL, "User-Request", "Session-Timeout", "Admin-Reset", "User-Error",
		"NAS-Error", "NAS-Request", "NAS-Reboot", "User-Error", "Lost-Carrier",
		"Idle-Timeout",
	};

	if (term > 0 && term < (int)(sizeof(names) / sizeof(names[0])))
		return names[term];
	return "Unknown";
}

/* ------------------------------------------------------------- the chain */

/*
 * `body` sealed into `line`: " seq <n+1>", the digest over the previous
 * digest and everything up to there, " sha256 <hex>", a newline. The chain
 * is NOT advanced: prisma_trail_commit() does that once the line is on disk,
 * so a line that could not be written leaves no gap in the sequence.
 * Returns the line's length, or -1 when it does not fit.
 */
static int prisma_trail_seal(const struct prisma_trail *t, const char *body,
			     char *line, size_t n, unsigned char digest[32])
{
	int len, i;

	len = snprintf(line, n, "%s seq %llu", body, t->seq + 1);
	if (len < 0 || (size_t)len + 8 + 64 + 2 > n)
		return -1;
	prisma_sha256(t->head, 32, line, (size_t)len, digest);
	len += snprintf(line + len, n - len, " sha256 ");
	for (i = 0; i < 32; i++)
		len += snprintf(line + len, n - len, "%02x", digest[i]);
	line[len++] = '\n';
	line[len] = 0;
	return len;
}

/* The chain one entry on, for a sealed line now on disk. */
static void prisma_trail_commit(struct prisma_trail *t, const unsigned char digest[32],
				const char *line)
{
	memcpy(t->head, digest, 32);
	t->seq++;
	if (!t->month[0]) {
		memcpy(t->month, line, 7);
		t->month[7] = 0;
	}
}

static int prisma_trail_unhex(const char *s, unsigned char out[32])
{
	int i, hi, lo;

	for (i = 0; i < 32; i++) {
		hi = s[2 * i]; lo = s[2 * i + 1];
		hi = hi >= '0' && hi <= '9' ? hi - '0' : hi >= 'a' && hi <= 'f' ? hi - 'a' + 10 : -1;
		lo = lo >= '0' && lo <= '9' ? lo - '0' : lo >= 'a' && lo <= 'f' ? lo - 'a' + 10 : -1;
		if (hi < 0 || lo < 0)
			return -1;
		out[i] = (unsigned char)(hi << 4 | lo);
	}
	return 0;
}

/*
 * One line of the file read back into the chain: its sequence and digest
 * become the head, and the first line's month the file's. A line with no
 * digest - a write cut short by a power loss - is not an entry and changes
 * nothing. Returns 1 for an entry, 0 otherwise.
 */
static int prisma_trail_take(struct prisma_trail *t, const char *line)
{
	const char *sha = strstr(line, " sha256 "), *sq;
	unsigned char d[32];
	unsigned long long seq;

	if (!sha || strlen(sha + 8) < 64 || prisma_trail_unhex(sha + 8, d))
		return 0;
	sq = strstr(line, " seq ");
	if (!sq || sq > sha)
		return 0;
	seq = strtoull(sq + 5, NULL, 10);
	if (!seq)
		return 0;
	memcpy(t->head, d, 32);
	t->seq = seq;
	if (!t->month[0] && strlen(line) > 7) {
		memcpy(t->month, line, 7);
		t->month[7] = 0;
	}
	return 1;
}

/* ------------------------------------------------------------- the file */

/*
 * `line` appended to `path` and fsynced: 0, or -1 with errno. One write of
 * the whole line under O_APPEND, so two writers never interleave parts of
 * lines; a write that fails part-way is cut off again (ftruncate to the size
 * before it), because a fragment with no newline would glue itself to the
 * next entry and break the chain at an entry nobody touched.
 */
static int prisma_trail_append(const char *path, const char *line, size_t len)
{
	struct stat st;
	ssize_t w;
	int fd, err;

	fd = open(path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0640);
	if (fd < 0)
		return -1;
	if (fstat(fd, &st)) {
		err = errno;
		close(fd);
		errno = err;
		return -1;
	}
	w = write(fd, line, len);
	if (w != (ssize_t)len || fsync(fd)) {
		err = w < 0 ? errno : (w != (ssize_t)len ? ENOSPC : errno);
		if (ftruncate(fd, st.st_size)) {
			/* nothing more can be done; the reader reports the fragment */
		}
		close(fd);
		errno = err;
		return -1;
	}
	close(fd);
	return 0;
}

/* One session still held at the end of a file, as the scan found it. */
struct prisma_trail_open {
	struct prisma_trail_open *next;
	char sid[40];
	char fields[PRISMA_TRAIL_LINE];	/* "session <sid> user ... address <a>" */
	char since[32];
};

/* "session <sid> ..." up to `end` (exclusive) into `o`, and the sid. */
static void prisma_trail_fields(struct prisma_trail_open *o, const char *from,
				const char *end)
{
	size_t n = (size_t)(end - from);

	if (n >= sizeof(o->fields))
		n = sizeof(o->fields) - 1;
	memcpy(o->fields, from, n);
	o->fields[n] = 0;
	if (sscanf(o->fields, "session %39s", o->sid) != 1)
		o->sid[0] = 0;
}

/*
 * The sessions one line opens or closes, applied to the list `*open`:
 * a start or a carry adds one, a stop takes its session away. Returns the
 * list; the caller frees it with prisma_trail_free().
 */
static struct prisma_trail_open *prisma_trail_track(struct prisma_trail_open *open,
						    const char *line)
{
	const char *kind = strchr(line, ' '), *end, *since;
	struct prisma_trail_open *o, **pp;
	char sid[40];

	if (!kind)
		return open;
	kind++;
	end = strstr(kind, " seq ");
	if (!end)
		return open;
	if (!strncmp(kind, "start session ", 14) || !strncmp(kind, "carry session ", 14) ||
	    !strncmp(kind, "delegate session ", 17)) {
		/* A delegate (#609) restates the session with its prefix, as a
		 * carry does: what a control plane killed later closes with. */
		int skip = kind[0] == 'd' ? 9 : 6;

		o = calloc(1, sizeof(*o));
		if (!o)
			return open;
		since = strstr(kind, " since ");
		if (!strncmp(kind, "start ", 6)) {	/* since is its own time */
			prisma_trail_fields(o, kind + 6, end);
			snprintf(o->since, sizeof(o->since), "%.*s", (int)(kind - 1 - line), line);
		} else if (since && since < end) {
			prisma_trail_fields(o, kind + skip, since);
			snprintf(o->since, sizeof(o->since), "%.*s", (int)(end - since - 7), since + 7);
		} else {
			free(o);
			return open;
		}
		/* A session id is one session: a second start replaces the first. */
		for (pp = &open; *pp; pp = &(*pp)->next)
			if (!strcmp((*pp)->sid, o->sid)) {
				struct prisma_trail_open *dup = *pp;
				*pp = dup->next;
				free(dup);
				break;
			}
		o->next = open;
		return o;
	}
	if (!strncmp(kind, "stop session ", 13) && sscanf(kind + 13, "%39s", sid) == 1) {
		for (pp = &open; *pp; pp = &(*pp)->next)
			if (!strcmp((*pp)->sid, sid)) {
				o = *pp;
				*pp = o->next;
				free(o);
				break;
			}
	}
	return open;
}

static void prisma_trail_free(struct prisma_trail_open *open)
{
	while (open) {
		struct prisma_trail_open *next = open->next;
		free(open);
		open = next;
	}
}

/*
 * The file at `path` read back at start: the chain's head into `t`, and the
 * sessions it leaves open - those a control plane that died never closed -
 * returned for the caller to close. A file whose last byte is not a newline
 * (a power cut mid-write on a filesystem that kept the fragment) gets one,
 * so the next entry starts on a line of its own; the fragment stays, and
 * the verifier names it. -1 when the file exists and cannot be read.
 */
static int prisma_trail_recover(struct prisma_trail *t, const char *path,
				struct prisma_trail_open **open)
{
	char line[PRISMA_TRAIL_LINE * 2];
	size_t len = 0;
	FILE *f;
	int c = '\n';

	memset(t, 0, sizeof(*t));
	*open = NULL;
	f = fopen(path, "re");
	if (!f)
		return errno == ENOENT ? 0 : -1;
	while (fgets(line, sizeof(line), f)) {
		len = strlen(line);
		c = len ? line[len - 1] : c;
		if (len && line[len - 1] == '\n')
			line[--len] = 0;
		if (prisma_trail_take(t, line))
			*open = prisma_trail_track(*open, line);
	}
	fclose(f);
	if (c != '\n')
		prisma_trail_append(path, "\n", 1);
	return 0;
}

/*
 * The file moved aside as `<path>.<month>` when an entry of a later month
 * than its own is about to be written: 1 when it moved, 0 when it stays
 * (nothing in it yet, the same month, a clock gone backwards, or a file of
 * that name already there - never overwritten). By link and unlink, not
 * rename, because rename would replace an existing file without a word.
 */
static int prisma_trail_rotate(struct prisma_trail *t, const char *path,
			       const char *month)
{
	char aside[4096], dir[4096], *slash;
	int fd;

	if (!t->month[0] || strncmp(month, t->month, 7) <= 0)
		return 0;
	if (snprintf(aside, sizeof(aside), "%s.%s", path, t->month) >= (int)sizeof(aside))
		return 0;
	if (link(path, aside))
		return 0;
	if (unlink(path)) {
		unlink(aside);
		return 0;
	}
	snprintf(dir, sizeof(dir), "%s", path);
	slash = strrchr(dir, '/');
	if (slash) {
		*slash = 0;
		fd = open(*dir ? dir : "/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
		if (fd >= 0) {
			fsync(fd);
			close(fd);
		}
	}
	t->month[0] = 0;
	return 1;
}
