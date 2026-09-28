/*
 * test_bpa_sock.c - does the exporter see that its collector closed?
 * (bpa_sock.h, #493)
 *
 * On real TCP sockets over loopback, because what is under test is what the
 * kernel reports: a connection that is open and idle, one whose peer has
 * sent data (a collector does not, but it must not read as a close), one
 * whose peer closed (the FIN), one whose peer reset it, and a descriptor that
 * is not a socket at all.
 *
 * Mutation shown red: bpa_sock_peer_closed() returning 0 always, which is
 * the exporter before #493 - it asked nothing and sent into the close.
 */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include "bpa_shim.h"
#include "../src/plugins/bpa/bpa_sock.h"

static int failures = 0;
static unsigned checks = 0;

#define CHECK(cond, ...)                                                      \
  do                                                                          \
    {                                                                         \
      checks++;                                                               \
      if (!(cond))                                                            \
	{                                                                     \
	  printf ("  FAIL: ");                                               \
	  printf (__VA_ARGS__);                                               \
	  printf ("\n");                                                      \
	  failures++;                                                         \
	}                                                                     \
    }                                                                         \
  while (0)

/* A connected pair over loopback: *cli is the exporter's end, *srv the
 * collector's. */
static int
tcp_pair (int *cli, int *srv)
{
  struct sockaddr_in a = { .sin_family = AF_INET };
  socklen_t l = sizeof (a);
  int lst = socket (AF_INET, SOCK_STREAM, 0), one = 1;

  a.sin_addr.s_addr = htonl (INADDR_LOOPBACK);
  setsockopt (lst, SOL_SOCKET, SO_REUSEADDR, &one, sizeof (one));
  if (bind (lst, (struct sockaddr *) &a, sizeof (a)) || listen (lst, 1)
      || getsockname (lst, (struct sockaddr *) &a, &l))
    return -1;
  *cli = socket (AF_INET, SOCK_STREAM, 0);
  if (connect (*cli, (struct sockaddr *) &a, sizeof (a)))
    return -1;
  *srv = accept (lst, 0, 0);
  close (lst);
  return *srv < 0 ? -1 : 0;
}

int
main (void)
{
  int cli, srv;

  printf ("=== an open connection is open ===\n");
  if (tcp_pair (&cli, &srv))
    {
      printf ("  FAIL: no loopback TCP here\n");
      return 1;
    }
  CHECK (!bpa_sock_peer_closed (cli), "an open, idle connection read as closed");
  CHECK (write (srv, "x", 1) == 1, "the collector could not write");
  usleep (20000);
  CHECK (!bpa_sock_peer_closed (cli),
	 "a peer that sent a byte read as a peer that closed");
  printf ("  idle, or with bytes waiting from the peer: open\n");

  printf ("\n=== the collector exits: its FIN is seen before the next send ===\n");
  {
    char c;
    CHECK (read (cli, &c, 1) == 1, "the byte the peer sent was not there");
  }
  close (srv);
  usleep (20000);
  CHECK (bpa_sock_peer_closed (cli),
	 "a peer that closed read as open: the next send would go into the "
	 "RST, which is #493");
  close (cli);
  printf ("  a closed peer is seen\n");

  printf ("\n=== a reset connection ===\n");
  if (tcp_pair (&cli, &srv) == 0)
    {
      struct linger lg = { .l_onoff = 1, .l_linger = 0 };
      setsockopt (srv, SOL_SOCKET, SO_LINGER, &lg, sizeof (lg));
      close (srv); /* linger 0: an RST, not a FIN */
      usleep (20000);
      CHECK (bpa_sock_peer_closed (cli), "a reset connection read as open");
      close (cli);
    }
  printf ("  a reset is a close\n");

  printf ("\n=== what is not a socket is the caller's ===\n");
  CHECK (!bpa_sock_peer_closed (-1), "fd -1 read as closed");
  CHECK (!bpa_sock_peer_closed (987), "a descriptor that is not open read as "
	 "closed");
  printf ("  -1 and a closed descriptor: not this function's answer\n");

  printf ("\n%u checks, %d failures\n", checks, failures);
  printf (failures ? "%d FAILURES\n" : "ALL TESTS PASSED\n", failures);
  return failures ? 1 : 0;
}
