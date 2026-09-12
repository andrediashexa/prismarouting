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
typedef int32_t i32;

/* The two VPP spellings a parser reaches for. Plain memmove and memcpy: the
 * real ones differ in what they promise about overlap and alignment, not in
 * what they compute, and a unit test that used a different copy from the
 * datapath would be testing something else. */
#define clib_memmove	 memmove
#define clib_memcpy_fast memcpy

#define PREDICT_TRUE(x)	     __builtin_expect (!!(x), 1)
#define PREDICT_FALSE(x)     __builtin_expect (!!(x), 0)
#define static_always_inline static inline __attribute__ ((__always_inline__))

typedef char clib_error_t;

static char __attribute__ ((unused)) bpa_shim_errbuf[] = "bpa: pool configuration rejected";

/* VPP's %v formats a vec-string and is not a printf specifier, so we must
 * not pass the caller's format to vsnprintf. The unit test only checks
 * whether a pool was rejected, not the wording. */
#define clib_error_return(e, ...) (bpa_shim_errbuf)

#endif
