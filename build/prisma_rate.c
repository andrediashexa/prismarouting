/*
 * Prisma Routing (#607): the rate a Cisco-AVPair carries, for accel-ppp-ng's
 * shaper. build/accel-ppp-build.sh copies this file into accel-pppd/shaper/
 * and build/accel-ppp-ng.patch #includes it from shaper.c; test/
 * test_prisma_rate.c compiles it alone. It depends on libc and nothing else,
 * which is what lets the parser be tested without accel-ppp.
 *
 * WHAT IS READ, strictly - the forms Cisco's BNGs document and ISPs send:
 *
 *   lcp:interface-config=rate-limit input|output <bps> <normal-burst>
 *       <max-burst> conform-action transmit exceed-action drop
 *   lcp:interface-config#<n>=...                         (the indexed form)
 *   ip:sub-qos-policy-in=<name>,  ip:sub-qos-policy-out=<name>
 *   subscriber:sub-qos-policy-in=<name>, subscriber:sub-qos-policy-out=<name>
 *
 * `input` is what the router receives on the subscriber's interface: the
 * subscriber's UPLOAD; `output` and `-out` are the download. A policy only
 * names a QoS policy, so its rate is the operator's own `rate-profile` of that
 * name: its upload rate for `-in`, its download rate for `-out`.
 *
 * Anything else under a key that is not one of these is not a rate (0): an
 * interface-config that is not `rate-limit` (an `ip unnumbered`, say), or
 * another key. A rate key whose value is not exactly one of the shapes above -
 * an `access-group` rate-limit, which limits one ACL's traffic and not the
 * session's, a missing action, a rate out of bounds - is malformed (-1), and
 * the caller applies NONE of the packet's Cisco rates: half a plan is not
 * one.
 */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#define PRISMA_RATE_UP    1
#define PRISMA_RATE_DOWN  2
#define PRISMA_RATE_NAME_MAX 32
/* 10 Gbit/s, what the policer path takes (the shaper's integer bound). */
#define PRISMA_RATE_MAX_KBIT 10000000L

struct prisma_rate {
	int dir;                /* PRISMA_RATE_UP or PRISMA_RATE_DOWN */
	long kbit;              /* a rate-limit's rate; 0 for a policy name */
	long burst;             /* its normal burst, bytes */
	char name[PRISMA_RATE_NAME_MAX + 1]; /* a policy's name; "" otherwise */
};

/* `s` is a run of digits and nothing else, as a long; -1 otherwise */
static long prisma_rate_number(const char *s)
{
	long v = 0;

	if (!*s || strlen(s) > 11)
		return -1;
	for (; *s; s++) {
		if (!isdigit((unsigned char)*s))
			return -1;
		v = v * 10 + (*s - '0');
	}
	return v;
}

/* `s` is a policy or profile name: 1-32 of [A-Za-z0-9._-] */
static int prisma_rate_name_ok(const char *s)
{
	size_t n = strlen(s), i;

	if (n < 1 || n > PRISMA_RATE_NAME_MAX)
		return 0;
	for (i = 0; i < n; i++)
		if (!isalnum((unsigned char)s[i]) && s[i] != '.' && s[i] != '_'
				&& s[i] != '-')
			return 0;
	return 1;
}

/*
 * One Cisco-AVPair value: 1 a rate or a policy name (into *out), 0 not a rate
 * AVPair, -1 a rate AVPair that is malformed.
 */
static int prisma_rate_avpair(const char *avpair, struct prisma_rate *out)
{
	char buf[256], *val, *tok[12], *save = NULL, *t;
	const char *key;
	int n = 0;
	long bps, nb, mb;

	memset(out, 0, sizeof(*out));
	if (!avpair || strlen(avpair) >= sizeof(buf))
		return 0;
	strcpy(buf, avpair);
	val = strchr(buf, '=');
	if (!val)
		return 0;
	*val++ = 0;
	key = buf;

	if (!strcmp(key, "ip:sub-qos-policy-in") || !strcmp(key, "subscriber:sub-qos-policy-in")
			|| !strcmp(key, "ip:sub-qos-policy-out") || !strcmp(key, "subscriber:sub-qos-policy-out")) {
		if (!prisma_rate_name_ok(val))
			return -1;
		out->dir = strstr(key, "-in") ? PRISMA_RATE_UP : PRISMA_RATE_DOWN;
		strcpy(out->name, val);
		return 1;
	}

	if (strncmp(key, "lcp:interface-config", 20))
		return 0;
	if (key[20] == '#') {
		if (prisma_rate_number(key + 21) < 0)
			return 0;
	} else if (key[20])
		return 0;

	for (t = strtok_r(val, " \t", &save); t && n < 12; t = strtok_r(NULL, " \t", &save))
		tok[n++] = t;
	if (n == 0 || strcmp(tok[0], "rate-limit"))
		return 0;
	/* rate-limit input|output <bps> <normal> <max> conform-action transmit
	 * exceed-action drop: nine words, exactly */
	if (n != 9 || (strcmp(tok[1], "input") && strcmp(tok[1], "output"))
			|| strcmp(tok[5], "conform-action") || strcmp(tok[6], "transmit")
			|| strcmp(tok[7], "exceed-action") || strcmp(tok[8], "drop"))
		return -1;
	bps = prisma_rate_number(tok[2]);
	nb = prisma_rate_number(tok[3]);
	mb = prisma_rate_number(tok[4]);
	if (bps < 8000 || bps / 1000 > PRISMA_RATE_MAX_KBIT || nb < 1
			|| nb > 1000000000L || mb < nb)
		return -1;
	out->dir = strcmp(tok[1], "input") ? PRISMA_RATE_DOWN : PRISMA_RATE_UP;
	out->kbit = bps / 1000;
	out->burst = nb;
	return 1;
}

/*
 * A `cisco-profile=<name>,<down kbit>,<up kbit>` line of [shaper] - the VTY's
 * `aaa radius rate-profile` - into *name, *down, *up; 0, or -1 malformed.
 */
static int prisma_rate_profile_line(const char *line, char *name, long *down, long *up)
{
	char buf[128], *a, *b;

	if (!line || strlen(line) >= sizeof(buf))
		return -1;
	strcpy(buf, line);
	a = strchr(buf, ',');
	if (!a)
		return -1;
	*a++ = 0;
	b = strchr(a, ',');
	if (!b)
		return -1;
	*b++ = 0;
	if (!prisma_rate_name_ok(buf))
		return -1;
	*down = prisma_rate_number(a);
	*up = prisma_rate_number(b);
	if (*down < 0 || *up < 0 || *down > PRISMA_RATE_MAX_KBIT || *up > PRISMA_RATE_MAX_KBIT)
		return -1;
	strcpy(name, buf);
	return 0;
}
