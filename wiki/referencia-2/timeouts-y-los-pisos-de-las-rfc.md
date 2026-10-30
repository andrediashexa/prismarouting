# Timeouts y los pisos de las RFC

Cuánto tiempo vive cada tipo de sesión, cuáles son los pisos que establecen las RFC, y qué se rompe cuando usted baja de ellos. Para quien tiene el pool llenándose y pensó en acortar un timeout.

# La tentación, y lo que cuesta

Acortar un timeout es lo primero en que cualquiera piensa cuando el pool se llena, y funciona: los puertos se reciclan más rápido y caben más suscriptores en una dirección pública.

Lo que también hace es cortar conexiones que están **ociosas, no terminadas**: una sesión SSH que quedó abierta durante el almuerzo, un pool de base de datos, un sensor que habla una vez por hora. Esas vuelven semanas después como *"se desconecta solo, al azar"*, y nadie lo atribuye a un número cambiado en otro mes.

# Los pisos

| Timeout | Piso | Referencia | Predeterminado | Qué se rompe por debajo |
|---------|-----:|------------|---------------:|-------------------------|
| `udp`   | 120 s | RFC 4787 REQ-5 | 300 s          | un mapeo que se cierra mientras la aplicación todavía cree que existe |
| `icmp`  | 60 s | RFC 5508 REQ-2 | 60 s           | una respuesta de eco que llega después de que el mapeo se cerró; se lee como pérdida de paquetes en toda prueba de camino |
| `tcp-transitory` | 240 s | RFC 5382 REQ-5 | 240 s          | una conexión semicerrada descartada antes de que el par termine de cerrarla |
| `tcp-established` | 7440 s | RFC 5382 REQ-5 | 7440 s         | una conexión ociosa pero viva, cortada |

Los valores predeterminados ya están en el piso o por encima: un equipo que nadie ajustó no dispara ninguna advertencia.


:::info
Estos son **SHOULD** de las RFC, y el equipo los trata como tales: un valor por debajo del piso es **aceptado**. Usted puede tener un motivo, y un equipo que rechaza es menos útil que el que usted está reemplazando. Lo que no hace es aceptar en silencio.

:::

La advertencia va a **los dos lugares** - a quien configuró y al log - porque quien acorta un timeout cuando el pool se llena y quien diagnostica las desconexiones aleatorias seis semanas después no son la misma persona. Pero los dos no reciben la misma frase.

En la VTY, el `commit` imprime una línea por valor, con el nombre del timeout, el valor que pasó a regir y la RFC:

```
warning:  udp                 119   BELOW the floor RFC 4787 REQ-5 asks for
```

**El piso en segundos y lo que se rompe por debajo quedan en el log**, no en la pantalla del commit. Si quiere los tres en la misma frase, es el log el que responde - la tabla de arriba sirve para el resto.

Se dispara **una vez por cambio**: nunca por paquete, nunca por sesión. Y un valor exactamente **en** el piso no dice nada: una advertencia que siempre se dispara es una advertencia que el operador aprende a saltar.

# Qué timeout rige para una conexión TCP, y cuándo

Los cuatro pisos anteriores no se aplican todos al mismo tiempo. Una sesión TCP cambia de timeout según lo que pasa por ella, y eso decide cuánto tiempo queda un puerto fuera de circulación después de que la conexión terminó:

| Estado de la conexión | Timeout que rige | Por qué |
|-----------------------|------------------|---------|
| SYN enviado, nunca se estableció | `tcp-syn` (20 s por defecto) | un host que barre internet produce uno de estos por sondeo; retenidos por el transitorio, agotarían el bloque del propio suscriptor |
| establecida           | `tcp-established` | la conexión está viva, aunque ociosa |
| FIN visto, de cualquiera de los lados | `tcp-transitory` | alguien empezó a cerrar: sale del timeout largo de inmediato |
| RST visto, de cualquiera de los lados | ninguno: el puerto se libera | un reset terminó la conexión; no hay nada que retener |

La lectura práctica es esta: una conexión terminada con **reset** devuelve el puerto casi de inmediato; una terminada con el cierre normal espera el transitorio, no las dos horas; y solo queda en `tcp-established` la conexión que **nadie cerró**.


:::info
Esto importa cuando un suscriptor llena el bloque sin tráfico que lo justifique. Si los puertos estuvieran retenidos por conexiones ya terminadas, el número bajaría solo en pocos minutos. Un bloque que no se vacía es un bloque lleno de sesiones que nadie cerró (una aplicación que abandona sockets, o un host infectado) y no de restos de lo que ya terminó.

:::

El seguimiento vale **en ambos sentidos**: el reset o el cierre cuentan vengan de donde vengan, porque quien termina una conexión puede ser tanto el suscriptor como el otro lado.

# Configuración

Global, en el nivel `nat` > `cgnat`; solo los cuatro timeouts con piso:

```
configure
 nat
  cgnat
   timeout udp 300
   timeout icmp 60
   timeout tcp-transitory 240
   timeout tcp-established 7440
   exit
  exit
 commit
```

Estos globales valen también para los pools NAT64: esos están en `nat` > `nat64`, pero los timeouts, las interfaces y el log son los de `nat` > `cgnat`, compartidos por los dos.

Por pool: sobrescribe el global; si no está, lo hereda. Un pool acepta los cuatro anteriores y cuatro más que solo existen en él:

```
configure
 nat
  cgnat
   pool residencial
    timeout udp 300
    timeout tcp-syn 60
    timeout gre 3600
    timeout port-holddown 8
    timeout alg-expect 30
    exit
   exit
  exit
 commit
```

| Timeout solo de pool | Predeterminado | Qué expira |
|----------------------|---------------:|------------|
| `tcp-syn`            | 20 s           | TCP semiabierto, antes de que el handshake se complete |
| `gre`                | 1800 s         | el mapeo GRE de una llamada PPTP, ocioso |
| `port-holddown`      | 4 s            | cuánto tiempo espera un puerto liberado antes de ser reutilizado |
| `alg-expect`         | 60 s           | cuánto tiempo espera el primer paquete un puerto de medios anunciado por un ALG |

El `port-holddown` existe para que un segmento retrasado de la conexión que terminó no caiga en la conexión nueva que tomó el mismo puerto, lo que el suscriptor ve como un reset que nadie logra reproducir. El puerto vuelve al bloque del **mismo** suscriptor, así que esto no cambia quién responde por él.

## Retención de bloque, en BPA

En un pool BPA (`mode bpa`), un bloque que quedó ocioso no vuelve al pool de inmediato: queda reservado al suscriptor durante `block-holddown` segundos, global, en el nivel `nat` > `cgnat`. El valor predeterminado es 120 s.

```
configure
 nat
  cgnat
   block-holddown 120
   exit
  exit
 commit
```

Es la principal palanca del volumen de log: cada liberación y cada nueva asignación son un registro IPFIX, y con una retención demasiado corta un suscriptor que hace una pausa entre dos paquetes libera y reasigna el bloque todo el tiempo: dos registros más en el colector en cada pausa. El valor mínimo aceptado es 1 s. En un pool determinístico, solo vale para los bloques de la `reserve`: el bloque que la fórmula da a cada suscriptor nunca se libera.

Todos son ajustes **aplicados sin reconstruir**: no tiran sesiones.

# Cómo verificar

```
show this
```

Dentro de `nat` > `cgnat`, o dentro del pool, `show this` muestra los timeouts configurados en ese nivel. Y el `commit` advierte: **todo valor por debajo del piso** se marca en el momento en que se aplica el cambio, global y por pool con las sobrescrituras ya resueltas, para que el estado quede visible para quien no sabía que debía preguntar.


:::success
Ningún valor marcado por debajo del piso, y `show cgnat top` mostrando a los suscriptores lejos del límite del bloque: el pool está dimensionado sin necesidad de apretar timeouts.

:::

Si el pool se está llenando, acortar timeouts es el último recurso, no el primero. Antes: [ampliar la capacidad con un segundo pool](/s/wiki-es/doc/ampliar-la-capacidad-sin-tirar-suscriptores-eHEJEg47aX), reducir `block-size` en una ventana, o limitar a quien está consumiendo demasiado con `max-sessions`.

# Vea también

* [Ampliar la capacidad sin tirar suscriptores](/s/wiki-es/doc/ampliar-la-capacidad-sin-tirar-suscriptores-eHEJEg47aX): la alternativa a apretar timeouts
* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA): los campos por pool
* [Comportamiento NAT y compatibilidad](/s/wiki-es/doc/comportamiento-nat-y-compatibilidad-QvEXCgxsQE): la otra causa común de "se desconecta solo"
