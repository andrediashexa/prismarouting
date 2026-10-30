# El equipo arrancó y no reenvía

El equipo encendió, responde en la VTY, acepta configuración — y no pasa paquetes. Esta página es el orden de diagnóstico para ese caso: lo que ya dijo el autotest de arranque, las dos pantallas que muestran lo que tiene el dataplane, dónde queda el motivo cuando el dataplane se rindió, y cómo hacer que el equipo vuelva a reenviar. Para quien está en la consola de un equipo nuevo o recién reiniciado.

Un equipo sin interfaz de dataplane parece un equipo funcionando. Por eso el propio equipo dice, en tres lugares, qué decidió y por qué — y el diagnóstico consiste en leer esos tres lugares en orden.

# 1. Lea el autotest de arranque

En cada arranque el equipo imprime en la consola (serial, IPMI o la pantalla de la máquina virtual) un bloque que empieza con `=============== Prisma Routing: boot self-test ==============`. Cada línea es `[ ok ]`, `[warn]`, `[FAIL]` o `[info]`, y las fallas traen el motivo en las líneas de abajo. Las que responden "por qué no reenvía":

| La consola dice | Qué significa | Qué hacer |
|-----------------|---------------|-----------|
| `[warn] running in FIRST BOOT mode: no pinning and no hugepages.` | primer arranque después de la instalación: el ajuste de CPU y de memoria solo vale a partir del siguiente arranque, y hasta entonces el dataplane arranca en modo conservador, sin DPDK | reinicie el equipo. Es lo esperado, no un defecto |
| `[warn] running between two tuning plans until the next reboot:` | un cambio de ajuste pidió más workers de los que el kernel aisló en este arranque. El equipo reenvía con los workers que ya están aislados, y las dos líneas de abajo dicen cuántos ahora y cuántos después | reinicie cuando pueda para pasar al plan nuevo. Hasta entonces reenvía, con menos núcleos |
| `[info] DPDK is off until hugepages exist; no NIC can be claimed yet` | el mismo caso, visto desde el lado de la placa | lo mismo: reinicie |
| `[warn] no dataplane interface: this box has no PCI network card for the` … | el equipo no tiene ninguna placa que el dataplane pueda tomar | vea el paso 3; una placa virtio en una máquina virtual cuenta, una placa que Linux no ve no cuenta |
| `[warn] no dataplane interface: this box has <n> card(s) and claimed` … | el equipo tiene placas y no tomó ninguna: la única candidata era la placa por la que se lo alcanza, y no fue posible leer de ella dirección, MAC y gateway para devolvérselos a Linux | vea el paso 3 y nombre la placa (paso 6) |
| `[FAIL] the dataplane has no interface, and one is named` | se entregó una placa al dataplane y él no la tiene; las líneas de abajo dicen a qué driver está ligada | vea el paso 5 |
| `[FAIL] the dataplane holds interface(s) its driver refused to start:` | el dataplane tiene el puerto, pero el driver de la placa se negó a iniciarlo; todo lo demás parece correcto | vea el paso 5 |
| `[FAIL] rx queues: none opened on <placa>` | lo mismo, visto desde las colas: no se abrió ninguna cola de recepción | vea el paso 5 |
| `[warn] rx queues: <n> of <m> on <placa>, <k> requested` | el equipo reenvía, pero la placa abrió menos colas de las que pidió el ajuste: solo `<n>` workers reciben de ella | no impide el reenvío; es capacidad, no un defecto |
| `[warn] requested and NOT in effect: <regla>` | se pidió una regla de ajuste (hugepages, `isolcpus` y otras) y el kernel no la aplicó; la línea de abajo dice qué hacer | vea el paso 5, *hugepages* |
| `[FAIL] VPP not responding` | el dataplane no está respondiendo; la pantalla muestra las últimas líneas que escribió | vea el paso 4 |
| `[FAIL] ... has restarted <n> times: it is looping` | el dataplane arranca y cae en bucle | vea el paso 4 |
| `[warn] a card this box has is not on the dataplane` | una placa instalada después del arranque no fue tomada | `system reboot` entrega la placa; vea el paso 3 |

En un equipo que reenvía, la línea de la placa es `[ ok ]` y dice qué placa tomó el dataplane y por qué — `claimed by this box with nobody asked` cuando el equipo eligió solo, o el nombre configurado cuando alguien nombró una.


:::info
**El equipo toma todas las placas PCI que tiene, menos una reservada para Linux, y nunca la última.** La reservada es la de la red de gestión indicada en la instalación. En un equipo con una sola placa, toma esa placa y la dirección de gestión se va con ella al lado Linux del puerto.

:::

# 2. `show interface`

```
show interface
```

Si el dataplane no tiene ningún puerto además del interno, la pantalla empieza con `the dataplane has no interface to forward on:` seguido del motivo, leído de los mismos archivos que lee el autotest:

| Motivo | Qué hacer |
|--------|-----------|
| `DPDK is off until hugepages exist, so no NIC can be claimed yet.` | primer arranque: reinicie |
| `the dataplane is off on the installation medium, and a reboot does not change that: nothing here survives one.` | usted está en el medio de instalación: `install` coloca Prisma Routing en un disco |
| `no interface was named, so VPP was given no NIC.` | nombre la placa (paso 6) |
| `'<nombre>' was named and VPP was given no NIC.` | la placa nombrada no existe, no tiene dirección PCI, o lleva la ruta predeterminada y no fue posible leer de ella el camino de vuelta; verifique el nombre en `show hardware` |
| `'<dirección PCI>' is named in the startup configuration and the dataplane does not have it.` | la placa se entregó y el dataplane no la tiene; las líneas de abajo dicen el driver (paso 5) |

Si el puerto aparece, revise su estado: `error` es un puerto que el driver de la placa se negó a iniciar (paso 5). Una placa instalada con el equipo encendido aparece con la nota de que reenvía por AF_XDP hasta que el dataplane se reinicie.

# 3. `show hardware`

```
show hardware
```

Lista las placas **tal como las tiene Linux**: nombre, dirección PCI, driver, estado del enlace y velocidad, con la MAC y lo que ocurre con cada placa cuando se entregue al dataplane. La placa que lleva la ruta predeterminada viene marcada con `*** carries the DEFAULT ROUTE ***`.

Antes de la lista, la pantalla dice lo que el dataplane ya tiene — una placa que el dataplane tomó ya no aparece para Linux y, por eso, no está en la lista — o repite el motivo del paso 2 cuando no tiene nada. Al final, dice qué placas tomó el equipo por su cuenta, cuál reservó para Linux, y cuál está esperando el próximo arranque.

Es aquí donde se verifica si la placa existe para Linux. Una placa que no aparece ni aquí ni en el dataplane no existe para este equipo: revise la máquina virtual o el slot.

# 4. El dataplane se rindió: el archivo del motivo

El dataplane corre en un contenedor. Si no logra arrancar, el contenedor se reinicia algunas veces y luego deja de intentarlo. En ese punto la placa ya había sido quitada de Linux para entregársela al dataplane, y el equipo quedaría sin ninguna interfaz.

**Un vigilante de placas se encarga de eso.** Cada 5 segundos, a partir de dos minutos después del arranque, verifica si el contenedor del dataplane se detuvo definitivamente; si se detuvo:


1. graba el motivo en `/var/log/prisma-routing/why-the-dataplane-stopped` — el estado del contenedor, el código de salida y las últimas 80 líneas que escribió el dataplane;
2. devuelve **todas** las placas tomadas al driver del kernel;
3. vuelve a levantar las interfaces de Linux, para que el equipo vuelva a ser alcanzable.

Devuelve las placas una vez por arranque. Antes de llegar a eso, se ocupa de dos casos: un dataplane que se cayó y que Docker levantó de nuevo recibe del vigilante la configuración vigente, algunos segundos después; y un dataplane que salió solo con status 0, sin que nadie lo mandara, se inicia de nuevo, hasta cinco veces por arranque. En la práctica, un equipo cuyo dataplane no arranca vuelve a tener la placa en Linux unos dos minutos después del arranque, sin dataplane.

**El archivo queda en** `**/var/log**`**, y no en** `**/run**`**, porque el siguiente paso de todo el mundo es reiniciar**, y el reinicio borra el log del contenedor. Léalo antes de tocar cualquier cosa. Con el dataplane detenido, `show version` apunta a él:

```
show version
```

La línea del plugin dice que el dataplane no está respondiendo y termina con `the dataplane gave up and /var/log/prisma-routing/why-the-dataplane-stopped has what it printed before it did`. Para leer el archivo, una cuenta de administrador abre un shell con `start shell`:

```
start shell
cat /var/log/prisma-routing/why-the-dataplane-stopped
exit
```

Las últimas líneas son las que dicen el motivo. Las más comunes están en el paso 5.

# 5. Las causas, y la solución de cada una

**Hugepages.** El dataplane necesita memoria reservada en páginas grandes: de 1 GB cuando la CPU ofrece ese tamaño, de 2 MB cuando no lo ofrece — el caso de muchas máquinas virtuales con el modelo de CPU predeterminado del hipervisor. El ajuste elige el tamaño solo y graba la reserva para el siguiente arranque.

* En el primer arranque la reserva todavía no existe: reinicie (paso 1).
* Si el autotest dice `requested and NOT in effect:` con una regla de hugepages, el kernel no hizo la reserva pedida; la línea de abajo dice qué hacer. En una máquina virtual, revise la memoria asignada a la máquina: 8 GB es el piso a usar, y una máquina de 6 GB no levantó el dataplane.
* Si el archivo del motivo termina en `rte_eal_init failed: Cannot allocate memory (12)` o en `Invalid argument (22)` en un equipo con páginas de 2 MB, es el defecto corregido en las versiones 0.119 y 0.120: lleve el equipo a una versión actual.

**Una placa que el driver rechazó.** El puerto aparece en `show interface` en estado `error`, y el autotest dice `the dataplane holds interface(s) its driver refused to start:`. Todo lo demás — driver, par con Linux, dirección — parece correcto, y el puerto no pasa paquetes.

* La causa conocida es el **MTU**: el equipo inicia cada puerto con el menor MTU máximo entre las placas que tomó; un puerto en `error` después de configurar un `mtu` es un valor que la placa no soporta. Baje el `mtu` de esa interfaz.
* El motivo del driver queda en el journal del dataplane; `show interface` y el autotest indican dónde.

**Una placa entregada y no tomada.** El autotest dice `[FAIL] the dataplane has no interface, and one is named`, y la línea siguiente dice la situación:

| La línea siguiente dice | Qué significa |
|-------------------------|---------------|
| que está en un driver que usa DPDK | la entrega se hizo; el motivo está en el journal del dataplane |
| que está en un driver del kernel | DPDK no logra tomar la placa a través de él |
| que ningún driver está ligado a ella | la placa quedó sin dueño |
| que no está cargado ningún driver que use DPDK | ni `vfio-pci`, ni `uio_pci_generic`, ni `igb_uio` |

En un equipo que toma varias placas, una placa que no se pudo entregar queda afuera y el dataplane arranca con las demás.

**Una placa ixgbe que el kernel rechazó por el módulo SFP+.** La placa no aparece en `show hardware`, y el journal del arranque (`journalctl -b | grep ixgbe`) dice `unsupported SFP+ or QSFP module type was detected`. El driver rechazó el módulo, no la placa. Active `unsupported-sfp allow` en `system` > `hardware` y reinicie el equipo; vea [Enrutamiento, SSH, usuarios y SNMP](/s/wiki-es/doc/enrutamiento-ssh-usuarios-y-snmp-aAKbYtvEg3).

**La placa nombrada no existe.** Si `show interface` dice que el nombre configurado no recibió placa, verifique el nombre en `show hardware` y nómbrela de nuevo (paso 6).

**Una placa que usa la configuración guardada no estaba en el arranque.** El equipo no rechaza la configuración entera por eso: aplica todo lo demás, deja afuera lo que depende de esa placa (su bloque, sus sub-interfaces, lo que la nombra en el enrutamiento) y lo dice en la consola. `show configuration pending` muestra lo que está esperando. Mientras haya algo pendiente, `save` se rechaza, para que el archivo no pierda esas líneas; cuando la placa aparece, el equipo aplica solo lo que faltaba, y `clear configuration pending` es usted renunciando a ella.

# 6. Corrija, y reinicie el dataplane

Para indicar qué placa debe tomar el dataplane:

```
configure
 system
  dataplane
   interface <nombre>
 commit
```

Un nombre que el equipo no tiene se rechaza, y el rechazo lista los que tiene. `commit` graba la elección y recalcula el ajuste, pero **no** entrega la placa: la pantalla dice `NOT IN EFFECT YET` y explica lo que la entrega va a cortar.

Después de corregir la causa, haga que el dataplane vuelva a arrancar, de una de estas dos maneras:

```
system dataplane restart
```

o

```
system reboot
```

`system dataplane restart` devuelve todas las placas a Linux, recalcula el ajuste, vuelve a tomar las placas y arranca el dataplane con la configuración **guardada**. Una placa que el vigilante había devuelto se vuelve a tomar aquí o en el siguiente arranque. Las dos formas requieren `root` o una cuenta con `operator`.


:::warning
**Reiniciar el dataplane corta el reenvío hasta que vuelva, y todo suscriptor pierde su bloque de puertos y las sesiones abiertas;** los bloques se asignan de nuevo a medida que vuelve el tráfico. Y el dataplane vuelve con la configuración **guardada**: ejecute `save` antes, o lo que se aplicó con `commit` y no se guardó no vuelve.

:::

# Cómo verificar

```
show interface
show hardware
```


:::success
`show interface` lista la placa con el estado `up`, y no la frase `the dataplane has no interface to forward on`; `show hardware` dice que el dataplane tiene la placa. En el siguiente arranque, el autotest muestra `[ ok ]` en la línea de la placa, `[ ok ] every tuning rule this box can read back is in effect` y ningún `[FAIL]`.

:::

# Ver también

* [Instalación del appliance](/s/wiki-es/doc/instalacion-del-appliance-MgWUcARKzM) — lo que pregunta la instalación, incluida la red de gestión que queda con Linux
* [Dimensionamiento y requisitos](/s/wiki-es/doc/dimensionamiento-y-requisitos-FuEOaBC4vd) — memoria, CPU y placas
* [Operación y diagnóstico](/s/wiki-es/doc/operacion-y-diagnostico-iHMcBJQ91I) — por dónde empezar cuando el problema no es la placa
* [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) — cuando la solución es una versión actual
