/* bpa_shim.h - minimal VPP type/macro stand-ins for standalone unit tests. */
#ifndef __included_bpa_shim_h__
#define __included_bpa_shim_h__

#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int16_t i16;
typedef int32_t i32;
typedef double f64;
/* f32, because bpa_rate.h's bucket is one: the tokens are a float in the
 * subscriber's record, and a shim that made them double would measure an
 * arithmetic this box does not do. VPP's own vppinfra names it the same. */
typedef float f32;

/* The two VPP spellings a parser reaches for. Plain memmove and memcpy: the
 * real ones differ in what they promise about overlap and alignment, not in
 * what they compute, and a unit test that used a different copy from the
 * datapath would be testing something else. */
#define clib_memmove	 memmove
#define clib_memcpy_fast memcpy
#define clib_memset	 memset

#define PREDICT_TRUE(x)	     __builtin_expect (!!(x), 1)
#define PREDICT_FALSE(x)     __builtin_expect (!!(x), 0)
#define static_always_inline static inline __attribute__ ((__always_inline__))

typedef char clib_error_t;

static char __attribute__ ((unused)) bpa_shim_errbuf[] = "bpa: pool configuration rejected";

/* VPP's %v formats a vec-string and is not a printf specifier, so we must
 * not pass the caller's format to vsnprintf. The unit test only checks
 * whether a pool was rejected, not the wording. */
#define clib_error_return(e, ...) (bpa_shim_errbuf)

/* The verdict line every suite in `make test` ends with, in the one spelling
 * tools/run-tests.sh reads.
 *
 * Seven C suites had five spellings of this between them and two printed no
 * verdict at all, so a suite killed before its last check and a suite whose
 * last check failed were the same thing from outside. That is #218: two
 * `make test` failures under load whose assertion was never identified,
 * because the only evidence kept was a count of summary lines. A runner can
 * only tell "stopped early" from "failed" if finishing leaves a mark. */
static inline int
bpa_test_verdict (u32 checks, int failures)
{
  printf ("\n%u checks, %d failures\n", checks, failures);
  if (failures)
    printf ("%d FAILURES\n", failures);
  else
    printf ("ALL TESTS PASSED\n");
  return failures ? 1 : 0;
}

#endif
