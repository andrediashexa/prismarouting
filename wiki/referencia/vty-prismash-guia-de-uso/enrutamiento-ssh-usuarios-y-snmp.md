# Enrutamiento, SSH, usuarios y SNMP

Todo lo que no es CGNAT: rutas estáticas, OSPF e IS-IS, política de enrutamiento, la placa del dataplane, DNS, SSH, cuentas y SNMP — cada declaración con su argumento y su efecto. Todos estos cambios exigen la capacidad de **administrador** (`prisma-admin` o root).

# Enrutamiento (FRR)

```
router static
router bgp <AS>
router ospf [vrf <nombre>]
router ospf6
router isis <nombre>
```

Todo lo que enruta queda debajo de `router`: los protocolos, y también las rutas que nadie aprende — las estáticas. Los protocolos son de FRR. Dentro de cada bloque, `?` y TAB ofrecen las declaraciones que esta VTY modela (las tablas de abajo); el resto de la sintaxis de FRR también se acepta, y cada línea la verifica el propio FRR en el momento en que se escribe — una línea que él no acepta se rechaza en el prompt, con la respuesta de FRR, y no se guarda nada. BGP tiene página propia: [Configuración de BGP](/s/wiki-es/doc/configuracion-de-bgp-s4fDtTXgPx).

`router ospf` y `router ospf6` son **instancias separadas**, no dos mitades de lo mismo: la primera lleva IPv4, la segunda lleva IPv6, y un equipo puede correr las dos al mismo tiempo. Además, no activan la interfaz de la misma forma. OSPFv2 acepta `network <prefijo> area <área>` dentro del bloque; OSPFv3 no tiene ningún `network` - quien coloca la interfaz en el área es `ipv6 ospf6 area <área>` dentro del bloque `interface`:

```
configure
 router ospf6
  ospf6 router-id 10.0.0.1
  exit
 interface ens18
  ipv6 ospf6 area 0
  exit
 commit
```

Si usted escribe solo `router ospf6` y espera que encuentre las interfaces por sí mismo, la configuración se acepta y ninguna adyacencia se levanta: falta la línea en la interfaz.

## Lo que ofrece el bloque `router ospf`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `ospf router-id` | `<A.B.C.D>` | el identificador de este equipo en OSPF |
| `network` | `<A.B.C.D/M> area <área>` | corre OSPF en las interfaces que cubre ese prefijo |
| `area`  | `<área> ...` | ajustes de un área, en la sintaxis de FRR |
| `passive-interface` | `<interfaz>` | corre OSPF en la interfaz sin enviar hello — lo que debe ser todo puerto orientado a suscriptores. FRR lo guarda como `ip ospf passive` en el bloque de la interfaz |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <nombre>\|policy <política>]` | anuncia `0.0.0.0/0`; con `policy`, mientras haya una ruta que la política acepta ([Política de enrutamiento](/s/wiki-es/doc/politica-de-enrutamiento-policy-options-QBSNhzhSCN)) |
| `export` | `<política>` | exporta a OSPF lo que acepte una política de `policy-options`; lo que ningún término acepta **no** se exporta |
| `redistribute connected\|static\|bgp\|isis` | `[metric <n>] [metric-type 1\|2] [route-map <nombre>]` | inyecta rutas de otro origen. `bgp` en un equipo de borde es una tabla completa dentro del IGP: asígnele una route-map |
| `auto-cost reference-bandwidth` | `<1-4294967>` | en Mbit/s. Sin esto, todo enlace de 100 Mbit/s en adelante cuesta 1, y en un equipo de 10G no hay elección de camino |
| `maximum-paths` | `<1-256>` | cuántos caminos de igual costo instalar |
| `log-adjacency-changes` | `[detail]` | registra cada cambio de adyacencia |
| `timers throttle spf` | `<retardo> <espera-inicial> <espera-máxima>` | en milisegundos |
| `distance` | `<1-255>` | distancia administrativa |
| `neighbor` | `<A.B.C.D> [poll-interval <n>] [priority <n>]` | un vecino en una red no broadcast |

## Lo que ofrece el bloque `router ospf6`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `ospf6 router-id` | `<A.B.C.D>` | el identificador de este equipo en OSPFv3; tiene forma de dirección IPv4 y no es una dirección |
| `area`  | `<área> ...` | ajustes de un área |
| `redistribute connected\|static\|bgp\|isis` | `[route-map <nombre>]` | inyecta rutas de otro origen |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <nombre>]` | anuncia `::/0` |
| `auto-cost reference-bandwidth` | `<1-4294967>` | como en OSPF |
| `maximum-paths` | `<1-256>` | cuántos caminos de igual costo instalar |
| `log-adjacency-changes` | `[detail]` | registra cada cambio de adyacencia |
| `timers throttle spf` | `<retardo> <espera-inicial> <espera-máxima>` | en milisegundos |
| `distance` | `<1-255>` | distancia administrativa |

No hay `network` ni `export` en OSPFv3: la interfaz entra por la línea `ipv6 ospf6 area` en su bloque.

## Lo que ofrece el bloque `router isis <nombre>`

| Comando | Argumento | Qué hace |
|---------|-----------|----------|
| `net`   | `<49.xxxx.xxxx.xxxx.xxxx.00>` | la dirección NSAP de este equipo; sin ella la instancia no se levanta |
| `is-type` | `level-1\|level-2-only\|level-1-2` | en qué niveles participa el equipo; el núcleo de un proveedor suele correr `level-2-only` |
| `metric-style` | `wide\|narrow\|transition` | `narrow` se detiene en 63 y no expresa una topología de 10G en adelante; `transition` envía los dos durante una migración |
| `topology ipv6-unicast` |           | un SPF separado para IPv6 (RFC 5120) |
| `set-overload-bit` | `[on-startup <0-86400>]` | les dice a todos que no transiten por este equipo; vea el drenaje más abajo |
| `export` | `<política>` | exporta a IS-IS lo que acepte una política; lo que ningún término acepta no se exporta |
| `redistribute ipv4\|ipv6` | `<origen> level-1\|level-2 [metric <n>] [route-map <nombre>]` | inyecta rutas de otro origen |
| `default-information originate` | `ipv4\|ipv6 level-1\|level-2 [always] [metric <n>] [route-map <nombre>\|policy <política>]` | anuncia una ruta default |
| `domain-password` | `[clear\|md5] <contraseña>` | autenticación de nivel 2; nunca se muestra de vuelta |
| `area-password` | `[clear\|md5] <contraseña>` | autenticación de nivel 1; nunca se muestra de vuelta |
| `log-adjacency-changes` |           | registra cada cambio de adyacencia |
| `lsp-mtu` | `<128-4352>` | el mayor LSP que genera el equipo; si es mayor que la menor MTU de las interfaces, no salen |
| `lsp-gen-interval` | `<1-120>` | segundos entre regeneraciones de LSP |
| `spf-interval` | `<1-120>` | segundos entre ejecuciones del SPF |
| `max-lsp-lifetime` | `<350-65535>` | segundos que vale un LSP |

Las declaraciones de OSPF e IS-IS que van **en la interfaz** (`ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`) están en [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg).

`export <política>` rechaza una política que dependa de lo que solo lleva una ruta BGP — comunidad, AS-path, local-preference, prepend, próximo salto —, en lugar de ignorarlo en silencio. Las políticas se escriben en `policy-options`, descrito en [Política de enrutamiento](/s/wiki-es/doc/politica-de-enrutamiento-policy-options-QBSNhzhSCN); `test policy <política> <prefijo>` dice qué hace con una ruta antes de aplicarla.

## Rutas estáticas: el bloque `router static`

Una ruta estática no necesita ningún protocolo y es lo primero que se configura en un equipo nuevo:

```
configure
 router static
  0.0.0.0/0 next-hop 200.150.0.1
  192.168.0.0/24 next-hop 172.16.0.1 distance 200 tag 100 description link para o POP-2
  2001:db8::/32 next-hop 2001:db8:0:1::1
  exit
 commit
```

Dentro del bloque:

| Comando | Qué hace |
|---------|----------|
| `<prefijo> next-hop <próximo-salto\|interfaz\|blackhole\|Null0\|reject>` | una ruta estática |
| `... distance <1-255>` | la distancia administrativa; sin ella vale la del enrutamiento |
| `... tag <1-4294967295>` | una marca numérica, con la que una política de redistribución puede coincidir |
| `... bfd [multi-hop] [source <dirección>] [profile <nombre>]` | la ruta sale de la tabla mientras la sesión BFD hacia el próximo salto esté caída (vea la sección BFD, más abajo) |
| `... description <texto>` | un texto suyo sobre la ruta, de hasta 63 caracteres; va siempre al final |
| `no <prefijo> next-hop <próximo-salto>` | elimina la ruta, con la distancia, tag y descripción que tenga |
| `exit`  | sale del bloque |

`distance` y `tag` pueden ir en cualquier orden. **Una ruta es el prefijo y el próximo salto:** escribir el mismo par otra vez cambia esa ruta, no crea una segunda al lado. Para cambiar solo la descripción, escriba la ruta completa otra vez con la descripción nueva.

**La familia sale del prefijo.** Un prefijo con dos puntos es IPv6 y no puede ser otra cosa, así que no existe un comando para cada familia. Pedir la familia otra vez sería darle al operador la oportunidad de contradecirse.

**La descripción queda guardada en el propio equipo**, porque el enrutamiento no tiene dónde guardarla. Aparece en `show configuration` y `show running-config` junto a la ruta, entra en el `commit` y en el `rollback` como cualquier otro cambio, y desaparece sola si la ruta deja de existir.


:::warning
**La forma** `**route <prefijo> <próximo-salto>**` **dentro del bloque ya no existe.** Se rechaza en el prompt y también en un archivo guardado. Un equipo cuyo archivo guardado todavía tiene esa forma rechaza el archivo completo en el arranque y se levanta sin ninguna configuración. En un equipo que tiene rutas estáticas, ejecute `save` después de actualizar y antes de reiniciar: graba las rutas vigentes ya en la forma nueva. [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) lo explica.

:::

`commit check` rechaza un próximo salto que no está en ninguna red conectada:

```
error: this next hop is on no connected network: 172.31.99.1
    router static / 10.55.0.0/16 next-hop 172.31.99.1
    the route would be accepted and never reach the forwarding table
```

Si se aceptara, esa ruta quedaría en FRR y nunca llegaría a la tabla de reenvío.

Observe que el rechazo nombra la ruta **en el bloque en que está** - `router static / 10.55.0.0/16 next-hop ...` - y no como una línea suelta. Es así incluso cuando la ruta se escribió en el nivel superior, en la grafía del enrutamiento, `ip route ...`: el equipo muestra dónde vive la ruta en la configuración, que es donde usted la va a editar. Una estática dentro de una VRF no tiene ninguna grafía de nivel superior que buscar, y por eso aparece el bloque.

**`router static` es la tabla principal.** Las rutas estáticas de una VRF van en el bloque `vrf <nombre>` del enrutamiento, en su propia grafía - `ip route <prefijo> <próximo-salto>` o `ipv6 route ...` -, y el bloque se cierra con `exit` o `exit-vrf`:

```
configure
 vrf red
  ip route 10.55.0.0/16 172.31.99.1
  exit-vrf
 commit
```

`commit check` verifica el próximo salto en la tabla **de esa VRF**, no en la principal: un próximo salto que solo existe en la principal se rechaza allí, y uno que solo existe en la VRF se acepta. Una VRF que el enrutamiento todavía no tiene activa - sin el `vrf <nombre> table <n>` que crea su dispositivo en Linux - no tiene tabla contra la cual verificar; el equipo dice que esas rutas no se verificaron, y no rechaza.

## BFD: la caída del vecino en milisegundos

BGP solo percibe que el vecino murió cuando se agota el hold time - 90 segundos por defecto - y una ruta estática nunca lo percibe: sigue apuntando a un próximo salto que dejó de reenviar. El caso que importa es el vecino que **deja de responder con el enlace arriba** - un switch en el medio que perdió la VLAN, una placa trabada -, porque entonces ninguna interfaz cae para avisarle a nadie. BFD intercambia paquetes pequeños con el vecino a intervalos de milisegundos y tira la sesión cuando dejan de llegar.

```
configure
 bfd
  profile rapido
   receive-interval 100
   transmit-interval 100
   detect-multiplier 3
  exit
  peer 10.0.0.2 interface enp1s0
   profile rapido
  exit
 exit
 router bgp 65000
  neighbor 10.0.0.2 bfd profile rapido
 exit
 router static
  203.0.113.0/24 next-hop 10.0.0.2 bfd profile rapido
 exit
 commit
```

Con 100 ms y multiplicador 3, la sesión cae después de 300 ms sin paquetes. Medido en el laboratorio, con el vecino mudo y el enlace arriba: BFD vio la caída en 252 ms, la ruta estática salió de la tabla de reenvío en 252 ms y la ruta aprendida por BGP en 325 ms - con el hold time de BGP en 30 segundos. Sin BFD, ninguna de las dos salió en 12 segundos.

Dentro de `bfd`:

| Comando | Qué hace |
|---------|----------|
| `peer <dirección> [multihop] [local-address <dirección>] [interface <interfaz>] [vrf <nombre>]` | una sesión con un vecino; entra en su bloque |
| `profile <nombre>` | un conjunto de tiempos con nombre, que usan sesiones y protocolos; entra en su bloque |
| `exit`  | sale del bloque |

Dentro de un `peer` o de un `profile`:

| Comando | Qué hace |
|---------|----------|
| `detect-multiplier <2-255>` | cuántos paquetes perdidos hasta que la sesión cae (por defecto 3) |
| `receive-interval <10-4294967>` | el intervalo más lento en que este equipo quiere recibir, en ms (por defecto 300) |
| `transmit-interval <10-4294967>` | el intervalo más rápido en que este equipo envía, en ms (por defecto 300) |
| `echo-mode` | envía paquetes de eco que el vecino devuelve; solo IPv4, a menos que el vecino también sea FRR |
| `echo receive-interval` y `echo transmit-interval` | los intervalos del eco, en ms (por defecto 50) |
| `passive-mode` | espera que el vecino hable primero |
| `minimum-ttl <1-254>` | solo multihop: el menor TTL aceptado (por defecto 254) |
| `shutdown` | mantiene la sesión caída, conservando la configuración |
| `profile <nombre>` | solo dentro de un `peer`: la sesión usa los tiempos de ese perfil; lo que se configure en la propia sesión prevalece |

El mismo `profile <nombre>` significa dos cosas: en el nivel de `bfd` **crea** el perfil, dentro de un `peer` **usa** el perfil. El prompt muestra dónde está usted.

Quien pide la sesión es el protocolo: `neighbor <dirección> bfd [profile <nombre>]` en BGP ([Configuración de BGP](/s/wiki-es/doc/configuracion-de-bgp-s4fDtTXgPx)), y `bfd` después del próximo salto en una ruta estática. Un `peer` en el bloque `bfd` sirve para darle a la sesión tiempos propios, y para que exista aunque ningún protocolo la pida.

Para ver:

* `show bfd peers` - dos líneas por sesión: dónde corre (salto único o multihop, la interfaz, el origen, el perfil, y si la pidió un protocolo), y luego el estado, desde hace cuánto, el intervalo, en cuánto tiempo cae, el diagnóstico cuando lo hay y cuántas veces cayó.
* `show bfd peer <dirección>` - cada sesión con ese vecino en detalle: los tiempos de este equipo y los del vecino, el tiempo de detección, el eco, los paquetes que entraron y salieron, y cuántas veces subió y cayó.

En OSPF, OSPFv3 e IS-IS, BFD va en la interfaz, con o sin perfil: `ip ospf bfd`, `ip ospf bfd profile <nombre>` (y `ipv6 ospf6 bfd ...`, `isis bfd ...`). El equipo escribe la línea `bfd` sola antes de la del perfil, porque FRR rechaza el perfil sin ella.

Las sesiones también salen por la API, campo a campo y con una advertencia en cada cambio de estado, y por SNMP, en la BFD-STD-MIB (RFC 7331), que responde el agente del propio equipo. Una sesión que cae o vuelve genera las advertencias `bfdSessDown` y `bfdSessUp` de la propia MIB (desde la 0.152.3), para cada `snmp trap-host` configurado; vea [Monitoreo y SNMP](/s/wiki-es/doc/monitoreo-y-snmp-zQ9bTfrh8z).

La configuración de BFD también se lee y se escribe campo a campo por la API: las sesiones y los perfiles del bloque `bfd`, el perfil de BFD de un vecino BGP y el BFD de una ruta estática. Una sesión se identifica por la dirección, el multihop, la dirección local, la interfaz y la VRF, como la identifica FRR; eliminar una sesión, un perfil o el bloque completo elimina todo lo que está dentro de ellos.


:::warning
**En un equipo actualizado por una versión de corrección, BFD solo funciona después del reboot.** Quien corre las sesiones es `bfdd`, y lo activa el contenedor de enrutamiento cuando ese contenedor se levanta en la versión nueva - lo que, en una actualización de corrección, ocurre en el próximo reboot. Hasta entonces, `commit` rechaza cualquier configuración con `bfd`, diciendo que `bfdd` no está corriendo y que el reboot lo activa. Es a propósito: sin `bfdd`, BGP y la ruta estática aceptan la palabra `bfd` y no se crea ninguna sesión - el vecino no quedaría vigilado por nadie.

:::

## Ver la tabla: `show route`, no `show ip route`

Tres tablas guardan rutas en este equipo y solo una reenvía paquetes:

```mermaid
flowchart LR
    RIB["RIB de FRR<br/>lo que FRR decidió"] --> K["FIB del kernel<br/>lo que FRR instaló"]
    K -->|"netlink / linux_nl"| VFIB["FIB de VPP<br/>lo que reenvía"]
```

| Comando | Responde a partir de | Úselo cuando |
|---------|----------------------|--------------|
| `show route` | **FIB de VPP**       | es la pregunta normal: qué reenvía este equipo |
| `show route verbose` | las dos, comparadas  | el tráfico no pasa y la ruta "está ahí" |
| `show route <prefijo>` | las dos, para un destino | investigar un destino específico |
| `show ip route` | RIB de FRR (pasado a `vtysh`) | verificar lo que decidió FRR, no lo que hace el equipo |

```
prisma> show route
  Destination    Next hop    Interface Kind
  0.0.0.0/0      200.150.0.1 ens19     forward
  200.150.0.0/24 -           ens19     connected
  203.0.113.0/24 200.150.0.1 ens19     forward

  3 entries carrying traffic
```

Las columnas tienen el ancho de lo que el equipo tiene de hecho. Cuando una ruta no cabe en la consola — un prefijo IPv6 largo, por ejemplo —, cada ruta se convierte en un pequeño bloque, con `via:`, `on:` y `kind:` en líneas propias, en lugar de una tabla que la terminal partiría por la mitad.


:::warning
`show ip route` responde con la tabla de FRR. En un equipo que está descartando el tráfico, la ruta aparece **presente y correcta** ahí — porque quien reenvía es la FIB de VPP, y la respuesta de FRR no dice nada sobre si la ruta llegó allí.

Cuando el tráfico no pasa y la ruta parece correcta, use `show route verbose`: compara las dos y marca dónde discrepan.

:::

La columna VPP dice lo que VPP **tiene** — `forward`, `connected`, `local`, `neighbour`, `drop` o `absent` — y no sí/no. Un prefijo que VPP descarta a propósito y uno del que nunca oyó hablar son situaciones distintas, con causas distintas.

Las direcciones locales y las rutas por interfaces que VPP no posee — la de gestión, por ejemplo — **no** se marcan como discrepancia. Están en FRR y ausentes de la FIB correctamente y para siempre.

## Qué daemons corren

`zebra`, `mgmtd` y `staticd` se levantan siempre. `bgpd`, `ospfd`, `ospf6d` e `isisd` los habilita el instalador - el paquete de Debian los trae desactivados, y un equipo instalado sin esto ofrecía los protocolos en la VTY sin correr ninguno. `bfdd`, que corre las sesiones BFD, lo activa el propio contenedor de enrutamiento al levantarse, y por eso llega también por una actualización de corrección - pero solo a partir del próximo reboot.

`commit check` rechaza un `router <protocolo>` cuyo daemon no está arriba:

```
error: router bgp 65001: bgpd is not running
    the statement would commit and reach nothing. Enable bgpd in
    /etc/frr/daemons and restart frr
```

El autotest de arranque informa cuáles están respondiendo.

## Drenar el equipo para mantenimiento: el bit de sobrecarga

Es el drenaje de IS-IS. Con él activado, el equipo les dice a todos que **no transiten por él**: lo que termina en él sigue siendo alcanzable, pero deja de ser camino de paso — que es lo que usted quiere antes de tocar un equipo en producción.

```
configure
 router isis HEXA
  set-overload-bit
  exit
 commit
```

`no set-overload-bit` lo quita. También acepta `set-overload-bit on-startup <0-86400>`, que lleva el bit durante esa cantidad de segundos después de que el protocolo se levanta — para que el equipo que acaba de reiniciar no atraiga tráfico de paso antes de haber convergido.

El modo de falla de esta función no es activarla: es **olvidarla activada**. Un equipo drenado funciona, atiende, responde a todo lo que usted pregunte, y no lleva el tráfico que debería — durante semanas, si nadie mira.

Por eso la verificación:

```
show isis adjacency
```

Si está activado, la pantalla dice `overload bit set`.


:::info
Esa línea se lee del **LSP que este equipo está inundando**, no de la configuración que pidió el bit. La diferencia es el punto: un drenaje reportado por las palabras que lo pidieron es reportado por una línea que puede no haber surtido efecto. Lo que afirma la pantalla es lo que están recibiendo los vecinos.

Del lado del vecino, el mismo hecho aparece en `show isis database`, en la última columna `ATT/P/OL` de la línea del LSP de este equipo: `OL` es el tercero de los tres, y vale `0/0/1` cuando el bit está activado.

:::

# Tres trampas de enrutamiento

## 1. La MTU heredada traba OSPF

El TAP de `linux_cp` viene con la MTU de la interfaz del dataplane: 1500, el valor por defecto del equipo desde la 0.152.1, o 9000 en un equipo instalado antes de ella. Una interfaz que hereda ese valor sin que el otro lado esté de acuerdo deja la adyacencia OSPF detenida en Exchange. Ajústela con `mtu` dentro del bloque `interface` — cambia la interfaz del dataplane **y** su TAP, juntos.

Vale igual para OSPFv3: también compara la MTU antes de cerrar la adyacencia, y se traba de la misma forma, sin ningún log que diga por qué. `commit check` avisa cuando las dos mitades de la interfaz discrepan; `ipv6 ospf6 mtu-ignore` es la salida cuando no se pueden igualar los dos lados.

## 2. IS-IS no va sobre IP

IS-IS corre directamente sobre el enlace, no sobre IP. Eso cambia lo que debe estar bien para que la adyacencia se levante, y un diagnóstico hecho por dirección IP no ve el problema.

## 3. El CGNAT comiéndose su propio plano de control


:::warning
**Si un pool público contiene una dirección de peering, la sesión BGP cae de forma intermitente sin nada obvio en ningún lugar.**

Las sesiones BGP y las adyacencias OSPF/IS-IS terminan en las direcciones **del propio equipo**. `bpa-out2in` corre en el arco `ip4-unicast`, antes de `ip4-lookup` y por lo tanto antes de que `ip4-local` decida que el paquete es local — es decir, ve primero el paquete de BGP.

:::

Dos defensas, ambas obligatorias en el producto:


1. La creación del pool **se rechaza** cuando el conjunto público contiene una dirección configurada en una interfaz local. El error nombra la dirección y la interfaz.
2. Una dirección configurada **después** del pool no hay forma de rechazarla, así que se reporta con nivel de error en `show logging` — el operador lee eso, no el stderr del proceso de VPP.

En la práctica, las direcciones de pool normalmente **no** son direcciones de interfaz: llegan al equipo por una ruta del upstream, no por configuración local.

# Interfaces

Las declaraciones de interfaz que importan para el enrutamiento — `mtu`, que cambia la interfaz del dataplane **y** su TAP juntos, `urpf strict|loose|off`, y las líneas de OSPF e IS-IS en el puerto — están con el resto del bloque en [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg). uRPF en una interfaz interna es lo que impide que un origen falsificado sea traducido.


:::info
**Toda interfaz con un bloque** `**interface**` **existe para el enrutamiento con su propio nombre.** No hay línea que escribir: el par que ve el enrutamiento se crea solo. `linux-name` solo sirve para dar **otro** nombre. Un puerto que "está configurado y no habla con nadie" es otro asunto; el autotest de arranque dice qué puertos quedaron sin par, si alguno quedó.

:::

# Sistema: la placa entregada al dataplane

```
configure
 system
  dataplane
   interface enp2s0
   exit
  exit
 commit
```

| Comando | Dónde | Qué hace |
|---------|-------|----------|
| `interface <placa>` | `system` > `dataplane` | entrega esta placa, por el nombre que le da **Linux**, al dataplane en su próximo inicio; un nombre que Linux no tiene se rechaza con la lista de los que tiene |
| `no interface` | `system` > `dataplane` | el equipo deja de tomar placas en el próximo inicio del dataplane |
| `control-plane`, `main-thread`, `workers`, `cgnat-workers` | `system` > `cpu` | los núcleos por rol |
| `control-plane`, `buffers` | `system` > `memory` | la memoria por rol |
| `unsupported-sfp allow` | `system` > `hardware` | la placa ixgbe (X520 y familia) acepta un módulo SFP+ que Intel no homologó; `no unsupported-sfp` vuelve al valor por defecto, que rechaza |
| `host <dirección> [...]` | `system` > `logging` | envía el registro de eventos del equipo a un syslog; vale **en el commit** (vea más abajo) |
| `vrf <nombre>` | `system` > `management` | los servicios del propio equipo pasan a vivir en la VRF de gestión; vale **en el commit** (vea más abajo) |

Todas, menos `logging` y `management`, valen a partir del próximo arranque o del próximo inicio del dataplane: `commit` graba la elección y **no reinicia nada**. `cpu` y `memory` se explican en [Dimensionamiento y requisitos](/s/wiki-es/doc/dimensionamiento-y-requisitos-FuEOaBC4vd), y la elección de la placa en [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg).

## Módulo SFP+ no homologado en una placa ixgbe

El driver `ixgbe` (Intel X520, X540, X550 y familia) rechaza la placa completa cuando el módulo SFP+ no está en su lista. La placa desaparece de Linux y no llega al dataplane, y el journal del arranque dice:

```
ixgbe 0000:01:00.0: failed to load because an unsupported SFP+ or QSFP module type was detected.
```

Con un módulo de terceros, que es común, o con un módulo que a veces se lee mal, active la aceptación:

```
configure
 system
  hardware
   unsupported-sfp allow
   exit
  exit
 commit
```

Vale a partir del próximo arranque: el equipo pone `ixgbe.allow_unsupported_sfp=1` en la línea de comandos del kernel, que el driver lee cuando carga. `show system hardware` muestra lo que está configurado y lo que tiene el arranque actual. Intel advierte que un módulo que no probó puede ser inestable; por eso viene desactivado.

## A dónde va el registro de eventos: `system` > `logging`

Lo que muestra `show log` — commits, reinicios con su causa, enlaces, módulos, sesiones BGP y BFD, pools sin bloques, logins — va también a hasta cuatro colectores syslog:

```
configure
 system
  logging
   host 192.0.2.10
   host 192.0.2.11 protocol tcp severity warning
   exit
  exit
 commit
```

| Opción | Por defecto | Qué hace |
|--------|-------------|----------|
| `port <n>` | 514 en UDP, 601 en TCP | el puerto del colector |
| `protocol udp\|tcp` | `udp`       | UDP: un datagrama por evento. TCP: un flujo, con el encuadre por conteo de octetos (RFC 6587) |
| `severity error\|warning\|notice\|info` | `info` (todo) | el nivel mínimo enviado: ese y los más graves |
| `source-address <dirección>` | la de la ruta | la dirección de origen |
| `vrf <nombre>` | la tabla por defecto | la VRF por donde sale; tiene que ser un `vrf <nombre> table <n>` de la configuración |

`no host <dirección>` deja de enviar a él; un segundo `host` con la misma dirección reemplaza al primero. El colector es una **dirección**, nunca un nombre: un equipo cuyo DNS cayó es justamente el que más necesita enviar los eventos. Vale en el `commit`, sin reiniciar nada.

Cada evento sale en RFC 5424, facility `daemon`, con el tipo del evento (`LINK`, `BGP`, `COMMIT`...) en el MSGID y los campos como datos estructurados:

```
<28>1 2026-10-07T00:01:01.250Z edge-1 prisma-routing - LINK [event@67033
source="dataplane" kind="link" subject="memif1/0" reason="link"] interface
memif1/0: link down, administratively up: the wire or the far end
```

**No se pierde nada si el colector TCP cae.** Quien envía es el vigilante de eventos del equipo, a partir del propio `events.log`, que sirve de cola: cada destino guarda hasta dónde ya envió, y lo que esperó se envía cuando el colector vuelve — sin repetir —, incluso después de reiniciar el vigilante o el equipo. `show system logging` muestra cada destino y si está llegando:

```
192.0.2.10:514 udp  info and above
  sending
  sent 3 event(s) since the watcher started, the last at 2026-10-07 00:01:01
192.0.2.11:601 tcp  warning and above
  not reached: connection refused; tried again every 30 s
  sent 0 event(s) since the watcher started
```

**No lo confunda con la exportación del CGNAT.** `nat` > `cgnat` > `logging syslog` envía los registros de quién usó qué puerto público, para orden judicial, y sigue igual. Son dos configuraciones y dos flujos separados — uno no lleva las líneas del otro — y deben ir a colectores distintos.

## La gestión en una VRF propia: `system` > `management`

Desde la 0.152.4, la gestión puede vivir en una VRF propia, para que la tabla principal quede libre para la default de los suscriptores mientras SSH, la API y SNMP siguen alcanzables por el gateway de gestión. La placa de gestión entra en una VRF como cualquier interfaz, con sus rutas en el bloque `vrf <nombre>`, y `system` > `management` > `vrf <nombre>` hace que los servicios del propio equipo la sigan: sin él, la placa queda en la VRF y el equipo **no** responde por ella. Con él, SSH, la API y SNMP responden en las direcciones de la VRF, y lo que el propio equipo origina - DNS, NTP, las descargas de `system upgrade`, las notificaciones SNMP, `system` > `logging` - sale por las rutas de gestión, salvo que la tabla principal tenga una ruta específica al destino. IPFIX por UDP, que envía el dataplane, sigue saliendo por la tabla principal.

Vale en el `commit` y de nuevo en cada arranque. `commit check` rechaza una VRF que la configuración no crea con `vrf <nombre> table <n>` y, en el commit que la activa, una VRF sin ninguna interfaz. `show system management` muestra la VRF configurada y lo que el sistema tiene en vigor. El ejemplo completo, y el caso de la placa de gestión que solo tiene Linux (la virtio de una máquina virtual), están en [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg).

:::warning
**Use** `**commit confirmed**`**.** Si la sesión que hizo el commit pierde el acceso, nadie confirma, y el rollback saca la placa de la VRF y devuelve la gestión a la tabla principal. Sacar la gestión de la VRF termina las sesiones SSH abiertas por ella; el equipo atiende las nuevas por la tabla principal.

:::

# SSH

```
configure
 ssh port <1-65535>
 ssh listen-address <dirección> [<dirección> ...]
 no ssh listen-address
 ssh permit-root-login yes|no|prohibit-password|forced-commands-only
 ssh password-authentication yes|no
```

| Valor de `permit-root-login` | Significado |
|----------------------------|-------------|
| `yes`                      | contraseña o clave |
| `no`                       | de ninguna manera |
| `prohibit-password`        | solo clave  |
| `forced-commands-only`     | solo comandos forzados |

| Ajuste | Por defecto |
|--------|-------------|
| `ssh port` | `22`        |
| `ssh listen-address` | `0.0.0.0 [::]` (todas las direcciones, IPv4 e IPv6) |
| `ssh permit-root-login` | `prohibit-password` |
| `ssh password-authentication` | `yes`       |

`listen-address` tiene que ser una dirección: cualquier otra cosa se rechaza en el prompt, porque SSH rechazaría su propio archivo y no se levantaría — en un equipo cuyo otro camino es un cable serial.

**Desde la versión 0.152.5,** `**ssh listen-address**` **acepta una lista**, IPv4 e IPv6 juntos, como `snmp listen-address` y `api listen-address` desde la 0.151.8:

```
configure
 ssh listen-address 192.0.2.166 2001:db8:1000:6::166
 commit
```

SSH pasa a responder en cada dirección de la lista, y solo en ellas. `show running-config` muestra la IPv6 entre corchetes, como la escribe el propio sshd: `ssh listen-address 192.0.2.166 [2001:db8:1000:6::166]`.

* **Sin** `**listen-address**` — o después de `no ssh listen-address` — SSH responde en todas las direcciones del equipo, **IPv4 e IPv6** (`0.0.0.0 [::]`), que es el valor por defecto del propio sshd. Hasta la 0.152.4, cualquier `commit` que tocara `ssh` grababa `ListenAddress 0.0.0.0` y SSH por IPv6 dejaba de responder.
* **Una dirección que el equipo no tiene se rechaza en el** `**commit**`, antes de avisar al sshd: `error: ssh listen-address 192.0.2.99: not an address of this box`. Una dirección de una interfaz que está en una VRF solo se acepta cuando la gestión está en esa VRF (`system` > `management` > `vrf`), que es lo que le permite al sshd escuchar en ella.
* Un `0.0.0.0` (o `::`) junto con una dirección de la misma familia se rechaza: el comodín ya escucha en todas.
* **Una configuración guardada antes de la 0.152.5 carga igual.** Una dirección sola sigue siendo una dirección sola. Un equipo que nunca tuvo un `commit` de `ssh` guardó `ssh listen-address [::]` — la primera dirección del valor por defecto del sshd, leída sola; cargada en un equipo en que el sshd está en el valor por defecto, esa línea sigue valiendo IPv4 e IPv6, en lugar de quitar SSH por IPv4 en el arranque.

**El** `**commit**` **rechaza una configuración en la que nadie más puede entrar por SSH**, o en la que nadie que entra puede cambiar nada — quitarle el rol al último administrador, por ejemplo. La consola no cuenta: puede no estar al alcance de quien está configurando.

# Nombre del equipo (hostname)

```
configure
 hostname cgnat-sp1
 commit
```

El nombre del equipo, en un solo lugar: el de Linux (lo que responde `hostname`, el `/etc/hostname` y la línea `127.0.1.1` de `/etc/hosts`) y el `sysName` que responde SNMP. Antes de la versión 0.145.0, cambiar el nombre era editar esos archivos a mano.

El nombre sigue las reglas de hostname: letras, dígitos y guiones, con puntos separando las partes, hasta 64 caracteres, y **comenzando por letra** — FRR rechaza cualquier otro. `no hostname` vuelve al nombre por defecto de la instalación, `prisma-routing`.

`show running-config` muestra el nombre que tiene el equipo ahora. Una configuración guardada antes de la 0.145.0 no tiene esa línea y no cambia el nombre del equipo al cargar; el primer `save` después del upgrade pasa a grabarla.

A partir de la versión 0.148.0 **el prompt muestra el nombre del equipo**: `cgnat-sp1>` en el modo operacional y `cgnat-sp1# [edit]` en el de configuración. Cambia justo después del `commit` que renombra el equipo, en la misma sesión. Un equipo con el nombre por defecto de la instalación (`prisma-routing`) sigue mostrando `prisma>`, como en las pantallas de esta guía. Un nombre con más de 20 caracteres aparece cortado, con un `~` al final.

Los daemons de FRR reciben el nombre en el `commit`, pero siguen mostrando el nombre con el que se iniciaron.

# DNS

```
configure
 dns server 1.1.1.1
 dns server 2606:4700::1111
 commit
```

Los servidores DNS que consulta el equipo, sin editar `/etc/resolv.conf` a mano. Hasta tres, consultados en el orden en que se escribieron. Un cuarto se rechaza, porque el sistema solo consulta los tres primeros y un cuarto aparecería en la configuración sin usarse nunca. `no dns server <dirección>` elimina uno.

**En un equipo con gestión por DHCP, DHCP deja de tocar el DNS mientras haya** `**dns server**` **configurado.** Sin esto, la próxima renovación del lease volvería a poner los servidores de DHCP, sin aviso. Eliminado el último `dns server`, el equipo vuelve a usar lo que DHCP (o la instalación) había definido.

`show running-config` muestra los `dns server` que se configuraron por la VTY. Los servidores recibidos por DHCP no aparecen ahí, porque no son una configuración suya.

**Un comando por ssh cae en la VTY.** `ssh <cuenta>@<equipo> "show cgnat pool"` lo ejecuta la VTY, con el rol de la cuenta. Solo root y los administradores (`prisma-admin`) reciben un shell del sistema, ya sea por comando directo, por `scp`/`rsync` o cuando la VTY falla. Una cuenta de CGNAT o de solo lectura que intenta un comando del sistema recibe una explicación y la sesión termina.

# Cuentas de usuario

```
configure
 user <nombre>
  role administrator|cgnat-operator
  operator
  ssh-key <tipo> <clave> [comentario]
  password <hash>
  no role
  no operator
  no ssh-key <clave>
  no password
  exit
 no user <nombre>
```

| Comando | Qué hace |
|---------|----------|
| `role administrator\|cgnat-operator` | lo que la cuenta puede cambiar; sin rol, solo lectura |
| `operator` | la cuenta puede actualizar, reiniciar y apagar el equipo y reiniciar el dataplane; `no operator` lo quita |
| `ssh-key <tipo> <clave> [comentario]` | una clave pública autorizada, como aparece en `authorized_keys`; se puede repetir |
| `password <hash>` | un hash crypt; el texto plano se rechaza |
| `no ssh-key <clave>` | quita una clave; basta con su comienzo |
| `no user <nombre>` | elimina la cuenta; `root` no se puede eliminar — restrínjalo con `ssh permit-root-login` |

El hash aparece como `password <set, not shown>` en `show this` y `show configuration`.

`**password**` **recibe un hash crypt, nunca una contraseña en texto plano.** Genérelo con:

```sh
openssl passwd -6
```

## `role` — lo que la cuenta puede cambiar

| Valor | Lo que la cuenta puede cambiar |
|-------|--------------------------------|
| `role administrator` | enrutamiento, CGNAT, cuentas y la propia máquina |
| `role cgnat-operator` | solo la configuración de CGNAT |
| sin `role` | solo lectura                   |

**La configuración es la que decide.** El rol se escribe en el bloque del usuario, sale en `show running-config` y sobrevive al `save` — y el `commit` lo materializa en los grupos del sistema, agregando los que el rol exige y **quitando** los que ya no exige. Una promoción hecha por fuera, directamente en el grupo, se deshace en el commit siguiente: es aquí donde se escribe.

Además del grupo del rol, toda cuenta creada aquí entra en los grupos que dan **alcance** — los sockets del dataplane y los del enrutamiento. Sin ellos la cuenta tiene el cargo y no puede ejercerlo: todo comando falla por permisos, incluidos los de lectura. Por eso, volver a aplicar el rol por la VTY arregla una cuenta cuyas membresías se desacomodaron.

**Actualizar, reiniciar, apagar el equipo y reiniciar el dataplane no vienen de ningún rol.** Desde la 0.149.26 es `operator`, junto al rol: da exactamente `system upgrade <versión>`, `system reboot`, `system shutdown` y `system dataplane restart`, en la VTY y por la API, y nada más de lo que es de root. Vale con cualquier rol, o sin rol: una cuenta del NOC que solo lee la configuración puede ser la que reinicia el equipo. Y un administrador sin `operator` es rechazado en esos cuatro comandos, por nombre. `root` ya puede todo eso y no recibe `operator`.

`no role` devuelve la cuenta a solo lectura. Un rol que no existe se rechaza con la lista de los que existen:

```
error: role 'supervisor': expected one of administrator, cgnat-operator
```

Y `root` no recibe rol — sus derechos vienen de ser root, no de un grupo:

```
error: root's rights come from being root, not from a group
    this VTY does not manage root's memberships
```

El veredicto queda en la primera línea y el motivo en la segunda, dentro del ancho de la consola. Como frase única pasaba de cien columnas y la terminal la partía donde cayera.

El rechazo vale para cualquier rol dado a `root`, incluso el nombre correcto de un rol que existe: no es el rol lo que está mal, es la cuenta.

# SNMP

El agente es **de solo lectura por construcción** — no existe un camino de escritura que deshabilitar.

```
configure
 snmp enabled yes|no
 snmp listen-address <dirección> [<dirección> ...]
 snmp port <1-65535>
 snmp enterprise-number <1-4294967295>
 snmp community <string>
 snmp trap-host <dirección> community <string> [inform]
 no snmp enabled|listen-address|port|enterprise-number
 no snmp community <string>
 no snmp trap-host <dirección>
```

| Comando | Qué define | Por defecto |
|---------|------------|-------------|
| `snmp enabled` | corre el agente o no | `no`        |
| `snmp listen-address` | una o más direcciones de escucha, IPv4 e IPv6 | `127.0.0.1` |
| `snmp port` | puerto UDP | `161`       |
| `snmp enterprise-number` | el número bajo el cual se sirve la MIB de Prisma; `no snmp enterprise-number` vuelve al del producto | `67033`, el número de Prisma en la IANA |
| `snmp community` | una comunidad SNMPv2c, de solo lectura; puede haber más de una | ninguna     |
| `snmp trap-host` | adónde enviar las notificaciones: la dirección, la comunidad y, con `inform`, una notificación confirmada en lugar de un trap; una por dirección, y escribir la misma dirección de nuevo la reemplaza | ninguno |

Con el valor por defecto `127.0.0.1`, el agente solo responde desde dentro del equipo: para que el NMS lo alcance, `listen-address` debe ser una dirección que el NMS alcance.

Para responder en IPv4 e IPv6 al mismo tiempo, liste las dos direcciones en la misma línea, separadas por un espacio — `snmp listen-address 192.0.2.166 2001:db8:1000:6::166`. `api listen-address` acepta la lista de la misma forma. Escuchar en `0.0.0.0` o `::` no es el camino: abriría el servicio también en las interfaces del dataplane, incluidas las públicas.

`no snmp <ajuste>` devuelve el ajuste al valor por defecto de la tabla.

El agente responde **SNMPv2c y SNMPv3**, nunca v1: una comunidad solo vale en v2c, que es la versión con los contadores de 64 bits que usan las interfaces y la MIB del producto.

**Activar el agente sin comunidad y sin usuario v3 se rechaza**: sería un agente que nadie puede consultar. La comunidad y las contraseñas de los usuarios v3 aparecen como `<set, not shown>` en `show configuration`.


:::info
La MIB de Prisma se sirve bajo el número del producto en la IANA, **67033**, sin nada que configurar (desde la 0.149.13). `snmp enterprise-number <número>` la sirve bajo el número de su organización; vea [Monitoreo y SNMP](/s/wiki-es/doc/monitoreo-y-snmp-zQ9bTfrh8z).

:::

Para el NMS, el equipo entrega sus propios archivos: `show snmp mib` imprime la PRISMA-ROUTING-MIB ya bajo el número de empresa del equipo, para cargar en el NMS, y `show snmp zabbix-template` imprime una plantilla de Zabbix 7.0 con el mismo número, para importar - los ítems, los disparadores y un ítem por cada notificación que el equipo envía.

## Usuarios SNMPv3

```
configure
 snmp-user <nombre>
  auth <sha|md5> <passphrase>
  priv <aes|des> <passphrase>
  exit
```

El agente solo atiende a un usuario v3 con autenticación **y** privacidad: configure los dos, `auth` y `priv`.

## Política de enrutamiento: lo que verifica la VTY

Las prefix-lists y route-maps se escriben en la sintaxis del enrutamiento y él las guarda. La VTY completa los nombres que el equipo ya tiene:

```
prisma# [edit] ip ?
  prefix-list  a named list of prefixes, for matching in policy
prisma# [edit] ip prefix-list <TAB>
  PL-CLIENTES   ip prefix-list
```

El autocompletado sale de la misma fuente que responde `show running-config`, así que no ofrece un nombre que el equipo no tiene.

Tres cosas de una prefix-list son del equipo, por encima de la sintaxis del enrutamiento: una entrada acepta `description <texto>` al final, guardada por el equipo porque el enrutamiento no tiene descripción por entrada; `ip prefix-list <lista> insert seq <n> before|after seq <m>` cambia una entrada de lugar, al estilo Junos; e `ip prefix-list <lista> resequence` renumera la lista de 10 en 10. Las descripciones acompañan a las entradas en los dos casos. Para cambiar el nombre de una prefix-list, una route-map o un peer group BGP en todos los lugares que lo citan, use `rename` en el modo de configuración.


:::warning
**Una política necesita los daemons de enrutamiento arriba.** Las prefix-lists y route-maps no pertenecen a ningún protocolo, así que no es `bgpd` quien las guarda — y con el daemon dueño detenido, el enrutamiento **acepta la línea y no guarda nada**, sin error.

`commit` rechaza en ese caso y dice qué daemon falta y qué archivo editar:

```
error: 2 policy statement(s) need mgmtd, which is not running
    first one: ip prefix-list PL-CLIENTES seq 10 permit 200.150.0.0/24
    they would commit and store nothing. mgmtd is started by frr itself, not
    listed in /etc/frr/daemons - so frr did not finish starting. 'systemctl
    restart frr' and read its journal
```

El consejo es **distinto** del que sale para `bgpd`, y a propósito: `zebra`, `mgmtd` y `staticd` no están en `/etc/frr/daemons`, quien los levanta es el propio `frr`. Si uno de ellos no está arriba, no hay línea que habilitar - lo que pasó es que `frr` no terminó de levantarse, y la respuesta está en su journal. Las estáticas responden con la misma frase, cambiando `staticd` por `mgmtd`.

Una estrofa `router bgp` que no va a ninguna parte se nota: ninguna sesión se levanta. Una prefix-list que no va a ninguna parte es invisible hasta que el tráfico que debía filtrarse no se filtra.

:::

Las advertencias del enrutamiento ahora llegan al operador incluso cuando el `commit` tiene éxito:

```
frr: The route-map 'RM-CLIENTES' does not exist.
commit complete
```

Antes esa línea se descartaba, y la persona se iba con una política apuntando a nada.

## Lo que pasó a significar `commit complete`

Después de aplicar, el equipo **lee de vuelta la configuración que FRR está corriendo** y la compara con lo que usted escribió. Si una declaración que introdujo este commit no está ahí, rechaza y dice cuáles.

Esto existe porque la herramienta que aplica la configuración puede terminar con éxito habiendo descartado una línea. El síntoma es el peor que existe: usted sale de la ventana de mantenimiento creyendo que aplicó, y el problema aparece días después, sin que nadie relacione una cosa con la otra.


:::info
**No todo lo que FRR acepta lo guarda, y eso no es un error.** Tres formas, todas medidas en este equipo y no deducidas:

* **Omite.** `ip ospf hello-interval 10` es su valor por defecto, así que lo acepta y no lo graba. Pero `ip ospf dead-interval 40` también es el valor por defecto y ese sí lo graba — es decir, **no se puede predecir por el valor**, es declaración por declaración.
* **Reescribe.** `passive-interface vppwan0` vuelve como `ip ospf passive`, dentro de la estrofa de la interfaz y no de la del router. `continue 20` vuelve como `on-match goto 20`. El propio FRR avisa cuando usted escribe la primera.
* **Ya lo tenía activado.** `neighbor 10.0.0.2 activate` en una familia ipv4-unicast es implícito, así que lo acepta y no graba nada.

El equipo conoce estas formas y no se queja de ellas. Usted no necesita cambiar cómo escribe.

:::

Una línea de route-map o de prefix-list se trata con más rigor. Si el enrutamiento aceptó la sintaxis y el daemon que aplica la política rechazó la línea, `commit` dice `FRR refused <n> line(s) of your route-maps and lists`, lista las líneas y **vuelve al estado anterior**. Una cláusula que perdió un `set` o un `match` sigue corriendo, haciendo lo que nadie escribió, por eso el equipo deshace el commit en lugar de solo avisar.

Si aparece un rechazo aquí, la lectura es: **lo que guarda este equipo y lo que FRR está corriendo no son lo mismo.** `show configuration` muestra la de este equipo; `start shell` y luego `vtysh -c "show running-config"` muestra la de FRR.

# Listas de AS-path y de comunidad

Son las que consulta una route-map en `match as-path`, `match community` y `match large-community`.

```
bgp as-path access-list AS-CLIENTES permit ^65001(_65001)*$
bgp community-list standard CM-NAO-EXPORTAR permit 65000:666
bgp large-community-list standard LC-REGIAO permit 65000:1:1
```

`?` y TAB completan los nombres que ya existen en el equipo, en los dos lugares: donde se crea la lista y donde la route-map la consulta.

## Una expresión que no compila se rechaza en el momento

FRR compila la expresión **en el momento de aplicar**, es decir, en medio del commit. Aquí se compila cuando usted la escribe:

```
error: the regular expression does not compile: missing ), unterminated
    subpattern at position 6
    bgp as-path access-list AS-BAD permit ^65001(
```

## Una expresión que compila y coincide con lo que no debe

Este es el error común, y **no** se puede rechazar — la expresión es legítima. `permit 65001` compila, es válida, y también coincide con `165001` y `650012`: sin `^` y `$`, una comparación exacta se convierte en comparación por fragmento.

Y el `_` de FRR no es el carácter guion bajo: significa **cualquier frontera** — comienzo, fin o separador entre ASN.

Leer la propia expresión de vuelta no muestra esto. Contrastarla con los caminos que el equipo **realmente recibió** sí lo muestra:

```
note: AS-LOOSE '65002' matches 2 of 5 received AS path(s); e.g. '65001 65002'
```

Si el número resulta mucho mayor de lo que usted esperaba, falta un ancla. Si es cero, la lista existe y no filtra nada.


:::info
Cuando el equipo todavía no recibió ningún camino, lo dice en lugar de responder cero. "Coincidió 0" y "no había contra qué comparar" son respuestas distintas, y solo una de ellas habla de su expresión.

:::

## Un nombre que nadie definió genera una advertencia

```
warning: no AS-path list called 'AS-CLIENTES' is defined; until one is, the
    clause matches nothing
    match as-path AS-CLIENTES
```

La cláusula hace commit y no coincide con nada mientras la lista no exista. **No es un rechazo**: el enrutamiento acepta la cláusula y pasa a usarla por sí solo en el momento en que aparece la lista, así que escribir primero la política y después las listas es un orden de trabajo legítimo — y rechazar el commit por eso tiraría también cualquier otro cambio que estuviera en él.

Igual vale la pena leer la advertencia: si la lista no va a aparecer, el nombre tiene un error de tipeo y la cláusula no coincide con nada para siempre — y nada más en el equipo habla de eso.

Las dos grafías de lista de comunidad valen como definición: `bgp community-list standard NOME` y `bgp community-list NOME`.

## Cómo verificar

* `commit check` muestra los conteos y los rechazos sin tocar el equipo;
* `show bgp ipv4 unicast` muestra los caminos recibidos, que es contra lo que se hizo el conteo;
* `show route` muestra lo que **reenvía** — si la política era para quitar una ruta, es ahí donde desaparece.

# Route-maps: la política en sí

Una route-map es una lista de cláusulas numeradas. Cada cláusula dice **a qué rutas se aplica** (`match`) y **qué cambia en ellas** (`set`). La primera cláusula que coincide decide; las de abajo no se consultan.

```
ip prefix-list PL-MEUS-BLOCOS seq 10 permit 200.150.8.0/22

route-map RM-TRANSITO-OUT permit 10
 match ip address prefix-list PL-MEUS-BLOCOS
 set as-path prepend 65000 65000
exit
route-map RM-TRANSITO-OUT permit 20
exit

router bgp 65000
 address-family ipv4 unicast
  neighbor 200.150.0.2 route-map RM-TRANSITO-OUT out
 exit-address-family
 exit
```

`?` y TAB dentro de la cláusula listan lo que entienden `match` y `set`, y completan nombres que ya existen en el equipo: prefix-lists, listas de AS-path, listas de comunidad, otras route-maps y vecinos.

## Lo que se rechaza y lo que solo se advierte

**Una cláusula** `**deny**` **con** `**set**` **se rechaza.** El `set` nunca corre — la ruta fue denegada. Es un error siempre.

**Una cláusula** `**permit**` **sin** `**match**` **genera una advertencia, no un rechazo.** Deja pasar todo. Es una forma común e intencional de terminar una política — y es también exactamente cómo se ve una política a medias. El equipo no puede distinguirlas, y nadie puede después, así que lo dice:

```
warning: no 'match': this permits every route. If that is meant, it is the end
    of the policy; if not, the policy is unfinished
    route-map RM-TRANSITO-OUT permit 20
```

Si era la intención, la advertencia es ruido de una línea. Si no lo era, es la diferencia entre una política que filtra y una que deja pasar todo.

`**set local-preference**` **con un valor que no es un número se rechaza.** La gramática del enrutamiento para esa cláusula acepta **cualquier palabra**: `set local-preference banana` se acepta en el prompt, pasa el `commit check` y termina en la configuración vigente de BGP — donde no ajusta nada. La cláusula queda ahí, con apariencia de política, sin hacer nada:

```
error: 'banana' is not a local-preference: expected 0-4294967295. FRR takes
    this statement with any word in it and then sets nothing
    set local-preference banana
```

El equipo rechaza aquí porque el enrutamiento no rechaza en ningún lugar. Es lo contrario de `maximum-paths` y `set weight`, en los que el enrutamiento es la autoridad y rechaza en el `commit`: ahí el equipo solo informa el rango y deja el rechazo a quien manda.

El rechazo **solo sale para lo que este cambio está pidiendo**. Un `set local-preference` de esos que el equipo ya llevaba no vuelve a rechazarse en cada commit: un equipo con una línea así en la configuración vigente quedaría con todo commit rechazado, incluidos los que no tienen nada que ver con route-map, y rechazar un cambio por un estado que ya estaba ahí deja al operador fuera de su propio equipo.

**Una cláusula que ninguna ruta alcanza genera una advertencia.** Si la cláusula 10 deja pasar todo, la 20 nunca se consulta. Los números de secuencia son suyos y el equipo no renumera nada — pero una política que parece correcta y no hace nada merece una frase.

Quien manda en el orden es el **número de secuencia**, no el orden en que se escribieron las cláusulas. Insertar una cláusula 5 cuando ya existe una 10 que deja pasar todo pone la nueva adelante, y para eso existen los números: el equipo lee en el mismo orden que el enrutamiento y no advierte nada.

`on-match next` y `continue` cambian ese recorrido — la cláusula que coincide no termina la política, la lectura sigue adelante. En una route-map que tiene una de estas, el equipo ya no dice qué cláusulas son alcanzables: a dónde va la lectura depende de si la cláusula coincidió o no, y eso no está en la configuración.

## Lo que no se verificó aparece marcado

La mayor parte de una route-map sigue pasando directamente al enrutamiento. Sin marca, quien lee la configuración no distingue lo que se verificó de lo que solo se transmitió — y termina leyendo garantía en ambos:

```
route-map RM-CLIENTES permit 10
 match ip address prefix-list PL-CLIENTES
 set tag 100  <- not checked
```

La marca es solo de la visualización: no va a la configuración guardada, ni a una revisión de rollback, ni al enrutamiento. Y si usted copia la pantalla a un archivo y la vuelve a cargar, se quita en la lectura — la pantalla y el archivo no divergen.

## Cómo verificar

* `show running-config` muestra las route-maps como el equipo las lee de vuelta, con lo que no se verificó marcado;
* `commit check` muestra las advertencias sin tocar el equipo;
* `show route` muestra lo que **reenvía** — si la política era para cambiar el camino, es aquí donde aparece el cambio.

### Rango de prefijo: `ge` y `le`

En una entrada de prefix-list con `ge` y `le`, debe cumplirse `longitud de la máscara ≤ ge ≤ le ≤ 32` (o `≤ 128`, en IPv6). Cuando no se cumple, la línea se rechaza en el `commit` y en el `commit check`, con el nombre de la sentencia:

```
error: prefix range refused for 10.0.0.0/8: mask length <= ge <= le <= 32 does
    not hold (length 8, ge 24, le 16)
    ip prefix-list PL-CLIENTES seq 10 permit 10.12.0.0/8 ge 24 le 16
```

La sentencia rechazada va en una **línea propia**, completa, debajo del motivo - así sigue siendo buscable en la pantalla de `show configuration`, que es donde usted la va a corregir.

Observe que en el motivo el prefijo aparece normalizado - `10.12.0.0/8` se convierte en `10.0.0.0/8`, porque `/8` solo mira el primer octeto. La línea de abajo trae la sentencia como usted la escribió, y es por ella que se busca.


:::info
Este rechazo ocurre **antes** de que la línea llegue al enrutamiento. Es a propósito: rechazada más adelante, también tiraba el commit siguiente, con el mismo mensaje apuntando a un prefijo que no estaba en ninguna configuración — ni en la vigente, ni en la candidata. Quien viera eso buscaría el prefijo en su propia configuración y no encontraría nada que corregir.

:::

## El prefijo público de un pool tiene que anunciarse

```
warning: pool 'clientes': nothing here announces 200.150.0.16/28
    subscribers behind it would be translated to an address the internet cannot route back to
    announce it from another box, or originate it here with
    'ip route 200.150.0.16/28 blackhole' and 'network 200.150.0.16/28' under the BGP address family
```

Las dos últimas líneas son la parte accionable: la advertencia no solo dice que falta el anuncio, dice las dos líneas que lo resuelven cuando es aquí donde se quiere anunciar.

Esta es la única verificación que cruza las dos mitades del equipo. Configure bien el CGNAT, configure bien el BGP, y el servicio puede estar muerto igual: si nada anuncia un prefijo que **cubra** el lado público del pool, todo suscriptor detrás de él se traduce a una dirección a la que internet no sabe devolver el tráfico. El tráfico sale y nada vuelve.

Las dos mitades se configuran en lugares distintos, por personas distintas, en días distintos — y nada unía una con la otra.


:::info
La advertencia entiende **cobertura**, no igualdad: anunciar `200.150.0.0/24` cubre un pool en `200.150.0.16/28`.

**No rechaza** el commit — anunciar desde otro equipo es un diseño legítimo. Y **se calla** cuando hay `redistribute`, porque ahí no puede saberlo. Una advertencia que se dispara en un equipo que funciona se ignora, y la siguiente, verdadera, se ignora junto con ella.

:::

# API de gestión (gRPC)

A partir de la versión 0.148.4 el equipo ofrece una API de gestión en gRPC (`prisma.v1`), para la interfaz web y otros sistemas que necesiten configurar y leer el equipo sin pasar por la terminal. **No es una segunda forma de configurar**: cada llamada de configuración es la propia VTY trabajando — el `configure`, las líneas que usted escribiría, el `commit check`, el `commit`, el `commit confirmed`, el `rollback` y el `save` —, así que un rechazo por la API es el mismo rechazo de la VTY, con las mismas palabras, y un cambio hecho por la API aparece en `show running-config`.

Viene **desactivada**. Para activarla, usted necesita tres cosas:


1. **Los certificados.** La API solo acepta TLS mutuo: el equipo tiene su propio certificado y solo atiende a quien presente un certificado de cliente firmado por la autoridad (CA) que usted indique. Coloque en `/etc/prisma-routing/api/` los archivos `server.key`, `server.crt` y `clients-ca.crt` (la CA de los clientes). Con OpenSSL, en cualquier máquina (la clave de la CA, `ca.key`, queda fuera del equipo):

```sh
# la CA que firma a los clientes
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=prisma clients" -keyout ca.key -out ca.crt
# el certificado del equipo, con la dirección que usa el cliente
openssl req -newkey rsa:3072 -nodes -subj "/CN=prisma-api" -keyout server.key -out server.csr
printf 'subjectAltName=IP:192.0.2.166\n' > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile server.ext -out server.crt
# el certificado del cliente: el CN es la cuenta del equipo que usa
openssl req -newkey rsa:3072 -nodes -subj "/CN=webui" -keyout webui.key -out webui.csr
printf 'extendedKeyUsage=clientAuth\n' > client.ext
openssl x509 -req -in webui.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile client.ext -out webui.crt
```

En el equipo van `server.key`, `server.crt` y el `ca.crt` con el nombre `clients-ca.crt`; el cliente usa `ca.crt`, `webui.crt` y `webui.key`.


2. **La cuenta.** El certificado del cliente dice, en el campo CN, **qué cuenta del equipo** es. La llamada tiene exactamente los permisos que esa cuenta tiene en la VTY: `prisma-admin` configura todo, `prisma-cgnat` solo el CGNAT, y cualquier otra solo lee; una cuenta con `operator` también actualiza, reinicia y apaga el equipo y reinicia el dataplane. Todo lo que cambia queda registrado en el historial a su nombre.
3. **La dirección.** La API escucha **solo en la red de gestión**:

```
configure
api enabled yes
api listen-address 192.0.2.166
api port 50051
commit
```

Activarla sin indicar la dirección se rechaza en el commit:

```
error: api enabled yes needs 'api listen-address <management address>'
```

Para desactivarla, `no api enabled` y `commit`.

Lo que ofrece la API, en resumen: abrir una copia de la configuración (la candidata), editarla con las líneas de la VTY, comparar, validar, aplicar (incluso con confirmación), confirmar, volver a una revisión y guardar; leer interfaces, pools, suscriptores, BGP, OSPF, IS-IS, VRRP y bundles; ejecutar cualquier `show`; y recibir en vivo los contadores de las interfaces y los totales del CGNAT. Si alguien cambia la configuración mientras su candidata está abierta, el commit de usted se rechaza mostrando la diferencia, en lugar de pasar por encima.

Desde la 0.149.2 también:

* **lista la tabla de rutas** ya separada en campos (VRF, prefijo, próximos saltos, protocolo, distancia, métrica, si fue la elegida), en páginas, con filtro por VRF, familia, prefijo y protocolo — pensada para la tabla completa: la primera página llega mientras el resto todavía se está leyendo, y la lista entera es una sola fotografía, sin rutas repetidas ni faltantes aunque la tabla cambie en el medio. Y da el conteo por VRF y protocolo;
* informa la **memoria** del equipo en números y la **carga de cada worker** del dataplane — paquetes por llamada y la fracción del tiempo dedicada a paquetes, porque la CPU de un worker que hace polling queda siempre en 100%;
* informa la **velocidad del enlace** de cada interfaz, para calcular la utilización en %;
* guarda **24 horas de historial** de los contadores, una muestra por minuto, que sobrevive a un reinicio de la API (un upgrade, por ejemplo): el gráfico de la interfaz web no recomienza desde cero;
* pone la **revisión** y el **comentario** en la advertencia de cada commit, para que el cliente sepa qué commits fueron suyos;
* responde al **health check** estándar de gRPC, con el mismo certificado de cliente;
* le dice a quien llama **qué cuenta es y su rol** (administrador, operador de CGNAT o solo lectura), sin abrir una candidata.

También lee **en campos el resto de las pantallas del CGNAT**: quién estaba detrás de una dirección pública y un puerto, y si la respuesta vale para cualquier instante (el pool determinístico, por la fórmula) o solo para ahora (un bloque dinámico o de reserva, que ya fue de otro); los suscriptores más cerca de agotar los bloques; los descartes; la memoria y cuántas sesiones caben todavía; los bloques de cada worker; cuántas identidades tiene el equipo (nunca una identidad); la verificación del trail de configuración; los ALG activados en cada pool; los reenvíos de puertos; y si este equipo y el par de HA mapean a los suscriptores de la misma forma. En un pool NAT64 el suscriptor viene como su prefijo IPv6. Cada respuesta trae también la pantalla de la VTY, igual a lo que muestra la terminal, porque es la misma lectura.

Y lee en campos **las pantallas que faltaban**: `show ospf interface` (cada interfaz con el área, el estado, el costo, los vecinos, la MTU que anuncia OSPF al lado de la MTU del dataplane, y si las dos traban la adyacencia), `show isis interface` (los circuitos, los que están sin vecino y si el dataplane está entregando IS-IS al sistema), `show hardware` (las placas que tiene Linux, cuál lleva la ruta por defecto y lo que el dataplane ya tomó) y `show logging` (la configuración de log de cada daemon de enrutamiento). Desde la 0.151.14 también lee `show log` — el registro de lo que pasó en el equipo, con los mismos filtros — y `GetSystem` dice por qué el dataplane y el enrutamiento arrancaron por última vez (`boot`, `operator`, `upgrade` o `unexpected`), para que la interfaz web no tenga que adivinar "se reinició solo". Desde la 0.152.2 sigue el registro en vivo (`WatchEvents`, el `monitor log` de la API), configura los destinos syslog de `system` > `logging` y lee, en `GetSystem`, si cada uno está llegando. `show snmp mib` y la plantilla de Zabbix vienen **como archivo**, ya con el número de empresa del equipo, para importar directamente en el NMS.

`ping`, `traceroute` y `mtr` llegan **línea a línea, mientras corren**, cada respuesta o salto ya en campos y un resumen al final. Valen las mismas reglas de la VTY: la misma dirección, el mismo conteo (hasta 100), el mismo límite de 300 segundos, y el equipo rechaza con las mismas palabras. Si quien llamó desiste a la mitad, el equipo detiene la prueba.

# Vea también

* [Acceso, login y roles](/s/wiki-es/doc/acceso-login-y-roles-ZiA9s69LH4) — lo que puede cambiar cada grupo
* [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg) — OSPF e IS-IS en el puerto, MTU, uRPF
* [Configuración de BGP](/s/wiki-es/doc/configuracion-de-bgp-s4fDtTXgPx) — vecinos, `policy-options` y RPKI
* [Monitoreo y SNMP](/s/wiki-es/doc/monitoreo-y-snmp-zQ9bTfrh8z) — lo que responde el agente SNMP
* [VTY prismash — guía de uso](/s/wiki-es/doc/vty-prismash-guia-de-uso-gPIR8ZKwdC) — los modos y el árbol de comandos
