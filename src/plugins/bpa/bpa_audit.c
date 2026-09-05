/*
 * bpa_audit.c - trilha de configuracao datada.
 *
 * No modo deterministico nao ha evento de alocacao a logar: a prova de quem
 * usou (IP publico, porta, instante) e o algoritmo mais a configuracao do
 * pool. Isso so vale enquanto se souber QUAL configuracao vigia naquele
 * instante. Se o pool mudou dia 10 e o oficio pergunta sobre o dia 5, a
 * formula atual responde a pergunta errada com toda a confianca.
 *
 * Por isso toda mudanca de pool e registrada, datada em UTC, em arquivo
 * append-only. O registro tem a definicao completa, para poder ser relido
 * sem depender de nada mais.
 */

#include <fcntl.h>
#include <time.h>
#include <unistd.h>

#include "bpa.h"

#ifndef BPA_AUDIT_PATH_DEFAULT
#define BPA_AUDIT_PATH_DEFAULT "/var/log/hexarouting/config-history.log"
#endif

static u8 *
bpa_audit_format_pool (u8 *s, bpa_pool_t *p)
{
  ip4_address_t a;
  u32 i;

  for (i = 0; i < p->inside.n_ranges; i++)
    {
      a.as_u32 = clib_host_to_net_u32 (p->inside.ranges[i].base);
      s = format (s, " inside %U+%u", format_ip4_address, &a,
		  p->inside.ranges[i].count);
    }
  for (i = 0; i < p->outside.n_ranges; i++)
    {
      a.as_u32 = clib_host_to_net_u32 (p->outside.ranges[i].base);
      s = format (s, " outside %U+%u", format_ip4_address, &a,
		  p->outside.ranges[i].count);
    }
  s = format (s, " block %u ports %u-%u policy %U mode %U", p->block_size,
	      p->port_min, p->port_max, format_bpa_policy, p->policy,
	      format_bpa_mode, p->mode);
  return s;
}

void
bpa_audit (const char *op, bpa_pool_t *p, u8 *name_only)
{
  bpa_main_t *bm = &bpa_main;
  struct tm tm;
  time_t now;
  u8 *line = 0;
  int fd;

  now = (time_t) unix_time_now ();
  gmtime_r (&now, &tm);

  line = format (0, "%04d-%02d-%02dT%02d:%02d:%02dZ %s pool %v",
		 tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour,
		 tm.tm_min, tm.tm_sec, op, p ? p->name : name_only);
  if (p)
    line = bpa_audit_format_pool (line, p);

  /*
   * Tambem para o log do VPP: um arquivo local sobrevive a um reboot, nao a
   * uma perda de disco. Quem leva a serio manda o log do VPP para syslog
   * remoto, e ai a trilha sai da caixa.
   */
  vlib_log_notice (bm->log_class, "%v", line);

  fd = open ((char *) bm->audit_path, O_WRONLY | O_APPEND | O_CREAT, 0640);
  if (fd < 0)
    {
      vlib_log_err (bm->log_class,
		    "nao consegui abrir a trilha de configuracao em %v: a "
		    "prova do modo deterministico depende dela",
		    bm->audit_path);
      goto done;
    }

  line = format (line, "\n");
  if (write (fd, line, vec_len (line)) != (ssize_t) vec_len (line))
    vlib_log_err (bm->log_class, "escrita incompleta na trilha de configuracao");
  else
    fsync (fd); /* compliance: nao pode ficar so no cache */
  close (fd);

done:
  vec_free (line);
}

static clib_error_t *
bpa_show_history_command_fn (vlib_main_t *vm, unformat_input_t *input,
			     vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u8 buf[4096];
  int fd, n;

  fd = open ((char *) bm->audit_path, O_RDONLY);
  if (fd < 0)
    {
      vlib_cli_output (vm, "nenhuma trilha em %v", bm->audit_path);
      return 0;
    }

  vlib_cli_output (vm, "trilha de configuracao: %v", bm->audit_path);
  while ((n = read (fd, buf, sizeof (buf) - 1)) > 0)
    {
      buf[n] = 0;
      vlib_cli_output (vm, "%s", buf);
    }
  close (fd);
  return 0;
}

VLIB_CLI_COMMAND (bpa_show_history_command, static) = {
  .path = "show bpa pool history",
  .short_help = "show bpa pool history",
  .function = bpa_show_history_command_fn,
};

static clib_error_t *
bpa_audit_path_command_fn (vlib_main_t *vm, unformat_input_t *input,
			   vlib_cli_command_t *cmd)
{
  bpa_main_t *bm = &bpa_main;
  u8 *path = 0;

  if (unformat (input, "%v", &path))
    {
      vec_free (bm->audit_path);
      bm->audit_path = path;
      return 0;
    }

  vlib_cli_output (vm, "%v", bm->audit_path);
  return 0;
}

VLIB_CLI_COMMAND (bpa_audit_path_command, static) = {
  .path = "bpa audit-path",
  .short_help = "bpa audit-path [<caminho>]",
  .function = bpa_audit_path_command_fn,
};
