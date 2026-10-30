# Montar un par activo/standby

Cómo poner dos equipos Prisma Routing sosteniendo la misma dirección de servicio, uno activo y otro de reserva, y cómo probar que la conmutación funciona antes de necesitarla. Para quien ya tiene los dos equipos instalados y reenviando; el porqué de cada elección está en [Alta disponibilidad](/s/wiki-es/doc/alta-disponibilidad-bjNhRjSh49).


:::info
**El par preserva la dirección, no la conexión.** No existe sincronización de sesiones: las conexiones en curso se caen en la conmutación, como en cualquier NAT. En un pool **determinístico** el suscriptor vuelve con la **misma dirección pública y el mismo bloque de puertos**, porque el bloque es una fórmula de la configuración. En un pool **BPA** (asignación dinámica) el equipo que asume no sabe qué bloques había entregado el otro: el suscriptor recibe **un bloque nuevo**, asignado y registrado por el equipo que asumió.

:::

En los ejemplos, el equipo A es el que debe quedarse con el servicio y el equipo B es la reserva. `enp1s0` es el puerto interno (suscriptores) y `enp2s0` el puerto externo (upstream); reemplácelos por los nombres de sus equipos. Los nombres de los puertos no necesitan ser iguales en los dos: el par se empareja por el número del router virtual y por la dirección, nunca por el nombre de la interfaz.

# 1. Los mismos pools en los dos equipos

Configure los pools de CGNAT **exactamente iguales** en los dos equipos: prefijos internos y externos (escritos o tomados de una `prefix-list`), `block-size`, `ports`, `policy`, `mode`, `reserve` y las VRF — y, en un pool NAT64, `subscriber-length` y `nat64-prefix`. Son los campos que deciden dónde cae cada suscriptor, y dos equipos que discrepan en ellos fallan **en silencio** — la conmutación pone a los suscriptores en bloques que el otro nunca usó, y ninguno de los dos registra nada anormal.

Lo que no afecta el mapeo puede diferir sin problema: timeouts, ALGs, clamp de TCP, listas de bypass, límites de sesión y de tasa, reenvíos de puertos, `block-stride`, alarma de utilización.

Cómo configurar cada campo está en [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA).

# 2. `vrrp` en los puertos interno y externo

En cada puerto que los vecinos usan como gateway, declare un router virtual (RFC 5798) con una dirección de servicio que **ninguno de los dos equipos** tenga como dirección propia. Cada equipo mantiene su propia dirección en el puerto; la dirección de servicio es la que se mueve.

En el equipo A, prioridad mayor, y cada puerto monitoreando (`track`) al otro:

```
configure
 interface enp1s0
  ipv4 address 100.64.3.252/22
  vrrp 1 address 100.64.3.254
  vrrp 1 priority 200
  vrrp 1 track enp2s0 decrement 150
  exit
 interface enp2s0
  ipv4 address 198.51.100.2/29
  vrrp 2 address 198.51.100.1
  vrrp 2 priority 200
  vrrp 2 track enp1s0 decrement 150
  exit
```

En el equipo B, los **mismos números de router virtual y las mismas direcciones de servicio**, con la dirección propia de cada puerto y una prioridad **diferente** — la predeterminada, 100, sirve:

```
configure
 interface enp1s0
  ipv4 address 100.64.3.253/22
  vrrp 1 address 100.64.3.254
  vrrp 1 track enp2s0 decrement 150
  exit
 interface enp2s0
  ipv4 address 198.51.100.3/29
  vrrp 2 address 198.51.100.1
  vrrp 2 track enp1s0 decrement 150
  exit
```

Qué cambia cada línea:

| Línea | Rango | Qué hace por usted |
|-------|-------|--------------------|
| `vrrp <id> address <A.B.C.D>` | id de 1 a 255 | la dirección que los suscriptores (o el upstream) usan como gateway; responde ARP con la MAC virtual `00:00:5e:00:01:<id>`, que cambia de equipo junto con ella |
| `vrrp <id> priority <n>` | 1 a 254, predeterminado 100 | quién se queda con el servicio: gana la mayor |
| `vrrp <id> track <interface> decrement <n>` | 1 a 254 | si la interfaz monitoreada cae, el equipo anuncia la prioridad menos `n`. Con 200 y `decrement 150`, el equipo A baja a 50 y pierde frente a los 100 de B |

**Sin** `**track**`**, el par sobrevive a un equipo muerto pero no a un equipo sordo.** Un equipo que perdió el upstream y sigue vivo continuaría con la dirección interna, recibiendo a los suscriptores y sin tener adónde enviarlos.

**Prioridades iguales en los dos equipos funcionan, pero quien decide es el direccionamiento**, no usted: la RFC desempata por la dirección mayor. Use prioridades diferentes.

El intervalo de anuncio no es configurable: es el predeterminado de la RFC, un segundo, y debe ser igual en los dos equipos. La toma tarda alrededor de tres segundos y medio.

**Un [pool NAT64](/s/wiki-es/doc/nat64-suscriptores-solo-ipv6-en-el-mismo-espacio-publico-45g9ORUleS) en el par necesita también el gateway IPv6.** Una dirección IPv6 en `vrrp <id> address` crea, al lado del router IPv4, el router virtual IPv6 del mismo id, con la misma prioridad, el mismo `track` y el mismo `preempt`. La primera dirección IPv6 debe ser una link-local (por ejemplo `vrrp 1 address fe80::1`, antes de la global) — el `commit check` rechaza un router IPv6 sin ella —, y es a esa link-local adonde los suscriptores IPv6, o el BNG delante de ellos, apuntan el gateway. Su MAC virtual es `00:00:5e:00:02:<id>`. El `track` es por familia: un router IPv4 que sigue una interfaz sin dirección IPv4 nunca la ve activa, y el `commit check` avisa nombrando la interfaz.


:::info
**La dirección virtual no responde ping.** El equipo no activa el modo *accept*, y la RFC 5798 manda no responder ICMP en ese caso. El reenvío **a través** de ella funciona normalmente. Su monitoreo debe hacer ping a la dirección propia de cada equipo, que es lo que dice si está vivo.

:::

# 3. `commit`, y lo que el `commit check` rechaza

Antes del `commit`, `commit check` verifica la candidata. Dos rechazos son específicos de `vrrp`, y ambos salen ya en el `check` — no en medio de la aplicación:

Un router virtual sin dirección:

```
error: interface enp1s0: vrrp 1 has no address
    a virtual router with nothing to stand for would elect a master that
    answers for nothing
```

Un `track` apuntando a una interfaz que el equipo no tiene:

```
error: interface enp1s0: vrrp 1: no interface enp5s0
    the dataplane does not have the interface this virtual router was told to
    track
```

Y `vrrp` en una interfaz que el equipo no tiene se rechaza con las mismas palabras que cualquier otra línea en una interfaz inexistente. Corrija y ejecute `commit`.


:::warning
**Cambiar un router virtual que ya existe cuesta dos elecciones.** El router no se edita sin reconstruir: el equipo lo borra y lo recrea. Si tiene la dirección de servicio, el peer asume durante la reconstrucción y el `preempt` la devuelve enseguida. El equipo advierte antes de aplicar:

```
  changing vrrp 1 on enp1s0 restarts it: this box stops advertising while it is
  rebuilt, the peer takes the address, and preempt hands it back - two
  elections, a few seconds apart
```

Las conexiones en curso se caen en las dos conmutaciones. Crear un router nuevo no cuesta nada; cambiar cualquier campo de uno existente es un cambio de ventana de mantenimiento.

:::

# 4. `save` en cada equipo, y la copia hacia el otro

`commit` lo hace valer ahora; `save` lo hace sobrevivir al arranque. Ejecute `save` en los dos equipos:

```
save
```

El equipo responde `configuration saved to /etc/prisma-routing/prisma-routing.conf`.

Para que un equipo verifique al otro, necesita tener la configuración del otro. VRRP no transporta configuración alguna, así que llega como archivo: copie el archivo que el `save` del equipo A grabó a `/etc/prisma-routing/peer.conf` del equipo B, y el de B a la misma ruta en A. La copia se hace con `scp` mediante una cuenta de administrador — `root` o `prisma-admin` pasan por un shell real; desde dentro de la VTY, `start shell` abre uno. En el equipo B, por ejemplo:

```
scp prisma-admin@<equipo-a>:/etc/prisma-routing/prisma-routing.conf /etc/prisma-routing/peer.conf
```


:::warning
**El archivo lleva los hashes de contraseña y las claves públicas de las cuentas del otro equipo.** No deje `peer.conf` legible por todos, ni copie ese archivo fuera de los dos equipos.

:::

**Copie de nuevo siempre que cambie el otro equipo.** No hay sesión entre los dos: el `peer.conf` es lo que se copió por última vez, y `show cgnat ha` imprime la fecha de la copia justamente para que un archivo viejo salte a la vista.

# 5. `show cgnat ha` en los dos equipos

```
show cgnat ha
```

La pantalla responde tres preguntas, en este orden:

**Qué mapea este equipo** — la huella digital del mapeo y los pools:

```
  this box
    mapping fingerprint  <digest>
    pools                <pools>
```

**Su rol** — cada router virtual, con el estado, las direcciones, la MAC virtual y la prioridad que está **anunciando**. En el equipo A, con todo en su lugar, el estado es `master, holding the address`; en B, `backup`. Si la prioridad anunciada difiere de la configurada, la línea dice los dos números:

```
      priority           50 (configured 200, and something it tracks is down)
```

Eso es un cable desconectado, no un problema de VRRP.

**El par** — leído del `peer.conf`, con la fecha de la copia en la línea `copied`. Con los routers virtuales emparejados:

```
    every virtual router this box carries has one on the peer with the same id,
    the same address and a different priority
```

y con los pools iguales:

```
  the two boxes map every subscriber to the same public address and the same
  port block
```

Las advertencias que pueden aparecer en su lugar:

| Advertencia | Qué significa | Qué hacer |
|-------------|---------------|-----------|
| la misma prioridad en los dos equipos | quién se queda con el servicio lo decide el direccionamiento | cambie la prioridad de uno de ellos |
| un router virtual que solo tiene un equipo | no hay reserva para esa dirección | declare el mismo id y dirección en el otro |
| un pool que no mapea de la misma forma | la conmutación cambia la dirección y el bloque de los suscriptores de ese pool | iguale el pool en los dos equipos y copie el `peer.conf` de nuevo |
| sin `peer.conf`, o ilegible | el equipo no afirma que los dos concuerdan | haga la copia del paso 4 |

`show cgnat ha peer <archivo>` compara contra otro archivo en lugar del `peer.conf`.

# 6. `show interface` dice el rol del puerto

Mirando un solo puerto, durante una falla:

```
show interface enp1s0
```

En un puerto con router virtual, la pantalla trae el rol y cada dirección de servicio con la MAC virtual:

```
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

En el equipo de reserva la línea dice `backup`. Es el rol leído del dataplane, no la configuración. Un puerto sin router virtual no muestra línea alguna.

# 7. El autotest de arranque

En cada arranque, el equipo compara las líneas `vrrp` del archivo guardado con lo que el dataplane realmente tiene, y lo dice en la consola:

```
  [ ok ] every virtual router the saved configuration names is on the
         dataplane: 1 holding an address, 1 standing by
```

Si el archivo declara un router que el dataplane no aceptó, la línea es `[FAIL]` y dice cuál (interfaz e id): el equipo está en el par solo en el papel, y el otro sostiene el servicio solo, sin reserva. El motivo del rechazo está en el journal de `prisma-routing`. Un equipo sin `vrrp` no recibe línea alguna.

# 8. Cambios de pool en un par: `commit diverge`

En un equipo con `vrrp` y con un `peer.conf` legible, el `commit` **rechaza** un cambio que haría que los dos mapearan a los suscriptores de formas diferentes, y dice qué pool diverge:

```
error: this change maps subscribers differently from the peer
    this box stands for a service address with another one, so a takeover would
    translate them there
```

El rechazo termina con `if that is what you mean, type: commit diverge`. Como los dos equipos no reciben el `commit` en el mismo instante, toda ampliación correcta pasa por ese momento. El procedimiento:


1. En el primer equipo, haga el cambio y confírmelo con `commit diverge`.
2. En el segundo, haga el mismo cambio. Con el `peer.conf` antiguo, también puede rechazarlo; use `commit diverge` de nuevo.
3. `save` en los dos y copie los dos archivos de nuevo (paso 4).
4. `show cgnat ha` en los dos debe volver a decir que mapean igual.

`commit check` muestra el mismo rechazo, para que usted decida antes de escribir el `commit`. En un equipo sin `vrrp` nada de esto es un rechazo: un `peer.conf` ahí solo genera una advertencia.

El rechazo no ve que el otro equipo cambie después de la copia — solo ve el archivo. Por eso la fecha en `show cgnat ha`.

# 9. `preempt`: quien vuelve, retoma

`preempt` está siempre activo. Cuando el equipo A vuelve — reiniciado, con el cable de vuelta, con la prioridad restaurada —, **retoma** la dirección de servicio, y las conexiones en curso se caen de nuevo en esa segunda conmutación. El par termina donde dice la configuración, y no donde lo dejó la última falla. Para sacar de servicio el equipo activo sin apagarlo, bájele la prioridad, como se describe en [Recetas del día a día](/s/wiki-es/doc/recetas-del-dia-a-dia-maiZboGbqY).

# 10. Probar la conmutación

Haga esto en una ventana, antes de necesitar el par:


1. En los dos equipos, `show cgnat ha`: A en `master, holding the address`, B en `backup`, el par emparejado y los pools iguales.
2. Anote la dirección pública y el bloque de un suscriptor de prueba en el equipo A:

   ```
   show cgnat subscriber <ip del suscriptor>
   ```
3. Provoque la falla en el equipo A. Una a la vez, y con tiempo para volver entre ellas:
   * **desconecte el cable del upstream** (`enp2s0`): prueba el `track`. El equipo A debe anunciar 50 y perder frente a B;
   * **baje la prioridad** por la VTY (`vrrp 1 priority 50` y `vrrp 2 priority 50`, `commit`): prueba la elección;
   * **apague el equipo A**: prueba la pérdida del equipo entero.
4. En el equipo B, `show cgnat ha` debe mostrar `master, holding the address`, y el suscriptor debe volver a navegar en algunos segundos. Verifique `show cgnat subscriber` en B: en un pool determinístico, la misma dirección pública y el mismo bloque; en un pool BPA, un bloque nuevo.
5. Deshaga la falla. Con el `preempt`, el equipo A retoma el servicio; verifique de nuevo en los dos.


:::info
Detectar el cable desconectado depende de que la placa informe la caída de enlace al dataplane. Verifíquelo en su placa: con el cable desconectado, `show cgnat ha` en el equipo A debe mostrar la prioridad anunciada menor que la configurada.

:::

# Cómo verificar

En los dos equipos:

```
show cgnat ha
```


:::success
Un equipo con `master, holding the address` y el otro con `backup` en cada router virtual; en la sección del par, `every virtual router this box carries has one on the peer with the same id, the same address and a different priority`; al final, `the two boxes map every subscriber to the same public address and the same port block`; y la fecha en `copied` posterior al último cambio del otro equipo. En la consola de arranque, la línea `[ ok ] every virtual router the saved configuration names is on the dataplane`.

:::
