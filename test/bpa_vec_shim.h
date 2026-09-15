/*
 * bpa_vec_shim.h - VPP vector and hash stand-ins, for the one unit test that
 * drives an allocator rather than arithmetic.
 *
 * test/bpa_shim.h covers the mapping core, which is pure arithmetic and needs
 * no container at all. bpa_dyn.c is the other kind: it holds state in VPP
 * vectors and a VPP hash, and what it does with them - which block comes off
 * the free stack, what a removal displaces - IS the behaviour under test. So
 * the containers have to behave the way VPP's do, not merely hold the same
 * elements.
 *
 * Each one below carries the VPP definition it reproduces, because that is the
 * thing this file can be wrong about. A vec_del1 that shifted instead of
 * swapping would still pass every "the right block was handed out" check and
 * would silently invalidate every free-list measurement, since the guess in
 * bpa_dyn_unfree() is derived from exactly that displacement.
 *
 *   vec_del1 (v, i):  v[i] = v[len-1]; len--        - swap with the last
 *   vec_pop (v):      len--; return v[len]          - a stack
 *   vec_add2 (v, p, n): len += n; p = &v[old_len]
 *   vec_validate (v, i): grow to i+1, ZEROED, keeping what was there
 *
 * Not a general vector. Every use in bpa_dyn.c is of one element type at a
 * time and none of them is aligned or heterogeneous, so a header-before-data
 * allocation with a length and a capacity is the whole of it.
 */
#ifndef __included_bpa_vec_shim_h__
#define __included_bpa_vec_shim_h__

#include <stdlib.h>
#include <string.h>

#include "bpa_shim.h"

typedef unsigned long uword;
typedef double f64;

typedef struct
{
  uword len;
  uword cap;
  uword elt_bytes;
} bpa_vec_hdr_t;

static inline bpa_vec_hdr_t *
bpa_vec_hdr (void *v)
{
  return (bpa_vec_hdr_t *) v - 1;
}

static inline uword
bpa_vec_len (void *v)
{
  return v ? bpa_vec_hdr (v)->len : 0;
}

/* Grows to hold at least `want` elements, zeroing everything past the old
 * length - vec_validate's contract, and the reason bpa_dyn_init() may set
 * owner[] after validating rather than before. */
static inline void *
bpa_vec_resize (void *v, uword want, uword elt_bytes)
{
  bpa_vec_hdr_t *h;
  uword old_len = bpa_vec_len (v);
  uword cap;

  if (v && bpa_vec_hdr (v)->cap >= want)
    {
      h = bpa_vec_hdr (v);
      if (want > h->len)
	memset ((char *) v + h->len * elt_bytes, 0,
		(want - h->len) * elt_bytes);
      h->len = want;
      return v;
    }

  cap = want < 8 ? 8 : want * 2;
  h = (bpa_vec_hdr_t *) malloc (sizeof (*h) + cap * elt_bytes);
  h->cap = cap;
  h->elt_bytes = elt_bytes;
  h->len = want;
  if (v)
    {
      memcpy (h + 1, v, old_len * elt_bytes);
      free (bpa_vec_hdr (v));
    }
  memset ((char *) (h + 1) + old_len * elt_bytes, 0,
	  (want - old_len) * elt_bytes);
  return h + 1;
}

#define vec_len(V) ((u32) bpa_vec_len ((void *) (V)))

#define vec_validate(V, I)                                                    \
  do                                                                          \
    {                                                                         \
      uword _i = (uword) (I) + 1;                                             \
      if (_i > bpa_vec_len ((void *) (V)))                                    \
	(V) = bpa_vec_resize ((void *) (V), _i, sizeof ((V)[0]));              \
    }                                                                         \
  while (0)

/* Only ever shrinks or restates the length in bpa_dyn.c, and VPP's asserts
 * that the capacity is already there. Reproduced with the same restriction. */
#define vec_set_len(V, L) (bpa_vec_hdr ((void *) (V))->len = (uword) (L))

#define vec_add1(V, E)                                                        \
  do                                                                          \
    {                                                                         \
      uword _l = bpa_vec_len ((void *) (V));                                  \
      (V) = bpa_vec_resize ((void *) (V), _l + 1, sizeof ((V)[0]));           \
      (V)[_l] = (E);                                                          \
    }                                                                         \
  while (0)

#define vec_add2(V, P, N)                                                     \
  do                                                                          \
    {                                                                         \
      uword _l = bpa_vec_len ((void *) (V));                                  \
      (V) = bpa_vec_resize ((void *) (V), _l + (uword) (N), sizeof ((V)[0])); \
      (P) = (V) + _l;                                                         \
    }                                                                         \
  while (0)

#define vec_pop(V)                                                            \
  (bpa_vec_hdr ((void *) (V))->len--, (V)[bpa_vec_hdr ((void *) (V))->len])

#define vec_del1(V, I)                                                        \
  do                                                                          \
    {                                                                         \
      uword _l = bpa_vec_len ((void *) (V)) - 1;                              \
      uword _i = (uword) (I);                                                 \
      if (_i < _l)                                                            \
	(V)[_i] = (V)[_l];                                                    \
      bpa_vec_hdr ((void *) (V))->len = _l;                                   \
    }                                                                         \
  while (0)

#define vec_free(V)                                                           \
  do                                                                          \
    {                                                                         \
      if (V)                                                                  \
	free (bpa_vec_hdr ((void *) (V)));                                    \
      (V) = 0;                                                                \
    }                                                                         \
  while (0)

#define vec_foreach(E, V)                                                     \
  for ((E) = (V); (E) < (V) + bpa_vec_len ((void *) (V)); (E)++)

/*
 * The hash. bpa_dyn.c asks four things of it - get, set, unset, free - keyed
 * by a subscriber index, and reads the result as a `uword *` that is null when
 * the key is absent. A sorted array with a binary search answers all four with
 * the right pointer discipline; the only VPP behaviour that matters here is
 * that hash_get returns a pointer INTO the table, which is why the value is
 * read before any further set.
 *
 * Deliberately not a hash. The test drives tens of thousands of keys, not
 * millions, and an ordered array is the version whose contents a failing check
 * can print in the order a reader expects.
 */
typedef struct
{
  uword key;
  uword val;
} bpa_hash_ent_t;

typedef struct
{
  bpa_hash_ent_t *ents;
  uword len;
  uword cap;
} bpa_hash_t;

static inline uword *
bpa_hash_create (void)
{
  bpa_hash_t *h = (bpa_hash_t *) calloc (1, sizeof (*h));
  return (uword *) h;
}

/* Index of `key`, or where it would be inserted, in *pos. Returns 1 on hit. */
static inline int
bpa_hash_find (bpa_hash_t *h, uword key, uword *pos)
{
  uword lo = 0, hi = h->len;

  while (lo < hi)
    {
      uword mid = (lo + hi) / 2;
      if (h->ents[mid].key < key)
	lo = mid + 1;
      else
	hi = mid;
    }
  *pos = lo;
  return lo < h->len && h->ents[lo].key == key;
}

static inline uword *
bpa_hash_get (uword *hp, uword key)
{
  bpa_hash_t *h = (bpa_hash_t *) hp;
  uword pos;

  if (!h || !bpa_hash_find (h, key, &pos))
    return 0;
  return &h->ents[pos].val;
}

static inline void
bpa_hash_set (uword *hp, uword key, uword val)
{
  bpa_hash_t *h = (bpa_hash_t *) hp;
  uword pos;

  if (bpa_hash_find (h, key, &pos))
    {
      h->ents[pos].val = val;
      return;
    }
  if (h->len == h->cap)
    {
      h->cap = h->cap ? h->cap * 2 : 16;
      h->ents =
	(bpa_hash_ent_t *) realloc (h->ents, h->cap * sizeof (h->ents[0]));
    }
  memmove (&h->ents[pos + 1], &h->ents[pos],
	   (h->len - pos) * sizeof (h->ents[0]));
  h->ents[pos].key = key;
  h->ents[pos].val = val;
  h->len++;
}

static inline void
bpa_hash_unset (uword *hp, uword key)
{
  bpa_hash_t *h = (bpa_hash_t *) hp;
  uword pos;

  if (!bpa_hash_find (h, key, &pos))
    return;
  memmove (&h->ents[pos], &h->ents[pos + 1],
	   (h->len - pos - 1) * sizeof (h->ents[0]));
  h->len--;
}

static inline void
bpa_hash_free_ (uword *hp)
{
  bpa_hash_t *h = (bpa_hash_t *) hp;

  if (!h)
    return;
  free (h->ents);
  free (h);
}

#define hash_create(E, V) bpa_hash_create ()
#define hash_get(H, K)	  bpa_hash_get ((H), (uword) (K))
#define hash_set(H, K, V) bpa_hash_set ((H), (uword) (K), (uword) (V))
#define hash_unset(H, K)  bpa_hash_unset ((H), (uword) (K))
#define hash_free(H)                                                          \
  do                                                                          \
    {                                                                         \
      bpa_hash_free_ (H);                                                     \
      (H) = 0;                                                                \
    }                                                                         \
  while (0)

#define clib_memset memset

#endif /* __included_bpa_vec_shim_h__ */
