# Configuración de CGNAT

La configuración de CGNAT está dentro de una jerarquía: se entra en `nat`, luego en `cgnat`, y es ahí donde están los pools, el logging y los timeouts. El lado de cada interfaz (`inside` u `outside`) va en el bloque de la propia interfaz. Esta página dice qué existe en cada nivel y cómo moverse entre ellos.

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] pool residencial
prisma# [edit nat / cgnat / pool residencial] prefix-inside 100.64.0.0/22
prisma# [edit nat / cgnat / pool residencial] exit
prisma# [edit nat / cgnat] top
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
```

El prompt siempre dice en qué nivel está usted. `?` y TAB ofrecen solo lo que vale en ese nivel: en `nat` aparece `cgnat`; en `cgnat` aparecen `pool`, `interface`, `logging`, `timeout` y el resto de esta página.

## Moverse entre los niveles

| Comando | Adónde va a parar |
|---------|-------------------|
| `exit`  | un nivel arriba: del pool a `cgnat`, de `cgnat` a `nat`, de `nat` a la raíz |
| `up`    | un nivel arriba, igual que `exit` |
| `up <n>` | `n` niveles arriba de una vez |
| `top` o `end` | la raíz del modo de configuración, desde cualquier nivel |

`commit` (con todas sus variantes) y `show` funcionan en cualquier nivel, incluso dentro de un pool. No hace falta salir para aplicar, y después del `commit` usted sigue en el nivel en que estaba. `rollback` funciona en `nat` y en `cgnat`; dentro de un pool, ejecute `exit` antes. `ping`, `traceroute` y `mtr` también responden en cualquier nivel.

En `nat` existen dos niveles: `cgnat`, para los pools con lado interno IPv4, y `nat64`, para los pools NAT64 (lado interno IPv6). Cualquier otra cosa se rechaza:

```
prisma# [edit nat] pool x
error: under 'nat' there is 'cgnat' and 'nat64'
    each enters its level; 'exit' leaves 'nat'
```


:::warning
**La forma de una sola línea ya no existe.** `nat cgnat pool residencial`, `nat cgnat interface ens18 inside` y la grafía más antigua `cgnat pool ...` se rechazan en el prompt y también en un archivo guardado:

```
prisma# [edit] nat cgnat pool residencial
error: the one-line CGNAT form is gone
    enter 'nat', then 'cgnat', and type the rest there: 'pool <name>',
    'interface <name> inside', 'logging ...'
```

Un equipo cuyo archivo guardado todavía tiene esa forma rechaza el archivo entero en el arranque. Después de actualizar, y **antes de reiniciar**, ejecute `save`: graba lo que está corriendo ya en la forma nueva. [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) lo explica.

:::

## Qué hay en `cgnat`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `pool <nombre>` | `<word>`  | crea o edita un pool (entra en el pool) |
| `interface <if> inside\|outside` |           | habilita CGNAT en una interfaz, del lado interno o externo |
| `block-holddown <s>` | `<1-4294967295>` | segundos que un bloque queda reservado después de quedar ocioso |
| `session-table buckets <n> memory <MB>` |           | cuántos buckets tiene la tabla de sesiones; el número de `memory` no dimensiona nada. Vale solo antes del primer pool: con la tabla ya creada, el dataplane lo rechaza |
| `memory` | `<GB>`    | la memoria dada a las sesiones del CGNAT, en GB enteros; vale a partir del próximo reinicio, y `show cgnat memory` dice si ya está vigente |
| `timeout <tipo> <segundos>` |           | timeouts globales, en la tabla de abajo |
| `logging ...` |           | exportación de los eventos de bloque (IPFIX o syslog), en la tabla de abajo |
| `identity ...` |           | de dónde lee el equipo quién estaba en una dirección privada, para ponerlo en el registro; tabla de abajo |
| `no pool <nombre>` |           | elimina un pool |
| `no interface <nombre>` |           | deshabilita CGNAT en la interfaz (la forma antigua; hoy es `no cgnat` en el bloque de la interfaz) |

Los campos de dentro de un pool están en la página [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA).

## Timeouts globales

`timeout <tipo> <segundos>`, dentro de `cgnat`:

| Tipo | Qué expira |
|------|------------|
| `udp` | sesiones UDP |
| `icmp` | sesiones ICMP |
| `tcp-transitory` | TCP en transición |
| `tcp-established` | TCP establecido |

Un pool puede sobrescribir esos valores (vea la página de configuración de pool). Un pool sin timeout propio hereda el global.

Un nombre de timeout que no es uno de los cuatro se rechaza en el prompt, con la lista de los que existen. Un valor por debajo del piso que establece la RFC de ese protocolo se **aplica** y se advierte en el `commit`: la decisión es suya, y la advertencia existe para que se tome con conocimiento. Los pisos, y lo que se rompe por debajo de cada uno, están en [Timeouts y los pisos de las RFC](/s/wiki-es/doc/timeouts-y-los-pisos-de-las-rfc-IgDiScQthb).

## Logging IPFIX

Dentro de `cgnat`:

| Comando | Qué hace |
|---------|----------|
| `logging udp <colector> src <origen> [port <n>]` | adónde va la exportación IPFIX por UDP |
| `logging enable` | habilita la exportación por UDP |
| `logging disable` | deshabilita la exportación por UDP |
| `logging udp disable` | borra el destino UDP |
| `logging tcp <colector> [port <n>]` | exporta IPFIX por TCP |
| `logging tcp disable` | detiene la exportación por TCP |
| `logging syslog <colector> enterprise <n> [port <n>] [facility <0-23>] [severity <0-7>]` | exporta los mismos eventos como líneas syslog RFC 5424, por TCP |
| `logging syslog disable` | detiene la exportación syslog |

`src` no es opcional, y el destino tiene que existir antes de `enable`: sin él, `commit check` rechaza y no se aplica nada. Sin `port`, el IPFIX va al 4739 y el syslog al 601.

TCP existe porque la pregunta que importa no es "¿el colector lo recibió?", sino **"¿qué pasa cuando el colector se cae?"**. Con UDP, la respuesta es silencio. Por TCP — IPFIX o syslog — lo que el colector no recibió queda guardado en el equipo y se reenvía, en orden, cuando vuelve.

El syslog existe para el colector que no habla IPFIX, que es la mayoría. El `enterprise` es obligatorio y es **su** número de empresa privada en la IANA: la RFC 5424 reserva los nombres sin ese sufijo para lo que la propia IANA registró, y toda línea que escribe el equipo lo lleva. Sin él el `commit` rechaza la línea.

UDP, TCP y syslog son independientes: se puede exportar por más de uno al mismo tiempo.

## Quién estaba en la dirección: `identity`

Una orden judicial nombra a una persona; el registro nombra una dirección privada en un instante. `identity` hace que el registro lleve la identidad del suscriptor, leída en el instante de la asignación, para que nadie tenga que cruzar dos bases bajo presión.

| Comando | De dónde viene la identidad |
|---------|-----------------------------|
| `identity file <ruta>` | un archivo con una línea por suscriptor: `<dirección-interna> <identidad>` |
| `identity dhcp-leases <ruta>` | el archivo de leases del servidor DHCP (formato ISC dhcpd); solo cuenta el lease activo |
| `identity radius secret <secreto> [port <n>]` | el accounting RADIUS que el BNG ya envía; sin `port`, 1813 |
| `identity radius disable` | deja de escuchar el accounting |
| `identity none` | ninguna tabla: todo registro dice `unknown` |

El equipo **no** es un servidor RADIUS: solo escucha el accounting, verifica cada paquete con el secreto compartido y descarta sin respuesta lo que no coincide. Sin `secret` el `commit` rechaza la línea.

La ruta es configuración; el contenido del archivo no lo es, y **ninguna pantalla muestra una identidad**. `show cgnat identity` dice cuántas tiene el equipo y cuándo las leyó, y el secreto del RADIUS sale como `<set, not shown>` en el `show running-config`.

Un pool dinámico, o determinístico con `reserve`, en un equipo que no exporta nada recibe una advertencia en el `commit check`: sin el registro, no hay forma de decir quién usaba un puerto.

## Cómo graba el equipo

`show running-config`, `show configuration` y el archivo que graba `save` traen el bloque entero, tal como se escribe:

```
! cgnat
nat
 cgnat
  pool residencial
   prefix-inside 100.64.0.0/22
   prefix-outside 203.0.113.0/28
   block-size 512
   policy interleaved
   mode deterministic
   exit
  exit
 exit
interface ens18
 cgnat inside
 exit
interface ens19
 cgnat outside
 exit
```

Dentro de `nat` / `cgnat` están los pools y después los ajustes globales. El lado de cada interfaz aparece en su bloque, junto con la dirección, la descripción y el MTU: quien lee un puerto ve ahí mismo que los suscriptores llegan por él.

## Habilitar en una interfaz

El lado del CGNAT es una propiedad de la interfaz y se escribe en su bloque:

```
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
prisma# [edit interface ens18] exit
prisma# [edit] interface ens19
prisma# [edit interface ens19] cgnat outside
prisma# [edit interface ens19] exit
```

`inside` es el puerto por donde llegan los suscriptores; `outside`, el puerto por donde sale el tráfico traducido. `no cgnat`, dentro del bloque, quita la interfaz del CGNAT.

La forma antigua, `interface ens18 inside` dentro de `nat` / `cgnat`, sigue aceptándose, para que los procedimientos y scripts ya escritos sigan funcionando. El equipo advierte dónde pasa a aparecer la línea:

```
prisma# [edit nat / cgnat] interface ens18 inside
  note: kept as 'cgnat inside' in the block of interface ens18
```

Un archivo guardado por una versión anterior, con las líneas dentro de `cgnat`, se lee normalmente, y el próximo `save` graba cada una en el bloque de su interfaz.


:::info
**La placa que va al dataplane no se elige aquí.** Se nombra en `system` > `dataplane` > `interface <placa>`, y `show hardware` marca cuál de ellas lleva la ruta por defecto — el camino por el que se gestiona el equipo. Nombrar esa placa se acepta, y el `commit` dice si la entrega tira la sesión en la que usted está; en un equipo de una sola placa, la dirección de gestión se va junto al par que mantiene el dataplane. El paso a paso está en [Primeros pasos](/s/wiki-es/doc/primeros-pasos-1atRsTUyKZ).

:::

# Ver también

* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA) — los campos de dentro del pool
* [Configurar el CGNAT paso a paso](/s/wiki-es/doc/configurar-el-cgnat-paso-a-paso-v0ecpO8lbt) — de cero al primer suscriptor traducido
* [commit, rollback y save](/s/wiki-es/doc/commit-rollback-y-save-6sAWJkXNO3) — cómo aplicar lo que se configuró aquí
