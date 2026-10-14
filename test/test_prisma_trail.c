/*
 * build/prisma_trail.c (#597): the PPPoE session trail's writing half,
 * driven alone.
 *
 * Each behaviour, shown red by a mutation:
 *   * SHA-256 is FIPS 180-2's (its three vectors; mutation: a round
 *     constant changed);
 *   * the chain is the configuration trail's: sha256(previous digest || the
 *     line up to its seq), zero before the first (mutation: the previous
 *     digest left out - the second entry's digest no longer matches the
 *     independent computation here);
 *   * a sealed line that is not committed advances nothing (mutation: seal
 *     advancing the sequence);
 *   * a name with a space cannot shift the fields after it (mutation: the
 *     space written as it is);
 *   * the file read back gives the head, the month, and the sessions left
 *     open - a stop closes, a carry reopens with its own since;
 *   * a torn last line gets a newline and the next entry stands alone;
 *   * rotation moves the file aside under its own month, never over an
 *     existing one, and never for a month that is not later.
 */
#include "../build/prisma_trail.c"

static int fails;
#define CHECK(c, msg) do { if (c) printf("  [ ok ] %s\n", msg); \
	else { printf("  FAIL: %s\n", msg); fails++; } } while (0)

static void hex(const unsigned char *d, char out[65])
{
	int i;
	for (i = 0; i < 32; i++)
		sprintf(out + 2 * i, "%02x", d[i]);
}

static int count_lines(const char *path)
{
	FILE *f = fopen(path, "r");
	int n = 0, c;
	if (!f)
		return -1;
	while ((c = fgetc(f)) != EOF)
		n += c == '\n';
	fclose(f);
	return n;
}

/* `body` sealed and written, the chain advanced: the module's write path */
static int put(struct prisma_trail *t, const char *path, const char *body)
{
	char line[PRISMA_TRAIL_LINE];
	unsigned char d[32];
	int len = prisma_trail_seal(t, body, line, sizeof(line), d);
	if (len < 0 || prisma_trail_append(path, line, (size_t)len))
		return -1;
	prisma_trail_commit(t, d, line);
	return 0;
}

int main(void)
{
	unsigned char d[32], d2[32], zero[32] = { 0 };
	char h[65], line[PRISMA_TRAIL_LINE], buf[256], dir[] = "/tmp/prisma-trail-XXXXXX";
	char path[300], aside[320];
	struct prisma_trail t, r;
	struct prisma_trail_open *open, *o;
	struct timespec ts;
	int len, n;

	printf("=== SHA-256, FIPS 180-2 ===\n");
	prisma_sha256("", 0, "abc", 3, d);
	hex(d, h);
	CHECK(!strcmp(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), "\"abc\"");
	prisma_sha256("ab", 2, "c", 1, d);
	hex(d, h);
	CHECK(!strcmp(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
	      "\"ab\" then \"c\" is the digest of \"abc\": the two parts are one message");
	prisma_sha256("", 0, "", 0, d);
	hex(d, h);
	CHECK(!strcmp(h, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"), "the empty message");
	prisma_sha256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, "", 0, d);
	hex(d, h);
	CHECK(!strcmp(h, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"),
	      "448 bits: the padding spills into a second block");

	printf("\n=== fields ===\n");
	prisma_trail_escape("joao silva%x", buf, sizeof(buf));
	CHECK(!strcmp(buf, "joao%20silva%25x"), "a space and a '%' are escaped, nothing else");
	prisma_trail_escape("", buf, sizeof(buf));
	CHECK(!strcmp(buf, "-"), "an empty value is '-', never an empty word");
	prisma_trail_escape("caf\xc3\xa9\n", buf, sizeof(buf));
	CHECK(!strcmp(buf, "caf%C3%A9%0A"), "bytes that are not printable ASCII are escaped");
	{
		char longname[400];
		memset(longname, 'a', sizeof(longname) - 1);
		longname[sizeof(longname) - 1] = 0;
		CHECK(prisma_trail_escape(longname, buf, sizeof(buf)) == 1 &&
		      strlen(buf) <= PRISMA_TRAIL_FIELD && !strcmp(buf + strlen(buf) - 2, "%+"),
		      "a name past the field's length is cut and says so with %+");
	}
	ts.tv_sec = 1791983531; ts.tv_nsec = 123456789;
	prisma_trail_stamp(&ts, buf);
	CHECK(!strcmp(buf, "2026-10-14T13:12:11.123Z"), "the time is UTC, milliseconds, Z");
	CHECK(!strcmp(prisma_trail_cause(1), "User-Request") && !strcmp(prisma_trail_cause(9), "Lost-Carrier")
	      && !strcmp(prisma_trail_cause(3), "Admin-Reset") && !strcmp(prisma_trail_cause(5), "NAS-Error")
	      && !strcmp(prisma_trail_cause(8), "User-Error") && !strcmp(prisma_trail_cause(99), "Unknown"),
	      "terminate causes as accel-ppp's Acct-Terminate-Cause spells them");

	printf("\n=== the chain ===\n");
	memset(&t, 0, sizeof(t));
	len = prisma_trail_seal(&t, "2026-10-14T13:12:11.123Z restart closed 0", line, sizeof(line), d);
	CHECK(len > 0 && t.seq == 0, "a sealed line advances nothing until it is committed");
	CHECK(strstr(line, " seq 1 sha256 ") && line[len - 1] == '\n', "the first entry is seq 1, one line");
	{
		const char *body = "2026-10-14T13:12:11.123Z restart closed 0 seq 1";
		prisma_sha256(zero, 32, body, strlen(body), d2);
		CHECK(!memcmp(d, d2, 32), "its digest is sha256(32 zero bytes || the line up to its seq)");
	}
	prisma_trail_commit(&t, d, line);
	CHECK(t.seq == 1 && !memcmp(t.head, d, 32) && !strcmp(t.month, "2026-10"),
	      "committed: seq 1, the head, the file's month");
	len = prisma_trail_seal(&t, "2026-10-14T13:12:12.000Z start session ab user u mac m interface i address a",
				line, sizeof(line), d);
	{
		const char *body = "2026-10-14T13:12:12.000Z start session ab user u mac m interface i address a seq 2";
		prisma_sha256(t.head, 32, body, strlen(body), d2);
		CHECK(!memcmp(d, d2, 32), "the second's digest covers the first's: a chain");
	}
	{
		char big[PRISMA_TRAIL_LINE];
		memset(big, 'x', sizeof(big) - 1);
		big[sizeof(big) - 1] = 0;
		CHECK(prisma_trail_seal(&t, big, line, sizeof(line), d) == -1, "a line that does not fit is refused");
	}

	printf("\n=== the file ===\n");
	if (!mkdtemp(dir)) {
		printf("  FAIL: no temporary directory\n");
		return 1;
	}
	snprintf(path, sizeof(path), "%s/pppoe-sessions.log", dir);
	memset(&t, 0, sizeof(t));
	CHECK(prisma_trail_recover(&r, path, &open) == 0 && r.seq == 0 && !open,
	      "no file yet: an empty chain and nothing open");
	put(&t, path, "2026-10-14T13:00:00.000Z restart closed 0");
	put(&t, path, "2026-10-14T13:00:01.000Z start session 0000000000000001 user sub1 mac 02:aa:00:00:00:01 interface host-acc0 address 100.64.0.10");
	put(&t, path, "2026-10-14T13:00:02.000Z start session 0000000000000002 user sub2 mac 02:aa:00:00:00:02 interface host-acc0 address 100.64.0.11");
	put(&t, path, "2026-10-14T13:00:03.000Z stop session 0000000000000001 user sub1 mac 02:aa:00:00:00:01 interface host-acc0 address 100.64.0.10 since 2026-10-14T13:00:01.000Z cause User-Request");
	put(&t, path, "2026-10-14T13:00:04.000Z carry session 0000000000000003 user sub3 mac 02:aa:00:00:00:03 interface host-acc0.100 address 100.64.0.12 since 2026-10-01T09:00:00.000Z");
	CHECK(count_lines(path) == 5, "five entries, five lines");
	CHECK(prisma_trail_recover(&r, path, &open) == 0 && r.seq == 5 && !memcmp(r.head, t.head, 32)
	      && !strcmp(r.month, "2026-10"),
	      "read back: the head and the month are the writer's");
	n = 0;
	for (o = open; o; o = o->next)
		n++;
	CHECK(n == 2, "two sessions left open: sub2's start and sub3's carry, sub1 stopped");
	for (o = open; o; o = o->next) {
		if (!strcmp(o->sid, "0000000000000002"))
			CHECK(!strcmp(o->since, "2026-10-14T13:00:02.000Z") &&
			      !strcmp(o->fields, "session 0000000000000002 user sub2 mac 02:aa:00:00:00:02 interface host-acc0 address 100.64.0.11"),
			      "  a start's since is its own time, its fields whole");
		if (!strcmp(o->sid, "0000000000000003"))
			CHECK(!strcmp(o->since, "2026-10-01T09:00:00.000Z") && strstr(o->fields, "address 100.64.0.12")
			      && !strstr(o->fields, "since"),
			      "  a carry's since is the one it carries, not its own time");
	}
	prisma_trail_free(open);

	/* A power cut mid-write: a fragment with no newline at the end. */
	{
		FILE *f = fopen(path, "a");
		fputs("2026-10-14T13:00:05.000Z start session 00000000000", f);
		fclose(f);
	}
	CHECK(prisma_trail_recover(&r, path, &open) == 0 && r.seq == 5,
	      "a torn last line is not an entry: the head stays at seq 5");
	prisma_trail_free(open);
	put(&r, path, "2026-10-14T13:00:06.000Z restart closed 1");
	{
		FILE *f = fopen(path, "r");
		char l[2048], last[2048] = "";
		while (fgets(l, sizeof(l), f))
			strcpy(last, l);
		fclose(f);
		CHECK(!strncmp(last, "2026-10-14T13:00:06.000Z restart closed 1 seq 6 sha256 ", 55),
		      "and the next entry is a line of its own, seq 6, after the fragment");
	}

	printf("\n=== rotation ===\n");
	CHECK(prisma_trail_rotate(&r, path, "2026-10") == 0, "the same month: the file stays");
	CHECK(prisma_trail_rotate(&r, path, "2026-09") == 0, "a clock gone back a month: the file stays");
	snprintf(aside, sizeof(aside), "%s.2026-10", path);
	CHECK(prisma_trail_rotate(&r, path, "2026-11") == 1 && access(aside, F_OK) == 0
	      && access(path, F_OK) != 0 && r.seq == 6 && !r.month[0],
	      "a later month: moved aside as .2026-10, the chain kept, the month open");
	put(&r, path, "2026-11-01T00:00:00.000Z restart closed 0");
	CHECK(!strcmp(r.month, "2026-11") && strstr(path, "pppoe") && count_lines(path) == 1,
	      "  the new file takes the next entry, seq 7, as its first");
	r.month[0] = 0;
	strcpy(r.month, "2026-10");
	CHECK(prisma_trail_rotate(&r, path, "2026-12") == 0 && access(path, F_OK) == 0,
	      "  a file of that name already there: never overwritten, nothing moves");
	unlink(aside);
	unlink(path);

	printf("\n=== a delegated prefix (#609) ===\n");
	memset(&t, 0, sizeof(t));
	snprintf(path, sizeof(path), "%s/d.log", dir);
	put(&t, path, "2026-10-14T14:00:00.000Z start session 0000000000000009 user sub9 mac 02:aa:00:00:00:09 interface host-acc0 address 100.64.0.19 prefix 2001:db8:100::/64");
	put(&t, path, "2026-10-14T14:20:00.000Z delegate session 0000000000000009 user sub9 mac 02:aa:00:00:00:09 interface host-acc0 address 100.64.0.19 prefix 2001:db8:100::/64 delegated 2001:db8:200::/56 delegated-since 2026-10-14T14:20:00.000Z since 2026-10-14T14:00:00.000Z");
	CHECK(prisma_trail_recover(&r, path, &open) == 0 && r.seq == 2 && open && !open->next,
	      "a start and its delegate are one session left open");
	CHECK(open && !strcmp(open->since, "2026-10-14T14:00:00.000Z"),
	      "  its since is still the start's, not the delegation's");
	CHECK(open && strstr(open->fields, "delegated 2001:db8:200::/56 delegated-since 2026-10-14T14:20:00.000Z")
	      && strstr(open->fields, "prefix 2001:db8:100::/64") && !strstr(open->fields, " since "),
	      "  and what a Control-Plane-Lost stop repeats holds the prefix delegated, and when");
	prisma_trail_free(open);
	put(&t, path, "2026-10-14T15:00:00.000Z stop session 0000000000000009 user sub9 mac 02:aa:00:00:00:09 interface host-acc0 address 100.64.0.19 prefix 2001:db8:100::/64 delegated 2001:db8:200::/56 delegated-since 2026-10-14T14:20:00.000Z since 2026-10-14T14:00:00.000Z cause User-Request");
	CHECK(prisma_trail_recover(&r, path, &open) == 0 && !open,
	      "  and its stop closes it");
	prisma_trail_free(open);
	unlink(path);
	rmdir(dir);

	if (fails) { printf("\n%d FAILURES\n", fails); return 1; }
	printf("\nALL TESTS PASSED\n");
	return 0;
}
