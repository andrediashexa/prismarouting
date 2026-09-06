/* bpa_dyn.c - dynamic (PBA) block allocator. */

#include "bpa_dyn.h"

/* block_idx <-> (pub_idx, blk_idx) reuses the pool policy split, so a
 * dynamic pool still spreads consecutive allocations across public
 * addresses when the policy is interleaved. Ver bpa_dyn_block_addr(). */

clib_error_t *
bpa_dyn_init (bpa_dyn_t *d, const bpa_pool_t *p, u32 worker, u32 n_workers)
{
  u32 i, n;

  clib_memset (d, 0, sizeof (*d));

  /* blocks in [0, capacity) whose index is congruent to worker */
  n = p->capacity / n_workers;
  if (worker < p->capacity % n_workers)
    n++;

  if (n == 0)
    return clib_error_return (
      0, "pool '%v': capacity %u does not reach worker %u; reduce the worker "
	 "count or enlarge the pool",
      p->name, p->capacity, worker);

  d->n_local = n;
  vec_validate (d->owner, n - 1);
  vec_validate (d->idle_since, n - 1);
  for (i = 0; i < n; i++)
    {
      d->owner[i] = BPA_NO_BLOCK;
      d->idle_since[i] = 0;
    }

  /* pushed high to low so popping hands out increasing block indices */
  vec_validate (d->free_list, n - 1);
  for (i = 0; i < n; i++)
    d->free_list[i] = n - 1 - i;
  vec_set_len (d->free_list, n);

  d->by_sub = hash_create (0, sizeof (uword));
  d->active = 1;
  return 0;
}

void
bpa_dyn_free (bpa_dyn_t *d)
{
  vec_free (d->owner);
  vec_free (d->idle_since);
  vec_free (d->free_list);
  vec_free (d->extras);
  hash_free (d->by_sub);
  clib_memset (d, 0, sizeof (*d));
}

int
bpa_dyn_acquire (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx, u32 worker,
		 u32 n_workers, bpa_block_t *b, int *is_new)
{
  u32 local, block_idx;
  uword *q;

  *is_new = 0;
  q = hash_get (d->by_sub, sub_idx);
  if (PREDICT_TRUE (q != 0))
    {
      local = (u32) (q[0] - 1);
      d->idle_since[local] = 0; /* still in use */
      goto found;
    }

  if (PREDICT_FALSE (vec_len (d->free_list) == 0))
    {
      d->n_exhausted++;
      return -1;
    }

  local = vec_pop (d->free_list);
  d->owner[local] = sub_idx;
  d->idle_since[local] = 0;
  hash_set (d->by_sub, sub_idx, (uword) local + 1);
  d->n_alloc++;
  d->n_allocations++;
  *is_new = 1;

found:
  block_idx = local * n_workers + worker;
  b->sub_idx = sub_idx;
  bpa_dyn_block_addr (p, block_idx, b);
  return 0;
}

int
bpa_dyn_acquire_extra (bpa_dyn_t *d, const bpa_pool_t *p, u32 sub_idx,
		       u32 worker, u32 n_workers, bpa_block_t *b, int *is_new)
{
  bpa_dyn_extra_t *e;
  u32 local, block_idx;

  *is_new = 0;
  if (PREDICT_FALSE (vec_len (d->free_list) == 0))
    {
      d->n_exhausted++;
      return -1;
    }

  local = vec_pop (d->free_list);
  d->owner[local] = sub_idx;
  d->idle_since[local] = 0;
  /* by_sub is deliberately NOT touched: it names the subscriber's first
   * block, and bpa_dyn_acquire must keep returning that one. */
  vec_add2 (d->extras, e, 1);
  e->sub_idx = sub_idx;
  e->local = local;
  d->n_alloc++;
  d->n_allocations++;
  d->n_extensions++;
  *is_new = 1;

  block_idx = local * n_workers + worker;
  b->sub_idx = sub_idx;
  bpa_dyn_block_addr (p, block_idx, b);
  return 0;
}

void
bpa_dyn_idle (bpa_dyn_t *d, u32 sub_idx, f64 now)
{
  uword *q = hash_get (d->by_sub, sub_idx);
  bpa_dyn_extra_t *e;

  if (q)
    d->idle_since[q[0] - 1] = now;
  /* Extensions go idle with the subscriber that holds them. Missing this
   * leaks a block per extension: owned forever, never reaped, and visible
   * only as a pool that slowly stops having free blocks. */
  vec_foreach (e, d->extras)
    if (e->sub_idx == sub_idx)
      d->idle_since[e->local] = now;
}

void
bpa_dyn_active (bpa_dyn_t *d, u32 sub_idx)
{
  uword *q = hash_get (d->by_sub, sub_idx);
  bpa_dyn_extra_t *e;

  if (q)
    d->idle_since[q[0] - 1] = 0;
  vec_foreach (e, d->extras)
    if (e->sub_idx == sub_idx)
      d->idle_since[e->local] = 0;
}

u32
bpa_dyn_reap (bpa_dyn_t *d, f64 now, u32 holddown, u32 worker, u32 n_workers,
	      bpa_dyn_release_t *out, u32 max_out)
{
  u32 i, freed = 0;

  if (!d->active)
    return 0;

  for (i = 0; i < d->n_local; i++)
    {
      if (freed >= max_out)
	break;
      if (d->owner[i] == BPA_NO_BLOCK)
	continue;
      if (d->idle_since[i] == 0)
	continue;
      if (now - d->idle_since[i] <= (f64) holddown)
	continue;

      out[freed].sub_idx = d->owner[i];
      out[freed].block_idx = i * n_workers + worker;

      /* Only the primary lives in by_sub; an extension has an entry in
       * extras instead, and removing the wrong one would strand the other. */
      if (hash_get (d->by_sub, d->owner[i])
	  && hash_get (d->by_sub, d->owner[i])[0] == (uword) i + 1)
	hash_unset (d->by_sub, d->owner[i]);
      else
	{
	  u32 k;
	  for (k = 0; k < vec_len (d->extras); k++)
	    if (d->extras[k].local == i)
	      {
		vec_del1 (d->extras, k);
		break;
	      }
	}
      d->owner[i] = BPA_NO_BLOCK;
      d->idle_since[i] = 0;
      vec_add1 (d->free_list, i);
      d->n_alloc--;
      d->n_releases++;
      freed++;
    }

  return freed;
}
