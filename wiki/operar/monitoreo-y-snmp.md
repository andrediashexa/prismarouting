# Monitoreo y SNMP

Qué ve su NMS de este CGNAT, y qué hay que hacer para que aparezca la mitad más útil. Para quienes se encargan de Zabbix, LibreNMS o equivalentes.

Todo proveedor ya tiene una NMS, y es donde alguien realmente mira. Un CGNAT cuya única observabilidad es alguien conectado al propio equipo es un CGNAT que nadie supervisa hasta que el cliente llama.

# Cómo habilitar el agente

```
configure
 snmp enabled yes
 snmp listen-address 10.0.0.1
 snmp port 161
 snmp enterprise-number 65535
 snmp community <comunidad-de-solo-lectura>
 snmp-user nms
  auth sha <passphrase>
  priv aes <passphrase>
  exit
 commit
 exit
save
```

| Línea | Qué hace por usted | Si se omite |
|-------|--------------------|-------------|
| `snmp enabled yes` | activa el agente   | el agente queda desactivado, que es el valor predeterminado |
| `snmp listen-address` | la dirección en la que atiende el agente | el agente escucha solo en `127.0.0.1`, y ninguna NMS externa lo alcanza |
| `snmp port` | el puerto UDP      | `161`       |
| `snmp enterprise-number` | el número bajo el cual se sirve la MIB de Prisma (vea más abajo) | vale el número del producto |
| `snmp community` | una comunidad SNMPv2c, de solo lectura | solo consulta el v3 |
| `snmp-user` | un usuario SNMPv3, con `auth <sha\|md5>` y `priv <aes\|des>` | solo consulta el v2c |

Cualquiera de estas líneas vuelve al valor predeterminado con `no` delante: `no snmp enterprise-number` vuelve al número del producto, `no snmp listen-address` vuelve a `127.0.0.1` (a partir de la versión 0.143.2).

El `commit` rechaza un agente activado sin ninguna comunidad y sin ningún usuario v3: sería un agente que nadie puede consultar. Y el agente se verifica por su **efecto**: se escribe el archivo, el servicio se reinicia, y si no levanta con la configuración nueva, el equipo restaura la anterior y lo dice en el `commit`.

A partir de la versión 0.144.1 el equipo responde **solo en SNMPv2c y SNMPv3**: una consulta en SNMPv1, incluso con la comunidad correcta, queda sin respuesta. El v1 no transporta contadores de 64 bits, así que los puertos del dataplane y los contadores de la MIB de Prisma ni siquiera aparecerían para él.

La configuración de SNMP contiene las comunidades, por eso solo **root** la lee. A partir de la versión 0.147.4, una sesión que no puede leerla (un administrador que no es root, por ejemplo) advierte debajo del `show running-config` que las líneas `snmp` son las de la última configuración guardada, y no las del equipo. Esa sesión no modifica el SNMP y solo graba un `save` con el SNMP de la configuración guardada, nunca desactivado. Antes de eso, un `save` hecho en la VTY desactivaba el SNMP en el siguiente arranque y borraba la comunidad.

El usuario v3 se crea con acceso de solo lectura y exige autenticación **y** privacidad: configure las dos líneas, `auth` y `priv`. El `commit` también funciona dentro del bloque `snmp-user`, sin necesidad de salir de él.

La comunidad y las frases secretas del v3 son contraseñas que viajan por la red, y se tratan como tales: `show running-config` muestra `<set, not shown>` en su lugar.

# Solo lectura, dos veces

El archivo de configuración del agente que genera el equipo contiene solo `rocommunity` y `rouser`, y **nunca** `rwcommunity`. Además, el propio agente responde a todo SET con `not-writable`.

La configuración pertenece a la VTY, donde se valida, se confirma con commit y es reversible. Un SET SNMP que reconfigura un CGNAT es una caída a un error de tipeo de distancia.

# Sin números viejos

Si el dataplane no está respondiendo, el agente sirve **nada** en lugar de los últimos valores que vio.


:::info
Un sistema de monitoreo que sigue reportando los números de ayer durante una falla **oculta** la falla; uno que reporta un hueco la muestra. Por eso la verificación de vida se ejecuta en **cada** consulta, antes de consultar la caché. Los números en sí quedan en caché durante diez segundos: consultar más rápido que eso devuelve el mismo valor.

:::

# Lo que se sirve

## El equipo, el procesador, la memoria y los discos

A partir de la versión 0.141.3, la NMS también lee lo que el net-snmp del propio equipo ya sabe responder; antes esto quedaba fuera de la vista, y una consulta al equipo no traía ni siquiera su nombre:

| Grupo | OID | Qué trae |
|-------|-----|----------|
| sistema (SNMPv2-MIB) | `.1.3.6.1.2.1.1` | `sysDescr` con el producto y la versión instalada, `sysUpTime`, `sysName` |
| HOST-RESOURCES-MIB | `.1.3.6.1.2.1.25` | procesadores, memoria y almacenamiento, procesos en ejecución |
| UCD-SNMP-MIB | `.1.3.6.1.4.1.2021` | carga, memoria, y la tabla de discos (`dskTable`) |

La tabla de discos ya viene con dos límites: la raíz, que avisa con menos del 10% libre, y **el sistema de archivos del log de retención** (`/var/log/prisma-routing`), que avisa con menos del 15% libre. Ese es el que importa: un disco lleno ahí es el log que guarda un año de registros de conexión dejando de grabar. En la NMS, alerte cuando `dskErrorFlag` (`.1.3.6.1.4.1.2021.9.1.100`) sea igual a 1.

Un núcleo de worker aparece siempre con una carga del 100%: no duerme, se queda consultando la placa. La carga real del dataplane no es esa; entra en una próxima etapa del SNMP.

## Los puertos del dataplane, por la IF-MIB

A partir de la versión 0.141.5, la `ifTable` y la `ifXTable` del equipo son los puertos **del dataplane** —los que transportan a los suscriptores—, y no los que ve Linux. Antes, la NMS que recorría la IF-MIB veía solo `lo` y la placa de gestión.

| Objeto | Qué trae |
|--------|----------|
| `ifName`, `ifDescr` | el nombre del puerto, el mismo de la VTY |
| `ifAlias` | la `description` configurada en la VTY |
| `ifHighSpeed`, `ifSpeed` | la velocidad del enlace, tal como la informa `show hardware-interfaces` |
| `ifAdminStatus`, `ifOperStatus` | activado en la configuración, y con enlace |
| `ifHCInOctets`, `ifHCOutOctets` y los de paquetes | los contadores de 64 bits, sumando todos los hilos del dataplane |

Entran todos los puertos del dataplane, incluidas las subinterfaces, y las placas físicas que siguen con Linux, como la de gestión. Quedan fuera las internas: `local0` y las `tap` que conectan el dataplane con el enrutamiento.

El `ifIndex` de cada puerto se guarda por nombre, así que un puerto **mantiene la misma fila** cuando el dataplane se reinicia: la NMS no mezcla el historial de un puerto con el de otro.

## El enrutamiento: BGP, OSPF e IS-IS

A partir de la versión 0.142.0, las sesiones de enrutamiento aparecen en las MIB estándar, servidas por los propios daemons que las ejecutan: lo que lee la NMS es el estado del daemon, no una copia de él:

| MIB | OID | Qué trae |
|-----|-----|----------|
| BGP4-MIB | `.1.3.6.1.2.1.15` | cada vecino: estado de la sesión, AS remoto, updates, último error |
| OSPF-MIB | `.1.3.6.1.2.1.14` | vecinos y sus estados, interfaces, tamaño de la LSDB |
| OSPFV3-MIB | `.1.3.6.1.2.1.191` | lo mismo, para OSPFv3 |
| ISIS-MIB | `.1.3.6.1.2.1.138` | adyacencias y circuitos |
| BGP4V2-MIB | `.1.3.6.1.3.5.1` | cada vecino BGP, **IPv4 e IPv6** (incluidos los *unnumbered*, por link-local): estado, AS remoto, errores y contadores de prefijos por familia; a partir de la 0.144.5 |

Lo que conviene alertar: `bgpPeerState` (`.1.3.6.1.2.1.15.3.1.2.<dirección del vecino>`) distinto de 6 (*established*), `ospfNbrState` distinto de 8 (*full*) e `isisISAdjState` distinto de 3 (*up*). Lo mismo vale para OSPFv3: `ospfv3NbrState` (`.1.3.6.1.2.1.191.1.9.1.8`) distinto de 8. Los cuatro fueron verificados por las pruebas del producto en una sesión real (el de OSPFv3 a partir de la versión 0.142.1). La BGP4-MIB solo conoce vecinos IPv4; para un vecino IPv6 use `bgp4V2PeerState` (`.1.3.6.1.3.5.1.1.2.1.13.1.<tipo>.<dirección>`, tipo 1 = IPv4, 2 = IPv6), que también debe ser 6.

A partir de la versión 0.142.1, también responde el tamaño de la tabla de rutas: `inetCidrRouteNumber` (`.1.3.6.1.2.1.4.24.6.0`) cuenta las rutas IPv4 e IPv6. (`ipCidrRouteNumber`, la versión antigua solo IPv4, no la responde el net-snmp del equipo.) La tabla en sí queda fuera a propósito: con la tabla completa de internet son cerca de un millón de filas, y una NMS que la recorriera sobrecargaría el equipo en cada consulta. Una caída brusca en ese número es una sesión que se cayó llevándose las rutas consigo.

No hace falta configurar nada además de `snmp enabled yes`: el commit activa el vínculo de los daemons de enrutamiento con el agente.


:::info
**Al desactivar el SNMP**, los daemons de enrutamiento siguen buscando el agente hasta la próxima vez que FRR se reinicie —FRR no permite deshacer ese vínculo mientras está en ejecución— y el `commit` lo advierte. En ese intervalo el log de FRR registra un intento de conexión cada 15 segundos por daemon. En el siguiente inicio de FRR (un reinicio, por ejemplo) el vínculo desaparece.

:::

## Timeouts, según la RFC 4008

`natDefTimeouts` en `.1.3.6.1.2.1.123.1.1`: seis objetos que se corresponden exactamente con los timeouts de este equipo, así que una NMS existente los lee sin plantilla personalizada.

| OID bajo `.1.3.6.1.2.1.123.1.1` | Objeto de la RFC 4008 | Timeout de este equipo |
|-------------------------------|-----------------------|------------------------|
| `.1.0`                        | `natBindDefIdleTimeout` | `port-holddown`        |
| `.2.0`                        | `natUdpDefIdleTimeout` | `udp`                  |
| `.3.0`                        | `natIcmpDefIdleTimeout` | `icmp`                 |
| `.4.0`                        | `natOtherDefIdleTimeout` | `gre`                  |
| `.5.0`                        | `natTcpDefIdleTimeout` | `tcp-established`      |
| `.6.0`                        | `natTcpDefNegTimeout` | `tcp-transitory`       |

El resto de la RFC 4008 es por interfaz o por *binding*: este equipo no rastrea ninguna de esas formas, y por *binding* significaría recorrer la tabla de sesiones, algo que ningún `show` de aquí tiene permiso de hacer.

## El CGNAT en la MIB estándar del IETF: NATV2-MIB

A partir de la versión 0.144.2 el equipo también sirve la **NATV2-MIB** (RFC 7659, `.1.3.6.1.2.1.234`), la MIB que el IETF definió para NAT y CGNAT: una NMS que ya la conoce lee los pools sin una plantilla nuestra. Cada pool aparece como una instancia de NAT, con su nombre, el tipo de filtrado, el rango de puertos, los suscriptores y puertos en uso ahora, los puertos rechazados y los rangos de direcciones públicas.

Solo los números agregados: no hay una tabla con una fila por suscriptor ni por sesión, que en un equipo en producción serían millones de filas. Para los detalles que la RFC no cubre —capacidad garantizada, ocupación, carga por worker— está la MIB de Prisma, más abajo.

## El VRRP en la MIB estándar del IETF: VRRPV3-MIB

A partir de la versión 0.144.3 el equipo también sirve la **VRRPV3-MIB** (RFC 6527, `.1.3.6.1.2.1.207`) para los routers virtuales VRRP del dataplane. Cada router es una fila indexada por el ifIndex de la interfaz (el mismo de la IF-MIB), por el VRID y por la familia de direcciones (1 = IPv4, 2 = IPv6), con el estado (inicializando, backup, master), la prioridad, la MAC virtual, el intervalo de anuncio, los modos preempt y accept y las direcciones virtuales.

Para alertar ante un cambio de master, siga `vrrpv3OperationsStatus`: el dataplane no guarda contadores por router, así que la tabla de estadísticas y las notificaciones de la RFC no se sirven.

## El LACP en la MIB estándar del IEEE: IEEE8023-LAG-MIB

A partir de la versión 0.144.4 el equipo también sirve la **IEEE8023-LAG-MIB** (IEEE 802.1AX, `.1.2.840.10006.300.43`) para los bundles LACP del dataplane. Cada bundle y cada miembro aparecen por el ifIndex de la interfaz (el mismo de la IF-MIB): el sistema y la prioridad del partner, las claves, a qué bundle está conectado cada miembro, el estado LACP de ambos lados, los LACPDU recibidos, enviados e inválidos, y el estado de las máquinas de recepción y de mux.

Para alertar cuando un miembro sale del bundle, siga `dot3adAggPortAttachedAggID` (pasa a 0) o `dot3adAggPortDebugMuxState` (deja de ser `collectingDistributing`). Los valores administrativos del partner, las tablas de conversación y el DRNI no se sirven: el dataplane no los tiene.

## Las direcciones de las interfaces: IP-MIB

A partir de la versión 0.145.1 las tablas de direcciones de la **IP-MIB** (`ipAddrTable`, `ipAddressTable` e `ipAddressPrefixTable`) listan las direcciones de las interfaces del dataplane, IPv4 e IPv6, **con el mismo ifIndex de la IF-MIB**. Es por esa relación que la NMS (Zabbix, por ejemplo) descubre qué dirección está en qué interfaz. Antes, veía las direcciones de las interfaces espejo de Linux, con una numeración que no coincidía con ningún puerto de la IF-MIB.

`ipForwarding` e `ipv6IpForwarding` responden 1 (*forwarding*): el equipo es un router. Las direcciones link-local IPv6 quedan fuera.

## El inventario y los sensores: ENTITY-MIB y ENTITY-SENSOR-MIB

El equipo también sirve la **ENTITY-MIB** (RFC 6933, `.1.3.6.1.2.1.47`) y la **ENTITY-SENSOR-MIB** (RFC 3433, `.1.3.6.1.2.1.99`), que toda NMS lee sin plantilla: el chasis, cada puerto que tiene caja (cage), el módulo dentro de ella y el diagnóstico (DDM) del módulo como sensores. Son las mismas lecturas de `prTransceiver` y de `show interface transceiver`, decodificadas por el mismo código.

El índice (`entPhysicalIndex`) sale del **ifIndex del puerto en la IF-MIB**, que no cambia con un reinicio ni con una placa nueva, así que el historial de un sensor en la NMS tampoco se pierde:

| Qué | `entPhysicalIndex` |
|-----|------------------|
| el chasis | 1                |
| el puerto | ifIndex × 1000   |
| el módulo en el puerto | ifIndex × 1000 + 1 |
| temperatura y tensión del módulo | \+ 2 y + 3       |
| bias, TX y RX del carril *n* | \+ 10*n* + 1, + 2 y + 3 |

En un puerto de ifIndex 1, por ejemplo: puerto 1000, módulo 1001, temperatura 1002, tensión 1003, bias 1011, TX 1012 y RX 1013. El módulo trae fabricante, modelo (*part number*), número de serie, revisión y fecha de fabricación (`entPhysicalMfgName`, `entPhysicalModelName`, `entPhysicalSerialNum`, `entPhysicalHardwareRev`, `entPhysicalMfgDate`), y `entAliasMappingIdentifier` relaciona cada puerto con su ifIndex.

Los sensores usan las unidades de la RFC: temperatura en centésimas de grado, tensión en décimas de milivoltio, bias en microamperios, y **potencia óptica en vatios**: décimas de microvatio, la resolución del propio módulo. La RFC 3433 no tiene dBm (el `dBm` que conocen algunas NMS es de una MIB de Cisco); en dBm, la potencia sigue en `prXcvrLaneRxPower` y `prXcvrLaneTxPower`. Los **límites del propio módulo** están en la MIB de Prisma, en `prSensorThresholdTable` (`.1.21`), con el mismo índice y la misma unidad del sensor, y `prSensorLevel` indica dónde está la lectura respecto de ellos. La RFC 3433 no tiene límites: le pide a la NMS que grabe los suyos, y este equipo es de solo lectura; además, los límites son los grabados en el módulo.

**Una placa que no lee módulos no aparece** —virtio, una TAP—: no hay caja que mostrar. Una caja vacía aparece como puerto sin módulo, y un módulo sin DDM o de cobre aparece sin sensores. Cuando un módulo entra, sale o se reemplaza (otro número de serie en la misma caja), `entLastChangeTime` cambia y el equipo envía el aviso `entConfigChange` de la propia ENTITY-MIB.

## La MIB de Prisma: el CGNAT, los registros de conexión y el equipo

Lo que **no tiene un OID estándar** —los pools del CGNAT, los descartes, el transporte de los registros de conexión, la versión que está ejecutando el dataplane— está en la **PRISMA-ROUTING-MIB**, la MIB del propio producto. A partir de la versión 0.143.4 se sirve sin ninguna configuración más allá de activar el SNMP, y el propio equipo entrega el archivo listo, con el número ya completado:

```
show snmp mib
```

Guarde la salida como `PRISMA-ROUTING-MIB.txt` y cárguela en la NMS: a partir de ahí, en lugar de números, la NMS muestra los nombres: `prPoolUtilisation`, `prLogTransportConnected`, etc.


:::info
**El número es el de Prisma en la IANA: 67033.** Hasta la 0.149.12 la MIB estaba bajo el 32473, que la IANA reserva para documentación y ejemplos (RFC 5612), porque el producto todavía no tenía número propio. Desde la 0.149.13 está bajo el 67033, sin nada que configurar, y nada debajo de él cambió de lugar: quien consulta `.1.3.6.1.4.1.32473` pasa a consultar `.1.3.6.1.4.1.67033`, con los mismos números debajo, y basta con volver a cargar en la NMS el archivo que imprima `show snmp mib`. Quien lo prefiera puede usar el número de su propia organización con `snmp enterprise-number <número>`; `no snmp enterprise-number` vuelve al del producto.

:::

### Los totales del equipo

En los mismos OID de siempre, así que una NMS ya configurada sigue funcionando:

| OID bajo `.1.3.6.1.4.1.<PEN>.1` | Objeto | Significado |
|-------------------------------|--------|-------------|
| `.1.0`                        | `prSubscribers` | suscriptores que tienen un bloque ahora |
| `.2.0`                        | `prPortsInUse` | puertos asignados sumando todos ellos |
| `.3.0`                        | `prPortsHeld` | puertos contenidos en los bloques actualmente retenidos |
| `.4.0`                        | `prSubscribersNearFull` | suscriptores al 90% o más del bloque |
| `.5.0`                        | `prPortRefusals` | puertos rechazados desde que se levantó el dataplane |
| `.6.0`                        | `prPacketsDropped` | paquetes que el CGNAT no dejó pasar desde que se levantó |


:::info
**A partir de la versión 0.143.0,** `**.1.0**` **a** `**.4.0**` **llegan como Gauge, y** `**.5.0**` **y** `**.6.0**` **como Counter**; antes los seis llegaban como Counter, y una NMS que calcula tasas dibujaba cada bajada como un pico falso de "desbordamiento de contador". Si su NMS guardó esos ítems como contador, cámbielos a medidor (gauge). Y `.5.0` ahora es un contador de verdad: suma todos los rechazos desde que se levantó el dataplane, y no solo los de los suscriptores que todavía retienen un bloque.

:::

### Los pools

Una fila por pool, en `prPoolTable` (`.10.1.1.<columna>.<pool>`):

| Objeto | Qué trae |
|--------|----------|
| `prPoolName`, `prPoolMode` | el nombre y el modo: determinístico o dinámico |
| `prPoolBlockSize`, `prPoolInsideAddresses`, `prPoolOutsideAddresses` | el tamaño del bloque y cuántas direcciones de suscriptor y públicas tiene el pool |
| `prPoolBlocksTotal`, `prPoolCapacity`, `prPoolGuaranteed` | cuántos bloques entrega el pool, cuántos suscriptores admite como máximo y cuántos **garantiza** en cualquier worker; es por este que se dimensiona |
| `prPoolSubscribers`, `prPoolBlocksHeld`, `prPoolUtilisation` | cuántos suscriptores y bloques están en uso ahora, y el porcentaje de bloques entregados |
| `prPoolSessions`, `prPoolPortsInUse`, `prPoolSubscribersNearFull` | sesiones abiertas, puertos asignados y suscriptores al 90% o más |
| `prPoolRefusals` | puertos rechazados desde que se creó el pool |

Los descartes están en `prDropTable` (`.10.2`), una fila por motivo, con el nodo y el motivo en las mismas palabras de `show cgnat drops`; el total de sesiones del equipo, en `prSessions` (`.10.3.0`).

### Los registros de conexión y el equipo

| Objeto | Qué trae |
|--------|----------|
| `prLogIpfixEnabled`, `prLogIpfixRecords`, `prLogIpfixDropped` | el IPFIX por UDP: activado, registros emitidos, y eventos sin colector adonde ir |
| `prLogTransportTable` (`.11.10`) | los flujos confiables —IPFIX por TCP (1) y syslog (2)—: activado, **conectado**, registros, enviados, volcados en el archivo de spill, **perdidos**, y cuántas veces se reconectó |
| `prSystemRelease`, `prSystemDataplaneBuild` | la versión instalada y la del dataplane que se está ejecutando |
| `prSystemDataplaneCurrent` | si las dos son la misma. **Falso después de una actualización, hasta el próximo reinicio**: el equipo sigue reenviando con la versión antigua, y un `commit` que necesita la nueva se rechaza |

### Capacidad, workers, HA, LACP e identidad

A partir de la versión 0.143.1:

| Objeto | Qué trae |
|--------|----------|
| `prSessionsFit` | cuántas sesiones caben todavía en la memoria del CGNAT; el mismo número de `show cgnat memory`. En cero, las sesiones nuevas se rechazan |
| `prWorkerTable` | suscriptores, sesiones y puertos de cada hilo del dataplane: un worker muy por delante de los demás es la señal de un suscriptor rechazado mientras sobran bloques en otro worker |
| `prVrrpTable` | cada router VRRP: interfaz, VR ID, la prioridad anunciada ahora y el estado: `master`, `backup`, `init`. Un cambio de estado es un cambio de rol |
| `prBundleTable` | cada bundle LACP: cuántos miembros configurados y cuántos **transportando tráfico** |
| `prIdentityEntries`, `prIdentityReadAt` | cuántos suscriptores nombra la tabla de identidad, y cuándo se leyó |
| `prRadiusReceived`, `prRadiusApplied`, `prRadiusRefused` | el accounting RADIUS que viene del BNG. Un rechazo suele ser un secreto distinto entre el BNG y el equipo |

Los números del CGNAT los mantiene el propio dataplane en el momento en que cambian, y el agente lee todo de una vez: consultar el equipo no cuesta un barrido de los suscriptores, por grande que sea.

### El estado del propio equipo: tuning, self-test, commits y trail

A partir de la versión 0.147.1:

| Objeto | Qué trae |
|--------|----------|
| `prTuningState` | `tuned` (el kernel tiene lo que pidió el tuner), `betweenPlans` (el equipo funciona con menos workers que los planificados hasta el próximo reinicio) o `firstBoot` (configuración conservadora hasta el reinicio) |
| `prTuningWorkers`, `prTuningWorkersNextBoot` | workers ahora y en el próximo arranque |
| `prTuningIsolated`, `prTuningIsolatedWanted` | CPU aisladas ahora y las que pidió el tuner |
| `prTuningHugepages`, `prTuningHugepagesWanted` | hugepages reservadas ahora y las pedidas. Menos que lo pedido es una memoria del dataplane menor que la planificada |
| `prSelfTestVerdict` | el veredicto del self-test de arranque: `ready`, `readyWithWarnings`, `notForwarding` (sano, pero sin interfaz para reenviar) o `failed` |
| `prSelfTestFailures`, `prSelfTestWarnings`, `prSelfTestRanAt` | fallas, advertencias y cuándo se ejecutó |
| `prConfigCommits` | configuraciones aplicadas desde el arranque (commits, la carga del arranque, rollbacks, reversiones) |
| `prConfigLastCommitAt`, `prConfigLastCommitBy` | cuándo y quién aplicó la última: `boot` para la carga del arranque, `confirm-expired` para una reversión |
| `prConfigConfirmPending`, `prConfigConfirmDeadline` | un `commit confirmed` esperando confirmación, y cuándo el equipo lo revierte si nadie lo confirma |
| `prConfigConfirmReverts` | `commit confirmed` que nadie confirmó y que el equipo revirtió; casi siempre un cambio que tiró la sesión de quien lo hizo |
| `prConfigRollbacks` | rollbacks a una revisión anterior |
| `prConfigSavedDiffers` | la configuración en uso no está guardada: un reinicio volvería sin los cambios. Falta un `save` |
| `prAuditState` | el trail de configuración: `intact`, `empty`, `broken` (entrada alterada o eliminada del medio), `truncated` (eliminadas del final) o `incomplete` (escrituras que fallaron) |
| `prAuditEntries`, `prAuditHeadSeq`, `prAuditHeadDigest`, `prAuditLost` | las entradas, la última y su SHA-256, y las perdidas. **Guarde el digest en la NMS**: un trail reescrito desde cero sigue verificando, y solo la cabeza guardada fuera del equipo muestra la diferencia |

El tuning, el self-test y los commits siguen respondiendo con el dataplane detenido: es justamente el equipo cuyo dataplane no levantó el que más los necesita. Los contadores se reinician en cada arranque. Un grupo que el equipo todavía no tiene (el self-test no se ejecutó, ninguna configuración aplicada desde el arranque) simplemente no aparece, en lugar de aparecer en cero.

### Los módulos ópticos de los puertos

A partir de la versión 0.151.15, `prTransceiver` trae el módulo de cada puerto: lo que muestra `show interface transceiver`, decodificado por el mismo código. Dos tablas, bajo el **ifIndex del puerto en la IF-MIB**: una por módulo y una por carril óptico (uno en un SFP, cuatro en un QSFP).

| Objeto | Qué trae |
|--------|----------|
| `prXcvrStatus` | `ddm` (módulo con diagnóstico), `noDdm`, `copper`, `absent` (caja vacía) o `unreadable` (el driver no lee módulos) |
| `prXcvrVendor`, `prXcvrPartNumber`, `prXcvrSerialNumber`, `prXcvrWavelength` | la identidad; un número de serie nuevo es un módulo reemplazado |
| `prXcvrTemperature`, `prXcvrVoltage` | en centésimas de grado y en milivoltios, con los cuatro límites del módulo al lado |
| `prXcvrState` | el peor nivel de cualquier lectura del módulo: `normal`, `warning`, `alarm`, o `unknown` cuando no graba límites |
| `prXcvrLaneRxPower`, `prXcvrLaneTxPower` | en centésimas de dBm, con los límites al lado y el nivel de cada una (`prXcvrLaneRxLevel`). **Sin luz (0 mW) es -4000**, -40,00 dBm |
| `prXcvrLaneBias` | la corriente del láser, en microamperios |

Un valor que el módulo no entrega, y los límites de quien no tiene límites, **no aparecen**; no aparecen en cero. Los mismos módulos están también en la ENTITY-MIB y la ENTITY-SENSOR-MIB, descritas más arriba; esta tabla se mantiene, en dBm, la unidad que muestra el switch del otro lado, y es en ella donde están las alarmas ópticas de la plantilla de Zabbix.

Hasta la versión 0.155.2, un `snmpget` de esta tabla y de `prSensorThresholdTable` respondía *No Such Instance*, aunque el `snmpwalk` funcionaba, y Zabbix consulta con GET, así que los ítems ópticos de la plantilla quedaban sin valor. A partir de la 0.155.3 las dos responden al GET; la actualización ajusta el `snmpd.conf` y reinicia snmpd por sí sola.

### El PPPoE: sesiones, servidores RADIUS y CoA

A partir de la versión 0.155.3, `prBng` (`.1.22`) trae el plano de control del PPPoE: el mismo `show stat` de accel-ppp que lee `show pppoe summary`, con los contadores que el equipo le agrega.

| Objeto | Qué trae |
|--------|----------|
| `prBngState` | `running`, `stopped` (PPPoE configurado y el plano de control detenido: nadie puede conectarse), `notConfigured`, o `noContainers` (equipo que ejecuta el dataplane como unidades de systemd) |
| `prBngUptime` | cuánto tiempo lleva activo el plano de control: los contadores de abajo cuentan a partir de ahí |
| `prBngSessionsActive`, `prBngSessionsStarting` | sesiones activas, y sesiones en negociación. `prBngSessionsActive` es siempre `prBngSessionsStarted` menos `prBngSessionsStopped` |
| `prBngSessionsStarted`, `prBngSessionsStopped` | sesiones que se establecieron, y que cayeron después de establecerse |
| `prBngCoaAcks`, `prBngCoaNaks`, `prBngDisconnectAcks`, `prBngDisconnectNaks` | los CoA y los Disconnect (RFC 5176) respondidos con ACK y con NAK |
| `prBngDaeDropped` | las solicitudes de CoA o Disconnect que no tuvieron ninguna respuesta: un origen que no es un `dynamic-authorization client`, o un secreto incorrecto |
| `prBngRadiusTable` (`.1.22.20`) | una fila por servidor de `aaa radius`, indexada por la dirección: el estado (`active` o `failed`, durante el `dead-time`), los Access-Request enviados, los **Access-Accept**, los **Access-Reject**, los sin respuesta en el `timeout`, y lo mismo para el accounting |

Los contadores **se reinician desde cero cuando se reinicia el plano de control** —un commit que cambia el bloque `pppoe`, o una caída—, y `prBngUptime` muestra cuándo fue. En un equipo **sin PPPoE**, `prBngState` indica `notConfigured`, los contadores quedan en cero y la tabla de servidores queda vacía: el equipo responde, no da error. Un Access-Reject es la respuesta del servidor —contraseña incorrecta, suscriptor desconocido o suspendido—, por eso la plantilla lo recopila y no genera alarma; lo que genera alarma es un servidor que no responde.

### El shaper del PPPoE: la cola de cada sesión

`prBngShaper` (`.1.22.30`) trae los totales del equipo de las colas por sesión del PPPoE — el shaper que, por defecto, aplica el plan de descarga (`rate-limit download shaper|policer` bajo `pppoe`).

| Objeto | Qué trae |
|--------|----------|
| `prBngShaperDownLimiter`, `prBngShaperUpLimiter` | qué aplica el plan en cada sentido: `shaper` (lo que pasa del plan espera en la cola), `policer` (lo que pasa del plan se descarta), o `notConfigured` sin PPPoE |
| `prBngShaperDownSessions`, `prBngShaperUpSessions` | sesiones con la descarga, y con la subida, pasando por el shaper ahora |
| `prBngShaperQueues` | colas con paquetes esperando ahora: suscriptores enviando más que su plan en este momento |
| `prBngShaperQueuedPackets`, `prBngShaperQueuedOctets` | paquetes, y bytes, esperando en todas las colas ahora |
| `prBngShaperPassed`, `prBngShaperDelayed` | paquetes dentro del plan, que pasaron directo, y por encima del plan, que esperaron y salieron |
| `prBngShaperDroppedQueueFull` | descartados con la cola de la sesión llena: el suscriptor pasó del plan por más tiempo del que la cola guarda — para eso existe el plan |
| `prBngShaperDroppedCodel` | descartados por CoDel: la forma de avisar a una conexión TCP que baje al plan |
| `prBngShaperDroppedBoxBuffers` | descartados porque las colas, juntas, ya ocupaban todos los buffers que el equipo reserva para ellas: falta del equipo, no del plan |
| `prBngShaperOctets` | bytes que salieron por el shaper |
| `prBngShaperBuffersHeld`, `prBngShaperBuffersCap` | buffers del dataplane ocupados por las colas ahora, y el máximo que pueden ocupar: un cuarto del total, dividido entre los workers |

Los contadores son de todo el equipo, los dos sentidos sumados, y **no retroceden cuando una sesión cae**: el dataplane suma lo que la sesión contó a un total antes de eliminarla. Se reinician desde cero solo cuando se reinicia el dataplane (`prSystemDataplaneUptime` muestra cuándo) — un reinicio del plano de control del PPPoE no los pone a cero. Una sesión sola no tiene fila en SNMP: con 16.000 sesiones, una tabla por sesión le costaría al agente cerca de un segundo por consulta. Para una sesión, use `show pppoe session <id>`.

## Zabbix: la plantilla lista

A partir de la versión 0.146.0 el equipo entrega una **plantilla para Zabbix 7.0** que monitorea todo lo que está en esta página: los puertos del dataplane, los totales, los pools y los workers del CGNAT, los registros de conexión, el VRRP, el LACP, el BGP, el OSPF, el OSPFv3, el IS-IS, el tamaño de la FIB, la CPU, la memoria y los discos, el estado del propio equipo (tuning, self-test, commits y trail, a partir de la 0.147.1), los módulos ópticos de los puertos (a partir de la 0.151.15), el PPPoE y sus servidores RADIUS (a partir de la 0.155.3), y los avisos que envía el equipo. Sale del propio equipo, ya con el número de la MIB de Prisma completado:

```
show snmp zabbix-template
```

Guarde la salida como `prisma-routing-zabbix.json` e impórtela en Zabbix, en *Recopilación de datos* > *Plantillas* > *Importar*. El archivo es JSON, que Zabbix importa de la misma forma que YAML. En el host, cree una interfaz SNMP v2c con una `snmp community` del equipo, o v3 con un `snmp user`, y vincule la plantilla **Prisma Routing by SNMP**. El equipo rechaza SNMPv1.

Las alarmas ya vienen listas: puerto activado administrativamente y operativamente caído, puerto por encima del 90% de la velocidad o con errores, pool por encima del 80% y del 95% de los bloques, suscriptores cerca de agotar los puertos, puertos rechazados, memoria del CGNAT agotándose, vecino BGP fuera de *established*, vecino OSPF que no llega a *full*, adyacencia IS-IS fuera de *up*, cambio de estado del VRRP, bundle LACP con menos miembros activos que configurados, la FIB perdiendo la mitad de las rutas en una hora, registros de conexión perdidos o transporte desconectado, dataplane ejecutando la versión antigua después de una actualización y el equipo sin responder SNMP. Los límites son macros de la plantilla, que usted ajusta en el host: `{$IF.UTIL.MAX}`, `{$PRISMA.POOL.UTIL.WARN}`, `{$PRISMA.POOL.UTIL.CRIT}`, `{$PRISMA.SESSIONS.FIT.WARN}` y las demás. Para un puerto que puede estar caído sin alarma, use `{$IFCONTROL:"<nombre del puerto>"}` = 0.

Los módulos ópticos (0.151.15) tienen las suyas: potencia de RX por debajo de la advertencia baja o por encima de la advertencia alta **del propio módulo**, temperatura alta, y cualquier lectura más allá de un límite de alarma. `{$PRISMA.XCVR.RX.MIN}` y `{$PRISMA.XCVR.RX.MAX}` (dBm) y `{$PRISMA.XCVR.TEMP.MAX}` (°C) se disparan junto a los límites del módulo —para vigilar un nivel que el módulo no vigila—, y `{$PRISMA.XCVR.CONTROL:"<puerto>"}` = 0 silencia un puerto (un carril de QSFP es `"<puerto> lane <n>"`).

El PPPoE (0.155.3) tiene las suyas: plano de control configurado y detenido, plano de control reiniciado por sí solo, las sesiones activas cayendo por debajo de `{$PRISMA.PPPOE.DROP.PCT}` (50%) del máximo de la última hora —solo por encima de `{$PRISMA.PPPOE.MIN}` (10) sesiones—, servidor RADIUS marcado como caído, servidor RADIUS sin responder Access-Request durante cinco minutos, y solicitudes de CoA o Disconnect respondidas con nada.

El shaper del PPPoE tiene las suyas: paquetes descartados por falta de buffers del equipo (`prBngShaperDroppedBoxBuffers`) por encima de `{$PRISMA.SHAPER.BOX.DROPS.MAX}` (0) por segundo durante cinco minutos, alarma alta; y las colas ocupando más de `{$PRISMA.SHAPER.BUFFERS.WARN}` (80%) de los buffers que pueden ocupar durante cinco minutos, aviso. La plantilla también recopila qué aplica cada sentido, las sesiones con shaper, las colas y los paquetes que pasaron, esperaron y fueron descartados por segundo.

Tres decisiones de la plantilla:

* **Sin alarma de CPU.** Los núcleos de los workers del dataplane están al 100% todo el tiempo, por diseño. La carga de cada núcleo se recopila, pero no genera alarma.
* **Una alarma por evento.** El VRRP y los pools generan alarma por la MIB de Prisma, que da el nombre de la interfaz y del pool. La misma información en la VRRPV3-MIB y en la NATV2-MIB solo se recopila, y lo mismo vale para los sensores de la ENTITY-SENSOR-MIB: las alarmas ópticas son las de `prTransceiver`. De la ENTITY-MIB viene solo un aviso informativo, de módulo reemplazado. El BGP genera alarma por la BGP4-MIB para vecinos IPv4 y por la BGP4V2-MIB solo para los IPv6.
* **Un puerto apagado por el operador no es un puerto caído.** La alarma de puerto caído solo se dispara con el puerto activado administrativamente.

Para recibir los **avisos** en Zabbix, apunte el equipo a un servidor o proxy con el receptor de traps de Zabbix: `snmp trap-host <dirección> community <comunidad>`. Cada aviso de la tabla de la sección siguiente cae en su propio ítem, y lo que no se reconozca cae en un ítem de reserva. A partir de la 0.152.3, un `bfdSessDown` también abre una alarma, que se cierra `{$PRISMA.BFD.TRAP.HOLD}` (10 minutos) después del último `bfdSessDown` o a mano. No se cierra con el `bfdSessUp`: Zabbix no sabe de qué sesión es cada aviso, y la vuelta de una sesión cerraría la caída de otra. Si la sesión volvió, el ítem del `bfdSessUp` y el `show bfd peers` del equipo lo dicen.

La plantilla se importó en Zabbix 7.0 y se probó contra un equipo de laboratorio: los descubrimientos encontraron los puertos, los pools, los workers, el VRRP y los bundles, los ítems recibieron valores, y los avisos `linkDown`, `linkUp` y `vrrpStateChange` llegaron cada uno a su ítem. El SNMPv3 y las tablas de enrutamiento no se probaron contra un Zabbix real. Las tablas de enrutamiento se verifican contra los daemons en las pruebas del producto.

## Lo que no se sirve

* **Nada por suscriptor ni por sesión.** Eso significaría recorrer la tabla de sesiones en cada consulta.
* **Ventiladores, fuentes y las temperaturas de la placa madre.** Necesitan los sensores del propio chasis (IPMI o `/sys/class/hwmon`), y el único equipo de prueba con hardware es una VM, que no tiene ninguno: no se pudo leer nada. Quedan para cuando haya un equipo físico para medir.
* **Las placas de red como entidades** (modelo, firmware, número de serie): el número de serie de la placa solo lo puede leer root, y el agente SNMP no se ejecuta como root. La descripción del puerto incluye la dirección PCI.
* **Si el par de HA coincide con este equipo** sobre el mapeo, los forwards y el resultado del self-test: la VTY lo muestra, en `show cgnat ha` y `show cgnat forward`, y el self-test lo informa en la consola en cada arranque.


:::info
El SNMPv3 se configura y el usuario se crea con acceso de solo lectura, pero una consulta autenticada por v3 **no** se verifica de punta a punta en las pruebas del producto. Verifique la suya con el `snmpget` del final de esta página antes de depender de ella.

:::

# Avisos: el equipo informa cuando algo cambia

A partir de la versión 0.143.3, el equipo no espera a la siguiente consulta: **avisa** a la NMS cuando algo cambia, a cada destino configurado:

```
configure
 snmp trap-host 10.0.0.9 community <comunidad>
 snmp trap-host 10.0.0.10 community <comunidad> inform
 commit
 exit
```

Cada destino recibe **trap**, a menos que indique `inform`: el inform lo confirma el receptor y se reenvía hasta que lo haga. La comunidad del destino se trata como contraseña: `show running-config` muestra `<set, not shown>`. `no snmp trap-host <dirección>` deja de enviarle avisos.

| Cuándo | Aviso |
|--------|-------|
| un puerto del dataplane cae o vuelve | `linkDown`, `linkUp` |
| una sesión BGP cae o se establece | `bgpBackwardTransition`, `bgpEstablished` |
| un vecino OSPF cambia de estado | `ospfNbrStateChange` |
| un router VRRP cambia de estado | `vrrpStateChange`, con la interfaz, el VR ID y el estado |
| un pool llega al 90% de los bloques | `poolNearlyFull`, con el nombre del pool |
| se rechazan puertos | `portsRefused` |
| un transporte de registros se conecta o se cae | `recordsTransportChange` |
| se pierden registros de conexión | `recordsLost` |
| cambian los miembros activos de un bundle | `bundleMembersChange` |
| una actualización dejó el dataplane antiguo en ejecución | `dataplaneNotCurrent` |
| el self-test de arranque falló | `selfTestFailed` |
| se revirtió un `commit confirmed` que nadie confirmó | `configReverted` |
| el tuning espera un reinicio | `tuningBetweenPlans` |
| el trail de configuración dejó de verificar | `auditTrailDamaged` |
| un pool se queda sin ningún bloque libre | `poolExhausted`, con el nombre del pool (desde la 0.152.2) |
| el RX, la temperatura, la tensión, el bias o el TX de un módulo supera un umbral, o vuelve | `transceiverStateChange`, con el puerto y el nivel (desde la 0.152.2) |
| un módulo entra, sale o se reemplaza en una caja | `entConfigChange`, el aviso de la propia ENTITY-MIB |
| una sesión BFD cae o vuelve | `bfdSessDown`, `bfdSessUp` de la BFD-STD-MIB, con el índice de la sesión y el diagnóstico (desde la 0.152.3) |
| disco, memoria o carga superan el límite | los avisos estándar de net-snmp |

Puerto caído y sesión BGP caída ya eran avisos estándar (`linkDown`/`linkUp` de la IF-MIB y `bgpBackwardTransition` de bgpd), y por eso no recibieron un aviso propio del producto: no llegan dos veces. Login, commit y reinicio **no** tienen aviso: esos están en `show log`, que va a un syslog con `system` > `logging` y se sigue en vivo con `monitor log`.

**Sesión BFD.** Los avisos son los de la propia BFD-STD-MIB (RFC 7331): `bfdSessDown` (`1.3.6.1.2.1.222.0.2`) cuando la sesión cae y `bfdSessUp` (`.0.1`) cuando vuelve. Cada uno trae `bfdSessDiag` con el índice de la sesión —el mismo de la tabla `bfdSessTable` que sirve el equipo, así que la NMS sabe qué fila consultar— y el diagnóstico: `1` es el tiempo de detección que expiró (el vecino dejó de responder), `3` es el vecino avisando que tiró la sesión. El BFD sigue detectando la caída en milisegundos y retirando las rutas en el acto; el aviso es lo que llega hasta 10 segundos después, porque sale del mismo vigilante que registra la caída en `show log` y que lee bfdd cada 10 segundos. Una sesión que cayó y volvió entre dos lecturas no se pierde: llegan los dos avisos. Cada sesión envía como máximo cuatro avisos en cinco minutos; si queda oscilando, la NMS recibe el estado final en cuanto la ventana lo permite. `bfdNotificationsEnable` responde `true(1)` solo mientras los avisos se están enviando de hecho —con al menos un `snmp trap-host` configurado— y `false(2)` sin destino.

Los avisos con nombre (`vrrpStateChange` y los demás) llegan como `mteTriggerFired` de la DISMAN-EVENT-MIB, con el nombre en el campo `mteHotTrigger`, y quedan bajo el mismo número de la MIB. El equipo verifica cada condición cada 30 o 60 segundos, así que el aviso llega hasta un minuto después del evento; el tuning y el trail, cada cinco minutos.

# Lo que conviene graficar

| Métrica | Por qué |
|---------|---------|
| suscriptores al 90% del bloque (`prSubscribersNearFull`) | es la alarma anticipada; cuando llega al 100% el cliente ya llamó |
| puerto rechazado (`prPortRefusals`) | cualquier subida es un suscriptor que no logra abrir conexiones |
| ocupación del pool (`prPoolUtilisation`) por encima del 80% | el pool se está quedando sin bloques para entregar |
| suscriptores del pool (`prPoolSubscribers`) cerca de `prPoolGuaranteed` | el pool está cerca de lo que puede garantizar |
| transporte activado y **no conectado** (`prLogTransportConnected`) | los registros de conexión están yendo al archivo de spill |
| registros perdidos (`prLogTransportDropped`, `prLogIpfixDropped`) subiendo | es un hueco en el log de retención |
| `prSystemDataplaneCurrent` falso | una actualización esperando un reinicio |
| `prSessionsFit` cerca de cero | las sesiones nuevas van a rechazarse por falta de memoria |
| `prVrrpState` cambiando | hubo un cambio de rol en el HA |
| `prBundleActiveMembers` menor que `prBundleMembers` | el bundle está funcionando con menos patas de las que tiene |
| `prRadiusRefused` subiendo | el BNG está enviando accounting que el equipo no acepta |
| paquetes no pasados (`prPacketsDropped`) | una subida súbita suele ser un cambio de configuración o de ruta; `prDropTable` dice el motivo |
| sesiones PPPoE activas (`prBngSessionsActive`) cayendo de golpe | un enlace de acceso, el plano de control, o el RADIUS rechazando a los suscriptores que vuelven |
| Access-Request sin respuesta (`prBngRadiusAuthTimeouts`) subiendo | el servidor RADIUS, el camino hasta él, o un secreto distinto |
| `prBngRadiusState` en `failed` | el servidor se está omitiendo; sin ningún servidor vivo, nadie entra |
| `prBngShaperDroppedBoxBuffers` subiendo | suscriptores perdiendo paquetes que cabían en el plan: las colas ocupan todos los buffers que el equipo reserva para ellas |

Lo mismo se ve por la VTY, sin NMS:

```
show cgnat top                              quién está más cerca de llenar el bloque
show cgnat pool                             cuántos están cerca del límite, por franja de uso
show cgnat drops                            lo que no pasó, y por qué
show cgnat memory                           cuántas sesiones caben todavía en la memoria del CGNAT
show interface utilization <nombre>         cuánto está pasando por el puerto, ahora
```

`show interface utilization` muestrea los contadores del dataplane varias veces y muestra la tasa en bit/s de cada puerto, recibiendo y transmitiendo, con un pequeño gráfico de barras de las muestras de esa ejecución. Por defecto son 5 muestras cada 2 segundos; `interval <segundos>` (0,1 a 60) y `count <n>` (1 a 120) lo cambian, y el nombre del puerto al final limita a uno solo:

```
show interface utilization interval 1 count 30 ens19
```

Donde el dataplane informa la velocidad del enlace, las barras y el porcentaje son de la tasa de línea de ese puerto. Donde no la informa —toda interfaz virtual—, las barras se escalan por la muestra más alta de la ejecución, y el pie lo indica en lugar de inventar un porcentaje. Ctrl-C termina el muestreo antes del final y muestra lo que ya se midió. Es la respuesta a *"¿el enlace está lleno?"* sin esperar a que el gráfico de la NMS cierre el intervalo, pero no guarda historial entre una ejecución y otra: el historial es trabajo de la NMS.

# Cómo verificarlo

Desde la propia NMS, o desde cualquier máquina que alcance el `listen-address`:

```sh
snmpget -v2c -c <comunidad> <dirección> .1.3.6.1.2.1.123.1.1.1.0
snmpget -v3 -l authPriv -u nms -a SHA -A <passphrase> -x AES -X <passphrase> <dirección> .1.3.6.1.2.1.123.1.1.1.0
```


:::success
Que el agente responda los timeouts de la RFC 4008 confirma que está funcionando; los contadores de la MIB de Prisma, bajo `.1.3.6.1.4.1.67033.1`, muestran el CGNAT desde donde alguien mira.

:::

Si el agente no responde nada, verifique primero si el `listen-address` es una dirección que la NMS alcanza, y después si el dataplane está vivo (`show version`): con el dataplane detenido, el silencio es una respuesta intencional, no una falla del SNMP.

# Vea también

* [Operación y diagnóstico](/s/wiki-es/doc/operacion-y-diagnostico-iHMcBJQ91I) — los mismos números por la VTY
* [Cuando se llena el bloque de un suscriptor](/s/wiki-es/doc/cuando-se-llena-el-bloque-de-un-suscriptor-LJuUIMODBQ) — qué hacer cuando `.4.0` y `.5.0` suben
* [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3) — la referencia de los comandos `snmp`
* [Timeouts y los pisos de las RFC](/s/wiki-es/doc/timeouts-y-los-pisos-de-las-rfc-IgDiScQthb) — los valores que expone la RFC 4008
