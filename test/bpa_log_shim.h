/*
 * bpa_log_shim.h - what the TCP exporter asks of VPP beyond a vector.
 *
 * bpa_log_tcp.c assembles an IPFIX message by hand into a VPP vector and
 * pushes it at a socket. Three of the four things it needs are already here:
 * the integer types and the byte-order helpers (bpa_shim.h, bpa_tcp_shim.h)
 * and the vector (bpa_vec_shim.h). This file adds the rest.
 *
 * Each addition carries the VPP definition it reproduces, for the reason
 * bpa_vec_shim.h gives: the vector operations ARE the behaviour under test
 * here, because the exporter's whole ordering guarantee is expressed through
 * where it inserts and what it deletes.
 *
 *   vec_insert (V, N, M)   insert N ZEROED elements at index M, shifting the
 *                          rest up. The template and the spill replay both
 *                          use it at M = 0, which is what puts older bytes in
 *                          front of newer ones.
 *   vec_delete (V, N, M)   remove N elements at index M, shifting the rest
 *                          down. Used at M = 0 to drop a message the socket
 *                          has taken whole.
 *   vec_reset_length (V)   length to zero, memory kept.
 *
 * unix_time_now() is VPP's wall clock, seconds since the UNIX epoch as an
 * f64. It is what an IPFIX Export Time is read from, and the distinction that
 * matters - and that cost the compliance log a wrong time of day once - is
 * that it is NOT vlib_time_now(), which counts from when VPP started.
 */
#ifndef __included_bpa_log_shim_h__
#define __included_bpa_log_shim_h__

#include <time.h>

#include "bpa_shim.h"
#include "bpa_vec_shim.h"
#include "bpa_tcp_shim.h"

static inline u64
bpa_bswap64 (u64 v)
{
  return ((v & 0xffull) << 56) | ((v & 0xff00ull) << 40)
	 | ((v & 0xff0000ull) << 24) | ((v & 0xff000000ull) << 8)
	 | ((v >> 8) & 0xff000000ull) | ((v >> 24) & 0xff0000ull)
	 | ((v >> 40) & 0xff00ull) | ((v >> 56) & 0xffull);
}

#define clib_host_to_net_u64(x) bpa_bswap64 (x)
#define clib_net_to_host_u64(x) bpa_bswap64 (x)

#define ARRAY_LEN(a) (sizeof (a) / sizeof ((a)[0]))

static inline f64
unix_time_now (void)
{
  struct timespec ts;

  clock_gettime (CLOCK_REALTIME, &ts);
  return (f64) ts.tv_sec + 1e-9 * (f64) ts.tv_nsec;
}

#define vec_insert(V, N, M)                                                   \
  do                                                                          \
    {                                                                         \
      uword _n = (uword) (N), _m = (uword) (M);                               \
      uword _l = bpa_vec_len ((void *) (V));                                  \
      (V) = bpa_vec_resize ((void *) (V), _l + _n, sizeof ((V)[0]));          \
      memmove (&(V)[_m + _n], &(V)[_m], (_l - _m) * sizeof ((V)[0]));         \
      memset (&(V)[_m], 0, _n * sizeof ((V)[0]));                             \
    }                                                                         \
  while (0)

#define vec_delete(V, N, M)                                                   \
  do                                                                          \
    {                                                                         \
      uword _n = (uword) (N), _m = (uword) (M);                               \
      uword _l = bpa_vec_len ((void *) (V));                                  \
      memmove (&(V)[_m], &(V)[_m + _n], (_l - _m - _n) * sizeof ((V)[0]));    \
      bpa_vec_hdr ((void *) (V))->len = _l - _n;                              \
    }                                                                         \
  while (0)

#define vec_reset_length(V)                                                   \
  do                                                                          \
    {                                                                         \
      if (V)                                                                  \
	bpa_vec_hdr ((void *) (V))->len = 0;                                  \
    }                                                                         \
  while (0)

#endif /* __included_bpa_log_shim_h__ */
