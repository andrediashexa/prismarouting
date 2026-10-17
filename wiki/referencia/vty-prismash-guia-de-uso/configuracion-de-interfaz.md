# Configuración de interfaz

Todo lo que se configura dentro de una interfaz: direcciones, VLAN, VRF, MTU, MAC, estado administrativo, filtro de origen, el lado del CGNAT, VRRP, OSPF e IS-IS en el puerto, loopbacks y bundles LACP. Para quienes están levantando un enlace, terminando suscriptores en VLAN o investigando un puerto que no pasa tráfico.

Se entra en una interfaz desde el modo de configuración, y se sale con `exit`:

```
prisma> configure
prisma# [edit] interface ens18
prisma# [edit interface ens18]
```

El prompt indica en qué interfaz está usted. `?` o TAB listan lo que es válido ahí dentro y nada más.

Modificar una interfaz exige la capacidad de **administrador**. La excepción es el lado del CGNAT (`cgnat inside|outside`), que una cuenta `cgnat-operator` también puede modificar.

## Lo que entiende una interfaz

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `description` | `<texto>` | para qué sirve la interfaz, hasta 63 caracteres; aparece en `show interface description` |
| `shutdown` |           | baja la interfaz administrativamente |
| `no shutdown` |           | la vuelve a levantar |
| `ipv4 address` | `<prefijo>` | dirección IPv4; se puede repetir |
| `ipv6 address` | `<prefijo>` | dirección IPv6; se puede repetir |
| `no ipv4 address` | `<prefijo>` | elimina **una** dirección, sin tocar las demás |
| `mtu`   | `<576-9216>` | MTU, en la interfaz y en su par Linux; sin ella, 1500 |
| `linux-name` | `<nombre>` | **otro** nombre con el que el enrutamiento e `ip` ven esta interfaz; sin él, toda interfaz ya entra en Linux con su propio nombre |
| `vrf`   | `<1-4294967295>` | tabla de enrutamiento donde viven las direcciones; `0` saca la interfaz de una |
| `vlan`  | `<1-4094> [inner <1-4094>]` | tag de la subinterfaz |
| `mac-address` | `<xx:xx:xx:xx:xx:xx>` | la MAC a la que responde la interfaz |
| `cgnat` | `inside\|outside` | el lado de esta interfaz en el CGNAT: `inside` es por donde llegan los suscriptores, `outside` por donde sale el tráfico traducido |
| `no cgnat` |           | saca la interfaz del CGNAT |
| `no ipv6 nd suppress-ra` |           | anuncia este equipo como router en este puerto (por defecto no lo anuncia) |
| `ipv6 nd suppress-ra` |           | vuelve al valor predeterminado: no anuncia |
| `ipv6 nd nat64` | `[<X:X::X:X/M>]` | coloca el prefijo NAT64 en los anuncios (PREF64, RFC 8781), para el CLAT del suscriptor; sin prefijo, `64:ff9b::/96`; se puede repetir. Vea *El prefijo NAT64 en los anuncios* más abajo |
| `no ipv6 nd nat64` | `[<X:X::X:X/M>]` | quita ese prefijo de los anuncios, o todos |
| `urpf`  | `strict\|loose\|off` | descarta paquetes cuyo origen no vuelve por aquí |
| `vrrp`  | `<1-255> address <A.B.C.D>` | una dirección de servicio que este equipo disputa con el par (RFC 5798); IPv4, sin máscara; se puede repetir |
| `vrrp`  | `<1-255> priority <1-254>` | quién se queda con la dirección cuando los dos están bien; gana el mayor |
| `vrrp`  | `<1-255> track <interface> decrement <1-254>` | cuánto baja la prioridad cuando cae ese otro puerto |
| `no vrrp` | `<1-255>` | quita el router virtual completo de esta interfaz |
| `member`, `lacp`, `load-balance` |           | solo en un bundle `ae<n>`; vea *Bundles LACP* más abajo |
| `ip ospf ...`, `ipv6 ospf6 ...` |           | OSPF y OSPFv3 en esta interfaz; vea *OSPF e IS-IS en la interfaz* más abajo |
| `ip router isis`, `ipv6 router isis` | `<instancia>` | IS-IS en esta interfaz, para IPv4 y para IPv6 |
| `isis ...` |           | ajustes de IS-IS en esta interfaz |
| `exit`  |           | sale de la interfaz |

## MTU

Una interfaz sin `mtu` queda en **1500**, el valor predeterminado del equipo desde la 0.152.1, y `no mtu` vuelve a él. Vale también para lo que crea el `commit`: una VLAN, un loopback, un bundle. En una placa que no soporta 1500, el predeterminado es el máximo de la placa.

```
interface ens18
 mtu 9000
 exit
```

El `mtu` cambia la interfaz del dataplane y su par en Linux, juntos. El `tcp-mss auto` de un pool se toma de la MTU de las interfaces del CGNAT: en 1500 da 1460.

En un equipo instalado **antes** de la 0.152.1 las interfaces siguen en 9000: la actualización escribió `mtu 9000` en cada una que no tenía `mtu`, y la placa sigue aceptando tramas de hasta 9000. Ahí, `no mtu` también lleva la interfaz a 1500.

## Direcciones

```
interface ens18
 ipv4 address 200.150.0.1/24
 ipv4 address 200.150.1.1/24
 ipv6 address 2001:db8::1/64
 exit
```

La palabra y el prefijo tienen que coincidir. `ipv6 address 10.0.0.1/24` se rechaza:

```
error: '10.0.0.1/24' is not an IPv6 prefix
```

No es un preciosismo. Sin la verificación, la dirección se aceptaría y se configuraría de forma equivocada, en silencio.

`no ipv4 address 200.150.1.1/24` elimina **esa** dirección. Las demás se quedan.


:::info
Al reemplazar una dirección por otra, la antigua sale antes de que entre la nueva: quien escribe las dos líneas en el mismo commit termina con una dirección, no con dos por un instante.

:::

## El nombre con el que el enrutamiento ve el puerto

El dataplane y el sistema operativo son dos mundos, y el enrutamiento vive en el segundo. Lo que conecta los dos es el par: la interfaz por la que el kernel, `ip` y FRR ven el puerto del dataplane.

**Toda interfaz entra en Linux con su propio nombre, por sí sola.** En cuanto una interfaz tiene un bloque `interface` - con cualquier cosa dentro, incluso solo un `no shutdown` -, aparece en `ip` y en el enrutamiento con el mismo nombre. Esto vale para placas, subinterfaces, loopbacks y placas agregadas con el equipo en funcionamiento. La `ens18` del dataplane es la `ens18` de FRR, y nada de esto aparece en la configuración: no hay ninguna línea que escribir.

El nombre es de Linux, y sus reglas se aplican: como máximo 15 caracteres y ninguna barra. Una placa que nadie nombró queda con la grafía que le da el dataplane, llena de barras, y esa Linux no la acepta. Para esa, y para quien quiere otro nombre, existe `linux-name`:

```
interface ens18
 linux-name vppwan0
 ipv4 address 200.150.0.1/24
 exit
```

Solo en ese caso aparece la línea en la configuración: cuando indica un nombre **diferente** del de la interfaz. Un nombre que Linux no acepta se rechaza indicando por qué:

```
error: linux-name: 'GigabitEthernet0/12/0' cannot be a Linux interface name (15 characters, no '/')
```

El par se **lee de vuelta** del equipo, no lo recuerda la VTY. Una configuración guardada por una versión anterior, que trae `linux-name ens18` en `interface ens18`, se lee normalmente, y el próximo `save` graba el archivo sin la línea. El equipo sigue exactamente igual.

El equipo no toma un nombre que Linux ya usa para otra cosa: pedir el par con un nombre ocupado falla, y haría caer el `commit` entero.

Un puerto cuyo nombre el enrutamiento no acepta - más de 16 caracteres, que es lo que recibe del dataplane una placa sin nombre - se rechaza en el `commit`, con el límite en la frase, antes de aplicar nada. En un equipo instalado esto no ocurre: toda placa reclamada conserva el nombre que tenía.

## Loopbacks

Una dirección en un puerto físico desaparece junto con el puerto. Un `router-id` de BGP, de OSPF o de IS-IS, un `update-source` de iBGP que sobrevive a la caída de un enlace, una dirección de origen de SNMP o de log que no cambia de lugar: todos quieren una interfaz que esté siempre activa.

**Nombrar un loopback es lo que lo crea**, de la misma forma que nombrar una subinterfaz: no hay nada más en el equipo desde donde crearlo.

```
configure
 interface loop0
  ipv4 address 10.99.0.1/32
  description router-id, que un puerto no se lleva consigo
  exit
 commit
```

El loopback entra en Linux y en el enrutamiento con su propio nombre, `loop0`, sin ninguna línea adicional, como cualquier otra interfaz.

`loop<n>` es la grafía del propio dataplane, y el número que usted escriba es el número que recibe: `loop3` produce `loop3`, y no el siguiente libre. Así la configuración y `show interface` no pueden discrepar sobre cómo se llama.

`no interface loop0` deshace todo el camino: dirección, par e interfaz. Un **puerto físico** sigue siendo rechazado: no es algo que una configuración pueda eliminar, y aceptar las palabras prometería lo que el `commit` no entrega.

## Bundles LACP (802.3ad)

Dos o más puertos se convierten en una sola interfaz, `ae<n>`, como en Junos. El bundle se crea cuando se nombra, igual que un loopback, y cada puerto entra con `member`:

```
configure
 interface ae0
  member ens19
  member ens20
  lacp rate fast
  ipv4 address 10.9.0.1/24
  exit
 commit
```

| Comando | Qué hace | Predeterminado |
|---------|----------|----------------|
| `member <puerto>` | un puerto en el bundle; se puede repetir |                |
| `lacp mode active\|passive` | quién inicia la negociación | `active`       |
| `lacp rate fast\|slow` | LACP cada segundo o cada 30 | `slow`         |
| `load-balance l2\|l23\|l34` | cómo se reparten los flujos entre los miembros | `l34`          |
| `no member <puerto>` | saca el puerto del bundle |                |
| `no interface ae0` | deshace el bundle; los puertos vuelven a ser puertos |                |

Las direcciones, la MTU, la descripción, OSPF, BGP y el lado del CGNAT van en `ae0`, nunca en los miembros. El `commit` rechaza un miembro que el equipo no tiene, un puerto en dos bundles y un miembro con configuración propia.

Cambiar `lacp mode`, `lacp rate` o `load-balance` rehace el bundle: el balanceo se fija en la creación, y el modo y el ritmo en la entrada de cada miembro. Por eso el cambio tira el bundle por un instante, y es mejor hacerlo en una ventana.

`show lacp` muestra el estado de cada miembro. Un miembro que está en el bundle aparece con `MUX-state: COLLECTING_DISTRIBUTING`.

Medido en un laboratorio contra un bond 802.3ad de Linux: los dos miembros negociaron y quedaron agregados en ambos lados, el tráfico atravesó el bundle y siguió atravesándolo con un miembro cortado.

## Subinterfaces y VLAN

**Cómo se crea una, de principio a fin.** Basta con escribir un nombre que el equipo todavía no tiene, en el formato `<padre>.<tag>`: eso es lo que crea la subinterfaz. El `?` lo indica en la línea debajo de la lista de interfaces: ofrece las que existen, y la nota de abajo dice qué nombres crean una nueva.

```
prisma> configure
prisma# [edit] interface ens18.100
prisma# [edit interface ens18.100] vlan 100
prisma# [edit interface ens18.100] ipv4 address 100.64.0.1/22
prisma# [edit interface ens18.100] exit
prisma# [edit] commit
commit complete
```

Después de eso la subinterfaz existe en el dataplane, lleva la tag y recibe su propio par en Linux con el mismo nombre, como toda interfaz que tiene un bloque; así Linux y el enrutamiento la ven con la dirección:

```
prisma> show interface
  Interface                  State  MTU    Address             Linux
  ens18                      up     1500   10.95.200.198/24    tap4096 ens18
  ens18.100                  up     1500   100.64.0.1/22       tap4096.100 ens18.100
```

**Si el padre está en** `**shutdown**`**, la subinterfaz queda esperando, y el** `**commit**` **pasa de todos modos.** VPP no levanta una subinterfaz mientras la interfaz padre está down, así que el equipo no lo intenta y lo dice:

```
warning: interface ens19.100: left shut down
    the dataplane will not bring a sub-interface up while its parent is down,
    and ens19 is shut down; 'no shutdown' on ens19 brings both up
```

Un `no shutdown` en el padre levanta las dos. Es una advertencia y no un rechazo: el `commit` aplica el resto normalmente.

**El padre debe existir.** `interface ens99.100` en un equipo sin `ens99` se rechaza en el `commit`, nombrando al padre:

```
error: interface ens99.100: its parent ens99 is not on this box
error: candidate did not pass validation; nothing applied
```

Una subinterfaz es una interfaz: todo lo de la tabla anterior vale dentro de ella.

```
interface ens18.100
 vlan 100
 ipv4 address 100.64.0.1/22
 exit

interface ens18.200
 vlan 200 inner 10
 exit
```

`no interface ens18.100` elimina la subinterfaz **y lo que estaba en ella**: una dirección dejada atrás describiría un puerto que no existe.


:::warning
**El número después del punto tiene que ser igual a la tag externa.** `ens18.7` con `vlan 100` se rechaza.

El equipo nombra la subinterfaz por la tag, y la configuración vigente se lee del equipo. Si los dos nombres pudieran divergir, usted leería un nombre y el equipo tendría otro.

:::


:::warning
**La tag va de 1 a 4094, en las dos posiciones.** `vlan 0`, `vlan 4095` y cualquier número por encima se rechazan en el momento en que usted lo escribe - y lo mismo vale para la tag interna, `vlan 100 inner 0` y `vlan 100 inner 4095`.

```
error: vlan 4095 out of range (1-4094): 802.1Q reserves 0 and 4095
```

El cero y el 4095 están reservados por el propio 802.1Q: cero es el marcado de prioridad, una trama que lleva el encabezado de tag y ninguna VLAN dentro, y 4095 no es asignable. Ningún switch del otro lado entrega ninguno de los dos, así que una subinterfaz construida sobre ellos parece configurada y no transporta nada.

:::

En una subinterfaz de dos tags (`inner`), las tramas que llegan con **más** de dos tags y el mismo par externo/interno también coinciden. Si su diseño depende de coincidir exactamente con dos, confirme el comportamiento antes de ponerlo en producción.

## VRF

```
interface ens18
 vrf 7
 ipv4 address 10.7.0.1/24
 exit
```

La tabla es numérica, igual que la de un pool: un número, un significado. La tabla 0 es la predeterminada y no necesita escribirse.

Mover una interfaz de VRF **se lleva las direcciones consigo**: se eliminan, la interfaz cambia de tabla, y las direcciones vuelven. Usted escribe solo la línea del `vrf`; el resto es consecuencia.

### La VRF de Linux: `vrf <nombre> table <n>`

```
vrf red table 10
interface ens18
 vrf 10
 ipv4 address 10.10.0.1/24
 exit
```

El enrutamiento llama a una VRF por su **nombre** —`router bgp 65000 vrf red`, el bloque `vrf red` con sus estáticas— y encuentra su tabla mediante un dispositivo VRF de Linux con ese nombre. `vrf red table 10` crea ese dispositivo en la tabla 10, la misma del `vrf 10` de las interfaces, y coloca en él el lado Linux de cada una de esas interfaces (el par de linux_cp, o el `linux-name`). Después del commit el BGP de la VRF se levanta sin ningún paso en Linux.

| Usted hace | En Linux |
|------------|----------|
| `vrf red table 10` | crea `red` en la tabla 10 y lo activa |
| `vrf 10` en una interfaz | su lado Linux entra en `red` |
| `no vrf` en una interfaz | su lado Linux sale de `red` |
| `no vrf red table` | elimina `red`; las interfaces salen con él |

Sin `table`, `vrf red` sigue siendo el bloque del enrutamiento, como antes.

Se rechazan: nombres que empiezan con `prisma-` (la `prisma-bp` es del propio equipo), el nombre `default`, las tablas 253 a 255 del propio Linux, una tabla que ya es de otra VRF y la tabla de la `prisma-bp` (128). La `prisma-bp` no aparece en `show running-config` y nunca se toca.

Las VRF aparecen en `show running-config` leídas de Linux, y el archivo guardado las vuelve a crear en el arranque, antes de que el enrutamiento las necesite.

### La gestión en una VRF propia

Para que la tabla principal lleve la default de los suscriptores y la gestión siga accesible por su propio gateway, la placa de gestión va a una VRF como cualquier interfaz, con sus rutas (las dos default, IPv4 e IPv6, y cualquier otra red de gestión) en el bloque `vrf` del enrutamiento, y `system` > `management` > `vrf` le indica al equipo que sus servicios viven ahí:

```
vrf mgmt table 100
interface enp6s18
  vrf 100
  exit
vrf mgmt
 ip route 0.0.0.0/0 10.40.200.1
 ip route 10.95.200.0/24 10.40.200.1
 ipv6 route ::/0 2804:4ed4:1000:6::1
 exit
router static
 0.0.0.0/0 next-hop 10.40.10.29
 exit
system
 management
  vrf mgmt
  exit
 exit
```

El `router static` es solo la tabla principal. La ruta estática de una VRF va en su bloque `vrf <nombre>`, cerrado con `exit` o `exit-vrf`.

Hágalo con `commit confirmed 10` y confirme con `commit` después de volver a entrar por la dirección de gestión, por IPv4 y por IPv6. Si la sesión se cae, nadie confirma y el rollback lo deshace todo: la VRF, las reglas y los ajustes del kernel. El equipo vuelve a responder por la tabla principal.

Hasta la 0.152.5, justo después de que la placa entraba en la VRF, su dirección IPv6 podía quedar sin respuesta durante más de 20 segundos, a veces hasta que el equipo hablara primero con el gateway: el dataplane descartaba las solicitudes de vecino que llegaban por multicast a una interfaz movida de tabla. A partir de la 0.152.6 responde ya a la primera, en esa VRF y en cualquier otra.

Hasta la 0.152.6, en un equipo ocupado, quitar la VRF (un `commit confirmed` que expiró, por ejemplo) y volver a ponerla podía dejar la default IPv6 de la VRF fuera de Linux hasta que FRR se reiniciara: zebra se quedaba con la ruta conectada de la placa inactiva. A partir de la 0.152.7 el equipo cambia la placa de VRF sin ninguna dirección en ella, espera a que zebra se ponga al día antes de devolver las direcciones y solo elimina una VRF después de que zebra la suelte. Vale para la gestión y para la VRF de cualquier cliente. Si zebra tarda más de 10 segundos, el commit continúa y lo advierte, y `show log` guarda lo que todavía tenía.

| Qué | Por dónde va |
|-----|--------------|
| SSH, API, SNMP y prisma-web entrando, IPv4 e IPv6 | por la VRF de gestión; la respuesta sigue a la conexión |
| DNS, NTP, descarga de `system upgrade`, traps, `system logging`, export del contenedor de log | por las rutas de la gestión, a menos que la tabla principal tenga una ruta específica para el destino |
| IPFIX por UDP generado por el dataplane | por la tabla principal: VPP lo envía por su cuenta. Un colector detrás de la gestión necesita una ruta específica en la principal, o el export por TCP |

Sin `system` > `management` > `vrf mgmt` la placa queda en la VRF y el equipo **no** responde por ella: los servicios escuchan en la tabla principal, y el snmpd y el sshd que escuchan en la dirección de gestión ni siquiera arrancan. Con él, el equipo activa `tcp_l3mdev_accept`/`udp_l3mdev_accept`, `ip_nonlocal_bind` (IPv4 e IPv6) y `keep_addr_on_down` de IPv6, y coloca dos reglas por familia para su propio tráfico - en el commit y en cada arranque, antes de que arranquen snmpd, sshd y la API. `show system management` muestra lo que está configurado y lo que está vigente en el host. `no vrf` dentro de `management` devuelve todo a la tabla principal. `commit check` rechaza una VRF que la configuración no crea, o sin ninguna interfaz dentro.

Para que el SSH responda solo en la gestión, IPv4 e IPv6, liste sus dos direcciones en `ssh listen-address` (desde la 0.152.5), como en `snmp` y en `api`: `ssh listen-address 10.40.200.166 2804:4ed4:1000:6::166`. Con la gestión en la VRF, el `commit` acepta esas direcciones aunque estén en la VRF, porque `ip_nonlocal_bind` permite que sshd escuche en ellas; sin el `vrf` en `management`, las rechaza.

## Estado administrativo

`shutdown` baja los dos lados: la interfaz que reenvía paquetes y el par que ve el enrutamiento. Bajar solo uno dejaría al protocolo de enrutamiento manteniendo una adyacencia sobre un puerto que dejó de reenviar: el vecino cree que el enlace está bien hasta que su propio temporizador expira, y el tráfico desaparece mientras tanto.

En un mismo commit, lo que baja se aplica **antes** de los demás cambios y lo que sube **después**: una interfaz que se está bajando deja de transportar tráfico antes de que la configuración cambie debajo de ella, y una que sube ya sube configurada.

## Anuncio de router IPv6

Una interfaz con dirección IPv6 **no anuncia** este equipo como router. Ese es el valor predeterminado, y es deliberado: en un puerto orientado al suscriptor, anunciar sería un segundo gateway para todos los que reciben un prefijo por DHCPv6-PD, junto al router que el suscriptor ya usa.

En los puertos en los que el anuncio tiene sentido —un uplink, un tránsito, un puerto orientado a otros routers— actívelo como excepción:

```
interface ens18
 no ipv6 nd suppress-ra
 exit
```

Activado, el anuncio sale cada 200 segundos con una validez de 600 segundos, más tres anuncios iniciales en cuanto se activa. `show running-config` muestra la línea solo en las interfaces donde se escribió, porque en las demás es el valor predeterminado.


:::info
`ipv6 nd suppress-ra` también se acepta. No cambia nada —dice en voz alta lo que ya vale— y sirve para dejar la intención escrita en la configuración, o para deshacer la excepción en una interfaz donde se activó.

:::

## El prefijo NAT64 en los anuncios (PREF64, 464XLAT)

En un acceso solo IPv6 atendido por un pool NAT64, el suscriptor todavía tiene programas que solo hablan IPv4: un juego, un cliente VPN, una aplicación que abre una conexión a una dirección IPv4 escrita literalmente. El 464XLAT (RFC 6877) lo resuelve con un **CLAT** del lado del suscriptor —en su router o en el celular—, que traduce ese IPv4 a IPv6 dirigido al prefijo NAT64, y el pool NAT64 de este equipo lo traduce de vuelta. Para eso el CLAT necesita saber **cuál** es el prefijo, y en una red con más de un pool, o con un prefijo que no es `64:ff9b::/96`, no tiene forma de adivinarlo.

El equipo se lo dice, en los propios anuncios de router —la opción PREF64 de la RFC 8781—, en la interfaz orientada a los suscriptores:

```
interface ens19
 no ipv6 nd suppress-ra
 ipv6 nd nat64 64:ff9b:1::/48
 exit
```

El prefijo es el `nat64-prefix` de un pool NAT64 de este equipo (vea [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA)). Sin prefijo, `ipv6 nd nat64` anuncia `64:ff9b::/96`; la línea se puede repetir, un prefijo en cada una. Los tamaños aceptados son los de la RFC 6052: `/32`, `/40`, `/48`, `/56`, `/64` o `/96`.

El `commit check` rechaza dos cosas, cada una indicando qué hacer:

* `ipv6 nd nat64` en una interfaz que **no anuncia**: sin `no ipv6 nd suppress-ra` no hay anuncio que lleve el prefijo;
* un prefijo que **ningún pool NAT64 traduce**: el CLAT que aprendiera ese prefijo enviaría el IPv4 del suscriptor a un destino que el equipo descarta. El rechazo lista los prefijos que usan los pools de este equipo.

**Quien envía ese anuncio es FRR, no el dataplane.** El anuncio de VPP no tiene la opción PREF64; el de FRR sí, y sale por el mismo puerto. En una interfaz con `ipv6 nd nat64` el equipo pasa el anuncio a FRR y desactiva el del dataplane, para que el suscriptor escuche a **un solo** router, con el prefijo en cada anuncio. Al quitar el `ipv6 nd nat64`, el anuncio vuelve al dataplane. Usted no necesita escribir nada en FRR: las líneas son del equipo, y `show running-config` las muestra dentro del bloque de la interfaz y nunca en la parte de enrutamiento.


:::info
El CLAT también puede aprender el prefijo preguntando `ipv4only.arpa` a un resolvedor DNS64 (RFC 7050). Este equipo no es el resolvedor del suscriptor: el DNS64 que usted opera debe sintetizar con **el mismo** prefijo que anuncia el equipo, o los dos métodos discrepan.

:::

## Un par de equipos y la dirección que pasa de uno al otro

Dos equipos, una dirección de servicio, y el suscriptor apuntando a ella en lugar de a cualquiera de los dos. Es el VRRP de la RFC 5798, y aquí se ejecuta **en la interfaz del dataplane**, que es donde llegan los paquetes:

```
interface ens18
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

En el otro equipo, lo mismo con su dirección y una prioridad menor:

```
interface ens18
 ipv4 address 100.64.3.251/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 100
 vrrp 1 track ens19 decrement 150
 exit
```

**El** `**vrid**` **tiene que ser el mismo en los dos equipos**, y distinto del de cualquier otro par en el mismo dominio de broadcast: es el que indica de qué elección habla cada anuncio, y de él sale la MAC virtual (`00:00:5e:00:01:<vrid>`).

**La MAC virtual es lo que hace que el suscriptor no necesite saber nada.** Quien asume la dirección asume también esa MAC y envía un ARP gratuito. El CPE que tenía esa dirección en la tabla ARP sigue enviando a la misma MAC, y la trama pasa a llegar al otro equipo, sin que nadie limpie la caché en ningún lugar.

`**priority**` **es su afirmación de quién debe quedarse con el servicio.** Gana el mayor, y el equipo que vuelve de un mantenimiento **recupera** la dirección, porque eso es lo que dice la configuración. 255 se rechaza: la RFC reserva ese valor para el dueño de la dirección, y un equipo que comparte la dirección con otro no lo es.

`**track**` **es la falla que el resto no cubre.** Un equipo con el uplink arrancado sigue vivo y sigue anunciando: retiene la dirección del gateway y no alcanza nada. Con `track`, su prioridad baja por el decremento y el par asume. Elija el decremento de forma que la caída lleve la prioridad **por debajo** de la del otro: 200 con decremento 150 queda en 50, contra los 100 del par.

**El mismo** `**vrid**` **sirve para IPv6** (a partir de la versión 0.153.1). Una dirección IPv6 en el mismo `vrrp <vrid>` crea el router virtual IPv6 (VRRPv3, RFC 5798) junto al IPv4, con la misma prioridad, el mismo `track` y el mismo preempt; así el gateway IPv6 de los suscriptores cambia de equipo junto con el lado IPv4:

```
interface ens18
 ipv6 address 2001:db8:1200:ab00::2/56
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 address fe80::1
 vrrp 1 address 2001:db8:1200:ab00::1
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

El router IPv6 necesita **una** dirección link-local (`fe80::...`), que es la primera que anuncia y el gateway al que apuntan los suscriptores (RFC 5798, sección 5.2.9); el `commit check` rechaza un router IPv6 sin ella, y una segunda link-local se rechaza en el acto. La MAC virtual de IPv6 es `00:00:5e:00:02:<vrid>`, y quien asume envía un Neighbor Advertisement no solicitado. `show cgnat ha` muestra los dos routers del mismo `vrid`, el IPv6 marcado como `ipv6`.

**El** `**track**` **vale para la familia del router.** El dataplane solo considera "activa" una interfaz rastreada que tiene una dirección de la familia del router. El equipo activa IPv6 (solo la link-local, sin anuncio de router) en la interfaz que rastrea un router IPv6; para el router IPv4, la interfaz rastreada necesita una dirección IPv4, y el `commit check` lo advierte cuando no la tiene: un lado de suscriptores solo IPv6 rastreado por el router IPv4 del uplink nunca se vería activo.

**Lo que se rechaza en el momento en que usted lo escribe:** una dirección con máscara (`100.64.3.254/22`: la dirección virtual es una de las direcciones de la subred de la interfaz, no una subred), la prioridad 255, y una interfaz que se rastrea a sí misma: un puerto que cayó ya dejó de anunciar, y lo que conviene rastrear es el **otro** lado.

**Cada lado necesita el suyo.** Una dirección que se mueve en el lado del suscriptor y nada en el lado del uplink es un failover que el tráfico de vuelta no acompaña: configure un router virtual en cada lado, con un `vrid` diferente.

### Lo que la dirección virtual no hace

**No responde ping.** Quien no es dueño de la dirección no responde ICMP por ella, que es el comportamiento predeterminado de la RFC 5798 (sección 6.4.3), y es lo que hace este equipo. El monitoreo debe hacer ping a la dirección **propia** de cada equipo, que es la que dice si ese equipo está vivo; la dirección virtual solo dice cuál de los dos la tiene. Reenviar **a través** de ella funciona normalmente, y para eso existe.

**No sincroniza ninguna sesión.** Las conexiones en curso caen en el failover, como caen en cualquier NAT. Lo que no cambia es la dirección pública del suscriptor, porque en modo determinístico el mapeo es función de la configuración; la página de alta disponibilidad explica qué preserva esto y qué no.

### Cómo ver el rol de cada equipo

```
prisma> show cgnat ha
  this box
    mapping fingerprint  e3b0c44298fc1c14
    pools                residencial

  role
    ens18 vrrp 1   master, holding the address
      address(es)        100.64.3.254
      virtual mac        00:00:5e:00:01:01
      priority           200
```

Cuando algún puerto rastreado está caído, la línea de la prioridad dice las dos cosas —lo que se está anunciando y lo que se configuró— porque un equipo que anuncia menos de lo configurado es un equipo con un cable desconectado, y no un problema de VRRP.

## OSPF e IS-IS en la interfaz

Los protocolos de enrutamiento entran en el puerto mediante declaraciones dentro del propio bloque `interface`, que es donde FRR las guarda. Estas van al enrutamiento palabra por palabra; las de la tabla del principio de la página las aplica el equipo en el dataplane.

```
interface ens19
 ip ospf area 0
 ip ospf network point-to-point
 ip ospf cost 10
 ip router isis CORE
 isis network point-to-point
 isis metric 100
 exit
```

### OSPF (`ip ospf ...`)

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `ip ospf area` | `<área>`  | ejecuta OSPF en esta interfaz, en esta área |
| `ip ospf cost` | `<1-65535>` | el costo de esta interfaz |
| `ip ospf network` | `point-to-point\|broadcast` | `point-to-point` evita la elección de DR, que es lo que quiere un enlace de tránsito; `broadcast` es el predeterminado en Ethernet |
| `ip ospf hello-interval` | `<1-65535>` | segundos entre hellos |
| `ip ospf dead-interval` | `<1-65535>` | segundos hasta que el vecino se da por caído |
| `ip ospf priority` | `<0-255>` | la prioridad de este equipo en la elección de DR |
| `ip ospf authentication` | `message-digest\|null` | MD5, o ninguna, sobrescribiendo la del área |
| `ip ospf authentication-key` | `<clave>` | la clave en texto; nunca se vuelve a mostrar |
| `ip ospf message-digest-key` | `<1-255> md5 <clave>` | una clave MD5; nunca se vuelve a mostrar |
| `ip ospf passive` |           | ejecuta OSPF aquí y no envía hello |
| `ip ospf bfd` |           | supervisa la adyacencia con BFD |
| `ip ospf mtu-ignore` |           | no compara la MTU en el intercambio de base: la salida cuando los dos lados discrepan y no se pueden igualar |

### OSPFv3 (`ipv6 ospf6 ...`)

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `ipv6 ospf6 area` | `<área>`  | ejecuta OSPFv3 en esta interfaz, en esta área; es la **única** forma de poner una interfaz en OSPFv3 |
| `ipv6 ospf6 cost` | `<1-65535>` | el costo de esta interfaz |
| `ipv6 ospf6 network` | `point-to-point\|broadcast` | como en OSPF |
| `ipv6 ospf6 passive` |           | ejecuta OSPFv3 aquí y no envía hello |
| `ipv6 ospf6 bfd` |           | supervisa la adyacencia con BFD |
| `ipv6 ospf6 mtu-ignore` |           | no compara la MTU |

`ipv6 ospf6 hello-interval`, `dead-interval` y `priority` también se aceptan, en la grafía de FRR, pero el `?` no los ofrece: escritos con el valor predeterminado, FRR no los guarda.

### IS-IS

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `ip router isis` | `<instancia>` | ejecuta IS-IS para IPv4 en esta interfaz |
| `ipv6 router isis` | `<instancia>` | ejecuta IS-IS para IPv6 en esta interfaz, en la topología que tenga la instancia |
| `isis circuit-type` | `level-1\|level-2-only\|level-1-2` | en qué niveles participa esta interfaz |
| `isis network point-to-point` |           | sin elección de DIS ni pseudonodo; configurado de un solo lado, la adyacencia no se levanta |
| `isis metric` | `<0-16777215> [level-1\|level-2]` | la métrica de esta interfaz; por encima de 63 exige `metric-style wide` en la instancia |
| `isis passive` |           | anuncia la interfaz y no envía hello |
| `isis hello-interval` | `<1-600>` | segundos entre hellos |
| `isis hello-multiplier` | `<2-100>` | cuántos hellos perdidos hasta que el vecino cae |
| `isis password` | `clear\|md5 <contraseña>` | autenticación de esta interfaz; nunca se vuelve a mostrar |
| `isis bfd` |           | supervisa la adyacencia con BFD |

Las claves y contraseñas aparecen como `<set, not shown>` en `show this`, `show configuration` y `show running-config`. La configuración guardada y las revisiones de rollback guardan el valor real; de lo contrario, un rollback no podría restaurarlo.

El bloque del protocolo (`router ospf`, `router ospf6`, `router isis`) está en [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3).

## Cómo eliminar un ajuste: `no`

`no ?` dentro de la interfaz lista lo que se puede quitar, y después de la palabra se sigue ofreciendo el argumento - `no urpf ?` muestra `strict`, `loose` y `off`.

Quitar un ajuste **no es lo mismo que ajustarlo al valor predeterminado**: la configuración deja de decir eso, y la interfaz queda con lo que el equipo ya tenía. `mac-address aa:bb:cc:dd:ee:ff` seguido de `no mac-address` devuelve la interfaz a su propia MAC, que es lo que quiere quien escribió y se arrepintió.

La interfaz lleva dos tipos de línea: las de este equipo - `description`, `shutdown`, `mtu`, `urpf`, `vlan`, `vrf`, `mac-address`, `linux-name`, direcciones, `ipv6 nd suppress-ra`, `ipv6 nd nat64`, `cgnat`, `vrrp`, `member`, `lacp`, `load-balance` - y las de los protocolos de enrutamiento - `ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`. `no` funciona en ambos, y en cada caso va a quien es dueño del ajuste.

### `no vlan` no existe, y el equipo dice qué hacer en su lugar

Una subinterfaz **es** su tag: el nombre `ens18.700` y la VLAN 700 son la misma información, y el equipo se niega a crear una en la que los dos discrepen. No existe, por lo tanto, una subinterfaz sin tag; lo que se quiere es eliminar la subinterfaz:

Escribir `no vlan 700` dentro de la subinterfaz se rechaza, y el rechazo dice qué hacer en su lugar:

```
error: vlan cannot be removed
    a sub-interface is named after its tag, so one without a tag is not
    something the dataplane can hold
    remove the sub-interface itself: no interface <name>
```

El rechazo viene en líneas propias, dentro del ancho de la consola: el veredicto en la primera, el motivo en el medio, y **lo que hay que escribir en su lugar en la última**. Durante un tiempo salía en una sola frase, más ancha que la pantalla, y la terminal la cortaba donde cayera, lo que ponía justamente el consejo final en cualquier lugar. Ahora la última línea es siempre la última línea.

`no vlan`, sin número, recibe el mismo rechazo: no hay tag que quitar de ninguna forma.

`no interface ens18.700`, en el nivel superior, elimina la subinterfaz y todo lo que estaba en ella.

## Una interfaz en detalle

`show interface` las lista todas. Al nombrar una, la respuesta cambia de tabla a informe:

```
prisma> show interface ens18
  ens18
    state       up
    mtu         9000
    addresses   200.150.0.1/24
    linux       tap4096 vppwan0
    vrf         0 (default)
    mac         02:fe:c1:ed:07:aa
    urpf        off
    cgnat       not attached
    counters
      rx bytes                     120
      punt                           2
```

Los contadores son la razón de que exista el informe. *Activo* y *activo y descartando el cuatro por ciento de lo que llega* son idénticos en una tabla, y es el segundo el que genera el ticket.

**En un equipo que forma un par, el informe dice el rol del puerto**, justo debajo del CGNAT:

```
    cgnat       in2out
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

Es lo primero que importa de un puerto cuando existe un peer: si los suscriptores están en este equipo o en el otro. `show cgnat ha` lo responde para el equipo entero; aquí es para el puerto que usted ya está mirando.

Lo que aparece es el **rol vigente**, leído del dataplane; lo que se *configuró* está en `show this` y en el archivo guardado. Un puerto sin router virtual no muestra ninguna línea: la mayoría no lo tiene, y una advertencia que aparece siempre es una advertencia que nadie lee.

El nombre exacto tiene prioridad: pedir `ens18` en un equipo que también tiene `ens18.100` trae el informe de la interfaz, no la lista de las dos.

## La placa entregada al dataplane no es una de estas

`interface <nombre>` configura una interfaz que el dataplane **ya tiene**. Qué placa física recibe es otra decisión, un nivel más abajo, y vive en la parte superior del modo de configuración:

```
prisma> configure
prisma# [edit] system
prisma# [edit system] dataplane
prisma# [edit system / dataplane] interface enp2s0
prisma# [edit system / dataplane] commit
```

La diferencia es práctica. En un equipo que todavía no recibió ninguna placa, el dataplane no tiene ninguna interfaz, así que no hay `interface <nombre>` en la que entrar, y `show interface` está vacío justamente cuando usted necesita elegir. Por eso el nombre aquí es el que **Linux** le da a la placa, y quien lista los candidatos es el propio `?`.

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `interface`, en `system` > `dataplane` | `<placa>` | entrega esta placa al dataplane en su próximo inicio |
| `no interface`, en el mismo nivel |           | el equipo deja de tomar placas: estado seguro, alcanzable por red |

El `commit` guarda la elección, recalcula el ajuste de arranque y **no reinicia el dataplane**: la entrega tira todas las sesiones del equipo, incluida la suya, así que la ventana la elige usted. Un nombre que Linux no tiene se rechaza en el momento en que usted lo escribe, con la lista de las placas que sí tiene; el caso, con lo que imprime el equipo, está en [Primeros pasos](/s/wiki-es/doc/primeros-pasos-1atRsTUyKZ).

La placa que lleva la ruta default **no** se rechaza: un equipo nuevo se configura en la consola, y es el ajuste de arranque el que, en el arranque que entrega la placa, lee el camino de vuelta por ella y rechaza la placa si no logra leerlo.

`system dataplane restart`, en el modo operativo, es otra cosa: reinicia el dataplane que ya existe, sin cambiar de placa.

## Placa agregada con el equipo en funcionamiento

El dataplane solo toma placas por DPDK cuando **se inicia**. Para una placa agregada en el hipervisor con el equipo en funcionamiento, el equipo no espera al próximo arranque, y tampoco reinicia el dataplane por sí solo: un reinicio tira el CGNAT entero, con el bloque de puertos y las sesiones de todos los suscriptores.

En su lugar, **conecta la placa al dataplane en funcionamiento mediante AF_XDP**, que lee la placa por el driver del kernel. La placa reenvía en el acto, y nadie pierde sesiones.

**Lo que el equipo hace por sí solo**, segundos después de que aparece la placa:

* verifica si la regla del arranque tomaría esa placa. La placa reservada para gestión, el `PRISMA_DATAPLANE_IF` y la placa que tiene dirección o la ruta default siguen siendo de Linux, exactamente como en el arranque;
* conecta la placa con el **mismo nombre** que le dio Linux (`ens20`, por ejemplo) y con su propia MAC.

**Lo que usted hace** es configurar la placa como cualquier otra: `interface ens20`, la dirección y `no shutdown`. Entra en Linux y en el enrutamiento con su propio nombre, como las demás. `show interface` advierte, debajo del conteo, que está en AF_XDP y qué la lleva a DPDK.

**Pasar la placa a DPDK** es una decisión suya, en la ventana que usted elija:

```
prisma> system dataplane restart
```

**Ejecute** `**save**` **antes.** Lo que vuelve después del reinicio es la configuración **guardada**, y el comando advierte si hay algo confirmado y no guardado. Dice lo que cuesta y reinicia el dataplane. La placa vuelve por DPDK, con el mismo nombre y la misma configuración, junto con las demás placas de datos. Medido en una VM: el reenvío se detuvo durante 18 segundos y volvió sin pérdidas. Un reinicio del equipo hace lo mismo.


:::info
**El costo de AF_XDP es velocidad, no corrección.** En una placa virtio con una sola cola, el propio kernel advierte que el envío queda en un modo más lento. Para una placa que va a transportar suscriptores en volumen, déjela en AF_XDP solo hasta la próxima ventana.

:::

# Cómo verificarlo

* `show interface <nombre>` trae el informe, con estado, direcciones, VRF, MAC y contadores;
* `show interface terse` trae una línea por interfaz con **dos** columnas de estado: `Admin`, que es lo que alguien configuró, y `Link`, que es lo que está haciendo el cable: up/down es un problema de enlace, down/down es `shutdown`;
* `show interface description` muestra la descripción de todas, junto a esas dos columnas;
* `show interface utilization <nombre>` muestrea los contadores y muestra cuánto está pasando ahora, en bit/s;
* `monitor interface <nombre>` muestra lo mismo en vivo, en un gráfico por sentido redibujado cada segundo hasta `q`;
* `show running-config` muestra el bloque de la interfaz tal como el equipo lo lee de vuelta: lo que está ahí es lo que está vigente, no lo que se escribió.

# Vea también

* [Configuración de CGNAT](/s/wiki-es/doc/configuracion-de-cgnat-WTgNXqphc5) — el nivel `cgnat`, y lo que significa el lado de cada interfaz
* [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3) — los bloques `router ospf`, `router ospf6` y `router isis`
* [Alta disponibilidad](/s/wiki-es/doc/alta-disponibilidad-bjNhRjSh49) — el par de equipos y lo que preserva el VRRP
* [uRPF y orígenes falsificados](/s/wiki-es/doc/urpf-y-origenes-falsificados-E8NumaaxV4) — cuándo usar `strict` y cuándo `loose`
* [Multi-tenant con VRF](/s/wiki-es/doc/multi-tenant-con-vrf-VtS5k4vFue) — cuando cada cliente tiene su propia tabla
