/*
 * bpa_log.c - exportacao IPFIX de eventos de bloco (RFC 8158).
 *
 * Reaproveita a infraestrutura vnet/ipfix-export do VPP: template, numero de
 * sequencia e transporte ja existem. O que este arquivo acrescenta e o
 * template da Tabela 21 da RFC 8158 e a emissao dos eventos.
 *
 * Nao ha registro por fluxo, de proposito. No modo dinamico sai um registro
 * por alocacao e um por liberacao; no deterministico nao sai nenhum, porque
 * nao ha evento - a prova e o algoritmo mais a configuracao.
 */

#include <vnet/ipfix-export/flow_report.h>
#include <vnet/udp/udp_local.h>

#include "bpa.h"
#include "bpa_log.h"

bpa_log_main_t bpa_log_main;

/*
 * Template da Tabela 21 da RFC 8158.
 *
 * portRangeEnd e natPoolId sao opcionais na norma, mas vao junto: sem o fim
 * do bloco o coletor teria de conhecer o block_size vigente na data do
 * evento para saber o alcance, e essa e exatamente a informacao que a
 * trilha de configuracao existe para guardar.
 */
static ipfix_report_element_t bpa_block_elements[] = {
  { observationTimeMilliseconds, 8 },
  { natEvent, 1 },
  { sourceIPv4Address, 4 },
  { postNATSourceIPv4Address, 4 },
  { portRangeStart, 2 },
  { portRangeEnd, 2 },
  { natPoolId, 4 },
};

static vlib_frame_t *
bpa_log_data_callback (flow_report_main_t *frm, ipfix_exporter_t *exp,
		       flow_report_t *fr, vlib_frame_t *f, u32 *to_next,
		       u32 node_index)
{
  /*
   * Os registros sao empurrados por bpa_log_drain(), que escreve e despacha
   * na mesma passagem. Este callback existe so porque a infraestrutura o
   * exige; nao ha nada para puxar por timer.
   */
  return f;
}

clib_error_t *
bpa_log_enable (int enable)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  vnet_flow_report_add_del_args_t a;
  ipfix_exporter_t *exp;
  u16 template_id = 0;
  int rv;

  if ((u8) (enable != 0) == lm->enabled)
    return 0;

  exp = pool_elt_at_index (frm->exporters, 0);

  if (enable && ip_address_is_zero (&exp->ipfix_collector))
    return clib_error_return (
      0, "no IPFIX collector configured; use "
	 "'set ipfix exporter collector <ip> src <ip>' first");

  clib_memset (&a, 0, sizeof (a));
  a.is_add = enable;
  a.domain_id = 1;
  a.src_port = UDP_DST_PORT_ipfix;
  a.rewrite_callback = vnet_flow_rewrite_generic_callback;
  a.flow_data_callback = bpa_log_data_callback;
  a.report_elements = bpa_block_elements;
  a.n_report_elements = ARRAY_LEN (bpa_block_elements);
  a.stream_indexp = &lm->stream_index;

  rv = vnet_flow_report_add_del (exp, &a, &template_id);
  if (rv)
    return flow_report_add_del_error_to_clib_error (rv);

  lm->template_id = template_id;
  lm->flow_report_index = a.flow_report_index;
  lm->enabled = enable ? 1 : 0;
  return 0;
}

/*
 * Enfileira um evento a partir do dataplane. A fila e por thread, entao nao
 * ha lock: cada worker so escreve na sua.
 */
void
bpa_log_queue_event (bpa_per_thread_t *pt, u8 event, u32 ip_priv, u32 ip_pub,
		     u16 port_lo, u16 port_hi, u32 pool_id)
{
  bpa_log_event_t *e;

  /* Basta um transporte habilitado: com so o TCP ligado, a versao anterior
   * descartava todo evento de alocacao aqui, em silencio. */
  if (PREDICT_FALSE (!bpa_log_main.enabled && !bpa_log_tcp.enabled))
    return;

  if (PREDICT_FALSE (vec_len (pt->log_queue) >= BPA_LOG_QUEUE_MAX))
    {
      pt->log_dropped++;
      return;
    }

  vec_add2 (pt->log_queue, e, 1);
  e->event = event;
  e->ip_priv = ip_priv;
  e->ip_pub = ip_pub;
  e->port_lo = port_lo;
  e->port_hi = port_hi;
  e->pool_id = pool_id;
}

/*
 * Despacha o buffer parcial da thread corrente, se houver.
 *
 * Nao basta deixar isso para o flow_data_callback: o buffer escrito numa
 * passagem era perdido antes de a proxima chamada do callback acontecer, e
 * so o ultimo lote chegava ao coletor. Escrever e despachar tem que ser a
 * mesma operacao.
 */
static void
bpa_log_flush (vlib_main_t *vm)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  flow_report_per_thread_t *ptd;
  ipfix_exporter_t *exp;
  flow_report_t *fr;

  if (!lm->enabled)
    return;

  lm->n_flush_called++;
  exp = pool_elt_at_index (frm->exporters, 0);
  fr = pool_elt_at_index (exp->reports, lm->flow_report_index);

  if (vm->thread_index >= vec_len (fr->per_thread_data))
    return;

  ptd = vec_elt_at_index (fr->per_thread_data, vm->thread_index);

  if (ptd->buffer == 0 || ptd->next_data_offset <= exp->all_headers_size)
    {
      lm->n_flush_empty++;
      return;
    }

  lm->n_flush_sent++;

  vnet_ipfix_exp_send_buffer (
    vm, exp, fr, pool_elt_at_index (exp->streams, fr->stream_index),
    vm->thread_index, ptd->buffer);
}

/*
 * Drena todas as filas. Roda sob a barreira dos workers, no processo, que e
 * o unico contexto de onde o export IPFIX efetivamente sai do fio.
 */
/* Emite nos transportes habilitados. UDP para telemetria, TCP para o log
 * que nao pode perder registro; os dois podem estar ligados ao mesmo tempo. */
static void
bpa_log_emit (vlib_main_t *vm, f64 now, u8 event, u32 ip_priv, u32 ip_pub,
	      u16 port_lo, u16 port_hi, u32 pool_id)
{
  bpa_log_block_event (vm, vm->thread_index, event, ip_priv, ip_pub, port_lo,
		       port_hi, pool_id);
  bpa_log_tcp_record (now, event, ip_priv, ip_pub, port_lo, port_hi, pool_id);
}

void
bpa_log_drain (vlib_main_t *vm)
{
  bpa_main_t *bm = &bpa_main;
  flow_report_main_t *frm = &flow_report_main;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  u32 i, j;

  if (!bpa_log_main.enabled && !bpa_log_tcp.enabled)
    return;

  /*
   * Registro emitido antes do primeiro template e indecifravel: o coletor
   * nao sabe o formato e descarta. Verificado em lab/ipfix.sh, onde os
   * quatro primeiros eventos sairam no fio e nenhum coletor conseguiria
   * le-los. Para log de compliance isso e perda silenciosa logo apos um
   * restart, que e justamente quando ninguem esta olhando.
   *
   * Os eventos ficam na fila ate o template sair. A fila e limitada, entao
   * um coletor inalcancavel resulta em perda contabilizada, nao em memoria
   * crescendo sem fim.
   */
  if (bpa_log_main.enabled)
    {
      exp = pool_elt_at_index (frm->exporters, 0);
      fr = pool_elt_at_index (exp->reports, bpa_log_main.flow_report_index);
      if (fr->last_template_sent == 0)
	return;
    }

  for (i = 0; i < vec_len (bm->per_thread); i++)
    {
      bpa_per_thread_t *pt = vec_elt_at_index (bm->per_thread, i);

      for (j = 0; j < vec_len (pt->log_queue); j++)
	{
	  bpa_log_event_t *e = vec_elt_at_index (pt->log_queue, j);

	  bpa_log_emit (vm, vlib_time_now (vm), e->event, e->ip_priv,
			e->ip_pub, e->port_lo, e->port_hi, e->pool_id);
	}
      vec_reset_length (pt->log_queue);
    }

  bpa_log_flush (vm);
  bpa_log_tcp_service (vlib_time_now (vm));
}

void
bpa_log_block_event (vlib_main_t *vm, u32 thread_index, u8 event, u32 ip_priv,
		     u32 ip_pub, u16 port_lo, u16 port_hi, u32 pool_id)
{
  bpa_log_main_t *lm = &bpa_log_main;
  flow_report_main_t *frm = &flow_report_main;
  flow_report_per_thread_t *ptd;
  ipfix_exporter_t *exp;
  flow_report_t *fr;
  vlib_buffer_t *b0;
  u64 now_ms;
  u16 offset;
  u8 *d;

  if (PREDICT_TRUE (!lm->enabled))
    {
      lm->n_dropped++;
      return;
    }

  exp = pool_elt_at_index (frm->exporters, 0);
  fr = pool_elt_at_index (exp->reports, lm->flow_report_index);

  b0 = vnet_ipfix_exp_get_buffer (vm, exp, fr, thread_index);
  if (PREDICT_FALSE (b0 == 0))
    {
      lm->n_dropped++;
      lm->n_getbuf_fail++;
      return;
    }

  ptd = vec_elt_at_index (fr->per_thread_data, thread_index);
  offset = ptd->next_data_offset;
  d = (u8 *) vlib_buffer_get_current (b0) + offset;

  /* milissegundos desde a epoca, na escala do proprio exportador */
  now_ms = (u64) ((frm->unix_time_0 + vlib_time_now (vm) - frm->vlib_time_0)
		  * 1e3);

  clib_memcpy_fast (d, &(u64){ clib_host_to_net_u64 (now_ms) }, 8);
  d += 8;
  d[0] = event;
  d += 1;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (ip_priv) }, 4);
  d += 4;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (ip_pub) }, 4);
  d += 4;
  clib_memcpy_fast (d, &(u16){ clib_host_to_net_u16 (port_lo) }, 2);
  d += 2;
  clib_memcpy_fast (d, &(u16){ clib_host_to_net_u16 (port_hi) }, 2);
  d += 2;
  clib_memcpy_fast (d, &(u32){ clib_host_to_net_u32 (pool_id) }, 4);

  offset += BPA_LOG_RECORD_SIZE;
  ptd->next_data_offset = offset;
  b0->current_length = offset;
  lm->n_records++;

  /* despacha antes que o proximo registro nao caiba */
  if (offset + BPA_LOG_RECORD_SIZE > exp->path_mtu)
    vnet_ipfix_exp_send_buffer (
      vm, exp, fr, pool_elt_at_index (exp->streams, fr->stream_index),
      thread_index, b0);
}
