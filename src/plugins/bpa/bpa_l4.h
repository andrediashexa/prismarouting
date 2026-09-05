/*
 * bpa_l4.h - L4 identifier extraction, shared by the translation nodes and
 * the handoff nodes. The handoff nodes need the destination port to work
 * out which subscriber owns a returning packet.
 */

#ifndef __included_bpa_l4_h__
#define __included_bpa_l4_h__

#include <vnet/ip/ip4_packet.h>
#include <vnet/ip/icmp46_packet.h>

#include <vnet/buffer.h>

#include "bpa_session.h"

/*
 * Returns 0 and the L4 id; -1 when the packet is not translatable.
 *
 * `*l4h` sai zero quando o pacote nao carrega cabecalho L4 - o caso do
 * fragmento que nao e o primeiro. Nesses, quem fornece as portas e o
 * reassembly virtual raso do VPP, que as guardou do primeiro fragmento. Sem
 * isso, um fragmento sem porta nao casa com sessao nenhuma e e descartado:
 * DNS grande, VPN e jogo param de funcionar de forma intermitente.
 */
static_always_inline int
bpa_l4_id (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, int want_dst,
	   u16 *port, void **l4h)
{
  u32 hlen = ip4_header_bytes (ip);
  void *p = (u8 *) ip + hlen;

  if (PREDICT_FALSE (vnet_buffer (b)->ip.reass.is_non_first_fragment))
    {
      *l4h = 0; /* nao ha L4 aqui: so o endereco sera reescrito */
      /* O reassembly guarda a porta em ordem de rede. Ler como ordem de
       * host devolve o valor com os bytes trocados - 12345 (0x3039) vira
       * 14640 (0x3930) - e o node abre uma sessao fantasma, queimando uma
       * porta do bloco por fragmento. */
      *port = clib_net_to_host_u16 (want_dst
				      ? vnet_buffer (b)->ip.reass.l4_dst_port
				      : vnet_buffer (b)->ip.reass.l4_src_port);
      return 0;
    }

  if (PREDICT_FALSE ((u8 *) p + 8
		     > (u8 *) vlib_buffer_get_current (b) + b->current_length))
    return -1;

  *l4h = p;

  if (proto == BPA_PROTO_ICMP)
    {
      icmp46_header_t *icmp = p;
      /* Only echo carries an identifier usable as a port. ICMP errors need
       * the embedded packet rewritten, which is a separate job. Both echo
       * request and reply keep the identifier in the same place, so the
       * direction does not change where we read it from. */
      if (icmp->type != ICMP4_echo_request && icmp->type != ICMP4_echo_reply)
	return -1;
      *port = clib_net_to_host_u16 (((u16 *) (icmp + 1))[0]);
      return 0;
    }

  /* TCP and UDP both start with source port then destination port. */
  *port = clib_net_to_host_u16 (((u16 *) p)[want_dst ? 1 : 0]);
  return 0;
}

static_always_inline int
bpa_l4_src (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, u16 *port,
	    void **l4h)
{
  return bpa_l4_id (b, ip, proto, 0, port, l4h);
}

static_always_inline int
bpa_l4_dst (vlib_buffer_t *b, ip4_header_t *ip, u8 proto, u16 *port,
	    void **l4h)
{
  return bpa_l4_id (b, ip, proto, 1, port, l4h);
}

#endif /* __included_bpa_l4_h__ */
