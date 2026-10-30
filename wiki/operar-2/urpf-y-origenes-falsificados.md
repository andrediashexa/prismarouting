# uRPF y orígenes falsificados

Qué pasa cuando llega un paquete con una dirección de origen que el remitente no posee, por qué eso es un problema de **prueba** y no solo de seguridad, y qué resuelve el uRPF — y qué no resuelve.

# Por qué esto importa más aquí que en un router común

Todas las demás propiedades de este equipo existen para que el log pueda **responder**. Esta existe para impedir que quede **equivocado** con toda confianza.

Un paquete que llega a una interfaz interna con origen falsificado se traduce como cualquier otro, recibe un puerto del bloque de esa dirección y — en modo determinístico — se atribuye a ese suscriptor por aritmética, durante todo el tiempo que alguien quiera preguntar.


:::warning
El registro queda **indistinguible de uno verdadero**, porque nada en el sistema conoce la diferencia. Una orden judicial respondida con ese registro señala a un suscriptor que no hizo nada.

:::

# La defensa: uRPF

La verificación es el *unicast Reverse Path Forwarding*: la dirección de origen tiene que **enrutar de vuelta por el camino por donde vino el paquete**. Es una función del propio VPP, habilitada por interfaz y **desactivada por defecto**.

```
configure
 interface ens19
  urpf strict
  exit
 commit
```

La verificación va en la interfaz **interna**, la que tiene `cgnat inside` en su bloque. `urpf off`, o `no urpf`, la desactiva de nuevo. El cambio se aplica en el momento, sin tirar sesiones, y va al archivo con el próximo `save`.

| Modo | Acepta | Dónde usarlo |
|------|--------|--------------|
| `strict` | la ruta del origen apunta a la interfaz por donde llegó el paquete | la interfaz interna de un CGNAT |
| `loose` | el origen tiene ruta en algún lugar | topologías asimétricas, donde `strict` descartaría tráfico que es correcto |
| `off`, o ausente (por defecto) | cualquier cosa | donde no se conoce el camino de retorno |


:::info
**Desactivado por defecto a propósito.** Activar `strict` en una topología con caminos de retorno asimétricos descarta tráfico que funcionaba un instante antes — y lo hace en silencio: no hay más síntoma que algunas cosas dejan de funcionar. Esa decisión le corresponde a quien conoce la topología.

:::

La verificación se ejecuta **antes** de la traducción, así que un paquete rechazado nunca llega al CGNAT: no se toma ningún puerto del bloque al que apuntaba, y no existe ninguna sesión que atribuir a nadie.

Por eso el descarte del uRPF **no aparece** en `show cgnat drops`: esa pantalla cuenta solo lo que rechazó el propio CGNAT, y el paquete bloqueado aquí nunca llegó a él. Son dos cosas diferentes — uno es un ataque o un error de configuración; el otro, `no pool for source address`, es una dirección que este equipo simplemente no traduce. El descarte del uRPF entra en el contador de descartes de la propia interfaz, en `show interface <nombre>`.

# Dónde se detiene la protección


:::warning
**Un suscriptor que falsifica la dirección del vecino no es detectado.** En la topología común, todo suscriptor de un pool es alcanzable por la misma interfaz interna — así que la FIB responde "sí, por ahí" para la dirección de la víctima exactamente como responde para la del atacante. El `strict` lo deja pasar, y el mapeo se atribuye al vecino.

:::

Ese es el límite de lo que una verificación **de ruta** puede ver, y no es una brecha que un uRPF mejor pueda cerrar. Detectar eso exige:

* la dirección atada al suscriptor al que se entregó — correlación con RADIUS o DHCP; o
* la red de acceso rechazando la falsificación antes de que el paquete llegue aquí, que es donde resulta más barato hacerlo.

Lo que el uRPF **detecta** es todo lo que viene de fuera del espacio de suscriptores inyectado en un enlace interno, y el tráfico de un enlace de acceso que reclama direcciones que pertenecen a otro. En un equipo con más de una interfaz interna, eso es la mayor parte.

Ese límite está **verificado**, no solo descrito: la verificación envía la dirección de un vecino por la interfaz correcta y exige que se traduzca — de modo que la frontera fallaría como prueba si alguien algún día afirmara lo contrario.

# Cómo verificar

```
show interface ens19
```

La pantalla de la interfaz trae una línea `urpf` con el modo que el **dataplane** está aplicando — `strict`, `loose` u `off` —, leído del equipo y no del archivo, y los contadores del puerto, donde entran los descartes.

El autotest de arranque verifica lo mismo en cada arranque: cuando la configuración guardada activa el uRPF en alguna interfaz, dice si el dataplane realmente lo tiene activado. Con todo correcto, la línea es:

```
  [ ok ] uRPF is on in the dataplane, as the saved configuration asks
```

Si la configuración lo pide y el dataplane no lo tiene, el autotest lo señala como falla — una protección que simplemente no está ahí no tiene ningún contador que la delate.


:::success
Con `urpf strict` en la interfaz interna, `show interface` confirmando el modo y el tráfico legítimo pasando normalmente (`show cgnat top` con los suscriptores de siempre): usted cerró la puerta al origen falsificado que viene de fuera del espacio de suscriptores, sin tirar a quien estaba bien.

:::

# Ver también

* [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg) — el bloque `interface` donde se configura `urpf`
* [Logging IPFIX y trail de auditoría](/s/wiki-es/doc/logging-ipfix-y-trail-de-auditoria-LHGHuvEHJy) — el registro que esto protege
* [Operación y diagnóstico](/s/wiki-es/doc/operacion-y-diagnostico-iHMcBJQ91I) — por dónde empezar cuando un suscriptor desaparece
