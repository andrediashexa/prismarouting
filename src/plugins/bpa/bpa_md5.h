/*
 * bpa_md5.h - MD5, for RADIUS and for nothing else. See bpa_md5.c for why
 * this product carries its own and why that is not a security choice.
 */
#ifndef __included_bpa_md5_h__
#define __included_bpa_md5_h__

#ifdef BPA_STANDALONE
#include "bpa_shim.h"
#endif

void bpa_md5 (const u8 *data, u32 n, u8 out[16]);

#endif /* __included_bpa_md5_h__ */
