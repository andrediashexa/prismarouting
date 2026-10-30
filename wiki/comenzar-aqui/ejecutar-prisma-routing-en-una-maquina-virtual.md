# Ejecutar Prisma Routing en una máquina virtual

Lo que una máquina virtual necesita para que el equipo reenvíe tráfico, y lo que cambia en ella respecto a un servidor físico. Para quien va a evaluar el producto en un hipervisor, o ponerlo en producción en uno.

# Memoria y procesador

| Recurso | Qué solicitar | Qué pasa si falta |
|---------|---------------|-------------------|
| RAM     | **6 GB como mínimo, 8 GB recomendado** | con 4 GB el equipo arranca en el perfil conservador, sin hugepages, y el dataplane no recibe ninguna placa: `show interface` solo muestra `local0` y no se reenvía nada |
| Instrucciones del procesador | **SSE4.2** (AVX2 se aprovecha cuando existe) | sin SSE4.2 el dataplane muere al arrancar, en bucle; el autotest de arranque verifica el procesador **antes** de mirar el dataplane y dice qué falta |
| Páginas de 1 GB (`pdpe1gb`) | deseable, no obligatorio | el equipo pasa a páginas de 2 MB por sí solo; vea *Modelo de procesador sin páginas de 1 GB* más abajo |

Medido en una VM con 8 núcleos, después del segundo arranque, que es el que aplica el ajuste:

| RAM de la VM | Qué hace el equipo |
|--------------|--------------------|
| 4 GB         | arranca, no reenvía |
| 6 GB         | reenvía, con 1 GB de heap: caben unos 2,7 millones de sesiones |
| 8 GB         | reenvía, con 2 GB de heap |


:::warning
**El primer arranque todavía no es el arranque definitivo.** Las hugepages y el aislamiento de núcleos vienen de la línea de comandos del kernel, así que solo valen a partir del arranque siguiente. Una VM evaluada en el primer arranque está en el perfil conservador y no dice nada sobre lo que hace el equipo.

:::

El modelo de procesador importa. El modelo genérico `qemu64` de QEMU no tiene SSE4.2: asigne a la VM el procesador del anfitrión, o un modelo que tenga SSE4.2.

## Modelo de procesador sin páginas de 1 GB

Muchos modelos de procesador virtual no exponen páginas de 1 GB; es el caso del modelo predeterminado de una VM en Proxmox. El equipo lo detecta en el arranque y:

| Qué ajusta | Por qué |
|------------|---------|
| reserva las hugepages en páginas de **2 MB** en lugar de 1 GB | pedir páginas de 1 GB a un procesador que no las tiene no reserva nada, en silencio |
| mantiene el número de páginas que calculó, incluso contra el ajuste que el paquete del dataplane trae de fábrica | ese ajuste, en un equipo de 2 MB, reduciría la reserva después del arranque y el dataplane no arrancaría |
| direcciona la placa por dirección física | en un equipo de 2 MB es el único modo en que el dataplane puede usar la placa |

No hay nada que configurar: desde la versión 0.120 una VM sin páginas de 1 GB arranca y reenvía. Antes de eso arrancaba sin dataplane.

# Placas de red virtuales

| Situación | Qué hace el equipo |
|-----------|--------------------|
| placa **virtio** | es el caso común y el más probado; se entrega al dataplane como cualquier placa PCI |
| placa **virtio legacy** (la que QEMU asigna a una VM sin IOMMU) | también se entrega, desde la versión 0.121; antes de eso el dataplane arrancaba y no tomaba el puerto, sin decir por qué |
| interfaz **sin dirección PCI** (bridge, bond, veth, `lo`) | no se puede entregar al dataplane; el equipo arranca, enruta y no reenvía, y el autotest de arranque lo indica |

## VM sin IOMMU

La mayoría de las VM no tienen IOMMU virtual. El equipo lo detecta en el arranque y vincula la placa al dataplane con el driver `uio_pci_generic` en lugar de `vfio-pci`.

|     | Con IOMMU | Sin IOMMU |
|-----|-----------|-----------|
| driver utilizado | `vfio-pci` | `uio_pci_generic` |
| qué gana o pierde | la placa solo alcanza la memoria que recibió, así que un defecto en el dataplane no escribe fuera de ella | reenvía igual, sin esa protección |
| dónde verificarlo | `show hardware` indica, por cada placa aún no entregada, a qué driver la moverá el equipo | lo mismo  |

## Placa física pasada a la VM

Una placa física entregada a la VM por el hipervisor (*PCI passthrough*) se trata como cualquier placa PCI del equipo: el dataplane la reclama con la misma regla y con el nombre que le dio Linux. En una VM sin IOMMU virtual va a `uio_pci_generic`, como una placa virtio; hasta la 0.141.0 una placa física con ese driver no llegaba al dataplane, que arrancaba sin ella, y desde la 0.141.1 reenvía.

`uio_pci_generic` necesita una interrupción INTx legacy, y una placa que solo tiene MSI-X es rechazada por él; el equipo lo dice en el arranque, y lo que habilita `vfio-pci` es activar una IOMMU para la VM. En esa placa vale todo lo que [Dimensionamiento y requisitos](/s/wiki-es/doc/dimensionamiento-y-requisitos-FuEOaBC4vd) dice sobre placas físicas, y es en ella donde una medición de throughput significa algo.

## Placa agregada con la VM encendida

Una placa que el hipervisor agrega a la VM con el equipo en ejecución reenvía **de inmediato**, sin reiniciar nada:

| Etapa | Qué sucede |
|-------|------------|
| la placa aparece | el equipo solo la toma si la regla del arranque también la tomaría; una placa con dirección IPv4 o con la ruta predeterminada queda con Linux |
| el equipo la toma | entra en el dataplane por **AF_XDP**, con el nombre que le dio Linux y la MAC de la propia placa |
| usted la configura | como cualquier otra: `interface <nombre>`, dirección, `no shutdown`, `commit` |
| `show interface` | advierte, debajo del conteo, que fue agregada con el equipo en ejecución y que reenvía por AF_XDP hasta que el dataplane se reinicie |
| `system dataplane restart` | pasa la placa al modo definitivo (DPDK), en la ventana que usted elija. Un reboot hace lo mismo |

AF_XDP cuesta velocidad, no corrección: la placa la lee el driver del kernel. En una placa virtio de una sola cola, el propio kernel advierte que el envío queda en un modo más lento.


:::warning
`system dataplane restart` detiene el reenvío hasta que el dataplane vuelve y **descarta el bloque de puertos y las sesiones de todos los suscriptores**. El dataplane vuelve con la configuración **guardada**: lo que se aplicó con `commit` y no se guardó no vuelve. La pantalla indica el costo antes de actuar. Medido en una VM: 18 segundos sin reenviar.

:::

## La MTU que soporta la placa virtual

Toda placa virtual que configura el hipervisor anuncia un máximo, en general 1500. Desde la 0.152.1 el equipo arranca toda interfaz sin `mtu` en **1500**, su valor por defecto, así que una placa virtual común ya acepta el puerto. Una placa que no acepta ni ese valor rechaza el puerto: queda en error y no reenvía nada.

| Qué hace el equipo | Consecuencia |
|--------------------|--------------|
| lee en el arranque el máximo de cada placa que va a tomar | el número viene del hipervisor, no de una suposición |
| cuando alguna placa tomada tiene un máximo **por debajo de 1500**, hace que **todas** las interfaces arranquen en el **menor** máximo entre las placas tomadas | en una VM con una placa de 1400 junto a una de 9000, la de 9000 también arranca en 1400 |
| acepta `mtu <n>` en el bloque de la interfaz después | así se sube el puerto que soporta más |

Para transportar tramas mayores que 1500 en una VM, primero hay que aumentar la MTU en la placa virtual, en el hipervisor, y después con `mtu <n>` en la interfaz; el equipo lee el nuevo máximo de la placa en el arranque siguiente. Un equipo instalado antes de la 0.152.1 sigue en 9000: la actualización escribió `mtu 9000` en sus interfaces.

## Varias colas en la placa virtual (multiqueue)

El equipo le pregunta a la placa cuántas colas tiene, y solo ese número de workers recibe paquetes de ella. Una placa virtual con **una** cola en una VM de ocho núcleos hace que un worker reciba todo; el informe de arranque lo indica en una línea por placa, con el límite, en lugar de dejar el equipo lento sin explicación.

| Colas en la placa virtual | Workers que reciben paquetes |
|---------------------------|------------------------------|
| 1                         | 1, sin importar el número de núcleos |
| igual o mayor que el número de workers | todos                        |

Para aprovechar los núcleos de la VM, active en el hipervisor varias colas en la placa virtual (la opción suele llamarse *multiqueue*) con un número igual al de workers. El equipo vuelve a leer las colas en cada arranque.

# Apagar la VM

El *Shutdown* del hipervisor envía el botón de encendido (ACPI), y el equipo se apaga limpiamente: cierra los bloques en el log de retención y detiene los servicios antes de apagarse. Medido en un Proxmox: 41 segundos, dentro del tiempo que el hipervisor espera antes de forzar.


:::warning
**Hasta la versión 0.139.0 el equipo ignoraba ese botón.** En un equipo con esa versión o anterior, el *Shutdown* del hipervisor no hace nada y el apagado forzado que viene después equivale a desconectar el cable de alimentación: los bloques que el dataplane está reteniendo no se cierran en el log de retención. Actualice el equipo, o apáguelo por la VTY.

:::

**El agente de QEMU viene en el medio.** Desde la 0.156.0 la instalación trae `qemu-guest-agent`. Con el canal del agente habilitado en la VM (`agent: 1` en Proxmox), el hipervisor apaga el equipo a través del agente, congela el disco antes de un backup y muestra las direcciones del equipo en su panel. El agente solo corre cuando ese canal existe. Un equipo instalado con un medio anterior y actualizado con `system upgrade` no lo recibe, porque el paquete de actualización no trae paquetes del sistema: instálelo desde una shell de administrador con `apt-get install qemu-guest-agent`.

Apague o reinicie por la VTY, que indica el costo antes de actuar y cierra los bloques en el camino:

```
system shutdown
```

```
system reboot
```

# El equipo sugiere su propia afinación, y la del hipervisor

En una VM, cada vCPU es un hilo del hipervisor, y un worker del dataplane nunca suelta su CPU. Si dos vCPU de workers caen en el mismo núcleo físico (dos hermanos de HT), o si otra VM comparte el hilo con uno de ellos, ese worker gira más lento que los demás, y el tiempo que el hipervisor le quita a la VM aparece como *steal*. Fue lo que sucedió en un equipo de laboratorio: con tráfico casi nulo, los cinco workers giraban entre 2,41 y 3,29 millones de vueltas por segundo, con 7,7% de steal. Al fijar cada vCPU en un hilo del host, en el nodo NUMA de la placa, un núcleo físico por worker, pasaron a 3,21–3,30 millones, y el steal quedó solo en el plano de control.

El equipo lo indica por sí solo:

```
prisma> show system tuning advice
prisma> show system tuning advice seconds 10
```

Muestra lo que tiene (CPU, si es VM y de qué tipo, memoria y hugepages, cada placa del dataplane con sus colas y el worker de cada una, el layout, MTU y `tcp-mss`), mide una ventana de algunos segundos (5 por defecto) sin poner a cero ningún contador, e indica lo que está mal, siempre con el número medido, el límite y el porqué: workers desiguales (un worker más de 15% por debajo de la mediana, sin tráfico), steal superior a 1% en las CPU del dataplane, colas que no coinciden con los workers (solo en las placas por donde reenvía el dataplane: la placa de gestión, en la VRF de gestión, una placa apagada o una sin dirección y sin lado de CGNAT aparecen como verificadas, con el motivo, y no piden colas en el host ni reinicio del dataplane), worker fuera del nodo NUMA de la placa, menos hugepages de las que el equipo pidió, hilo principal dedicado en un equipo ocioso o compartido con un plano de control ocupado. No se modifica nada: aplicar queda en sus manos.

Lo que hay que hacer viene en dos partes. **En el equipo**, las líneas exactas de la VTY y si cada una requiere reinicio del dataplane o reboot. **En el hipervisor**, cuando es una VM: el principio, un *hookscript* de Proxmox (`/var/lib/vz/snippets/prisma-pin.sh`), las líneas `qm set` y un bloque `<cputune>` de libvirt, generados a partir del rol de cada vCPU de este equipo. Los números de las CPU del host vienen como `HOST_*` (el script se niega a ejecutarse mientras quede alguno), a menos que usted pegue la topología del host:

```
prisma> show system tuning advice host-topology
```

Pegue la salida de `lscpu -e=CPU,NODE,SOCKET,CORE` del host y, en una línea aparte, `nic-node <n>` con el nodo NUMA de la placa (`cat /sys/bus/pci/devices/<pci>/numa_node`); termine con una línea vacía. El equipo entonces coloca el hilo principal en el primer núcleo físico del nodo de la placa, el plano de control en el hermano de HT de ese núcleo (el hilo principal está casi siempre inactivo, así que es el lugar más tranquilo del nodo), cada worker en un núcleo exclusivo, los demás hilos de QEMU en los últimos hermanos que quedan, y las demás VM en el otro nodo.

Fijar no basta para mantener el resto lejos de esas CPU, así que también viene la **línea de comandos del kernel del host**: `isolcpus`, `nohz_full` y `rcu_nocbs` en todas las CPU del host que recibe la VM. Con `isolcpus`, el planificador del host nunca coloca allí otra VM, un servicio del host o un hilo del kernel, a menos que esté fijado allí, así que no hay nada más que configurar; los otros dos quitan el tick del timer y los callbacks de RCU de esos núcleos. Requiere reiniciar el host y es estático. (Una partición de cpuset del cgroup hace lo mismo en tiempo de ejecución, pero Proxmox no la gestiona.)

La misma línea reserva la memoria de la VM en páginas de 1 GB en el nodo de la placa (`hugepagesz=1G hugepages=<nodo>:<GB>`), y las líneas `qm set` anclan la memoria allí: `numa0: cpus=0-<n-1>,hostnodes=<nodo>,memory=<MB>,policy=bind` con `hugepages: 1024`. Los buffers del dataplane y el DMA de la placa viven en esa memoria, y la memoria en el otro socket cuesta latencia y rendimiento en cada paquete; `numa: 1` por sí solo no ancla nada. En el equipo de laboratorio, la VM tenía 16,1 GB en el nodo 1 y nada en el nodo 0: local por suerte del primer acceso, no por anclaje. Pegue también la salida de `numastat -p <pid de qemu de la VM>` después de la topología y el equipo le dirá dónde está la memoria ahora.

Al final viene una tabla de dimensionamiento: para VM de 4 a 32 vCPU, cuántas quedan para el plano de control, si el hilo principal es dedicado, cuántos workers resultan (calculado por el propio ajuste del equipo) y cuántos hilos del host reservar. Es el número para decidir cuántas vCPU asignar a una VM nueva.

# Medir el rendimiento en una VM

Un número de rendimiento medido en una VM mide el **camino del switch virtual del hipervisor**, no el equipo. El costo del dataplane es por paquete, y en una VM lo que se agota primero es la cantidad de paquetes por segundo que el hipervisor entrega a la placa virtual.

| Qué se mide | Qué indica |
|-------------|------------|
| paquetes por segundo | el límite real del camino: es lo que se agota primero |
| bits por segundo con tramas pequeñas | poco: el mismo límite de paquetes, con pocos bits en cada uno |
| bits por segundo con tramas grandes | más bits en el mismo número de paquetes; por eso una medición con tramas grandes parece mejor, sin que el equipo haya cambiado |

El equipo en una VM sirve para evaluar la funcionalidad, la configuración y el comportamiento del CGNAT. El rendimiento de producción se mide en una placa física a la que el dataplane acceda directamente.

# Vea también

* [Dimensionamiento y requisitos](/s/wiki-es/doc/dimensionamiento-y-requisitos-FuEOaBC4vd): cuánta máquina y cuántas direcciones exige su caso
* [Instalación del appliance](/s/wiki-es/doc/instalacion-del-appliance-MgWUcARKzM): la instalación en disco, paso a paso
* [Configuración de interfaz](/s/wiki-es/doc/configuracion-de-interfaz-5s5Ar68nsg): `mtu`, direcciones y el bloque `interface`
* [Operación y diagnóstico](/s/wiki-es/doc/operacion-y-diagnostico-iHMcBJQ91I): el autotest de arranque y lo que indica
* [Gestión en una VRF propia](/s/wiki-es/doc/gestion-en-una-vrf-propia-ibjQgO0n3X): la placa de gestión (en una VM, en general virtio) en una VRF propia, con su gateway
