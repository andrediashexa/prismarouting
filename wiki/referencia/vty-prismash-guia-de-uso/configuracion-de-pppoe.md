# Configuración de PPPoE

Esta página es la referencia del bloque `pppoe` y del bloque `aaa` > `radius`: el equipo termina las sesiones PPPoE de los suscriptores, autentica a cada uno en su RADIUS, limita el ancho de banda de cada sesión y traduce su tráfico por el CGNAT, como lo hace con un suscriptor detrás de una interfaz `inside`. Indica qué hace cada declaración, qué rechaza el equipo y cómo leer las sesiones.

```
prisma> configure
prisma# [edit] aaa
prisma# [edit aaa] radius
prisma# [edit aaa / radius] server 192.0.2.10 secret <secreto> priority 1
prisma# [edit aaa / radius] server 192.0.2.11 secret <secreto> priority 2
prisma# [edit aaa / radius] exit
prisma# [edit aaa] exit
prisma# [edit] pppoe
prisma# [edit pppoe] interface ens19
prisma# [edit pppoe] gateway 100.64.0.1
prisma# [edit pppoe] address-pool 100.64.0.10-100.64.3.250
prisma# [edit pppoe] dns 8.8.8.8
prisma# [edit pppoe] exit
prisma# [edit] commit
```

El `commit` genera la configuración del plano de control PPPoE a partir de la candidata y la pone en marcha. Como todo lo demás, el cambio entra en el `rollback`, en el `commit confirmed` y en el trail de auditoría. Los archivos que lee el plano de control los genera el equipo y se reescriben en cada commit; no se editan a mano.


:::warning
Todo cambio en el bloque `pppoe` reinicia el plano de control PPPoE: **todas las sesiones caen** y los CPE vuelven a marcar. El reenvío de los demás suscriptores, el enrutamiento y la VTY no se ven afectados. Un cambio solo en `aaa` > `radius` (un servidor más, otro modo, otro tiempo) **no tira a nadie**: el plano de control relee la configuración del RADIUS con las sesiones activas. Las excepciones son el primer servidor (el PPPoE pasa de usuarios locales a RADIUS) y el `dynamic-authorization`, que reinician.

:::

## Declaraciones

| Declaración | Qué hace |
|-------------|----------|
| `interface <nombre>` | una interfaz de acceso: la placa, o una subinterfaz de ella con **una** etiqueta VLAN (`vlan <id>` en el bloque de la subinterfaz). Puede haber varias, todas de la misma placa |
| `gateway <dirección>` | la dirección del equipo en cada sesión (la dirección local del IPCP). Obligatoria |
| `address-pool <prefijo>` o `<primera>-<última>` | direcciones para quien el RADIUS no envía `Framed-IP-Address`. Hasta 16 |
| `dns <dirección>` | un resolvedor entregado al CPE. Hasta 2 |
| `authentication pap chap mschap-v2` | los métodos ofrecidos; los tres si se omite |
| `session-limit <n>` | como máximo esa cantidad de sesiones; 16.000 si se omite. Cada sesión es una interfaz en el dataplane, y el segmento de estadísticas se dimensiona para ese número en el arranque, a partir de la configuración guardada. El `commit` rechaza más de 100.000 (el máximo medido) y más de lo que la RAM del equipo permite que el segmento soporte; hasta el próximo reinicio, el equipo acepta solo lo que soporta el segmento en uso, y el `show pppoe summary` lo indica |
| `mtu <576-1500>` | MTU y MRU del PPP; 1492 si se omite |
| `tcp-mss <536-1460>` | limita el MSS del TCP en cada sesión |
| `rate-limit default <download>/<upload>` | el límite de ancho de banda de una sesión para la cual el RADIUS no envió ninguno, en kbit/s o con K, M o G (`20M/10M`); sin él, esa sesión no tiene límite |
| `ac-name <nombre>` | el nombre del concentrador en el PADO; `prisma-routing` si se omite |
| `local-user <nombre> password <contraseña>` | un usuario local, para laboratorio sin RADIUS |
| `ipv6 prefix-pool <prefijo>` | habilita IPv6 en las sesiones; cada sesión recibe un /64 de este prefijo como WAN, anunciado por RA (SLAAC). De /16 a /64. Hasta 16 |
| `ipv6 delegated-pool <prefijo> length <n>` | un prefijo `/n` de este para cada sesión, delegado por DHCPv6 (prefix delegation) para la LAN del cliente. Hasta 16 |
| `ipv6 dns <dirección>` | un resolvedor IPv6 entregado al CPE, en el RA y en el DHCPv6. Hasta 2 |
| `ipv6 enable` | habilita IPv6 sin pool: los prefijos vienen solo del RADIUS |

`no <declaración>` deshace cada una; `no pppoe`, en la raíz del modo de configuración, elimina el bloque entero y apaga el plano de control PPPoE en el `commit`.

El RADIUS ya no está dentro del `pppoe`: está en `aaa` > `radius`, más abajo. Escribir `radius ...` dentro del `pppoe` se rechaza indicando el lugar nuevo. Un equipo que viene de la 0.154.0 con `radius server ...` en el bloque `pppoe` no necesita nada: al cargar la configuración guardada, cada una de esas líneas se convierte en la equivalente en `aaa` > `radius`, con el mismo secreto, en el mismo orden (el primer servidor recibe `priority 1`), y el equipo advierte línea por línea y regraba el archivo.

## RADIUS: `aaa` > `radius`

| Declaración | Qué hace |
|-------------|----------|
| `server <dirección> secret <secreto> [auth-port <n>] [acct-port <n>] [role auth\|acct\|both] [priority <1-255>]` | un servidor. Puertos 1812 y 1813 si se omiten. `role auth` solo autentica, `role acct` solo recibe contabilización, `both` (el predeterminado) ambos. `priority`: el menor se intenta primero; sin ella, el servidor va después del último. Hasta 8 |
| `mode failover` o `mode round-robin` | cómo se usan los servidores; `failover` si se omite |
| `timeout <1-60>` | segundos que una solicitud espera respuesta; 3 si se omite |
| `retries <0-10>` | reenvíos al mismo servidor antes de pasar al siguiente; 1 si se omite |
| `dead-time <1-3600>` | segundos que un servidor que no respondió queda fuera; 30 si se omite |
| `nas-identifier <nombre>` | el NAS-Identifier enviado; `prisma-routing` si se omite |
| `interim-interval <segundos>` | actualizaciones intermedias de contabilización: 0 (ninguna) o de 60 a 86400; 300 si se omite |
| `dynamic-authorization listen <dirección> [port <n>]` | dónde el equipo recibe Disconnect y CoA (RFC 5176); puerto 3799 si se omite |
| `dynamic-authorization client <dirección> secret <secreto>` | un servidor que puede enviar Disconnect y CoA, con su secreto. Hasta 16 |
| `rate-profile <nombre> down <tasa> up <tasa>` | las tasas que significa un `sub-qos-policy` del `Cisco-AVPair` con ese nombre (más abajo). Hasta 64 |

`**failover**`: toda solicitud va al servidor de menor `priority`. Si no responde en `timeout` segundos, `retries` veces, la solicitud va al siguiente, y el que no respondió queda fuera durante `dead-time` segundos: el próximo suscriptor ya va directo al siguiente. Con los valores predeterminados, un servidor caído le cuesta 6 segundos a **un** suscriptor y ninguno a los demás. Pasado el `dead-time`, vuelve a ser el primero. Dos servidores con la misma `priority` reparten la carga entre sí.

`**round-robin**`: cada solicitud va al siguiente servidor activo, en rotación. Los Accounting-Start también se turnan; los Interim y el Stop de una sesión van siempre al servidor que recibió su Start.

La contabilización sigue la misma elección, entre los servidores que reciben contabilización; los Interim y el Stop de una sesión van al servidor que recibió su Start, mientras este responda. Sin ningún servidor de contabilización (todos `role auth`), la contabilización queda desactivada, y el `show pppoe summary` lo indica.

Una sesión nunca se tira porque la contabilización se quedó sin respuesta.

```
prisma# [edit aaa / radius] server 192.0.2.10 secret <secreto> priority 1
prisma# [edit aaa / radius] server 192.0.2.11 secret <secreto> priority 2
prisma# [edit aaa / radius] server 192.0.2.20 secret <secreto> role acct priority 3
prisma# [edit aaa / radius] dynamic-authorization listen 10.0.0.1
prisma# [edit aaa / radius] dynamic-authorization client 192.0.2.10 secret <secreto>
```

## Límite de ancho de banda por suscriptor

El límite de cada sesión viene del RADIUS, en el Access-Accept, en dos atributos propios de Prisma (proveedor 67033, el número IANA del producto):

| Atributo | Número | Tipo | Qué es |
|----------|--------|------|--------|
| `Prisma-Rate-Limit-Down` | 1      | entero | download (del equipo al suscriptor), en kbit/s |
| `Prisma-Rate-Limit-Up` | 2      | entero | upload (del suscriptor al equipo), en kbit/s |

0, o el atributo ausente, significa sin límite en ese sentido. `20000` es 20 Mbit/s. El máximo es 10 Gbit/s.

El `Cisco-AVPair` también se lee cuando el paquete no trae ningún atributo de Prisma (si trae ambos, vale el de Prisma):

| `Cisco-AVPair` | Qué limita |
|--------------|------------|
| `lcp:interface-config=rate-limit output <bps> <burst> <burst-máx> conform-action transmit exceed-action drop` | el download, en bits por segundo |
| `lcp:interface-config=rate-limit input <bps> ...` (el mismo formato) | el upload: `input` es lo que el equipo recibe del suscriptor |
| `lcp:interface-config#1=...` | lo mismo, en la forma numerada |
| `ip:sub-qos-policy-out=<nombre>` o `subscriber:sub-qos-policy-out=<nombre>` | el download del `rate-profile` con ese nombre |
| `ip:sub-qos-policy-in=<nombre>` o `subscriber:sub-qos-policy-in=<nombre>` | el upload del `rate-profile` con ese nombre |

Una política solo trae un nombre; la tasa que significa se configura en el equipo, en `aaa` > `radius`:

```
prisma# [edit aaa / radius] rate-profile PLANO_100M down 100M up 50M
```

Un `Cisco-AVPair` de tasa fuera de esos formatos (un `rate-limit` con `access-group`, sin las acciones, una tasa con sufijo) hace que **ninguna** de las tasas Cisco del paquete valga, y el equipo registra el motivo: medio plan no es un plan. Un nombre sin `rate-profile` deja ese sentido sin límite, también registrado. El `Mikrotik-Rate-Limit` no se lee.

El diccionario para instalar en su servidor RADIUS lo entrega el propio equipo:

```
prisma> show aaa radius dictionary
```

En FreeRADIUS, guarde lo que imprime ese comando como `/usr/share/freeradius/dictionary.prisma` y agregue `$INCLUDE /usr/share/freeradius/dictionary.prisma` al archivo `/etc/freeradius/3.0/dictionary`. En IXC o en otro sistema, registre el proveedor 67033 y los dos atributos de la tabla.

El límite es un policer en el dataplane, en la interfaz de la propia sesión: lo que excede el límite se descarta. La ráfaga aceptada (el balde del policer) es medio segundo de la tasa, nunca menos de 15.000 bytes, venga la tasa de los atributos de Prisma o del `Cisco-AVPair`: los números de ráfaga del `Cisco-AVPair` se verifican y no se usan. Medio segundo es lo que permite que **una** conexión TCP, como la de una prueba de velocidad, llegue al plan: con una décima de segundo (0.155.1) llegaba a la mitad (25 Mbit/s en un plan de 50), aunque el UDP pasaba exacto. Hasta la 0.155.0 el balde era ocho veces mayor, y una prueba corta medía entre 7% y 90% por encima del plan. Una sesión sin límite del RADIUS recibe el `rate-limit default` del bloque `pppoe`, si existe.

## IPv6

Sin ninguna declaración `ipv6`, la sesión sigue siendo solo IPv4, como antes: el CPE que pide IPv6 recibe un rechazo. Con ellas, cada sesión negocia el IPv6CP y recibe dos prefijos:

* **el de la WAN**, un /64, anunciado por Router Advertisement: el CPE arma su propia dirección en él (SLAAC). Viene del `Framed-IPv6-Prefix` del RADIUS cuando este envía uno, y del `ipv6 prefix-pool` cuando no;
* **el delegado**, para la LAN del cliente, entregado por DHCPv6 cuando el CPE lo pide (prefix delegation). Viene del `Delegated-IPv6-Prefix` del RADIUS cuando este envía uno, y del `ipv6 delegated-pool` cuando no.

```
prisma# [edit pppoe] ipv6 prefix-pool 2001:db8:100::/40
prisma# [edit pppoe] ipv6 delegated-pool 2001:db8:200::/36 length 56
prisma# [edit pppoe] ipv6 dns 2001:4860:4860::8888
```

El equipo es `fe80::1` en toda sesión; el router predeterminado del CPE es él. Los dos prefijos se enrutan en el dataplane hacia la sesión: el tráfico IPv6 del suscriptor se enruta, no lo traduce el CGNAT. El límite de ancho de banda de la sesión vale también para IPv6, en el mismo balde que IPv4: un plan de 20 Mbit/s es 20 Mbit/s sumando ambos.

El RADIUS recibe en la contabilización el `Framed-IPv6-Prefix` y el `Framed-Interface-Id` ya en el Accounting-Start, y el `Delegated-IPv6-Prefix` en una actualización intermedia enviada en el momento en que se delega el prefijo.

## Disconnect y CoA

Con `dynamic-authorization`, su RADIUS (o el IXC) puede, sin que el suscriptor vuelva a marcar:

* **tirar una sesión** con un Disconnect-Request, identificándola por `User-Name` o por `Acct-Session-Id`. El equipo responde Disconnect-ACK, la sesión cae y el Stop sale con `Acct-Terminate-Cause = Admin-Reset`;
* **cambiar el límite de ancho de banda** con un CoA-Request con `Prisma-Rate-Limit-Down` y/o `-Up`, o con un `Cisco-AVPair` de tasa: vale de inmediato, en la sesión activa. Un sentido ausente queda como estaba; 0 quita el límite en ese sentido;
* **darle un plazo a la sesión** con `Session-Timeout` en un CoA-Request: cae cuando vence.

Un CoA-Request con un atributo que el equipo no aplica (un `Mikrotik-Rate-Limit`, un `Filter-Id`), una tasa por encima de 10 Gbit/s, un `Cisco-AVPair` de tasa mal formado o un `sub-qos-policy` sin `rate-profile` se rechaza entero con CoA-NAK, y nada cambia. Una solicitud desde una dirección que no es `client`, o con el secreto equivocado, no recibe ninguna respuesta.

La dirección de cada sesión viene del RADIUS (`Framed-IP-Address`) cuando este envía una, y del `address-pool` cuando no. Para que el CGNAT traduzca las sesiones, las direcciones que reciben tienen que estar en el `prefix-inside` de un pool, como las de cualquier suscriptor.

## Qué rechaza el commit

| Situación | Por qué |
|-----------|---------|
| sin `interface`, sin `gateway` | el plano de control no tendría dónde atender ni qué dirección usar |
| sin servidor en `aaa` > `radius` y sin `local-user` | nadie para autenticar |
| sin `address-pool` y sin RADIUS | nadie para entregar dirección |
| `ipv6` sin `ipv6 prefix-pool` y sin RADIUS | ninguna sesión tendría prefijo de WAN, y sin él el CPE no pide el delegado |
| dos pools IPv6 que se superponen | el mismo prefijo iría a dos suscriptores |
| servidores en `aaa` > `radius`, ninguno que autentique (todos `role acct`) | nadie para autenticar |
| `dynamic-authorization listen` sin `client`, o `client` sin `listen` | nadie podría firmar una solicitud, o no habría dónde recibirla |
| interfaces de acceso en dos placas | el PPPoE del dataplane responde desde una sola MAC |
| una subinterfaz con dos etiquetas (QinQ) | el PPPoE del dataplane lee una sola etiqueta |
| una interfaz que el dataplane no tiene | nada que atender en ella |
| un secreto con espacio, coma, comillas o `#` | rompería la configuración generada |

## Los secretos del RADIUS

El secreto de cada servidor y de cada `client` queda en la configuración guardada, para que un `rollback` pueda restaurarlo, y **nunca aparece en pantalla**: `show running-config`, `show configuration` y `show configuration compare` muestran `<set, not shown>`. En un `compare`, cambiar el secreto aparece como una línea eliminada y una agregada, ninguna de las dos con el valor. La contraseña de un `local-user` se trata de la misma manera.

## Sesiones

| Comando | Qué muestra o hace |
|---------|--------------------|
| `show pppoe sessions` | una línea por sesión: el id, el usuario, la dirección, el límite (download/upload, `-` sin límite) y cuánto tiempo lleva activa; una sesión que todavía se está autenticando dice `start` en lugar del tiempo. Una sesión con IPv6 tiene debajo una línea `ipv6` con el prefijo de la WAN y una `delegated` con el prefijo delegado |
| `show pppoe session <id>` | una sesión completa: los prefijos IPv6, la MAC del CPE, el tráfico, el límite y, para cada sentido limitado, cuántos paquetes dejó pasar el policer y cuántos descartó |
| `show pppoe summary` | si el plano de control PPPoE está en ejecución, cuántas sesiones están activas, iniciando y terminando, cada servidor RADIUS (con `failed` si el plano de control lo dio por caído) y el resumen de la configuración |
| `clear pppoe session <id>` | cierra una sesión; el id es el de la primera columna de `show pppoe sessions`, el mismo `Acct-Session-Id` que recibió el RADIUS |
| `clear pppoe session user <nombre>` | cierra las sesiones de un usuario |
| `clear pppoe session address <A.B.C.D>` | cierra la sesión que tiene esa dirección interna, encontrada en `show pppoe sessions` |
| `clear pppoe session all` | cierra todas       |

Una sesión cerrada envía PADT al CPE y Accounting-Stop al RADIUS; el CPE vuelve a marcar. Las interfaces que el dataplane crea para las sesiones nunca entran en la configuración: son suscriptores marcando, no algo que alguien configuró.

Todo esto también está en la API de gestión (gRPC), con campos tipados: las sesiones, una sesión con sus policers, el resumen con los contadores del historial y de los rechazos, el historial y la verificación de la cadena, y el `clear` por id, usuario, dirección o todas. Los bloques `pppoe` y `aaa` se leen y escriben como campos mediante `GetConfig` y `SetConfig`; los secretos nunca vuelven en una lectura.

## Quién tenía la dirección: el historial de sesiones

Con PPPoE, la dirección interna del suscriptor se entrega en el momento de la marcación, por orden de llegada. El registro del CGNAT (el log de bloques, o la fórmula) indica qué **dirección interna** estaba detrás de una IP pública y un puerto; para saber **qué suscriptor** tenía esa dirección en ese instante, el equipo guarda su propio historial de sesiones. Lo escribe el plano de control al inicio y al final de cada sesión, responda o no algún servidor RADIUS, en `/var/log/prisma-routing/pppoe-sessions.log`, junto al trail de configuración: solo crece, cada línea va al disco antes de que la sesión continúe, y cada una lleva un hash encadenado a la anterior. En una sesión con IPv6, la línea de inicio trae el prefijo de la WAN, y el prefijo delegado entra en una línea propia en el momento en que se delega, repetida al final de la sesión.

| Comando | Qué muestra |
|---------|-------------|
| `show pppoe history address <A.B.C.D> at <hora>` | la sesión que tenía esa dirección interna en ese instante: el usuario, desde cuándo hasta cuándo, el id de la sesión, la MAC y la interfaz de acceso |
| `show pppoe history address <dirección IPv6> at <hora>` | la sesión cuyo prefijo de WAN contiene esa dirección, o cuyo prefijo delegado la contenía en ese instante: el delegado cuenta a partir del momento en que se delegó, no del inicio de la sesión |
| `show pppoe history user <nombre>` | las sesiones de un usuario |
| `show pppoe history since <hora>` | las sesiones que estuvieron activas desde esa hora en adelante |
| `show pppoe history verify` | verifica la cadena de hashes e indica la primera línea que no coincide |
| `show cgnat translation <público> <puerto> at <hora>` | en un pool determinístico, las dos etapas a la vez: la dirección interna por la fórmula y la sesión que la tenía en ese instante |

La hora es la del `show log`: una hora ISO con `Z` (la del log de bloques, copiada tal cual), sin zona horaria (la del equipo), o `30m`, `2d` hacia atrás. Las horas que muestra el historial son siempre UTC, como las del log de bloques.

```
prisma> show pppoe history address 100.64.0.11 at 2026-10-13T02:51:00Z
maria.souza held 100.64.0.11
  from 2026-10-13T02:49:02.789Z  to still held
  session 7f3a0c1e00000005  mac 02:aa:00:00:00:05  on ens19.100
```

En un pool **dinámico**, el bloque pertenece a quien lo tiene ahora: el `show cgnat translation` responde solo por el ahora y rechaza el `at`. Para un instante pasado, la línea `BLOCK-ALLOC` del log de bloques cuyo rango contiene el puerto da la dirección interna y la hora, y el `show pppoe history address <dirección> at <hora>` da el suscriptor.

El `show pppoe summary` tiene la línea `session trail`: cuántas líneas se escribieron y, en mayúsculas, cuántas **no** se pudieron escribir (volumen lleno, por ejemplo) - cada una es una sesión por la que el equipo no puede responder.


:::warning
Una cadena que coincide prueba que el archivo no fue editado, no que es el mismo archivo. Por eso el equipo envía la cabeza de la cadena fuera de él: cada hora mientras el historial crece, cuando el vigía de eventos arranca, y la última línea de cada mes cuando se rota el archivo, graba un evento `trail` con el `seq` y el `sha256` — y el registro de eventos va a los colectores de syslog configurados en `system` > `logging` y al `WatchEvents` de la API. Para verificar, compare el `head seq` y el `sha256` del `show pppoe history verify` con el último evento `trail` que recibió el colector (`show log kind trail` muestra la copia del propio equipo). Sin un colector de syslog configurado, esa copia no sale del equipo. El equipo nunca borra el historial: una vez al mes el archivo pasa a ser `pppoe-sessions.log.<AAAA-MM>`, y los meses que exceden el plazo de retención pueden ser archivados o eliminados por usted.

:::

## Dónde acepta PPPoE el equipo

**Solo en las interfaces de acceso.** Un PADI, o cualquier trama PPPoE, que llega por una interfaz que no está en el bloque `pppoe` — el uplink, un puerto `inside` del CGNAT — se descarta en el dataplane y se cuenta; nadie responde. Antes de esto, un CPE del lado equivocado del equipo recibía PADO y podía abrir sesión.

**Una MAC pertenece al puerto donde está su sesión.** Si la misma MAC aparece en dos interfaces de acceso (un CPE clonado, o dos OLT con la misma dirección), el equipo **mantiene la sesión que ya existe** y rechaza, contándolos, el descubrimiento y el control de esa MAC que vienen del otro puerto mientras la sesión esté viva. En cuanto el suscriptor se desconecta allí (su PADT), o 60 s después de la última señal de vida de la sesión (el mismo tiempo en que el plano de control la daría por muerta), el otro puerto puede marcar. Sin esto, las dos sesiones existían juntas y cada trama de control iba al puerto que había hablado por último: un suscriptor recibía el control de la sesión del otro.

```
prisma> show pppoe summary
```

Lo rechazado está en el `show bpa pppoe-access` del dataplane, en dos líneas — `discovery` y `session` — con cuántas tramas pasaron, cuántas llegaron por una interfaz que no es de acceso (`not-access`) y cuántas traían una MAC que pertenece a otro puerto (`held`).


:::info
Dos suscriptores **diferentes** con la misma MAC en dos puertos no se atienden al mismo tiempo: el dataplane identifica la sesión por la MAC y por el número de sesión, sin el puerto. El primero se queda; el segundo espera.

:::

## El registro: `show log`

Cada arranque y cada parada del plano de control PPPoE entra en el registro del equipo con el motivo, y también cada servidor RADIUS que el plano de control da por caído, y su regreso:

```
prisma> show log kind start
prisma> show log kind exit
prisma> show log kind radius
```

| Evento | Ejemplo |
|--------|---------|
| arranque solicitado por un commit | `the PPPoE control plane (prisma-bng) started again: PPPoE's configuration committed by ana` |
| salida por falla, con el código o la señal | `the PPPoE control plane (prisma-bng) exited with code 1: accel-pppd was killed by signal 9 (KILL)` |
| el arranque que vino después de ella, sin que nadie lo pidiera | `the PPPoE control plane (prisma-bng) started again, by its restart policy, after it exited: ...` |
| parada solicitada (commit, actualización, `no pppoe`) | `the PPPoE control plane (prisma-bng) stopped: stopped (SIGTERM): ...` |
| reinicio en una actualización que regraba los archivos | `... started again: PPPoE's files from an older release (format 4) written again in format 5` |
| servidor RADIUS caído, y de vuelta | `RADIUS server 192.0.2.10 failed: it did not answer, ...` / `RADIUS server 192.0.2.10 is active again: ...` |

Un arranque en nivel `warning` es uno que nadie pidió: es el que vale la pena investigar.

## Cómo verificar

```
prisma> show pppoe summary
prisma> show pppoe sessions
```

El resumen debe decir `running` y contar las sesiones activas; cada suscriptor que marcó aparece en `show pppoe sessions` con la dirección que recibió. En su RADIUS, cada sesión deja un Accounting-Start, las actualizaciones intermedias con los bytes de la sesión y, al caer, un Accounting-Stop.


:::info
QinQ y la alta disponibilidad de las sesiones todavía no existen. Una sesión es siempre IPv4 con IPv6 opcional: solo IPv6 no existe. El límite de ancho de banda es por descarte (policer), no por cola (shaper).

:::

## Ver también

* [Configuración de CGNAT](/s/wiki-es/doc/configuracion-de-cgnat-WTgNXqphc5) — el pool que traduce las sesiones
* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA) — el `prefix-inside` que cubre las direcciones de las sesiones
* [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg) — la subinterfaz con la VLAN de acceso
* [commit, rollback y save](/s/wiki-es/doc/commit-rollback-y-save-6sAWJkXNO3) — cómo aplicar lo que se configuró aquí
