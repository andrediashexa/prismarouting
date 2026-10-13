/*
 * build/prisma_rate.c (#607): the Cisco-AVPair rate forms accel-ppp-ng's
 * shaper is given by build/accel-ppp-ng.patch, read strictly.
 *
 * Each behaviour, shown red by a mutation:
 *   * `input` is the subscriber's upload, `output` the download (mutation:
 *     the two swapped);
 *   * the indexed key `lcp:interface-config#1` is the same form (mutation:
 *     only the bare key read);
 *   * a rate-limit that is not the whole documented shape is malformed and
 *     never a partial rate (mutation: the action words not checked);
 *   * a policy name is a name, its direction by -in/-out (mutation: a name
 *     with a space taken);
 *   * a key that is not a rate is not a rate, so a CoA carrying it is
 *     refused as unsupported rather than as malformed.
 */
#include <stdio.h>
#include "../build/prisma_rate.c"

static int fails;
#define CHECK(c, msg) do { if (c) printf("  [ ok ] %s\n", msg); \
	else { printf("  FAIL: %s\n", msg); fails++; } } while (0)

int main(void)
{
	struct prisma_rate r;
	char name[64];
	long d, u;

	printf("=== lcp:interface-config rate-limit ===\n");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 20000000 2000000 4000000 conform-action transmit exceed-action drop", &r) == 1
	      && r.dir == PRISMA_RATE_DOWN && r.kbit == 20000 && r.burst == 2000000 && !r.name[0],
	      "output: the download, 20000000 bps as 20000 kbit/s, the normal burst");
	CHECK(prisma_rate_avpair("lcp:interface-config#1=rate-limit input 10000000 1000000 2000000 conform-action transmit exceed-action drop", &r) == 1
	      && r.dir == PRISMA_RATE_UP && r.kbit == 10000,
	      "input, indexed key: the upload - what the router receives from the subscriber");
	CHECK(prisma_rate_avpair("lcp:interface-config#12=rate-limit  input\t64000 8000 8000 conform-action transmit exceed-action drop", &r) == 1
	      && r.kbit == 64, "spaces and tabs between the words");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output access-group 101 20000000 2000000 4000000 conform-action transmit exceed-action drop", &r) == -1,
	      "access-group: one ACL's traffic, not the session's - malformed, never applied");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 20000000 2000000 4000000", &r) == -1,
	      "no actions: malformed");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 20000000 2000000 4000000 conform-action transmit exceed-action transmit", &r) == -1,
	      "an exceed-action that does not drop is not a limit: malformed");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit sideways 20000000 2000000 4000000 conform-action transmit exceed-action drop", &r) == -1,
	      "a direction that is neither: malformed");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 20M 2000000 4000000 conform-action transmit exceed-action drop", &r) == -1,
	      "a rate with a suffix: Cisco's is bits per second, digits only");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 7999 2000 4000 conform-action transmit exceed-action drop", &r) == -1
	      && prisma_rate_avpair("lcp:interface-config=rate-limit output 10000001000 2000 4000 conform-action transmit exceed-action drop", &r) == -1,
	      "under 8 kbit/s or over 10 Gbit/s: malformed");
	CHECK(prisma_rate_avpair("lcp:interface-config=rate-limit output 20000000 4000000 2000000 conform-action transmit exceed-action drop", &r) == -1,
	      "a max burst under the normal one: malformed");
	CHECK(prisma_rate_avpair("lcp:interface-config=ip unnumbered Loopback0", &r) == 0,
	      "an interface-config that is not a rate-limit: not a rate");
	CHECK(prisma_rate_avpair("lcp:interface-config#x=rate-limit output 20000000 2000000 4000000 conform-action transmit exceed-action drop", &r) == 0
	      && prisma_rate_avpair("lcp:interface-configX=rate-limit output 20000000 2000000 4000000 conform-action transmit exceed-action drop", &r) == 0,
	      "a key that only begins like it: not a rate");

	printf("\n=== sub-qos-policy ===\n");
	CHECK(prisma_rate_avpair("ip:sub-qos-policy-in=PLANO_100M", &r) == 1
	      && r.dir == PRISMA_RATE_UP && !strcmp(r.name, "PLANO_100M") && r.kbit == 0,
	      "ip:...-in: a name, for the upload");
	CHECK(prisma_rate_avpair("subscriber:sub-qos-policy-out=gold.v2", &r) == 1
	      && r.dir == PRISMA_RATE_DOWN && !strcmp(r.name, "gold.v2"),
	      "subscriber:...-out: a name, for the download");
	CHECK(prisma_rate_avpair("ip:sub-qos-policy-out=bad name", &r) == -1
	      && prisma_rate_avpair("ip:sub-qos-policy-out=", &r) == -1
	      && prisma_rate_avpair("ip:sub-qos-policy-out=abcdefghijklmnopqrstuvwxyz0123456", &r) == -1,
	      "a name with a space, empty, or past 32: malformed");

	printf("\n=== not rates ===\n");
	CHECK(prisma_rate_avpair("ip:addr-pool=pool1", &r) == 0
	      && prisma_rate_avpair("no equals sign", &r) == 0
	      && prisma_rate_avpair(NULL, &r) == 0,
	      "another key, no key, nothing");

	printf("\n=== rate-profile lines ===\n");
	CHECK(prisma_rate_profile_line("PLANO_100M,100000,50000", name, &d, &u) == 0
	      && !strcmp(name, "PLANO_100M") && d == 100000 && u == 50000,
	      "name, download, upload");
	CHECK(prisma_rate_profile_line("x,1", name, &d, &u) == -1
	      && prisma_rate_profile_line("a b,1,2", name, &d, &u) == -1
	      && prisma_rate_profile_line("x,1,10000001", name, &d, &u) == -1
	      && prisma_rate_profile_line(NULL, name, &d, &u) == -1,
	      "a line that is not one: refused");
	CHECK(prisma_rate_number("123") == 123 && prisma_rate_number("12a") == -1
	      && prisma_rate_number("") == -1 && prisma_rate_name_ok("a.b-c_1")
	      && !prisma_rate_name_ok("a/b"),
	      "prisma_rate_number, prisma_rate_name_ok");

	if (fails) { printf("\n%d FAILURES\n", fails); return 1; }
	printf("\nALL TESTS PASSED\n");
	return 0;
}
