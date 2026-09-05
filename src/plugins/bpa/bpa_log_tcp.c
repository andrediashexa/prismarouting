/*
 * bpa_log_tcp.c - exportacao IPFIX sobre TCP, com buffer local.
 *
 * A infraestrutura vnet/ipfix-export do VPP so fala UDP. Para telemetria
 * isso basta; para log de compliance nao: perder um natEvent 17 recria a
 * lacuna entre liberar e realocar um bloco, e e nessa lacuna que uma
 * consulta judicial devolve o assinante errado (ver docs/logging.md).
 *
 * Aqui a mensagem IPFIX e montada a mao - o formato e simples e nao vale
 * torcer a infraestrutura de UDP para caber. O socket e nao bloqueante: um
 * coletor lento nao pode travar a thread principal do VPP. Quando ele fica
 * inalcancavel, os bytes vao para um arquivo de transbordo e voltam na
 * reconexao, em ordem.
 */

#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include "bpa.h"
#include "bpa_log.h"

#define BPA_TCP_TEMPLATE_ID  256
#define BPA_TCP_DOMAIN	     1
#define BPA_TCP_RETRY_SECS   5.0
#define BPA_TCP_TEMPLATE_IVL 60.0
/* Acima disto o buffer em memoria vai para o arquivo de transbordo. */
#define BPA_TCP_SPILL_BYTES  (1 << 20)

bpa_log_tcp_t bpa_log_tcp;

/* Elementos da Tabela 21 da RFC 8158, na mesma ordem do registro. */
static const u16 bpa_tcp_ies[][2] = {
  { 323, 8 }, { 230, 1 }, { 8, 4 }, { 225, 4 }, { 361, 2 }, { 362, 2 },
  { 283, 4 },
};

static void
bpa_tcp_put (u8 **b, const void *data, u32 n)
{
  u8 *p;
  vec_add2 (*b, p, n);
  clib_memcpy_fast (p, data, n);
}

static void
bpa_tcp_put_u16 (u8 **b, u16 v)
{
  v = clib_host_to_net_u16 (v);
  bpa_tcp_put (b, &v, 2);
}

static void
bpa_tcp_put_u32 (u8 **b, u32 v)
{
  v = clib_host_to_net_u32 (v);
  bpa_tcp_put (b, &v, 4);
}

/* Cabecalho de mensagem IPFIX (RFC 7011), com o tamanho preenchido depois. */
static u32
bpa_tcp_msg_start (u8 **b, f64 now)
{
  u32 at = vec_len (*b);

  bpa_tcp_put_u16 (b, 10); /* versao */
  bpa_tcp_put_u16 (b, 0);  /* tamanho, preenchido em msg_end */
  bpa_tcp_put_u32 (b, (u32) now);
  bpa_tcp_put_u32 (b, bpa_log_tcp.seq);
  bpa_tcp_put_u32 (b, BPA_TCP_DOMAIN);
  return at;
}

static void
bpa_tcp_msg_end (u8 **b, u32 at)
{
  u16 len = clib_host_to_net_u16 (vec_len (*b) - at);
  clib_memcpy_fast (*b + at + 2, &len, 2);
}

static void
bpa_tcp_append_template (f64 now)
{
  u8 **b = &bpa_log_tcp.outbuf;
  u32 at = bpa_tcp_msg_start (b, now);
  u32 set_at = vec_len (*b);
  u32 i;

  bpa_tcp_put_u16 (b, 2); /* set de template */
  bpa_tcp_put_u16 (b, 0);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, ARRAY_LEN (bpa_tcp_ies));
  for (i = 0; i < ARRAY_LEN (bpa_tcp_ies); i++)
    {
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][0]);
      bpa_tcp_put_u16 (b, bpa_tcp_ies[i][1]);
    }

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);
  bpa_log_tcp.last_template = now;
}

void
bpa_log_tcp_record (f64 now, u8 event, u32 ip_priv, u32 ip_pub, u16 port_lo,
		    u16 port_hi, u32 pool_id)
{
  u8 **b = &bpa_log_tcp.outbuf;
  u32 at, set_at;
  u64 ms;

  if (!bpa_log_tcp.enabled)
    return;

  /* Um registro por mensagem mantem o codigo simples; o custo e 20 bytes de
   * cabecalho por evento, e eventos de bloco sao raros por definicao. */
  at = bpa_tcp_msg_start (b, now);
  set_at = vec_len (*b);
  bpa_tcp_put_u16 (b, BPA_TCP_TEMPLATE_ID);
  bpa_tcp_put_u16 (b, 0);

  ms = clib_host_to_net_u64 ((u64) (now * 1e3));
  bpa_tcp_put (b, &ms, 8);
  bpa_tcp_put (b, &event, 1);
  bpa_tcp_put_u32 (b, ip_priv);
  bpa_tcp_put_u32 (b, ip_pub);
  bpa_tcp_put_u16 (b, port_lo);
  bpa_tcp_put_u16 (b, port_hi);
  bpa_tcp_put_u32 (b, pool_id);

  {
    u16 slen = clib_host_to_net_u16 (vec_len (*b) - set_at);
    clib_memcpy_fast (*b + set_at + 2, &slen, 2);
  }
  bpa_tcp_msg_end (b, at);
  bpa_log_tcp.seq++;
  bpa_log_tcp.n_records++;
}

static void
bpa_tcp_disconnect (void)
{
  if (bpa_log_tcp.fd >= 0)
    close (bpa_log_tcp.fd);
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.connected = 0;
}

static void
bpa_tcp_try_connect (f64 now)
{
  struct sockaddr_in sa;
  int fd, one = 1;

  if (now < bpa_log_tcp.next_attempt)
    return;
  bpa_log_tcp.next_attempt = now + BPA_TCP_RETRY_SECS;

  fd = socket (AF_INET, SOCK_STREAM, 0);
  if (fd < 0)
    return;
  fcntl (fd, F_SETFL, O_NONBLOCK);
  setsockopt (fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof (one));

  clib_memset (&sa, 0, sizeof (sa));
  sa.sin_family = AF_INET;
  sa.sin_port = clib_host_to_net_u16 (bpa_log_tcp.port);
  sa.sin_addr.s_addr = bpa_log_tcp.collector.as_u32;

  if (connect (fd, (struct sockaddr *) &sa, sizeof (sa)) == 0
      || errno == EINPROGRESS)
    {
      bpa_log_tcp.fd = fd;
      bpa_log_tcp.connected = (errno == EINPROGRESS) ? 0 : 1;
      bpa_log_tcp.pending_connect = (errno == EINPROGRESS) ? 1 : 0;
      bpa_log_tcp.n_connects++;
      /* toda conexao nova comeca pelo template: sem ele o coletor descarta */
      bpa_log_tcp.last_template = 0;
      return;
    }
  close (fd);
}

/* Grava o excedente em disco para nao crescer sem limite nem perder evento. */
static void
bpa_tcp_spill (void)
{
  int fd;

  if (vec_len (bpa_log_tcp.outbuf) < BPA_TCP_SPILL_BYTES)
    return;

  fd = open ((char *) bpa_log_tcp.spill_path,
	     O_WRONLY | O_APPEND | O_CREAT, 0640);
  if (fd < 0)
    {
      /* Nao ha para onde escrever: descartar e a unica saida, mas contada. */
      bpa_log_tcp.n_dropped += vec_len (bpa_log_tcp.outbuf);
      vec_reset_length (bpa_log_tcp.outbuf);
      return;
    }
  if (write (fd, bpa_log_tcp.outbuf, vec_len (bpa_log_tcp.outbuf))
      == (ssize_t) vec_len (bpa_log_tcp.outbuf))
    bpa_log_tcp.n_spilled += vec_len (bpa_log_tcp.outbuf);
  else
    bpa_log_tcp.n_dropped += vec_len (bpa_log_tcp.outbuf);
  close (fd);
  vec_reset_length (bpa_log_tcp.outbuf);
}

/* Reenvia o transbordo antes de qualquer coisa nova, para manter a ordem. */
static void
bpa_tcp_replay_spill (void)
{
  struct stat st;
  u8 *buf;
  int fd;
  ssize_t n;

  if (stat ((char *) bpa_log_tcp.spill_path, &st) || st.st_size == 0)
    return;

  fd = open ((char *) bpa_log_tcp.spill_path, O_RDONLY);
  if (fd < 0)
    return;

  buf = 0;
  vec_validate (buf, st.st_size - 1);
  n = read (fd, buf, st.st_size);
  close (fd);

  if (n == st.st_size)
    {
      /* Prepende: o transbordo e mais antigo que o que esta em memoria, e
       * uma alocacao tem que chegar ao coletor antes da sua liberacao. */
      vec_insert (bpa_log_tcp.outbuf, n, 0);
      clib_memcpy_fast (bpa_log_tcp.outbuf, buf, n);
      unlink ((char *) bpa_log_tcp.spill_path);
      bpa_log_tcp.n_replayed += n;
    }
  vec_free (buf);
}

void
bpa_log_tcp_service (f64 now)
{
  ssize_t n;

  if (!bpa_log_tcp.enabled)
    return;

  if (bpa_log_tcp.fd < 0)
    {
      bpa_tcp_try_connect (now);
      bpa_tcp_spill ();
      return;
    }

  if (bpa_log_tcp.pending_connect)
    {
      int err = 0;
      socklen_t l = sizeof (err);
      if (getsockopt (bpa_log_tcp.fd, SOL_SOCKET, SO_ERROR, &err, &l) || err)
	{
	  bpa_tcp_disconnect ();
	  bpa_tcp_spill ();
	  return;
	}
      bpa_log_tcp.pending_connect = 0;
      bpa_log_tcp.connected = 1;
      bpa_tcp_replay_spill ();
    }

  if (now - bpa_log_tcp.last_template > BPA_TCP_TEMPLATE_IVL)
    bpa_tcp_append_template (now);

  while (vec_len (bpa_log_tcp.outbuf))
    {
      n = send (bpa_log_tcp.fd, bpa_log_tcp.outbuf,
		vec_len (bpa_log_tcp.outbuf), MSG_NOSIGNAL);
      if (n > 0)
	{
	  vec_delete (bpa_log_tcp.outbuf, n, 0);
	  bpa_log_tcp.n_sent += n;
	  continue;
	}
      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
	break; /* coletor lento: fica no buffer, tenta no proximo tick */
      bpa_tcp_disconnect ();
      break;
    }

  bpa_tcp_spill ();
}

clib_error_t *
bpa_log_tcp_enable (ip4_address_t *collector, u16 port, int enable)
{
  if (!enable)
    {
      bpa_tcp_disconnect ();
      bpa_log_tcp.enabled = 0;
      return 0;
    }

  bpa_log_tcp.collector = *collector;
  bpa_log_tcp.port = port ? port : 4739;
  bpa_log_tcp.fd = -1;
  bpa_log_tcp.next_attempt = 0;
  bpa_log_tcp.enabled = 1;
  if (!bpa_log_tcp.spill_path)
    bpa_log_tcp.spill_path =
      format (0, "/var/log/hexarouting/ipfix-spill.bin%c", 0);
  return 0;
}
