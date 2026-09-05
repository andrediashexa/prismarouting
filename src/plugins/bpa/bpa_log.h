/*
 * bpa_log.h - exportacao IPFIX de eventos de bloco (RFC 8158).
 *
 * Loga alocacao de bloco, nao fluxo. E o que torna o CGNAT auditavel sem
 * gerar terabytes por dia. Ver docs/logging.md.
 */

#ifndef __included_bpa_log_h__
#define __included_bpa_log_h__

#include <vlib/vlib.h>
#include <vnet/ip/ip.h>

/* RFC 8158, registro natEvent */
#define BPA_NAT_EVENT_PORTS_EXHAUSTED 12
#define BPA_NAT_EVENT_BLOCK_ALLOC     16
#define BPA_NAT_EVENT_BLOCK_DEALLOC   17

/* Tabela 21 da RFC 8158: 8 + 1 + 4 + 4 + 2 + 2 + 4 */
#define BPA_LOG_RECORD_SIZE 25

/*
 * Evento enfileirado pelo dataplane.
 *
 * O node NAO chama o caminho de export direto: emitir IPFIX de dentro do
 * dispatch do grafo nao funciona - o buffer e escrito mas nunca sai do fio.
 * Descoberto em lab/ipfix.sh, onde so os eventos emitidos do contexto de
 * processo chegavam ao coletor. O dataplane enfileira e um processo drena.
 */
typedef struct
{
  u32 ip_priv;
  u32 ip_pub;
  u32 pool_id;
  u16 port_lo;
  u16 port_hi;
  u8 event;
} bpa_log_event_t;

/* Teto da fila por thread: se o processo atrasar, perde-se o excedente de
 * forma contabilizada, em vez de crescer sem limite. */
#define BPA_LOG_QUEUE_MAX 8192

typedef struct
{
  u32 template_id;
  u32 stream_index;
  u32 flow_report_index;
  u64 n_records;
  u64 n_flush_called;
  u64 n_flush_sent;
  u64 n_flush_empty;
  u64 n_getbuf_fail;
  u64 n_dropped; /* sem coletor configurado */
  u8 enabled;
} bpa_log_main_t;

extern bpa_log_main_t bpa_log_main;

clib_error_t *bpa_log_enable (int enable);

/* Emite de fato. So pode ser chamado de contexto de processo. */
void bpa_log_block_event (vlib_main_t *vm, u32 thread_index, u8 event,
			  u32 ip_priv, u32 ip_pub, u16 port_lo, u16 port_hi,
			  u32 pool_id);

/*
 * Exportacao sobre TCP, para quando perder registro nao e aceitavel.
 * O socket e nao bloqueante e o excedente vai para arquivo de transbordo.
 */
typedef struct
{
  int fd;
  ip4_address_t collector;
  u16 port;
  u8 enabled;
  u8 connected;
  u8 pending_connect;
  u8 *outbuf;
  u8 *spill_path;
  f64 next_attempt;
  f64 last_template;
  u8 need_template;
  u32 seq;
  u64 n_records, n_sent, n_spilled, n_replayed, n_dropped, n_connects;
  u64 n_service, n_send_calls;
  int last_errno;
} bpa_log_tcp_t;

extern bpa_log_tcp_t bpa_log_tcp;

clib_error_t *bpa_log_tcp_enable (ip4_address_t *collector, u16 port,
				  int enable);
void bpa_log_tcp_record (f64 now, u8 event, u32 ip_priv, u32 ip_pub,
			 u16 port_lo, u16 port_hi, u32 pool_id);
void bpa_log_tcp_service (f64 now);

/* Drena as filas de todas as threads. Chamado sob barreira. */
void bpa_log_drain (vlib_main_t *vm);

/* bpa_log_queue_event() e declarada em bpa.h, que ja enxerga
 * bpa_per_thread_t; declara-la aqui exigiria uma dependencia circular. */

#endif /* __included_bpa_log_h__ */
