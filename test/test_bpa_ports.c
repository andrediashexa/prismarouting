/* test_bpa_ports.c - unit test for the per-subscriber port allocator. */

#include <stdio.h>
#include <string.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_ports.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  if (failures < 10)                                                  \
	    {                                                                 \
	      printf ("  FALHA: ");                                           \
	      printf (__VA_ARGS__);                                           \
	      printf ("\n");                                                  \
	    }                                                                 \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* Drains the block and verifies every offset comes back exactly once. */
static void
test_exhaust (u16 n_ports)
{
  u64 storage[BPA_PORTMAP_MAX_WORDS];
  bpa_portmap_t pm;
  u8 seen[65536];
  u32 i, got = 0, dup = 0, oob = 0;

  memset (seen, 0, sizeof (seen));
  bpa_portmap_init (&pm, n_ports, storage);
  CHECK (pm.n_free == n_ports, "bloco %u: n_free inicial %u", n_ports,
	 pm.n_free);

  for (;;)
    {
      int off = bpa_portmap_alloc (&pm, -1);
      if (off < 0)
	break;
      if (off >= (int) n_ports)
	oob++;
      else if (seen[off])
	dup++;
      else
	seen[off] = 1;
      got++;
      if (got > (u32) n_ports + 8)
	break; /* runaway guard */
    }

  CHECK (got == n_ports, "bloco %u: entregou %u portas, esperado %u", n_ports,
	 got, n_ports);
  CHECK (dup == 0, "bloco %u: %u portas duplicadas", n_ports, dup);
  CHECK (oob == 0, "bloco %u: %u portas fora do bloco", n_ports, oob);
  CHECK (pm.n_free == 0, "bloco %u: n_free final %u", n_ports, pm.n_free);
  CHECK (bpa_portmap_alloc (&pm, -1) == -1, "bloco %u: alocou esgotado",
	 n_ports);

  for (i = 0; i < n_ports; i++)
    CHECK (seen[i] == 1, "bloco %u: offset %u nunca entregue", n_ports, i);

  printf ("  bloco %-5u %u portas distintas, esgotamento correto\n", n_ports,
	  got);
}

int
main (void)
{
  u64 storage[BPA_PORTMAP_MAX_WORDS];
  bpa_portmap_t pm;

  printf ("=== esgotamento sem colisao, varios tamanhos de bloco ===\n");
  test_exhaust (32); /* exercita o mascaramento da cauda */
  test_exhaust (64);
  test_exhaust (128);
  test_exhaust (512);
  test_exhaust (4096);

  printf ("\n=== preservacao de porta ===\n");
  {
    bpa_portmap_init (&pm, 512, storage);
    CHECK (bpa_portmap_alloc (&pm, 300) == 300, "nao preservou a porta 300");
    CHECK (bpa_portmap_is_used (&pm, 300), "300 nao ficou marcada");
    /* preferida ja ocupada: deve cair para outra, nunca repetir */
    CHECK (bpa_portmap_alloc (&pm, 300) != 300, "entregou 300 duas vezes");
    /* preferida fora do bloco: ignora e aloca normal */
    {
      int off = bpa_portmap_alloc (&pm, 9999);
      CHECK (off >= 0 && off < 512, "preferida invalida quebrou a alocacao");
    }
    printf ("  porta preferida respeitada, colisao e fora-de-faixa tratadas\n");
  }

  printf ("\n=== liberacao e reuso ===\n");
  {
    u16 free_me;
    int off;

    bpa_portmap_init (&pm, 128, storage);
    while (bpa_portmap_alloc (&pm, -1) >= 0)
      ;
    CHECK (pm.n_free == 0, "bloco nao esgotou");

    free_me = 77;
    bpa_portmap_free (&pm, free_me);
    CHECK (pm.n_free == 1, "n_free apos liberar = %u", pm.n_free);
    CHECK (!bpa_portmap_is_used (&pm, free_me), "77 continua marcada");

    off = bpa_portmap_alloc (&pm, -1);
    CHECK (off == free_me, "reuso devolveu %d, esperado %u", off, free_me);
    CHECK (pm.n_free == 0, "n_free apos realocar = %u", pm.n_free);
    printf ("  porta liberada volta a ser alocavel\n");
  }

  printf ("\n=== operacoes invalidas nao corrompem o mapa ===\n");
  {
    bpa_portmap_init (&pm, 128, storage);
    CHECK (bpa_portmap_alloc (&pm, -1) == 0, "primeira alocacao != 0");

    bpa_portmap_free (&pm, 0);
    CHECK (pm.n_free == 128, "n_free apos liberar = %u", pm.n_free);
    bpa_portmap_free (&pm, 0); /* double free */
    CHECK (pm.n_free == 128, "double free alterou n_free para %u", pm.n_free);

    bpa_portmap_free (&pm, 5000); /* fora do bloco */
    CHECK (pm.n_free == 128, "free fora de faixa alterou n_free para %u",
	   pm.n_free);
    printf ("  double free e free fora de faixa ignorados\n");
  }

  printf ("\n=== cauda nunca e entregue (bloco de 32 em palavra de 64) ===\n");
  {
    u32 i, bad = 0;
    bpa_portmap_init (&pm, 32, storage);
    for (i = 32; i < 64; i++)
      if (!bpa_portmap_is_used (&pm, (u16) i))
	bad++;
    CHECK (bad == 0, "%u bits de cauda ficaram livres", bad);
    printf ("  32 bits de cauda marcados como usados no init\n");
  }

  printf ("\n%s  (%u verificacoes, %d falhas)\n",
	  failures ? "FALHOU" : "TODOS OS TESTES PASSARAM", checks, failures);
  return failures ? 1 : 0;
}
