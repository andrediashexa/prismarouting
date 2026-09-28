/*
 * bpa_local.h - the box's own IPv4 addresses, as a set a packet can be asked
 * against in a few clocks (#489).
 *
 * bpa_to_us() answers "is this packet for the box itself" from the FIB, which
 * is the authority, and #470 asked it of every packet before the session
 * table. Measured on the development machine with lab/throughput.sh, three
 * interleaved pairs at 64 bytes: 524 clocks/packet with that lookup and 461
 * without it - about 60 clocks, 12% of bpa-in2out, spent on a question whose
 * answer is "no" for nearly every packet a CGNAT carries.
 *
 * So the question is asked of this set first, and of the FIB only when the
 * set says the destination is one of the box's interface addresses. The set
 * is a filter, never the answer: a destination it holds still goes to the
 * FIB, which also says which VRF the address is in. What it can miss is a
 * receive route that is not an interface address, and this product installs
 * none.
 *
 * Open addressing over a power-of-two vector at most half full, 0 marking an
 * empty slot - 0.0.0.0 is never an interface address. Addresses are kept as
 * the packet carries them (network order), so a lookup is one multiply, one
 * load and one compare on the usual miss.
 */
#ifndef __included_bpa_local_h__
#define __included_bpa_local_h__

static_always_inline u32
bpa_local_slot (u32 addr, u32 mask)
{
  return ((addr * 2654435761u) >> 12) & mask;
}

/* Whether `addr` is in the set. An empty set (no vector) holds nothing. */
static_always_inline int
bpa_local_has (const u32 *slots, u32 addr)
{
  u32 mask, i;

  if (!slots || addr == 0)
    return 0;
  mask = vec_len (slots) - 1;
  for (i = bpa_local_slot (addr, mask);; i = (i + 1) & mask)
    {
      if (slots[i] == addr)
	return 1;
      if (slots[i] == 0)
	return 0;
    }
}

/* The set of `addrs` (a vector, duplicates and zeros allowed), sized to the
 * power of two at least twice their count and never under 16 slots, so a
 * probe always ends at an empty slot. */
static inline u32 *
bpa_local_build (const u32 *addrs)
{
  u32 *slots = 0, size = 16, mask, i, j;

  while (size < 2 * vec_len (addrs))
    size <<= 1;
  vec_validate (slots, size - 1);
  mask = size - 1;
  for (i = 0; i < vec_len (addrs); i++)
    {
      if (addrs[i] == 0)
	continue;
      for (j = bpa_local_slot (addrs[i], mask);
	   slots[j] != 0 && slots[j] != addrs[i]; j = (j + 1) & mask)
	;
      slots[j] = addrs[i];
    }
  return slots;
}

#endif /* __included_bpa_local_h__ */
