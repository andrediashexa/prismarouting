/*
 * bpa_xcvr.h - the arithmetic of a transceiver read (#590): which bytes of a
 * module's EEPROM to ask DPDK for, and how a GNU build-id is found in an ELF
 * note segment.
 *
 * WHY THIS IS A HEADER. bpa_transceiver.c is the half that needs VPP and a
 * running DPDK - the interface pool, dl_iterate_phdr over the loaded
 * dataplane, the CLI's buffer - and is excused from unit tests for that. What
 * it decides that can go wrong quietly is here, where test/test_bpa_xcvr.c
 * calls it with nothing but bytes: a range list that asks for the wrong page
 * is a screen of thresholds read from the identity page, and a build-id
 * comparison that matches the wrong note is a call into a function at an
 * offset taken from a different build.
 */

#ifndef __included_bpa_xcvr_h__
#define __included_bpa_xcvr_h__

/*
 * DPDK's names for the module's memory map, as rte_eth_dev_get_module_info()
 * returns them (rte_dev_info.h; Linux's ETH_MODULE_SFF_* are the same
 * numbers): which document the EEPROM follows, and so where each byte is.
 */
#define BPA_XCVR_SFF_8079 0x1 /* SFP, page A0h only: 256 bytes */
#define BPA_XCVR_SFF_8472 0x2 /* SFP with diagnostics: A0h + A2h, 512 */
#define BPA_XCVR_SFF_8636 0x3 /* QSFP28/QSFP+: lower, page 00h..03h, 640 */
#define BPA_XCVR_SFF_8436 0x4 /* QSFP+: as 8636 */

/* The longest map DPDK hands out: SFF-8636, lower page and four uppers. */
#define BPA_XCVR_MAX_LEN 640

typedef struct
{
  u32 offset;
  u32 length;
} bpa_xcvr_range_t;

/*
 * The ranges to read for a module of DPDK `type` whose map is `len` bytes:
 * the STATIC part (identity, thresholds, calibration) when `with_static`,
 * and always the LIVE part (the readings and the status bits). Returns how
 * many ranges it wrote into `out` (at most 3), 0 for a type it has no map
 * for.
 *
 * NOT THE WHOLE MAP, because of what a read costs. On the 82599 (ixgbe)
 * every byte is a bit-banged I2C transaction - the driver reads the EEPROM
 * one byte at a time, at roughly 0.2-0.4 ms a byte - so the 512 bytes VPP's
 * `show hardware-interfaces detail` reads are a main thread busy for
 * 100-200 ms. The identity and thresholds do not change while the module is
 * in the cage; the readings do. So the static ranges are read once and kept
 * (bpa_transceiver.c re-reads them every BPA_XCVR_STATIC_SECONDS, which is
 * how a module swapped in the cage is noticed), and a poll reads only the 24
 * live bytes of an SFP.
 *
 * SFF-8472, offsets into DPDK's 512-byte map (A2h is at 256):
 *   A0h 0-95     identifier, connector, codes, vendor, part, serial, date,
 *                diagnostic type (92), options (93), compliance (94)
 *   A2h 0-95     thresholds (0-39), calibration constants (56-91)
 *   A2h 96-119   the readings (96-105), status (110), flags (112-117)
 * SFF-8636/8436 (lower page 0-127, page 00h upper 128-255, page 03h upper at
 * 512-639):
 *   128-255      identity, diagnostic type (220)
 *   512-583      page 03h 128-199: module and channel thresholds; only when
 *                the map has page 03h (len 640) - a flat-memory module's map
 *                is 256
 *   0-57         identifier, status (flat memory), flags, temperature,
 *                voltage, and the four lanes' rx power, bias and tx power
 */
static_always_inline u32
bpa_xcvr_ranges (u32 type, u32 len, int with_static, bpa_xcvr_range_t *out)
{
  u32 n = 0;

  switch (type)
    {
    case BPA_XCVR_SFF_8079:
      /* No diagnostics page: the identity is everything, so it is read on
       * every poll that wants it, which is the static one. */
      if (len < 96)
	return 0;
      if (with_static)
	out[n++] = (bpa_xcvr_range_t){ 0, 96 };
      return n;
    case BPA_XCVR_SFF_8472:
      if (len < 256 + 120)
	return 0;
      if (with_static)
	{
	  out[n++] = (bpa_xcvr_range_t){ 0, 96 };
	  out[n++] = (bpa_xcvr_range_t){ 256, 96 };
	}
      out[n++] = (bpa_xcvr_range_t){ 256 + 96, 24 };
      return n;
    case BPA_XCVR_SFF_8636:
    case BPA_XCVR_SFF_8436:
      if (len < 256)
	return 0;
      if (with_static)
	{
	  out[n++] = (bpa_xcvr_range_t){ 128, 128 };
	  if (len >= 640)
	    out[n++] = (bpa_xcvr_range_t){ 512, 72 };
	}
      out[n++] = (bpa_xcvr_range_t){ 0, 58 };
      return n;
    }
  return 0;
}

/* The document's name for DPDK's `type`, as the screen and prismash say it;
 * "unknown" for one DPDK may add later. */
static_always_inline const char *
bpa_xcvr_type_name (u32 type)
{
  switch (type)
    {
    case BPA_XCVR_SFF_8079:
      return "sff-8079";
    case BPA_XCVR_SFF_8472:
      return "sff-8472";
    case BPA_XCVR_SFF_8636:
      return "sff-8636";
    case BPA_XCVR_SFF_8436:
      return "sff-8436";
    }
  return "unknown";
}

/* The DPDK type for a name bpa_xcvr_type_name() gives (the stand-in's
 * `type` word), or 0 for none. */
static_always_inline u32
bpa_xcvr_type_of (const char *name)
{
  u32 t;

  for (t = BPA_XCVR_SFF_8079; t <= BPA_XCVR_SFF_8436; t++)
    if (!strcmp (name, bpa_xcvr_type_name (t)))
      return t;
  return 0;
}

/*
 * The GNU build-id in the ELF note segment `notes` of `len` bytes: copies it
 * into `id` (room for `id_max` bytes), returns its length, or 0 when the
 * segment holds none.
 *
 * Walked note by note - namesz, descsz and type, each 4 bytes, then the name
 * and the descriptor each padded to 4 - because a PT_NOTE segment carries
 * several notes (the ABI tag and the build-id are both in a Debian-built
 * object) and the build-id is not always first. A note whose sizes run past
 * the segment ends the walk: that is a segment this does not understand, not
 * one to read beyond.
 */
static_always_inline u32
bpa_xcvr_build_id (const u8 *notes, u32 len, u8 *id, u32 id_max)
{
  u32 at = 0;

  while (at + 12 <= len)
    {
      u32 namesz, descsz, type, name_at, desc_at;

      memcpy (&namesz, notes + at, 4);
      memcpy (&descsz, notes + at + 4, 4);
      memcpy (&type, notes + at + 8, 4);
      name_at = at + 12;
      desc_at = name_at + ((namesz + 3) & ~3u);
      if (namesz > len || descsz > len || desc_at > len
	  || desc_at + ((descsz + 3) & ~3u) > len)
	return 0;
      /* NT_GNU_BUILD_ID, owner "GNU" with its terminator. */
      if (type == 3 && namesz == 4 && !memcmp (notes + name_at, "GNU", 4))
	{
	  if (descsz > id_max)
	    return 0;
	  memcpy (id, notes + desc_at, descsz);
	  return descsz;
	}
      at = desc_at + ((descsz + 3) & ~3u);
    }
  return 0;
}

/* Is the build-id `id` (`n` bytes) the one spelled `hex`, lower case, as
 * `readelf -n` prints it and tools/dpdk-symbols.sh writes it? */
static_always_inline int
bpa_xcvr_build_id_is (const u8 *id, u32 n, const char *hex)
{
  static const char digits[] = "0123456789abcdef";
  u32 i;

  if (strlen (hex) != 2 * n || n == 0)
    return 0;
  for (i = 0; i < n; i++)
    if (hex[2 * i] != digits[id[i] >> 4] || hex[2 * i + 1] != digits[id[i] & 15])
      return 0;
  return 1;
}

#endif /* __included_bpa_xcvr_h__ */
