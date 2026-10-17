# Configuración de BGP

Cómo configurar sesiones BGP con `prismash`: vecinos, peer groups, familias de direcciones y lo que el equipo rechaza antes de enviarlo al enrutamiento. Para quien está levantando un tránsito, un peering o anunciando los prefijos públicos del CGNAT.

Se entra en BGP desde el modo de configuración:

```
prisma> configure
prisma# [edit] router bgp 65000
prisma# [edit router bgp 65000]
```

`?` o TAB listan lo que vale allí dentro. Dentro de una familia de direcciones, listan otro conjunto: son gramáticas diferentes, porque la misma palabra significa cosas diferentes en los dos lugares.

# Una sesión de principio a fin

```
router bgp 65000
 bgp router-id 10.0.0.1
 neighbor 200.150.0.2 remote-as 65001
 neighbor 200.150.0.2 description transito-primario
 address-family ipv4 unicast
  neighbor 200.150.0.2 activate
  network 200.150.8.0/22
 exit-address-family
 exit
```

`remote-as` es lo que crea la sesión: sin él FRR rechaza cualquier otra sentencia de ese vecino, y el equipo la rechaza antes, indicando qué vecino falta.

`activate` es lo que hace que la sesión **transporte** esa familia. Una sesión que levanta y no transporta nada es una sesión Established y muda.

# Lo que entiende el bloque `router bgp`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `bgp router-id` | `<A.B.C.D>` | el identificador de este equipo en BGP |
| `bgp log-neighbor-changes` |           | registra todo cambio de estado de sesión |
| `bgp graceful-shutdown` |           | drena el tráfico antes de un mantenimiento (RFC 8326; sección propia más abajo) |
| `bgp default ipv4-unicast` |           | activa IPv4 unicast en todo vecino sin necesidad de `activate` |
| `bgp bestpath as-path multipath-relax` |           | trata como iguales caminos de la misma longitud que vienen de AS diferentes |
| `neighbor <vecino> ...` |           | una sesión; la tabla siguiente |
| `address-family ipv4 unicast` o `ipv6 unicast` |           | entra en la familia de direcciones; se sale con `exit-address-family` o `exit` |
| `network` | `<prefijo>` | un prefijo que origina este equipo; solo se anuncia si está en la tabla de rutas |
| `redistribute` | `connected`, `static`, `ospf` o `isis` | inyecta en BGP rutas de otro origen |

En el nivel de `address-family` valen `neighbor` (la lista por familia, abajo), `network`, `redistribute` y las salidas `exit-address-family` y `exit`.

# Lo que entiende un vecino

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `remote-as` | `<1-4294967295>`, `internal` o `external` | el AS del otro lado; esto es lo que crea la sesión. `internal` es su propio AS, `external` cualquier otro |
| `description` | `<texto>` | para qué sirve la sesión, para quien la lea después |
| `password` | `<texto>` | contraseña MD5 de la sesión; nunca se vuelve a imprimir |
| `update-source` | `<dirección>` o `<interfaz>` | desde dónde sale la sesión |
| `ebgp-multihop` | `<1-255>` | saltos hasta un vecino que no está en una red conectada |
| `timers` | `<keepalive> <hold>` | temporizadores de la sesión |
| `maximum-prefix` | `<n> [<pct>] [restart <min>]` | tira la sesión por encima de ese número de prefijos |
| `next-hop-self` |           | anuncia este equipo como próximo salto |
| `bfd`   | `[profile <nombre>]` | sigue la sesión por BFD, para que la falla sea de milisegundos y no de minutos; con `profile`, con los tiempos de ese perfil (vea [BFD](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3), sección BFD) |
| `shutdown` |           | mantiene la sesión caída, conservando la configuración |
| `peer-group` | `<nombre>` | coloca el vecino en un peer group |
| `capability extended-nexthop` |           | rutas IPv4 sobre una sesión IPv6; es lo que usa una sesión sin dirección en el enlace |

Dentro de `address-family`, el mismo vecino entiende `activate`, `route-map`, `prefix-list`, `soft-reconfiguration inbound`, `remove-private-AS`, `default-originate`, `advertise`, `maximum-prefix` y `next-hop-self`. Los dos últimos aparecen en las dos listas a propósito: valen por familia, y el enrutamiento acepta los dos lugares. `default-originate policy <política>` y `advertise` están en [Política de enrutamiento](/s/wiki-es/doc/politica-de-enrutamiento-policy-options-QBSNhzhSCN); en la configuración vigente `advertise` aparece como `advertise-map`, la forma en que lo guarda el enrutamiento.

Esa tabla es lo que `prismash` **verifica**. El BGP del enrutamiento entiende mucho más que eso, y lo que no está aquí se transmite sin leerse, lo cual es legítimo, y por eso sale marcado con `<- not checked` en `show this` y en `show configuration`. Note que `soft-reconfiguration inbound` está en la lista de la familia de direcciones y no en la lista de arriba: escrita directamente dentro de `router bgp`, sale marcada.

# Sesión sin dirección en el enlace (BGP unnumbered)

Un tránsito o un peering con un vecino directamente conectado no necesita un /30 en el enlace. La sesión levanta por la dirección de enlace local que ya tienen los dos lados, y el vecino es la **interfaz**:

```
router bgp 65000
 neighbor vppwan0 interface remote-as external
 address-family ipv4 unicast
  neighbor vppwan0 activate
 exit-address-family
 exit
```

`remote-as` va en la misma línea, y acepta `external`, `internal` o el número de AS. `external` es el caso común: sirve cualquier AS diferente del suyo, que es exactamente lo que se quiere en un enlace punto a punto donde el otro lado es quien dice quién es.

La forma con peer group es la misma:

```
 neighbor UPLINKS peer-group
 neighbor UPLINKS remote-as external
 neighbor vppwan0 interface peer-group UPLINKS
```

El nombre allí es el nombre con el que **el enrutamiento** conoce la interfaz, el mismo que vale en `passive-interface` y en las estrofas de OSPF e IS-IS. Una interfaz de `linux_cp` responde a más de un nombre, y quien escriba el nombre del dataplane recibe una advertencia que indica cuál es el otro:

```
warning: FRR knows this interface as vppwan0, not host-veth-vpp: written this
    way the session has nothing to run over
    neighbor host-veth-vpp interface remote-as external
```

Note el orden: **el motivo va primero y la sentencia rechazada va al final, en una línea propia**. No es estilo. La sentencia es lo que usted va a buscar en `show configuration` y volver a escribir, así que tiene que salir completa y de una sola pieza; el motivo es para leer, así que se corta al ancho de la consola. Con los dos en la misma línea, un nombre de route-map largo o un vecino IPv6 ponían la línea entre 104 y 264 columnas, y lo que se perdía en el corte era la mitad de cada uno.


:::warning
El enlace debe transportar los anuncios de router del vecino (router advertisement) para que el enrutamiento descubra la dirección de enlace local del otro lado. Así es como una sesión sin dirección encuentra con quién hablar; sin eso la sesión queda en Idle con AS 0, aun con toda la configuración correcta. Verifíquelo con `show bgp summary`.

De este lado no hay nada que habilitar: lo que llega por la interfaz se entrega al enrutamiento. Si la sesión queda en Idle con AS 0, lo que falta está del otro lado o en el camino: un vecino que no anuncia, o un enlace que no entrega el multicast de enlace local. `show bgp neighbors <interfaz>` muestra la dirección aprendida cuando llega, y `(unspec)` mientras no ha llegado.

:::

# Peer groups

Cuarenta peerings no son cuarenta estrofas. Se declara el grupo, se le asigna el AS, y los miembros heredan:

```
router bgp 65000
 neighbor IX peer-group
 neighbor IX remote-as 65100
 neighbor 200.150.9.10 peer-group IX
 neighbor 200.150.9.11 peer-group IX
 exit
```

Al completar `neighbor` con TAB, el equipo muestra los vecinos que existen con el AS del otro lado al costado, y marca qué nombres son grupos: un grupo no es un router, y buscarlo como si lo fuera cuesta tiempo.

# Política de enrutamiento: `policy-options`

La política se escribe una vez, con nombre, y se aplica en cada vecino como `import` (lo que entra) o `export` (lo que sale). El formato es el de Junos. Las listas con nombre y las políticas quedan en `policy-options`:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list OUTRO-AS
prisma# [edit policy-options / prefix-list OUTRO-AS] 198.51.100.0/24
prisma# [edit policy-options / prefix-list OUTRO-AS] exit
prisma# [edit policy-options] community NOSSA members 65000:100
prisma# [edit policy-options] policy-statement DO-TRANSITO
prisma# [edit policy-options / policy-statement DO-TRANSITO] term sem-outro-as
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] from prefix-list OUTRO-AS
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] then reject
```

Lo que acepta cada nivel de `policy-options`:

| Nivel | Comando | Qué hace |
|-------|---------|----------|
| `policy-options` | `prefix-list <nombre>` | crea o edita una lista de redes y entra en ella; dentro, una red por línea (`<red>/<longitud>`, sin bits de host) y `no <red>` para quitarla |
| `policy-options` | `community <nombre> members <asn:valor>...` | un conjunto de communities con nombre; escribir de nuevo el mismo nombre lo reemplaza |
| `policy-options` | `as-path <nombre> <expresión>` | una expresión regular de AS-path con nombre |
| `policy-options` | `policy-statement <nombre>` | crea o edita una política y entra en ella |
| `policy-options` | `no <declaración>` | elimina una lista, una community, un as-path o una política |
| `policy-statement` | `term <nombre>` | crea o edita un term y entra en él |
| `policy-statement` | `no term <nombre>` | elimina el term completo |
| `term` | `from ...`, `set ...`, `then ...` | las tres partes, en la tabla de abajo |
| `term` | `no <declaración>` | elimina un `from`, un `set` o el `then` |

Un nombre (de lista, community, as-path o política) tiene letras, números, `.`, `_` y `-`, hasta 32 caracteres. Un `then` nuevo reemplaza al anterior del mismo term: vale la última palabra dicha.

Cada `term` se evalúa en el orden en que se escribió, y tiene tres partes:

| Parte | Qué dice |
|-------|----------|
| `from` | lo que la ruta debe cumplir: `prefix-list <nombre>`, `route-filter <red> exact\|orlonger\|longer\|upto /<n>`, `community <nombre>`, `as-path <nombre>`, `protocol bgp\|ospf\|isis\|static\|direct\|kernel`, `tag <n>`, `metric <n>`. Varias del mismo tipo valen como "o"; de tipos diferentes, como "y". Sin `from`, el term coincide con todo |
| `set` | lo que cambia en la ruta: `local-preference <n>`, `community add\|set\|delete <nombre>`, `as-path-prepend <asn>...`, `metric <n>`, `tag <n>`, `next-hop <dirección>` |
| `then` | cómo termina el term: `accept` o `reject` detienen la evaluación; `next term`, o ningún `then`, aplica los `set` y sigue al próximo term |

**Una ruta que ningún term decidió se acepta**, en el `import` y en el `export`, como en Junos. Para rechazar el resto, termine con un term sin `from` y con `then reject`.

La política se aplica dentro de la familia de direcciones de BGP, por vecino o peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export PARA-O-TRANSITO
 exit-address-family
```

El equipo traduce cada política aplicada a lo que entiende el enrutamiento. En la configuración vigente, el vecino aparece con `route-map PO-DO-TRANSITO-IN in`. Los nombres que empiezan con `PO-` son de esa traducción: una `route-map` o una `prefix-list` escrita a mano con ese comienzo se rechaza.

Medido en un laboratorio con dos enlaces al mismo tránsito y un tercer AS detrás de él:

* el `import` con `then reject` quitó del enlace el prefijo del otro AS;
* `set local-preference 200` en un enlace lo convirtió en el camino elegido, y el dataplane pasó a reenviar solo por él;
* el `export` con `as-path-prepend` y `community add` llegó así al vecino;
* sin las líneas de `import` y `export`, nada de eso sucedió.

## Verificar una política: `show policy` y `test policy`

`show policy` lista las políticas, cuántos terms tiene cada una y dónde las aplica el enrutamiento vigente. Con el nombre, muestra los terms tal como se escribieron:

```
prisma> show policy
policy-statement UP
  terms: 2
  applied: neighbor 10.0.0.2 import, ospf export
```

`test policy <política> <prefijo>` muestra lo que hace la política con una ruta hacia ese prefijo, term por term, antes de que usted la aplique en cualquier lugar:

```
prisma> test policy UP 198.51.100.0/23
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: reject
      matches
result: reject, by term 20
```

La prueba parte solo del prefijo. Una condición que el prefijo por sí solo no responde (community, as-path, protocolo, tag, métrica) no se adivina: el term aparece como "decide, si la ruta tiene eso", y la prueba sigue como si no lo tuviera, para que usted vea los dos caminos. Cuando ningún term decide, la respuesta indica los dos valores predeterminados: aceptado en un vecino BGP, no exportado a OSPF o IS-IS.

En modo de configuración, `show policy` mira la candidata, es decir, lo que usted está escribiendo y todavía no aplicó. `test policy` existe solo en el modo operacional y prueba la política vigente: para probar un cambio, haga el `commit` y pruebe a continuación, o lea la candidata con `show policy`.

## Exportar a OSPF e IS-IS

La misma política exporta rutas hacia OSPF o IS-IS. Dentro de `router ospf` o de `router isis`, `export <política>` redistribuye lo que ella acepta, y solo eso:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list LOOPBACKS
prisma# [edit policy-options / prefix-list LOOPBACKS] 10.255.0.0/16
prisma# [edit policy-options / prefix-list LOOPBACKS] exit
prisma# [edit policy-options] policy-statement PARA-O-OSPF
prisma# [edit policy-options / policy-statement PARA-O-OSPF] term estaticas
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] from protocol static
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] set metric 77
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] then accept
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] top
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

**En una exportación a OSPF o IS-IS, una ruta que ningún term aceptó no se exporta**, como en Junos. Es lo contrario de BGP, donde lo que nadie decidió pasa: aquí, solo entra en el protocolo lo que la política aceptó a propósito. `from protocol` indica de dónde vino la ruta: `static`, `direct` (las redes de las interfaces), `kernel`, `bgp`, o el otro protocolo interno; un term sin `from protocol` vale para cualquier origen.

`no export <política>` lo deshace. En IS-IS la exportación vale para IPv4 e IPv6 y para los dos niveles.

**Lo que solo existe en una ruta BGP se rechaza en una exportación a OSPF o IS-IS:** `from community`, `from as-path`, `set local-preference`, `set community`, `set as-path-prepend` y `set next-hop`. Un mapa que ignorara en silencio la condición de un term exportaría justamente lo que ese term se escribió para retener.

Medido en un laboratorio con una adyacencia OSPF y dos rutas estáticas: la política que aceptaba solo una de ellas, con `set metric 77`, hizo que el vecino recibiera esa ruta con costo 77, y solo esa. Con `redistribute static` en lugar de la política, llegaron las dos, con el costo predeterminado.

## Route-maps y listas, la forma del propio enrutamiento

`policy-options` es la forma recomendada, pero no la única: las route-maps y las listas del enrutamiento siguen aceptándose, y son lo que usa un vecino en `neighbor <vecino> route-map <nombre> in|out` y `neighbor <vecino> prefix-list <nombre> in|out`. Lo que `prismash` verifica de ellas, escrito en el nivel superior del modo de configuración:

| Comando | Qué crea |
|---------|----------|
| `ip prefix-list <nombre> ...` e `ipv6 prefix-list <nombre> ...` | una lista de prefijos, con `permit`/`deny` y rango `ge`/`le` |
| `bgp as-path access-list <nombre> ...` | una lista de expresiones de AS-path |
| `bgp community-list <nombre> ...` y `bgp large-community-list <nombre> ...` | una lista de communities o de large communities |
| `route-map <nombre> permit\|deny <seq>` | una cláusula de route-map; entra en ella |

Dentro de una cláusula de route-map:

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `match ip address prefix-list` | `<lista>` | rutas IPv4 cuyo prefijo está en la lista |
| `match ip next-hop prefix-list` | `<lista>` | rutas IPv4 cuyo próximo salto está en la lista |
| `match ipv6 address prefix-list` | `<lista>` | rutas IPv6 cuyo prefijo está en la lista |
| `match rpki` | `valid`, `invalid` o `notfound` | según el estado de la validación de origen (sección RPKI, abajo) |
| `match as-path` | `<lista>` | según el AS-path |
| `match community` y `match large-community` | `<lista>` | según las communities |
| `match metric` | `<0-4294967295>` | según el MED |
| `match peer` | `<vecino>` | según el vecino que envió la ruta |
| `set local-preference` | `<0-4294967295>` | qué camino prefiere este equipo; gana el mayor |
| `set metric` | `<valor>`, `+`/`-` valor, `rtt`, `+rtt`, `-rtt`, `igp` o `aigp` | el MED enviado, reemplazado o ajustado |
| `set community` y `set large-community` | `<AS:NN> [additive]` | las communities transportadas; `additive` mantiene las que ya estaban |
| `set next-hop` | `<A.B.C.D>` | el próximo salto anunciado |
| `set as-path prepend` y `set as-path exclude` | `<asn> [<asn> ...]` | alarga el camino, o le quita AS |
| `set weight` | `<0-4294967295>` | la preferencia local de este equipo, nunca anunciada |
| `set origin` | `igp`, `egp` o `incomplete` | el atributo de origen |
| `call`  | `<route-map>` | ejecuta otra route-map antes de decidir |
| `on-match next` u `on-match goto <seq>` |           | qué hacer cuando la cláusula coincide |
| `description` | `<texto>` | para qué sirve la cláusula |

Lo que no está en estas tablas también llega al enrutamiento, marcado con `<- not checked`. Los detalles (el orden de las cláusulas, lo que se rechaza, el rango `ge`/`le`, las expresiones de AS-path) están en [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3).

# Lo que se rechaza antes de llegar al enrutamiento

Son rechazos de **forma**, no de funcionamiento: si el vecino va a responder no está en la configuración, y el equipo no finge saberlo.

* **vecino sin** `**remote-as**`: FRR rechaza cualquier otra sentencia suya; en una sesión sin dirección FRR la acepta y deja el vecino detenido en Idle con AS 0 para siempre, lo que es peor, así que el rechazo vale para las dos formas;
* **sesión sin dirección apuntada a una interfaz que el equipo no tiene**: FRR la acepta, el vecino queda en Idle y no se dice nada más sobre él;
* **peer group que nadie declaró**: `neighbor X peer-group IX` sin el `neighbor IX peer-group` que crea el grupo;
* **miembro cuyo grupo tampoco tiene AS**: el miembro hereda el AS del grupo, así que un vecino sin AS propio está correcto cuando el grupo tiene uno; lo que no puede pasar es que ninguno de los dos lo tenga;
* `**update-source**` **apuntando a una interfaz que el equipo no tiene**: FRR lo acepta, la sesión sale de ningún lugar y nunca levanta, y la configuración sigue viéndose bien.

## Una route-map citada y nunca definida

```
error: no route-map called 'RM-TRANSITO-IN' is defined, and FRR denies
    everything this statement governs until one is; define it, or write
    'route-map RM-TRANSITO-IN permit 10' to let everything through
    neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
```

Este es el rechazo que evita la sesión silenciosa. El enrutamiento **acepta** apuntar un vecino a una route-map que no existe: la sesión levanta, queda Established, y no transporta ninguna ruta. Tres semanas después `show bgp summary` muestra una sesión activa con cero prefijos, y la configuración sigue viéndose perfectamente bien: la política está allí, escrita, apuntando a la nada.

El rechazo vale para las tres formas que se midieron negando todo: `neighbor <dirección> route-map`, `redistribute ... route-map` y `network <prefijo> route-map`. El mensaje **nombra la route-map que falta** e indica las dos salidas: definir la política, o escribir la cláusula que deja pasar todo.


:::info
`call` a una route-map inexistente **no** se rechaza, y la diferencia se midió, no se dedujo: el enrutamiento salta una llamada que no se resuelve y la cláusula sigue adelante, así que el prefijo se sigue anunciando. Es una línea muerta, no una línea peligrosa:

```
warning: no route-map called 'RM-COMUNIDADES' is defined; FRR skips a call it
    cannot resolve, so this line does nothing
    call RM-COMUNIDADES
```

Lo contrario (una route-map definida que nadie usa) también es solo una advertencia. La configuración muerta es desorden, no peligro, y es exactamente lo que queda en el equipo entre dos commits de quien arma la política empezando por la política.

:::


:::info
En un equipo que **ya está** en ese estado la sentencia sale como advertencia y el commit pasa. Es a propósito: la misma verificación se ejecuta cuando el equipo carga la configuración guardada en el arranque, y allí rechazaría el archivo **completo**: un equipo cuya configuración guardada tiene la referencia arrancaría sin nada. Se rechaza el commit que **introduce** la referencia; el equipo que ya la tiene sigue pudiendo trabajarse.

:::

# Lo que se advierte y no se rechaza

## Una sesión eBGP sin `maximum-prefix`

```
warning: this eBGP session has no 'maximum-prefix': a peer that leaks a full
    table fills this box and nothing here stops it. 'neighbor 200.150.0.2
    maximum-prefix 10000' tears the session down instead of the box
    neighbor 200.150.0.2 remote-as 65001
```

Un vecino que filtra la tabla completa dentro de un equipo dimensionado para algunos miles de rutas tira el equipo, y BGP **no impide nada por defecto**: el enrutamiento acepta cada prefijo que envíe el vecino hasta que se acaba la memoria. `maximum-prefix` es la única sentencia que tira la sesión en lugar del equipo, y hasta ahora nada en este equipo mencionaba que faltaba.

Es **advertencia y nunca rechazo**, y es la decisión correcta: un route server, un cliente cuya tabla se sabe pequeña y un enlace de laboratorio funcionan sin límite a propósito. La elección es de quien opera.


:::info
La advertencia **habla una vez**: sale por los vecinos que **este cambio** pone en ese estado. Un equipo con tres tránsitos deliberadamente sin límite cargaría tres advertencias en cada commit que hiciera por el resto de su vida, y una alarma que siempre está encendida es una alarma que nadie lee.

Las sesiones **iBGP** quedan fuera por decisión, no por olvido: del otro lado hay un equipo del mismo operador, la tabla es la que este AS ya decidió cargar, y un límite allí se dispara cuando la red crece y no cuando alguien filtra. Vale para `remote-as` igual al AS de la propia estrofa y para `remote-as internal`.

Un `maximum-prefix` en el **peer group** cubre a todos sus miembros: es donde quien tiene seis tránsitos pone el límite una sola vez. En cambio `maximum-prefix-out` no lo cubre: esa sentencia limita lo que **este** equipo anuncia, que es otro asunto.

:::

# La contraseña

`neighbor <dirección> password <contraseña>` es el secreto en sí, no un hash de él, y abre la sesión **en el otro router** también. Por eso no se vuelve a imprimir:

```
 neighbor 200.150.0.2 password <set, not shown>
```

Vale tanto en `show running-config` como en `show this` dentro del bloque. Esto es solo la visualización: la configuración guardada y las revisiones de rollback guardan el valor real, de lo contrario un rollback no podría restaurar la sesión, y por eso esos archivos no son legibles por cualquiera.

# Validación de origen (RPKI)

Un proveedor que valida orígenes en el resto de la red no debería tener, en el borde, un equipo que acepta lo que le digan. El enrutamiento de este equipo habla RTR: se conecta a un validador que usted ya opera y pasa a marcar cada ruta aprendida como `valid`, `invalid` o `notfound`, para que su política decida qué hacer con cada una.

```
configure
 rpki
  rpki cache tcp 10.0.0.10 8282 preference 1
  exit
 route-map RM-TRANSITO-IN deny 5
  match rpki invalid
  exit
 route-map RM-TRANSITO-IN permit 10
  exit
 router bgp 65000
  neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
  exit
 commit
```

La primera cláusula descarta lo que el validador reprueba; la segunda deja pasar todo lo demás. Sin la segunda, la route-map niega todo al final, que es el comportamiento normal de una política, y aquí filtraría la sesión completa en lugar de filtrar solo lo inválido.

## Lo que entiende el bloque `rpki`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `rpki cache tcp` | `<host> <puerto> [source <A.B.C.D>] preference <1-255>` | un validador, por RTR sobre TCP |
| `rpki cache ssh` | `<host> <puerto> <usuario> <clave-privada> [known-hosts] [source <A.B.C.D>] preference <1-255>` | un validador, por RTR sobre SSH |
| `rpki polling_period` | `<1-86400>` | segundos entre actualizaciones |
| `rpki expire_interval` | `<600-172800>` | segundos hasta descartar lo que envió el validador |
| `rpki retry_interval` | `<1-7200>` | segundos hasta volver a intentar con un validador perdido |
| `rpki reset` |           | descarta lo que enviaron los validadores y vuelve a preguntar |

## La palabra del transporte no es opcional

La forma es `rpki cache tcp <host> <puerto> [source <A.B.C.D>] preference <1-255>`, o `rpki cache ssh ...`. Omitir el `tcp` no es un atajo: es un comando desconocido, y el equipo lo rechaza como rechazaría cualquier palabra que no existe.

## Es un módulo, y un módulo que no está cargado no existe

El soporte de RPKI viene en un paquete separado del enrutamiento, `frr-rpki-rtrlib`. El appliance ya lo instala. El síntoma de cuando falta es engañoso y conviene reconocerlo:

```
bgpd is not running
```

Toda palabra de RPKI responde eso, porque el enrutamiento se niega a arrancar cuando se le pide cargar un módulo que no encuentra. La respuesta apunta al lugar equivocado: parece enrutamiento caído, es un módulo ausente. Por eso el autotest de arranque verifica el módulo **por el nombre**, y no por la versión del enrutamiento, que informa soporte de RPKI esté el módulo presente o no.

## Las dos cosas que advierte el `commit`

Escribir la política antes de configurar el cache es un orden de trabajo legítimo, así que ninguna de las dos es un rechazo, pero las dos cambian lo que hace la política, y en silencio.

```
warning: no RPKI cache is configured, so every route is notfound and this
    clause matches all of them
    match rpki notfound
```

Sin cache, todo es `notfound`, y de eso trata la advertencia: una cláusula en `notfound` coincide con **todas** las rutas del equipo. El otro lado también se advierte, con la frase equivalente: una cláusula en `valid` o `invalid` no coincide con **ninguna**, porque sin validador ninguna ruta llega a ser ni una cosa ni la otra. La misma política, con y sin cache, hace cosas opuestas.

## La falla que esto evita, y la que crea


:::warning
Un validador que se cayó termina marcando **todo** como `notfound`. Una política que niega `invalid` sigue funcionando en ese estado; una política que trata `notfound` como sospechoso pasa a degradar todas las rutas del equipo el día en que el cache quede fuera de servicio, y un cache fuera de servicio es algo ordinario, no una catástrofe.

:::

Esa es la razón por la que la recomendación es negar `invalid` y no desconfiar de `notfound`. La primera falla cerrada para lo que el validador reprueba; la segunda falla cerrada para internet entera.

## Lo que dice el equipo cuando el cache desaparece

Con el cache activo, el resumen de BGP dice así:

```
  RPKI: 1 of 1 cache(s) connected
```

Y cuando desaparece:

```
  RPKI: 1 cache(s) configured, NONE connected
        validation is running on data the caches already sent, which expires;
        after that every route is notfound
```

Las dos mitades de esa segunda frase importan. La validación **no** se detiene de inmediato: la tabla de prefijos que ya envió el cache sigue vigente, y solo expira después del `expire_interval` del propio enrutamiento, 7200 segundos por defecto. Decir "nada está validado" lo mandaría a buscar un incidente que todavía no empezó; decir solo "cache fuera de servicio" ocultaría que va a empezar.


:::info
En `show rpki cache-connection`, la línea de encabezado `**Connected to group 1**` sigue allí mientras el cache esté configurado: medido setenta segundos después de que se mató el validador, todavía decía *Connected*. Quien dice la verdad es el marcador `**(connected)**` de cada cache, que desaparece en menos de un segundo. Todo en este producto lee el marcador; al verificar a mano, lea también el marcador.

:::

# Operar la sesión: lo que se escribe cuando algo está mal

## `show bgp summary`

```
prisma> show bgp summary
  Neighbour          AS       State          Prefixes  Up
  200.150.0.2        65001    Established    412       03:11:55
  200.150.1.2        65002    Established    398       01:02:37

  2 session(s), 2 established
  all 810 BGP prefix(es) are in the forwarding table
```

La última línea es la que existe solo aquí. Una sesión Established que transporta 412 prefijos es una respuesta verdadera a **otra** pregunta: quien reenvía es la tabla del dataplane, y un prefijo que el enrutamiento tiene y el dataplane no recibió no mueve ningún paquete. Cuando los dos discrepan:

```
  3 of 412 BGP prefix(es) are NOT in the forwarding table
    200.150.9.0/24
    ...
    'show route verbose' compares the two tables in full
```

El conteo es del total, no por sesión. La tabla de reenvío no guarda por qué sesión llegó la ruta, y poner un número por vecino allí sería poner un número que no se sostiene.

## Rutas que el equipo no está guardando

`show bgp neighbor <dirección> received-routes` muestra lo que envió el vecino **antes** de la política de entrada, y eso el equipo solo lo tiene si se le pidió guardarlo. Sin `soft-reconfiguration inbound` la tabla sale vacía, y "no recibí rutas" y "no las estoy guardando" son respuestas diferentes para mirar durante un incidente:

```
error: 200.150.0.2 does not keep what it received
    so this would print an empty table either way
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family; it costs memory
    'show bgp neighbor 200.150.0.2 routes' shows what survived inbound policy,
    which the box does have
```

`clear bgp <dirección> soft in` rechaza por el mismo motivo, y la salida que ofrece es la que siempre funciona: reiniciar la sesión y volver a tomar las rutas.

```
error: 200.150.0.2 does not keep what it received
    so there is nothing to re-apply
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family, which costs memory,
    or use 'clear bgp 200.150.0.2' to reset the session and take the routes
    again
```

Los dos rechazos vienen en líneas dentro del ancho de la consola. Durante un tiempo el veredicto y la primera explicación salían pegados en una sola línea, que medía de 92 a 106 columnas en una pantalla de 79.

Y note que el veredicto **empieza por la dirección**, sin la palabra `neighbour` delante. Se quitó porque la dirección que sigue ya dice de quién se habla, y costaba diez columnas en una línea que el equipo no corta: con un vecino IPv6 en lugar de uno IPv4, el veredicto llegaba a 87 columnas en una pantalla de 79. La dirección del vecino entra en todas las líneas de este rechazo, así que es ella la que manda en el ancho de todas, y por eso la palabra sobrante fue la primera en salir.

## Y cuando quien no responde es el enrutamiento

Son **tres** respuestas y no dos. "El vecino no guarda" es una lectura de la configuración vigente, y en un equipo cuyo enrutamiento no está respondiendo no hay configuración leída: no hay ninguna lectura. Decir allí que falta el `soft-reconfiguration inbound` lo mandaría a agregar una línea que el equipo quizás ya tiene, a un daemon que no está activo para recibirla, y apuntaría a un comando que le pregunta al mismo enrutamiento callado.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    an empty table here would be this box's control plane being down rather
    than the neighbour sending nothing: 'systemctl status frr' and its journal
    say why
```

`clear bgp <dirección> soft in` responde de la misma forma, y agrega la parte que importa allí: `**clear bgp**` **por sí solo tampoco lo resolvería**, porque quien sostiene la sesión es justamente el enrutamiento que está callado.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    nothing was re-applied, and 'clear bgp' would reset nothing either - both
    go to the FRR that is silent: 'systemctl status frr' and its journal say
    why
```

El veredicto de estos dos **no incluye la dirección del vecino**, y es deliberado: la dirección va en la línea de abajo. Nadie corta la primera línea, y con un vecino IPv6 en medio llegaría a 99 columnas en una pantalla de 79.

Una tabla vacía en un equipo así es el plano de control de **su** equipo fuera de servicio, y no el vecino sin enviar rutas. Son problemas diferentes y eso es lo que esta tercera respuesta existe para separar.

## `clear`

```
prisma> clear bgp 200.150.0.2
prisma> clear bgp 200.150.0.2 soft in
```

Es operacional: no cambia nada en disco. Exige una cuenta de administrador, porque reiniciar una sesión es una caída a propósito.

## Mantenimiento sin tirar el tráfico

```
router bgp 65000
 bgp graceful-shutdown
 exit
```

RFC 8326. Antes de un mantenimiento, el tráfico **se drena** en lugar de caerse: los anuncios salen marcados con la comunidad de graceful shutdown, y las rutas que recibió este equipo pasan a tener preferencia local cero; es decir, deja de ser el camino preferido mientras asume quien tenga otro camino.

`no bgp graceful-shutdown` deshace las dos cosas. Un interruptor de mantenimiento que no vuelve atrás no es un interruptor de mantenimiento.


:::info
Verifique por el efecto, no por el texto: `show bgp <prefijo>` en el vecino debe mostrar la comunidad `gracefulShutdown`, y `show bgp <prefijo>` aquí debe mostrar preferencia local 0. Si la configuración tiene la línea y el vecino no ve la comunidad, la sesión todavía no volvió a anunciar.

:::

# Cómo verificar

* `show running-config` muestra la estrofa BGP tal como el equipo la lee de vuelta, con la contraseña omitida;
* `show this` dentro del bloque muestra solo lo que está en ese nivel;
* `show bgp summary` muestra las sesiones y cuántos prefijos trajo cada una;
* `show route` muestra lo que **reenvía**: una ruta que BGP aprendió y no está allí no mueve ningún paquete.

# Vea también

* [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3): prefix-lists, políticas y el prefijo público de un pool
* [commit, rollback y save](/s/wiki-es/doc/commit-rollback-y-save-6sAWJkXNO3): cómo se aplica y se deshace un cambio
