# Política de enrutamiento (policy-options)

Referencia de todo lo que `policy-options` acepta en el `prismash`: listas de prefijos, communities, large y extended communities, expresiones de AS-path, políticas con sus terms, dónde se aplica una política y cómo verificarla antes y después: cuántas rutas coincidieron con cada term, qué haría con una ruta y qué política decidió una ruta que tiene el equipo. Para quien escribe filtros de BGP o decide qué entra en OSPF e IS-IS.

La política se escribe una sola vez, con nombre, en el formato de Junos, y se aplica en tantos lugares como sea necesario. El equipo la traduce a lo que entiende el enrutamiento en el `commit`.

# Los niveles y los prompts

`policy-options` es un nivel del modo de configuración, y dentro de él hay otros dos, uno dentro del otro:

| Nivel | Cómo se entra | Prompt | Qué se escribe allí |
|-------|---------------|--------|---------------------|
| `policy-options` | `policy-options` en `[edit]` | `prisma# [edit policy-options]` | listas, communities, as-paths y políticas |
| lista de prefijos | `prefix-list <nombre>` | `prisma# [edit policy-options / prefix-list <nombre>]` | una red por línea   |
| política | `policy-statement <nombre>` | `prisma# [edit policy-options / policy-statement <nombre>]` | los terms           |
| term  | `term <nombre>` dentro de la política | `prisma# [edit ...ptions / policy-statement <nombre> / term <nombre>]` | `from`, `set` y `then` |

Un prompt de más de 44 caracteres conserva el **final**, que es donde caerá la próxima línea; por eso el del term empieza con `...`. Entrar en un nivel con nombre ya crea lo que nombra: una lista o un term recién creado existe antes de tener nada dentro.

En cualquier nivel, `exit` sube un nivel y `top` vuelve a `[edit]`. `?` y TAB listan lo que acepta el nivel.

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list CLIENTES
prisma# [edit policy-options / prefix-list CLIENTES] 203.0.113.0/24
prisma# [edit policy-options / prefix-list CLIENTES] exit
prisma# [edit policy-options] community MARCA members 65000:100
prisma# [edit policy-options] policy-statement SAIDA
prisma# [edit policy-options / policy-statement SAIDA] term 10
```

La misma política, en la forma en que el equipo la guarda y en la que usted puede escribirla o pegarla:

```
policy-options
 prefix-list CLIENTES
  203.0.113.0/24
  exit
 community MARCA members 65000:100
 policy-statement SAIDA
  term 10
   from prefix-list CLIENTES
   set as-path-prepend 65000 65000
   then accept
   exit
  term 20
   from community MARCA
   then reject
   exit
  exit
 exit
```

# El nivel `policy-options`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `prefix-list` | `<nombre>` | crea o edita una lista de redes y entra en ella |
| `community` | `<nombre> members <asn:valor>...` | un conjunto con nombre de communities. Cada miembro es `ASN:valor`, ambos de 0 a 65535. Escribir el mismo nombre de nuevo **reemplaza** el conjunto |
| `large-community` | `<nombre> members <asn:valor:valor>...` | un conjunto con nombre de large communities (RFC 8092). Cada parte va de 0 a 4294967295. Escribir el mismo nombre de nuevo reemplaza el conjunto |
| `extended-community` | `<nombre> members rt:<asn>:<valor>\|soo:<asn>:<valor>...` | un conjunto con nombre de extended communities: route target (`rt`) y site of origin (`soo`). El primer campo es un ASN o una dirección IPv4. Con ASN de hasta 65535 el valor llega hasta 4294967295; con ASN mayor o con dirección, hasta 65535 |
| `as-path` | `<nombre> <expresión>` | una expresión regular de AS-path con nombre. Las comillas alrededor se quitan. Escribir el mismo nombre de nuevo reemplaza la expresión |
| `policy-statement` | `<nombre>` | crea o edita una política y entra en ella |
| `no`    | `<declaración>` | elimina una lista, una community, un conjunto de large o extended communities, un as-path o una política, con todo lo que tiene dentro |

Un nombre, de lista, community, as-path, política o term, tiene letras, números, `.`, `_` y `-`, empieza por letra o número y tiene hasta 32 caracteres.

En la expresión de AS-path, `_` es la regla del enrutamiento: cualquier frontera (inicio, fin, espacio o coma), no un guion bajo. Una expresión que no compila se rechaza en el momento.

## Dentro de `prefix-list <nombre>`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `<red>` | dirección/longitud | pone la red en la lista. IPv4 e IPv6 pueden estar en la misma lista. Los bits de host tienen que ser cero: `10.0.0.1/8` se rechaza |
| `no`    | `<red>`   | quita la red de la lista |


:::info
**Una red en una lista coincide solo consigo misma**, con esa longitud exacta. `203.0.113.0/24` en la lista no coincide con `203.0.113.128/25`. Para que coincidan también los más específicos, use `from route-filter` en el term, más abajo.

:::

## Dentro de `policy-statement <nombre>`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `term`  | `<nombre>` | crea o edita un term y entra en él. Los terms se evalúan **en el orden en que fueron creados**, no por el orden del nombre |
| `default-action` | `accept` o `reject` | lo que la política hace con la ruta que ningún term decidió, en lugar del comportamiento por defecto del lugar donde se aplica. Escribirlo de nuevo lo reemplaza |
| `no`    | `term <nombre>` | elimina el term completo |

# El term: `from`, `set` y `then`

Un term tiene tres partes. `from` dice con qué tiene que coincidir la ruta, `set` dice qué cambia en ella, `then` dice cómo termina la evaluación. Todas son opcionales.

## `from`: con qué tiene que coincidir la ruta

| Condición | Argumento | Coincide cuando |
|-----------|-----------|-----------------|
| `from prefix-list` | `<nombre>` | el prefijo de la ruta está en la lista, exactamente |
| `from route-filter` | `<red> exact` | el prefijo es esa red, con esa longitud |
| `from route-filter` | `<red> orlonger` | el prefijo es esa red o uno más específico dentro de ella |
| `from route-filter` | `<red> longer` | el prefijo es uno más específico dentro de ella, sin incluirla a ella |
| `from route-filter` | `<red> upto /<n>` | el prefijo está dentro de ella, desde su longitud hasta `/n`. `n` está entre la longitud de la red y 32 (o 128) |
| `from community` | `<nombre>` | la ruta lleva la community del conjunto con nombre |
| `from as-path` | `<nombre>` | el AS-path de la ruta coincide con la expresión con nombre |
| `from protocol` | `bgp`, `ospf`, `isis`, `static`, `direct` o `kernel` | la ruta vino de ese protocolo. `direct` son las redes de las propias interfaces |
| `from tag` | `<0-4294967295>` | la ruta lleva esa tag |
| `from metric` | `<0-4294967295>` | la ruta tiene esa métrica |
| `from neighbor` | `<dirección>` | la ruta vino de ese vecino BGP |
| `from family` | `inet` o `inet6` | el prefijo es IPv4 (`inet`) o IPv6 (`inet6`). Sin otra condición de prefijo, coincide con todos los prefijos de la familia |
| `from origin` | `igp`, `egp` o `incomplete` | el atributo origin de BGP |
| `from local-preference` | `<0-4294967295>` | la ruta tiene esa preferencia local |
| `from next-hop` | `<dirección>` | el siguiente salto de BGP es ese |
| `from large-community` | `<nombre>` | la ruta lleva la large community del conjunto |
| `from extended-community` | `<nombre>` | la ruta lleva la extended community del conjunto |
| `from validation-state` | `valid`, `invalid` o `unknown` | el resultado de la validación RPKI de origen. `unknown` es la ruta que ningún ROA cubre |
| `from source-vrf` | `<vrf>` o `default` | la ruta fue importada de esa VRF, o de la tabla default. Solo en una política de `vrf-import` |
| `from policy` | `<política>` | llama a otra política como subrutina: si ella rechaza, la ruta se rechaza; si acepta, el term continúa. En un term `then reject` es distinto: el term rechaza exactamente lo que la subrutina acepta, y lo que ella rechaza sigue al siguiente term (hasta la 0.148.12 este caso rechazaba todo lo que tomaban las demás condiciones del term). Un term llama a una sola política |

**Las condiciones del mismo tipo valen como "o"; las de tipos distintos, como "y".** Dos `from prefix-list` coinciden con una ruta que esté en cualquiera de las dos; un `from prefix-list` y un `from community` exigen ambas cosas. `from prefix-list` y `from route-filter` son del mismo tipo: los dos hablan del prefijo.

`from tag` y `from metric` valen por un solo valor: un segundo, en el mismo term, reemplaza al primero.

`from neighbor`, `from origin`, `from local-preference`, `from next-hop`, `from validation-state` y `from source-vrf` aceptan varios valores, que valen como "o". Cada combinación de ellos se convierte en una regla en el enrutamiento, y un term puede tener como máximo 10 combinaciones: más que eso se rechaza en el `commit`, y la salida es dividir el term en dos.

`from validation-state` solo tiene sentido con un caché RPKI configurado. Sin caché, toda ruta es `unknown`, y el `commit` lo advierte.

**Un term sin** `**from**` **coincide con cualquier ruta.** Así se escribe el "todo lo demás" al final de una política.

## `set`: qué cambia en la ruta

| Acción | Argumento | Qué hace |
|--------|-----------|----------|
| `set local-preference` | `<0-4294967295>`, `+<n>` o `-<n>` | la preferencia local de BGP, o sumada o restada a la que tiene la ruta |
| `set community` | `add <nombre>` | agrega las communities del conjunto a las que la ruta ya tiene |
| `set community` | `set <nombre>` | reemplaza las communities de la ruta por las del conjunto |
| `set community` | `delete <nombre>` | quita de la ruta las communities del conjunto |
| `set large-community` | `add`, `set` o `delete` `<nombre>` | agrega, reemplaza o quita las large communities del conjunto |
| `set extended-community` | `add` o `set` `<nombre>` | agrega o reemplaza las extended communities del conjunto. No hay `delete`: el enrutamiento no sabe quitar extended communities por lista |
| `set as-path-prepend` | `<asn>...` | antepone esos ASN, en el orden escrito |
| `set as-path-prepend` | `last-as <1-10>` | repite el primer AS del camino (el del vecino) esa cantidad de veces |
| `set as-path` | `remove-private` | quita del AS-path todo AS privado (64512 a 65534 y 4200000000 a 4294967294), dondequiera que esté, incluso el del propio vecino |
| `set metric` | `<0-4294967295>` | la métrica: el MED en BGP, el costo en OSPF e IS-IS |
| `set tag` | `<0-4294967295>` | la tag de la ruta |
| `set next-hop` | `<dirección>` | el siguiente salto en BGP, IPv4 o IPv6 |
| `set next-hop` | `self`    | en un `export`, anuncia este equipo como siguiente salto |
| `set next-hop` | `peer-address` | en un `import`, usa la dirección del vecino como siguiente salto |
| `set next-hop` | `discard` | descarta el tráfico hacia el prefijo: es el blackhole disparado remotamente (RTBH). El siguiente salto pasa a ser 192.0.2.1 (o 100::1 en IPv6), que el equipo resuelve a un blackhole. En un vecino eBGP directo, el vecino necesita `neighbor <vecino> disable-connected-check`: sin eso el enrutamiento no resuelve ese siguiente salto, la ruta no se instala y el tráfico sigue la ruta menos específica. El `commit` lo rechaza e indica la línea que falta |
| `set med` | `<0-4294967295>`, `+<n>`, `-<n>` o `igp` | el MED de BGP; `igp` usa la métrica del IGP hasta el siguiente salto |
| `set weight` | `<0-65535>` | el peso, que solo vale en este equipo y pesa antes que la preferencia local |
| `set origin` | `igp`, `egp` o `incomplete` | el atributo origin |
| `set metric-type` | `1` o `2` | el tipo de la ruta externa en OSPF. Solo en un `export` hacia OSPF o en el `default-information` de OSPF |
| `set src` | `<dirección>` | la dirección de origen que lleva la ruta instalada en Linux, para lo que el propio equipo envía por ella. Solo en una `fib-policy`. Un term con `set src` IPv4 vale solo para rutas IPv4, como si tuviera `from family inet` (y lo mismo para IPv6) |

Los `set` solo se aplican cuando el term coincide.

## `then`: cómo termina el term

| Comando | Qué hace |
|---------|----------|
| `then accept` | acepta la ruta, con los `set` del term, y **detiene** la evaluación |
| `then reject` | rechaza la ruta y detiene la evaluación |
| `then next term` | aplica los `set` y sigue al siguiente term |
| `then next policy` | aplica los `set` y pasa la ruta a la siguiente política de la cadena. En una política sola, vale el comportamiento por defecto del lugar donde se aplica |
| sin `then` | lo mismo que `then next term` |

Un term tiene un solo `then`: escribir otro reemplaza al anterior.

Cambiar la ruta es siempre `set`. A diferencia de Junos, `then` no lleva acción: `then local-preference 200` se rechaza, y la forma correcta es `set local-preference 200` con `then accept`.

## `no` dentro del term

| Comando | Qué hace |
|---------|----------|
| `no from <condición>` | quita esa condición |
| `no set <acción>` | quita esa acción |
| `no then <como>` | quita el `then`, y el term vuelve a seguir al siguiente |

El `no` lleva la declaración completa, tal como se escribió: `no from prefix-list CLIENTES`, no `no from`.

# La ruta que ningún term decidió

El comportamiento por defecto depende de dónde se usa la política, como en Junos:

| Dónde se aplica la política | Ruta que ningún term aceptó ni rechazó |
|-----------------------------|----------------------------------------|
| `import` o `export` en un vecino BGP | **aceptada**                           |
| `export` hacia OSPF o IS-IS | **no exportada**                       |
| `network ... policy`, `aggregate-address ... policy`, `table-policy` | **aceptada**: anunciada, o instalada   |
| `fib-policy`                | **instalada**                          |
| `default-originate policy`, `default-information originate ... policy` | **no cuenta**: sin un term que acepte, el default no se origina |
| `suppress-policy`           | **no suprimida**                       |
| las dos políticas de `advertise` | **fuera**: no se anuncia ni cuenta como presente |
| `vrf-import ... policy`     | **no importada**: la VRF solo recibe lo que un term aceptó |

Para rechazar el resto en BGP, use `default-action reject` en la política, o termine con un term sin `from` y con `then reject`. Para exportar el resto a un IGP, `default-action accept`, o un term sin `from` y con `then accept`.

En una cadena, la ruta que ningún term de una política decidió pasa a la siguiente; el `default-action` de una política, cuando existe, decide allí mismo.

# Dónde se aplica una política

## En un vecino BGP: `import` y `export`

Dentro del `router bgp`, en la familia de direcciones, por vecino o por peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export SAIDA
 exit-address-family
 exit
```

| Comando | Qué hace |
|---------|----------|
| `neighbor <vecino> import <política>` | la política filtra y modifica lo que llega de ese vecino |
| `neighbor <vecino> export <política>` | la política filtra y modifica lo que se le anuncia |

### Una cadena de políticas

En lugar de una política, una lista entre corchetes: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. La ruta pasa por las políticas en orden; la primera que acepta o rechaza decide, y `then next policy` pasa la ruta adelante con lo que el term cambió. Lo mismo vale para `export [ ... ]` en OSPF e IS-IS. Una política no puede aparecer dos veces en la misma cadena.

La política tiene que existir: `import` o `export` de un nombre que ningún `policy-statement` tiene se rechaza en el prompt. En un vecino BGP las rutas son de BGP, así que un term con `from protocol` que no incluya `bgp` no se aplica allí.

En la configuración vigente el vecino aparece con la `route-map` a la que se tradujo la política: `neighbor 10.0.0.2 route-map PO-SAIDA-OUT out`, o `PO-BOGONS+CLIENTES-IN` para una cadena. Todos los nombres que empiezan por `PO-` son de esa traducción, y una `route-map` o lista escrita a mano con ese inicio se rechaza.

## En un IGP: `export <política>`

Dentro del `router ospf` o del `router isis <nombre>`, `export <política>` redistribuye al protocolo lo que la política acepta, y solo eso:

```
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

| Comando | Dónde | Qué hace |
|---------|-------|----------|
| `export <política>` | `router ospf` | redistribuye a OSPF lo que la política acepta, proveniente de `bgp`, `direct`, `kernel`, `static` o `isis` |
| `export <política>` | `router isis <nombre>` | lo mismo para IS-IS, proveniente de `bgp`, `direct`, `kernel`, `static` u `ospf`, en IPv4 e IPv6 y en los dos niveles |
| `no export <política>` | en ambos | deshace la exportación |

`from protocol` es lo que elige de dónde vino la ruta; un term sin `from protocol` vale para cualquier origen. La exportación cubre todos los orígenes desde el principio, así que un term agregado después vale sin escribir el `export` de nuevo.

El `router ospf6` no tiene `export`.

## En BGP, además del vecino

Dentro del `router bgp`, en la familia de direcciones:

| Comando | Qué hace |
|---------|----------|
| `neighbor <vecino> default-originate policy <política>` | anuncia la ruta default al vecino mientras haya en la tabla de BGP una ruta que la política acepte. Los `set` de la política van en la default |
| `network <prefijo> policy <política>` | origina el prefijo con los `set` de la política; si ella rechaza, el prefijo no se anuncia |
| `aggregate-address <prefijo> [summary-only] [policy <política>] [suppress-policy <política>]` | origina el agregado. `policy` da los `set` del agregado; `suppress-policy` retiene las rutas más específicas que ella acepta y deja pasar las demás. `summary-only` retiene todas, y por eso no va junto con `suppress-policy` |
| `neighbor <vecino> advertise <política> exist <condición>` | anuncia lo que la primera política acepta solo mientras exista en la tabla una ruta que la condición acepte |
| `neighbor <vecino> advertise <política> non-exist <condición>` | lo contrario: anuncia solo mientras **no** exista. Es el anuncio de respaldo |
| `table-policy <política>` | decide cuáles de las mejores rutas de BGP van a la tabla de enrutamiento y al reenvío. La ruta rechazada sigue en BGP y se sigue anunciando; solo no se instala. Acepta `set metric` y `set next-hop` |

BGP verifica la condición del `advertise` cada 60 segundos. Para menos, `bgp conditional-advertisement timer <segundos>` en el `router bgp`.

## En la tabla de enrutamiento: `fib-policy`

En `[edit]`, una política por protocolo decide lo que entra en la tabla de reenvío, después de que el protocolo eligió la ruta:

```
prisma# [edit] fib-policy ospf SO-O-QUE-SERVE
```

| Comando | Qué hace |
|---------|----------|
| `fib-policy bgp\|ospf\|isis\|static <política>` | la ruta que la política rechaza no se instala: el protocolo la conserva, pero el tráfico no la sigue. `set src <dirección>` le da a la ruta instalada la dirección de origen |
| `no fib-policy <protocolo> <política>` | deshace  |

Así se filtra lo que OSPF e IS-IS instalan: el enrutamiento no tiene filtro de entrada dentro de esos protocolos. Vale para IPv4 e IPv6. Una `fib-policy` solo acepta `set src`, y nada que solo tenga BGP.

## El default de OSPF e IS-IS

| Comando | Dónde | Qué hace |
|---------|-------|----------|
| `default-information originate [always] [metric <n>] [metric-type 1\|2] policy <política>` | `router ospf` | sin `always`, origina la default mientras haya una ruta que la política acepte, entre las que OSPF redistribuye. Con `always`, siempre, con los `set` de la política |
| `default-information originate ipv4\|ipv6 level-1\|level-2 [always] [metric <n>] policy <política>` | `router isis <nombre>` | la política se aplica a la propia ruta default: si la acepta, la default se origina con sus `set`; si la rechaza, no |

Aquí el protocolo elige las rutas que mira, y `from protocol` se rechaza.

## Entre VRF: `vrf-import`

Dentro del `router bgp <asn> vrf <vrf>` (o del `router bgp <asn>`, que es la tabla default), en la familia de direcciones, la VRF importa las rutas de BGP de otra VRF, o de la tabla default, mediante la política:

```
router bgp 65000 vrf red
 address-family ipv4 unicast
  vrf-import svc policy LEAK
  vrf-import default policy LEAK
 exit-address-family
 exit
```

| Comando | Qué hace |
|---------|----------|
| `vrf-import <vrf>\|default policy <política>` | la VRF recibe las rutas que la política acepta, con sus `set`. Lo que ningún term aceptó queda fuera |
| `vrf-import <vrf>\|default` | importa todo, sin política, en una familia que todavía no tiene política |
| `no vrf-import <vrf>\|default` | deshace; la política sale junto con la última importación de la familia |

**Una política por familia de direcciones.** El enrutamiento aplica la misma política a todas las VRF que importa una familia, así que una segunda política en la misma familia se rechaza, y también una importación sin política en una familia que ya tiene una. Para tratar cada origen de una forma distinta, use `from source-vrf` en los terms. Vale para IPv4 e IPv6, cada familia con la suya.

En una importación la política acepta `set local-preference`, `set community`, `set large-community`, `set med`, `set metric`, `set origin` y `set weight`. El siguiente salto es el de la VRF de origen, y es por él que va el tráfico: `set next-hop` se rechaza, y también `set extended-community`, porque los route targets son lo que el propio enrutamiento usa para importar. `from neighbor` se rechaza: la ruta importada viene de una VRF, no de un vecino, y quien dice cuál es la VRF es `from source-vrf`.

No hay `rd` ni `route-target` que configurar: el enrutamiento los elige por sí solo para este tipo de importación.

**La VRF de Linux viene de** `**vrf <nombre> table <n>**`**.** La VRF del enrutamiento necesita una VRF de Linux con el mismo nombre; declárela con `vrf red table 10`, en la misma tabla del `vrf 10` de sus interfaces (vea [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg)).

**La subred conectada de una VRF también cruza.** Cuando la VRF de origen tiene `redistribute connected` y la otra la importa, el enrutamiento instala esa ruta en Linux apuntando a la propia VRF de origen, sin siguiente salto. Desde la 0.149.0 el equipo lleva esa ruta al dataplane por su cuenta: la VRF que importa pasa a buscar el destino en la tabla de la VRF de origen. No hay nada que configurar; cada ruta llevada aparece una vez en el journal, con el nombre de las VRF:

```
prisma-routing-leaks: 10.10.0.0/24 in svc, through red (VPP table 30, a lookup in table 10): installed
```

Sale sola cuando se deshace la importación, y vuelve sola después de un reinicio del dataplane. Hasta la 0.148.16 la ruta existía en BGP y en Linux, pero no en el dataplane, y un host de la subred conectada no era alcanzable desde la otra VRF.

La `prisma-bp`, y cualquier VRF cuyo nombre empiece por `prisma-`, es del propio equipo: ninguna ruta entra ni sale de ella, ni por `vrf-import` ni por el `import vrf` del enrutamiento escrito a mano.

Una ruta estática puede llevar tag: `<prefijo> next-hop <destino> tag <n>` en el `router static`, y `from tag <n>` coincide con ella. Es la forma de marcar una estática para un `export` o para una condición.

# Verificar: `show policy` y `test policy`

| Comando | Modo | Qué muestra |
|---------|------|-------------|
| `show policy` | operativo y configuración | cada política, cuántos terms tiene y dónde la aplica el enrutamiento vigente |
| `show policy <política>` | operativo y configuración | lo mismo, con los terms tal como se escribieron y, en cada lugar donde se aplica, cuántas rutas coincidieron con cada term |
| `test policy <política> <prefijo>` | operativo | qué hace la política con una ruta para ese prefijo, term por term |
| `test policy <política> route <prefijo> ... [at <uso>]` | operativo | lo mismo para una ruta que usted describe (communities, AS-path, vecino, MED y el resto), en el lugar donde se usa la política, con la ruta después de los `set` |
| `test policy <política> rib <prefijo> ... [at <uso>]` | operativo | lo mismo para la ruta que tiene el equipo |
| `show policy route <prefijo> [vrf <nombre>]` | operativo y configuración | qué política decidió una ruta que tiene el equipo, en cada lugar por donde pasó, junto a lo que hizo el enrutamiento |

En el modo operativo ambos leen la política aplicada. En modo de configuración, `show policy` lee la candidata, lo que usted está escribiendo y todavía no aplicó.

```
prisma> show policy
policy-statement SAIDA
  terms: 2
  applied: neighbor 10.0.0.2 export, ospf export
```

Los demás lugares aparecen de la misma forma: `neighbor 10.0.0.2 default-originate`, `network 10.40.0.0/24`, `aggregate-address 10.30.0.0/16 policy` o `suppress-policy`, `neighbor 10.0.0.2 advertise` y, para la condición, `neighbor 10.0.0.2 advertise non-exist`, `table-policy`, `fib-policy static`, `ospf default-information`, `vrf-import`.

Una política escrita y todavía no usada en ningún lugar dice `applied: nowhere in the running routing`. Una política en una cadena aparece con la cadena completa: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. El `default-action`, cuando existe, aparece antes de los terms.

`test policy` recorre los terms en orden y se detiene en el que decide:

```
prisma> test policy SAIDA 203.0.113.0/24
  term 10: accept (set as-path-prepend 65000 65000)
      matches
result: accept (set as-path-prepend 65000 65000), by term 10
```

La prueba parte solo del prefijo. `from family` lo responde por el prefijo. Una condición que el prefijo solo no responde (community, large o extended community, as-path, protocolo, tag, métrica, vecino, origin, preferencia local, siguiente salto, validación RPKI, VRF de origen, política llamada) no se adivina: el term aparece como "decide, si la ruta tiene eso", y la prueba sigue como si no lo tuviera, para que usted vea los dos caminos. Un term con `then next policy` termina la prueba con `result: next policy, by term <term>`. Una política con `default-action` termina con `result: reject, by the policy's default-action` (o `accept`). Cuando ningún term decide y no hay `default-action`, la respuesta da los dos comportamientos por defecto:

```
prisma> test policy SAIDA 198.51.100.0/24
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: if the route's community match: reject
      the prefix alone does not say
result: no term decided it
    on a BGP neighbor that is accepted; exported into OSPF or IS-IS it is not
    as a network, aggregate-address, table or FIB policy it is accepted; as the
    routes an origination, a suppression or a conditional advertisement waits
    for, it is not
    imported into a VRF, by vrf-import, it is not
```

# Lo que se rechaza

Todo lo que está abajo se rechaza **en el prompt**, y nada se guarda en la candidata.

| Usted escribió | El equipo dice |
|----------------|----------------|
| un nombre con un carácter fuera de lo permitido o con más de 32 | `prefix-list '<nombre>': not a name` (o el tipo que sea), y debajo `a name is letters, digits, '.', '_' and '-', at most 32 characters`; el prompt sigue en el nivel en que estaba |
| `10.0.0.1/8` en una lista o en un `route-filter` | `'10.0.0.1/8' is not a network (address/length, host bits zero)` |
| `community C members 70000:1` | `'70000:1' is not a community: ASN:value, each 0-65535` |
| una expresión de as-path que no compila | `the expression does not compile`, con el motivo |
| `from route-filter 10.0.0.0/8 between` | `route-filter: exact, orlonger, longer or upto /<length>` |
| `from route-filter 10.0.0.0/8 upto /4` | `the length is between 8 and 32` |
| `from protocol rip` | `from protocol: bgp, ospf, isis, static, direct or kernel` |
| `from metric 99999999999` | `from metric <0-4294967295>` |
| una condición que no existe | `from:` seguido de la lista de condiciones |
| `large-community L members 1:2` | `'1:2' is not a large community: ASN:value:value, each 0-4294967295` |
| `extended-community E members xx:1:2` | `'xx:1:2' is not an extended community: rt:<asn>:<value> or soo:<asn>:<value>, or an address in place of the ASN` |
| `from family ipx`, `from origin bgp`, `from validation-state good` | la lista de lo que acepta la condición |
| `set extended-community delete E` | `set extended-community add\|set <name>`: el enrutamiento no quita extended communities por lista |
| `then next hop` | `then: accept, reject, next term or next policy; a change to the route is 'set ...'` |
| un segundo `from policy` en el mismo term | `a term calls one policy` |
| `neighbor ... import [ A A ]` o dos nombres sin corchetes | `usage: neighbor <peer> import\|export <policy> \| [ <policy>... ]` |
| `set next-hop self` en una política aplicada como `import`, `set next-hop peer-address` como `export`, `set metric-type` fuera de un `export` hacia OSPF | `this policy cannot be applied as import; nothing staged` (o `export`), con una línea por term culpable |
| una acción que no existe | `set:` seguido de la lista de acciones |
| `set src` en una política aplicada fuera de una `fib-policy` | `cannot be applied on network; nothing staged` (o donde sea), y debajo `set src is for a fib-policy` |
| en una `fib-policy`, un `set` que no es `src`; en una `table-policy`, uno que no es `metric` ni `next-hop`; en una `suppress-policy` o en un `advertise`, cualquier `set` | `set <acción> does nothing on <dónde>`, con lo que acepta ese lugar |
| `aggregate-address ... summary-only suppress-policy ...` | `summary-only suppresses every more specific route, and suppress-policy the ones it accepts: one or the other` |
| `fib-policy connected ...`, o uno de los comandos nuevos escrito a medias | `not how this statement is typed; nothing staged`, y debajo la forma correcta |
| `from protocol` en una política del `default-information` | `from protocol has nothing to match on ospf default-information` (o `isis`) |
| `set next-hop abc` | `set next-hop: 'abc' is not an address` |
| `then` con algo que no sea `accept`, `reject` o `next term` | `then: accept, reject or next term; a change to the route is 'set ...'` |
| `no` de algo que no existe | `policy-options has no such statement; nothing removed`, con la declaración |
| `neighbor ... import` o `export` de una política que no existe, o `export` en un IGP | `no policy-statement named <nombre>` |
| una `route-map` o lista con nombre que empieza por `PO-` | `the PO- names belong to policy-options; nothing staged` |
| en el `commit`, un term que cita una `prefix-list`, `community`, `large-community`, `extended-community`, `as-path` o política que no existe | `policy-statement <política> term <term>: no prefix-list <lista>` (o el tipo que sea); no se aplica nada |
| en el `commit`, `set next-hop discard` en el `import` de un vecino eBGP directo sin `disable-connected-check` | `set next-hop discard is not resolved on a single-hop eBGP neighbor: add 'neighbor <vecino> disable-connected-check', or the route is never installed and its traffic is forwarded` |
| en el `commit`, políticas que se llaman en círculo con `from policy` | `policy-statement <política>: from policy reaches itself again` |
| en el `commit`, un term `then reject` con `from policy X`, cuando X tiene un term con `from policy` que no es `then accept` | `policy-statement <política> term <term>: then reject with from policy X, and ...` |
| en el `commit`, dos cosas que el compilador nombraría igual en FRR: una política llamada `X-SUB` o `X-NOT` junto al mapa que llama `from policy X`, un term `1-Ta` de la política `X` junto al term `a` de la política `X-T1`, una comunidad borrada con el mismo nombre de la lista de un term | `policy-statement <política>: <uno> and <otro> compile to the same FRR route-map <nombre>; FRR keeps one object per name, so rename one of them` (o la lista que sea) |
| `vrf-import prisma-bp ...`, o un `vrf-import` dentro del `router bgp <asn> vrf prisma-bp` | `prisma-bp is a VRF of the box's own: no route leaks out of it` (o `into it`) |
| `vrf-import red` dentro del `router bgp <asn> vrf red` | `a VRF does not import from itself (red)` |
| `vrf-import` fuera de `address-family ipv4 unicast` o `ipv6 unicast` | `a VRF imports per address family` |
| `vrf-import <vrf> policy B` en una familia que ya importa mediante la política A, o `vrf-import <vrf>` sin política | `this address family imports through policy A`, y debajo la forma correcta |
| `set next-hop` o `set extended-community` en una política de `vrf-import`; `from neighbor` en ella | `set next-hop does nothing on vrf-import` (o la acción que sea), con lo que ella acepta; `from neighbor matches nothing on vrf-import` |
| `no vrf-import <vrf>` de una importación que la familia no tiene | `nothing removed`, y debajo `this address family does not import <vrf>` |
| `from source-vrf` en una política aplicada fuera de un `vrf-import` | `from source-vrf is for a vrf-import, where a route is leaked` |
| en el `commit`, un term con más de 10 combinaciones | `more than 10 combinations of neighbor, origin, local-preference, next-hop, validation-state and source-vrf; split it into terms` |

## Lo que solo existe en BGP, exportado a un IGP

Una ruta de OSPF o de IS-IS no tiene community, AS-path, preferencia local ni siguiente salto de BGP. Un term que habla de esas cosas se rechaza en el momento del `export`: el equipo responde `this policy cannot be exported into isis; nothing staged` (o `ospf`) y, debajo, una línea por term culpable, indicando el term y lo que en él `is for BGP routes`.

Lo que cuenta como exclusivo de BGP: `from community`, `from as-path`, `from neighbor`, `from origin`, `from local-preference`, `from next-hop`, `from large-community`, `from extended-community`, `from validation-state`, `from source-vrf`, `set local-preference`, `set community`, `set large-community`, `set extended-community`, `set as-path-prepend`, `set as-path`, `set med`, `set weight`, `set origin` y `set next-hop`. Vale también para las políticas que la exportada llama con `from policy` o que vienen después de ella en la cadena. El rechazo existe porque ignorar la condición en silencio exportaría justamente lo que el term se escribió para retener. Si la misma lógica sirve para ambos, escriba dos políticas: una para el vecino BGP y otra para el IGP.


:::warning
**Verifique con** `**show policy <política>**` **y** `**test policy**` **antes del** `**commit**`**.** Una política aplicada en un vecino decide lo que transporta la sesión: un term que no hace lo que usted pensaba corta o filtra hacia afuera prefijos en toda la sesión. El term puede escribirse antes de la lista que cita, pero el `commit` rechaza un term que cita una lista que no existe. Una `prefix-list` que existe y está **vacía** no coincide con ninguna ruta: el term que depende solo de ella no toma nada. Y recuerde que, en un vecino BGP, lo que ningún term decide se **acepta**; para anunciar solo lo que dicen las listas, termine la política con un term que rechace el resto.

:::

## Cuántas rutas coincidieron con cada term

`show policy <política>` termina con los contadores: en cada lugar donde se aplica la política, cuántas rutas coincidieron con cada term. Vienen del propio enrutamiento (el proceso que ejecuta ese uso, `bgpd` en BGP, `ospfd` e `isisd` en la exportación al IGP, `zebra` en la `fib-policy`, cuenta cuántas veces coincidió cada cláusula), y el equipo devuelve cada cláusula al term, y a la política dentro de la cadena, de donde vino:

```
prisma> show policy UP
policy-statement UP
  terms: 3
  ...
  applied: neighbor 10.0.0.2 import [ UP TAIL ]
  hits, the routes each term matched, by FRR's counters:
    neighbor 10.0.0.2 import
      bgpd, route-map PO-UP+TAIL-IN
              1  UP term cust
              1  UP term black
              1  UP term bogon
              1  TAIL term t
              1  no term decided: the use's default, accept
  a hit is a clause that matched a route, each time the daemon ran the map: a
  route evaluated again - a soft reset, a change - counts again. One route-map
  serves every place it is applied, and they share its counts.
```

Tres cosas para leerlo correctamente:

* **Cuenta coincidencias, no rutas.** Una ruta evaluada de nuevo (un soft reset, una actualización del vecino) cuenta de nuevo. El número sube con las rutas, pero no es el tamaño de la tabla.
* **Dos lugares que aplican la misma política comparten el contador.** Dos vecinos con `import UP` son una sola cuenta; la lista de lugares aparece encima de ella.
* `**-**` **y** `**no counter**` **no son cero.** `-` es un term que no existe en ese uso (un `from protocol` de otro protocolo, por ejemplo). `no counter` es un proceso que no respondió por esa política: no está en ejecución, o no la tiene.

## Probar con una ruta descrita: `test policy ... route`

En lugar de solo el prefijo, describa la ruta e indique dónde está la política: `neighbor`, `community`, `large-community`, `extended-community`, `as-path`, `origin`, `local-preference`, `med`, `tag`, `next-hop`, `validation-state`, `source-vrf` y `weight`. Las listas van separadas por coma, sin espacio: `community 65001:10,65001:20`, `as-path 65001,64512`. Una cadena se prueba con el nombre de las políticas unido por `+`: `UP+TAIL`.

```
prisma> test policy UP+TAIL route 203.0.113.0/24 neighbor 10.0.0.2 community 65001:10 as-path 65001
at import: UP+TAIL
  bgpd, route-map PO-UP+TAIL-IN
  the route: as described: a list not given is none, a value not given is not
  known
    prefix 203.0.113.0/24
    neighbor 10.0.0.2
    community 65001:10
    large-community (none)
    extended-community (none)
    as-path 65001
  UP term cust: match
      then accept
result: accept, by UP term cust
  the route after the sets:
    prefix 203.0.113.0/24
    neighbor 10.0.0.2
    community 65001:10 65000:538
    large-community (none)
    extended-community (none)
    as-path 65001
    local-preference 200
```

El `at` indica en qué uso: `import` (el valor por defecto), `export`, `default-originate`, `network`, `aggregate`, `suppress`, `advertise`, `condition`, `table`, `vrf-import`, `ospf-default`, `isis-default`, `ospf <protocolo>`, `isis <protocolo>` y `fib <protocolo>`. Eso cambia la respuesta: qué pasa con la ruta que ningún term decidió, con qué coincide un `from protocol`, y qué `set` valen allí; un `set` que ese uso ignora aparece como "changes nothing here".

La simulación sigue el orden y las reglas del enrutamiento: la cadena, el `then next policy`, el `default-action`, el `from policy` (cuyo rechazo rechaza la ruta; en un term `then reject`, lo que ella acepta se rechaza y lo que ella rechaza sigue). Una lista que usted no describió está vacía; un valor que usted no dio es **desconocido**, y el term que lo necesita no se adivina: la respuesta dice que supuso que no coincidió, y que vale solo si no coincide.

**Validación RPKI sin caché.** Sin un caché RPKI sincronizado, el enrutamiento no coincide con **ningún** estado de validación, ni siquiera `unknown`. La simulación lo indica cuando el estado de la ruta no se conoce, y una ruta leída del equipo sin estado RPKI no coincide con ningún `from validation-state`, igual que en el enrutamiento.

## Probar con una ruta que tiene el equipo: `test policy ... rib`

`test policy <política> rib <prefijo> [vrf <nombre>] [neighbor <dirección>] [at <uso>]` lee la ruta del equipo en lugar de que usted la describa: la ruta del protocolo del uso en la tabla de enrutamiento para una exportación a IGP y una `fib-policy`, y el mejor camino de BGP para el resto. En el `import`, es el camino que vino del `neighbor` (o el del mejor camino); en el `export`, el `neighbor` es a quién va la ruta; en el `vrf-import`, el `vrf` es la VRF **de donde** viene la ruta.

Lo que el equipo guarda de una ruta importada ya pasó por la política. Para probar el `import`, el equipo deshace lo que se puede deshacer e indica el resto:

* lo que la política aplicada puede cambiar se toma de la copia que envió el vecino, cuando existe, y solo existe con `neighbor <dirección> soft-reconfiguration inbound`;
* las communities que la política solo agrega se retiran;
* lo que no se puede saber queda desconocido, y la respuesta indica qué atributo;
* una ruta que el `import` rechazó solo puede probarse con `soft-reconfiguration inbound`, y sin sus communities, que esa copia no guarda.

## Qué política decidió esta ruta: `show policy route`

El enrutamiento no guarda qué term decidió cada ruta, solo cuántas rutas coincidieron con cada term. `show policy route <prefijo>` responde con lo que se puede saber: en cada lugar por donde pasó la ruta, el term que decidió, **simulado** como en `test policy ... rib`, y al lado lo que el enrutamiento hizo de verdad: el camino en la tabla de BGP, el anuncio al vecino, la ruta en la tabla de enrutamiento y en el reenvío, el LSA externo de OSPF:

```
prisma> show policy route 203.0.113.0/24
route 203.0.113.0/24
  neighbor 10.0.0.2 import: UP+TAIL
    simulated: accept, by UP term cust
    FRR: the path from 10.0.0.2 is in the BGP table: accepted
  table-policy: TBL
    simulated: accept, by no term: the default of the use
    FRR: zebra holds it from BGP
  fib-policy bgp: FIB
    simulated: accept, by term c
    FRR: zebra installed it
  ospf export, from bgp: OEXP
    simulated: accept, by term c
    FRR: ospfd originates it, as external
  neighbor 10.2.0.2 export: DN
    simulated: accept, by term mine
    FRR: advertised to 10.2.0.2
```

Si la simulación y el enrutamiento no coinciden, la línea lo indica; si la simulación tuvo que suponer algo que no sabía, también indica que la respuesta del enrutamiento es la que vale. Una ruta que el vecino envió con el propio AS del equipo en el camino es descartada por BGP antes de cualquier política, y aparece así, y no como rechazo de la política. El origen del default, el anuncio condicional y la supresión de un agregado no tratan sobre una ruta; los contadores de `show policy <política>` indican con qué coincidieron.

# Vea también

* [Configuración de BGP](/s/wiki-es/doc/configuracion-de-bgp-s4fDtTXgPx) — vecinos, peer groups y familias de direcciones, donde se aplican `import` y `export`
* [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3) — OSPF e IS-IS, y las route-maps y listas escritas en la forma del propio enrutamiento
* [commit, rollback y save](/s/wiki-es/doc/commit-rollback-y-save-6sAWJkXNO3) — cómo llega la política al enrutamiento y cómo deshacer
* [Comandos show](/s/wiki-es/doc/comandos-show-yfCeHVInDX) — `show policy` junto a los demás
