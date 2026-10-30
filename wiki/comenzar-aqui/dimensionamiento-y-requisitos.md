# Dimensionamiento y requisitos

Cuánta máquina y cuántas direcciones públicas exige su caso. Léalo antes de comprar hardware o de cerrar el bloque de IPv4.

# Requisitos de máquina

| Recurso | Mínimo | Por qué |
|---------|--------|---------|
| RAM     | **6 GB** (8 GB recomendado) | con menos, el equipo arranca y no reenvía: medido con 4 GB, el dataplane queda sin ninguna placa |
| CPU     | **SSE4.2** | el binario de VPP asume SSE4.2 y usa AVX cuando existe |
| Placas de red | **1**  | una basta, y el equipo entrega esa misma placa al dataplane llevándose consigo la dirección de gestión; dos es el formato de un appliance en el rack |
| Disco   | **raíz de 8 GB, más el volumen de log** | el instalador nunca deja la raíz con menos de 8 GB; el volumen de log tiene 30 GB por defecto y puede ser menor (sección *Volumen de log*) |


:::warning
**Una CPU sin SSE4.2 no es un problema lento, es un problema mudo.** VPP muere con `SIGILL` en milisegundos y, con `Restart=always`, el síntoma se convierte en un bucle de reinicio que no dice nada sobre la causa. Por eso el self-test de arranque verifica el conjunto de instrucciones **antes** de mirar VPP.

:::

Medido durante la validación: con 2 GB la ISO reporta `Out of memory: Killed process (vpp)` con VPP ocupando apenas **19 MB residentes**: no era el glotón, era la víctima elegida. Instalado en disco el consumo baja, porque no hay la superposición en RAM, pero el piso de un equipo que reenvía es 6 GB, por el motivo de la tabla anterior.


:::warning
**Por debajo de 6 GB el equipo no reenvía.** En una VM de 4 GB arranca en el perfil conservador, sin hugepages, y el dataplane no recibe ninguna placa.

:::

Medido en una VM con 8 núcleos, después del reboot que aplica el ajuste:

| RAM | Qué hace el equipo |
|-----|--------------------|
| 4 GB | arranca en el perfil conservador, sin hugepages: el dataplane no recibe placa y **no reenvía nada** |
| 6 GB | reenvía, con 1 GB de heap: caben unos 2,7 millones de sesiones |
| 8 GB | reenvía, con 2 GB de heap |

Hasta la versión 0.128 una VM de 6 GB no levantaba VPP; desde la 0.129 lo levanta.

# Cuántas placas de red

**Una placa ya sirve, y así es como la mayoría de las personas conoce este producto:** una máquina virtual con una placa virtual. El dataplane se queda con el puerto y la dirección de gestión se va con él, a la interfaz que el dataplane mantiene emparejada con ella del lado de Linux. El equipo deja de ser alcanzado *junto al* dataplane y pasa a ser alcanzado *a través* de él, con la misma MAC, así que nada del otro lado del cable necesita volver a aprender.

**Dos placas es el formato de un appliance en el rack**, y la diferencia no es el rendimiento: es dónde queda la gestión. El equipo reclama todas las placas menos la que se indicó como red de gestión en la instalación, y nunca la última. Con dos y una gestión indicada, la gestión queda **junto al** dataplane: reiniciar el dataplane no tira su sesión. Con una sola, la gestión pasa **a través** del dataplane, y reiniciarlo (o reiniciar el equipo) tira la sesión también; el equipo vuelve a ser alcanzable cuando el dataplane arranca, sin que nadie escriba nada en la consola.

| Placas | Qué hace el equipo | Qué paga usted |
|--------|--------------------|----------------|
| 1      | entrega la placa al dataplane y lleva la dirección de gestión al par | reiniciar el dataplane tira la sesión de gestión hasta que vuelve |
| 2 o más, con gestión indicada en la instalación | se queda con todas menos la de gestión | nada           |
| 2 o más, con gestión `none` | se queda con todas; la dirección de gestión, si la hay, va al par de la placa que la tenía | lo mismo que el equipo de una placa |

## Placa agregada con el equipo encendido

Una placa que se agrega a la máquina con el equipo en ejecución (en una máquina virtual, una placa agregada por el hipervisor) reenvía **de inmediato**, sin reiniciar nada, si la regla del arranque la reclamaría. Entra con el nombre que le dio Linux, se configura en la VTY como cualquier otra, y `show interface` advierte que está en un modo provisorio. `system dataplane restart`, en la ventana que usted elija, pasa esa placa al modo definitivo: cuesta las sesiones de todos los suscriptores y algunos segundos sin reenviar, y la pantalla lo indica antes de actuar.

## La placa debe ser un dispositivo PCI

El dataplane toma el puerto por su dirección PCI. Una interfaz que **no** tiene dirección PCI no se puede entregar: `lo`, un bridge, un bond y un veth no la tienen. En ese caso el equipo escribe `no-pci` en el archivo del dataplane, arranca, enruta, responde - y no reenvía nada. El autotest de arranque lo indica en lugar de dejar que usted lo descubra por el contador detenido.

## El equipo lee la placa antes de escribir su configuración

El ajuste de arranque le pregunta **a la placa** cuántas colas tiene y cuál es su MTU máxima, en lugar de deducirlo del procesador. Antes pedía una cola por worker, y en una placa virtual, que tiene **una** cola, eso hacía que el dispositivo fuera rechazado: el equipo arrancaba dueño de una placa que no podía iniciar. El número de colas se corrige a partir de la placa.

La MTU también se lee y se **corrige**. Desde la 0.152.1 toda interfaz sin `mtu` comienza en **1500**, el valor predeterminado del equipo; antes comenzaba en los 9000 del propio VPP, y con eso el `tcp-mss auto` daba 8960 y no rebajaba nada en un camino de 1500. Una placa que no acepta ni siquiera el valor predeterminado rechaza el dispositivo: el puerto queda en error y no reenvía nada. Por eso, cuando alguna placa reclamada tiene un máximo por debajo de 1500, el equipo hace que las interfaces comiencen en el **menor máximo** entre las placas que reclama. El valor vale para todas: en un equipo con una placa de 1400 junto a una de 9000, la de 9000 también comienza en 1400, y `mtu <n>` en el bloque de la interfaz sube ese puerto después.

El informe de arranque indica lo que encontró, una línea por placa. En una máquina de ocho núcleos con una placa virtual `enp1s0` de máximo 1500:

```
# NIC queues, enp1s0: 1, and this box has 7 worker(s). Capped at 1: only that many workers receive packets, so expect a fraction of the forwarding this CPU could do.
# interfaces start at: 1500, this box's default. 'mtu <n>' under
```

Con una placa de máximo 1400, aparece su línea y el párrafo comienza en el máximo de la placa:

```
# NIC max MTU, enp1s0: 1400, below the box's default of 1500
```

seguida de un párrafo que comienza con `# interfaces start at: 1400` y explica por qué ese valor vale para todas las placas.

**Un equipo instalado antes de la 0.152.1 sigue en 9000.** La actualización escribe `mtu 9000` en toda interfaz de la configuración guardada que no tenía `mtu` y deja el archivo `/etc/prisma-routing/mtu-9000-kept`; con él, el ajuste de arranque no escribe ningún `default-mtu`, como antes, y el informe dice:

```
# interfaces start at: VPP's 9000, kept on a box installed before
```

**El límite se indica, no se aplica en silencio.** Una cola con siete workers significa un worker recibiendo paquetes, y eso es una fracción de la capacidad que tendría ese procesador: es información de dimensionamiento, y ocultar el recorte cambiaría un equipo que no arranca por un equipo lento sin explicación.

Una placa con colas de sobra recibe una línea corta, y una placa que soporta el valor predeterminado **no** recibe ninguna línea de MTU:

```
# NIC queues, enp1s0: 8, enough for 7 worker(s)
```

Y cuando el equipo no logra leer las colas de la placa, **lo dice**, porque es exactamente ese el estado en el que vivía el defecto:

```
# NIC queues, enp1s0: could not be read, so this asks for 7, one per worker. A card with fewer will refuse the device.
```

En ese caso vuelve a pedir una cola por worker, que es el número que fallaba. Un equipo a punto de rechazar su propia placa no puede tener la misma cara que un equipo sano.

Un equipo **sin** placa de dataplane no habla de ninguna cola: un conteo de colas no es un hecho sobre una máquina que no tiene placa para contar.

## IOMMU: con o sin, y qué cambia

Si hay IOMMU en el firmware, la placa va a `vfio-pci`, que pasa el dispositivo por la IOMMU: un defecto en el dataplane no puede escribir en memoria que el dispositivo nunca recibió. Sin IOMMU, `vfio-pci` carga y rechaza todo dispositivo, así que el equipo usa `uio_pci_generic`, que **no tiene esa protección**, y **lo indica en pantalla**:

```
bind: no IOMMU groups on this box, so vfio-pci cannot take a device.
      Using uio_pci_generic, which has no IOMMU protection.
```

La elección se hace donde es posible explicarla, y no se descubre como un bind que falla sin motivo aparente. Active la IOMMU en el firmware (`VT-d` en Intel, `AMD-Vi` en AMD) si la máquina la tiene: es una opción de firmware, no un ajuste del producto.

### Cuando la entrega de la placa falla, el consejo es el de **su** equipo

Si la placa no acepta el driver, el equipo indica en qué estado quedó, y el peor estado de todos, placa sin ningún driver, se indica por extenso:

```
bind: 0000:5e:00.0 did NOT bind to vfio-pci; it is on no driver at all now.
      The dataplane will start without it. 'dmesg' says why vfio-pci
      refused the device.
```

Después de eso viene el consejo, y es **diferente en los dos equipos**. En un equipo con IOMMU:

```
      This box has IOMMU groups, so the kernel command line is not the
      cause: vfio-pci is being offered a device it will not take.
      Read this, on the box:
        /sys/bus/pci/devices/0000:5e:00.0/iommu_group
      A device with no group of its own cannot go, and a group whose
      other members are still held by host drivers cannot either.
```

Y en un equipo sin IOMMU:

```
      This box has no IOMMU groups, which is why vfio-pci was not
      tried. uio_pci_generic needs a legacy INTx interrupt, and a
      device that has only MSI-X is refused. Enabling an IOMMU in the
      firmware and on the kernel command line is what makes vfio-pci
      available instead.
```

Durante un tiempo los dos equipos recibían la misma frase (*"en un equipo con IOMMU la causa suele ser que falta* `*intel_iommu=on*` *en la línea de comandos"*), y era falsa en ambos, en direcciones opuestas. En el equipo con IOMMU, el parámetro **ya estaba** allí, y se mandaba al operador a revisar una línea de comandos que estaba correcta, en el único mensaje que recibe cuando el appliance arranca sin reenviar. En el equipo sin IOMMU la frase hablaba de otra máquina, y el parámetro que cita no habría ayudado: ese driver no usa ninguna IOMMU.

# El kernel, y el hardware que reconoce

El kernel del equipo es el de Prisma Routing: el mismo código y la misma configuración del kernel de Debian, recompilados, y `uname -r` termina en `-prisma`. Quedan **fuera** las clases de módulos que un router no usa: sonido, Wi-Fi, Bluetooth, NFC, captura de video, joystick, pantalla táctil, radioaficionados, CAN, 802.15.4, módem celular, FireWire, sensores industriales, tarjetas de video AMD y NVIDIA dedicadas y los drivers experimentales (*staging*).

**Toda placa de red, todo controlador de disco y todo driver de virtualización,** `**vfio**` **y** `**uio**` **se mantienen.** Una máquina cuyo hardware de red y de disco reconoce Debian 12 se reconoce aquí. Un kernel nuevo, incluida una corrección de seguridad, llega **en el medio de instalación** de una versión nueva, no por `apt` ni por el paquete de actualización: el paquete reemplaza los archivos del producto y no trae kernel. En un equipo ya en servicio, eso significa reinstalar desde el medio, con lo que [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) dice sobre lo que borra la reinstalación.

# Si el equipo va a recibir la tabla BGP completa

6 GB es el piso de un equipo que **no** carga la tabla completa. Al recibir una tabla IPv4 completa de un tránsito, el enrutamiento por sí solo pasa a ocupar cerca de **1,4 GB** de RAM. Medido con una sesión y un millón de prefijos: `bgpd` quedó con \~840 MB y `zebra` con \~550 MB, contra 37 MB sumados en un equipo que solo recibe la ruta predeterminada. Cada tránsito adicional con tabla completa vuelve a sumar, e IPv6 suma encima.

| Escenario de peering | RAM solo para el enrutamiento | Piso recomendado de la máquina |
|----------------------|------------------------------:|-------------------------------:|
| Solo ruta predeterminada | \~40 MB                       | 6 GB                           |
| Una tabla IPv4 completa | \~1,4 GB                      | 16 GB                          |
| Dos tránsitos con tabla completa, más IPv6 | más que eso                   | 32 GB                          |

El dataplane reserva la mayor parte de la RAM en hugepages y, sin ajuste, deja `máx(2, RAM/4)` GB para todo lo demás; `system memory control-plane <GB>` cambia ese número (sección *Memoria por rol*). En un equipo de 6 GB eso son 2 GB, y una tabla completa por sí sola ya ocupa casi todo; por eso 8 GB ya es justo y 16 GB es el número sensato.


:::info
**No existe un límite de memoria configurado para el enrutamiento, y es deliberado.** Un límite estrecho no hace más lento a BGP: mata a `bgpd` en medio de la convergencia, el servicio se reinicia cinco segundos después, converge de nuevo y vuelve a morir. Quien protege el equipo es el otro lado: el dataplane está fuera de la elección del OOM killer, de modo que, si falta memoria, lo que elige el kernel es el propio proceso de enrutamiento que creció, y el equipo sigue reenviando con lo que ya aprendió. El self-test de arranque indica en pantalla si esa protección está vigente.

:::

# Cuántos núcleos, y qué queda para el plano de control

El equipo entrega al dataplane **todos los núcleos físicos del procesador que atiende la placa de red, menos el primero**. Esos núcleos quedan aislados: el planificador de Linux no coloca nada más en ellos, y cada uno ejecuta un hilo de reenvío que nunca duerme. El primer núcleo queda para el sistema operativo, el enrutamiento, la VTY, el SSH - y el hilo principal del dataplane.

En un equipo de 4 u 8 núcleos eso está equilibrado. En un procesador de 32 núcleos, no: son 31 núcleos reenviando y **uno** cargando todo lo que no reenvía. Y el hilo principal del dataplane, que está en ese mismo núcleo, necesita detener el reenvío por un instante una vez por segundo para hacer el mantenimiento de las sesiones. Cuanto más trabajo de control compite por ese núcleo, más tiempo espera para ser planificado - y el reenvío espera con él.

| Núcleos del procesador | Reenviando | Para todo lo demás |
|-----------------------:|-----------:|-------------------:|
| 4                      | 3          | 1                  |
| 8                      | 7          | 1                  |
| 32                     | 31         | 1                  |

## Devolver núcleos

`workers <n>`, en `system` > `cpu` en la VTY (a partir de la 0.146.1; antes, solo editando `PRISMA_MAX_WORKERS` en `/etc/prisma-routing/tune.env`), limita cuántos núcleos físicos se convierten en hilos de reenvío. **Viene vacío**, y vacío significa sin límite: exactamente el comportamiento descrito arriba. Al completarlo, los núcleos liberados dejan de estar aislados y vuelven al planificador, donde el enrutamiento y la VTY pueden usarlos de verdad.

Cada núcleo devuelto es esa porción de la capacidad de reenvío. No existe un valor predeterminado porque el intercambio depende de cuánto trabajo de control carga el equipo: un equipo que solo recibe la ruta predeterminada casi no tiene plano de control; uno que recibe dos tablas completas, rehace la política BGP y además atiende consultas tiene bastante.

**Disminuir el número de workers cuesta un arranque; aumentarlo cuesta dos, y el segundo es diferente.** Medido:

| Qué hace usted | Próximo arranque | El arranque siguiente |
|----------------|------------------|-----------------------|
| Baja el límite (menos workers) | ya arranca con menos workers | los núcleos liberados salen del aislamiento y vuelven al planificador |
| Sube el límite, o borra el valor | arranca **en modo conservador**: sin workers, sin hugepages, sin placa de datos | arranca con el plan completo |

El modo conservador del segundo caso no es un defecto: el equipo no puede fijar un worker en un núcleo que el kernel todavía no aisló, y aislar es cosa de la línea de comandos del kernel, que solo vale en el arranque siguiente. Pero es un equipo sin reenvío durante un arranque. **Programe ese cambio para una ventana**, y revise el self-test del primer arranque antes de darlo por terminado.


:::info
**El self-test de arranque indica de qué lado está su equipo.** Imprime cuántos núcleos quedaron para el plano de control y advierte cuando quedó **uno** habiendo más de uno aislado, es decir, cuando había un núcleo para devolver y no se devolvió. Un equipo de dos núcleos no recibe esa advertencia: no tiene nada que devolver.

:::


:::warning
**El plano de control queda anclado a los núcleos que no son del dataplane.** En cada arranque, el equipo ancla el sistema, el enrutamiento, el SSH, la VTY y los contenedores de control a todos los núcleos que el kernel no aisló, y el dataplane se ejecuta en un grupo aparte, con los workers en sus núcleos. Antes esto era solo herencia: medido en un equipo de 4 núcleos con 1 a 3 aislados, el enrutamiento y el SSH podían ejecutarse en cualquiera de los cuatro, y un shell que pidiera el núcleo 3 lo conseguía. Ahora el mismo pedido se rechaza, y `show system cpu` muestra a qué núcleos está anclado el plano de control.

Quien decide cuánta CPU tiene el plano de control es la división de los núcleos por rol, justo abajo, y eso es una decisión de dimensionamiento, no un límite de proceso.

:::

## Núcleos por rol: plano de control, hilo principal y workers

Los núcleos físicos del procesador que atiende la placa se distribuyen en este orden: primero los del **plano de control**, después (si se pide) uno solo para el **hilo principal del dataplane**, y el resto se convierte en **worker**. Esto se configura en la VTY, en `system` > `cpu`:

```
prisma# [edit] system
prisma# [edit system] cpu
prisma# [edit system / cpu] control-plane 2
prisma# [edit system / cpu] main-thread dedicated
prisma# [edit system / cpu] commit
```

| Comando | Qué hace | Predeterminado |
|---------|----------|----------------|
| `control-plane <n>` | cuántos núcleos físicos quedan para el sistema, el enrutamiento, el SSH, la VTY y los contenedores | `1`            |
| `main-thread shared` | el hilo principal del dataplane comparte el primer núcleo del plano de control | sí             |
| `main-thread dedicated` | el hilo principal recibe un núcleo exclusivo, aislado como el de un worker; cuesta un worker | no             |
| `workers <n>` | como máximo `n` workers; los núcleos que sobran quedan para Linux, sin aislarse | un worker por cada núcleo restante |
| `cgnat-workers <n>` | cuántos workers traducen; los demás solo reciben de la placa y entregan al worker dueño del suscriptor | todos hacen ambas cosas |
| `no control-plane`, `no main-thread`, `no workers`, `no cgnat-workers` | vuelve al valor predeterminado |                |

**Por qué darle un núcleo al hilo principal:** una vez por segundo detiene los workers para expirar sesiones, y el reenvío espera mientras no se ejecuta. Si comparte núcleo con el enrutamiento, espera al enrutamiento; con una tabla BGP completa convergiendo, eso se nota. En un núcleo exclusivo, no espera a nadie.

**Workers de RX y workers de CGNAT.** Por defecto, todo worker recibe paquetes de la placa y también traduce. Con `cgnat-workers <n>`, los últimos `n` workers quedan solo con la traducción, y los demás solo reciben y entregan cada paquete al worker dueño de ese suscriptor. Eso cuesta una entrega entre workers por paquete; si compensa depende del tráfico y de la máquina, y es exactamente lo que mide un benchmark comparando las dos formas. Medido en un laboratorio con tres workers y el 0 solo recibiendo: todo paquete entró por el worker 0, la traducción ocurrió solo en los workers 1 y 2, y el pool dinámico quedó dividido entre esos dos.

Pedir tantos workers de CGNAT como tiene el equipo no separa nada: al menos uno debe recibir, y el equipo lo indica en el informe de arranque.

**El worker es lo primero.** En un equipo demasiado pequeño para lo que se pidió, el equipo mantiene al menos un worker, renuncia primero al núcleo dedicado y después a núcleos del plano de control, y lo indica en el informe de arranque con una línea `WARNING`.

El cambio va a `tune.env` (`PRISMA_CONTROL_PLANE_CORES`, `PRISMA_MAIN_THREAD` y `PRISMA_CGNAT_WORKERS`) y **vale en el arranque siguiente**, como todo lo que es aislamiento de núcleos, y con la misma regla de la sección anterior: quitar núcleos del plano de control aísla núcleos que no estaban aislados, y eso pasa por un arranque en modo conservador antes del plan completo. `show system cpu` muestra lado a lado lo que está configurado y lo que está ejecutando este arranque:

```
prisma> show system cpu
configured, in effect from the next reboot:
  control-plane cores   2
  VPP main thread       dedicated
  VPP workers at most   every core left
  CGNAT workers         all, each also receiving
this boot:
  VPP main thread       core 0
  VPP workers           1-3
  translating workers   all of them
  isolated from Linux   1-3
  control plane held to 0
```

`show system tuning advice` indica si el layout que se está ejecutando es el que el equipo debería tener (workers desiguales, colas que no coinciden, hilo principal que sobra o que falta) con la línea de la VTY que lo cambia, y trae una tabla de 4 a 32 vCPU con la división recomendada y los workers que el ajuste del equipo obtiene de cada una. En una VM, también trae la fijación de CPU para el hipervisor: vea [Ejecutar Prisma Routing en una máquina virtual](/s/wiki-es/doc/ejecutar-prisma-routing-en-una-maquina-virtual-et1W8N6fwr).

## Memoria por rol: plano de control, CGNAT y buffers

La RAM del equipo se divide en dos lados. Las **hugepages** son del dataplane: la memoria del CGNAT (sesiones y suscriptores), los buffers de paquetes y un margen. El **resto** es del plano de control: el sistema, el enrutamiento, el SSH, la VTY y los contenedores. Una hugepage nunca se devuelve a nadie, así que lo que el plano de control tiene garantizado es exactamente la RAM que no se convirtió en hugepage.

Los tres números se configuran en la VTY:

```
prisma# [edit] system
prisma# [edit system] memory
prisma# [edit system / memory] control-plane 12
prisma# [edit system / memory] buffers 65536
prisma# [edit system / memory] commit
```

| Comando | Qué hace | Sin él |
|---------|----------|--------|
| `system memory control-plane <GB>` | la RAM que conserva el plano de control, fuera de las hugepages | un cuarto de la RAM, como mínimo 2 GB |
| `system memory buffers <n>` | cuántos buffers de paquetes por nodo NUMA | 16384 por worker |
| `nat cgnat memory <GB>` | la memoria de las sesiones y de los suscriptores del CGNAT | calculada según la meta de suscriptores |

Una tabla BGP completa ocupa unos 840 MB en bgpd y unos 550 MB en zebra, medidos. Un equipo que recibe dos tablas completas y además atiende consultas necesita más que el valor predeterminado en una máquina pequeña. Si la suma pedida supera lo que da la RAM, el arranque limita las hugepages y lo indica en el informe.

Vale en el arranque siguiente, porque las hugepages se reservan en la línea de comandos del kernel. `show system memory` muestra lado a lado lo configurado y lo que tiene este arranque:

```
prisma> show system memory
configured, in effect from the next reboot:
  control plane keeps   12 GB
  CGNAT memory          8 GB
  packet buffers        65536 per NUMA node
this boot:
  RAM                   16.0 GB
  hugepages             4.0 GB
  VPP main heap         2G
  packet buffers        49152 per NUMA node
  statistics segment    VPP default, 32M
  control plane uses    3.0 GB
```

## Qué acepta el archivo de ajuste

`/etc/prisma-routing/tune.env` se lee en cada arranque, y cada uno de sus valores tiene un formato. Un valor fuera de ese formato se **rechaza en voz alta**, con el nombre de la variable y el valor entre comillas, y el equipo usa en su lugar el valor de fábrica.

| Variable | Acepta | De fábrica | Si se rechaza |
|----------|--------|------------|---------------|
| `PRISMA_DATAPLANE_IF` | nombre de interfaz, o vacío | vacío      | —             |
| `PRISMA_SUBSCRIBERS` | solo dígitos | `200000`   | `200000`      |
| `PRISMA_LOW_LATENCY` | `1` o `0`, nada más | `1`        | `1`           |
| `PRISMA_MAX_WORKERS` | solo dígitos, o vacío para sin límite | vacío      | sin límite    |
| `PRISMA_CGNAT_MEMORY_GB` | GB enteros, o vacío para la estimación según `PRISMA_SUBSCRIBERS` | vacío      | la estimación |
| `PRISMA_CONTROL_PLANE_CORES` | solo dígitos, 1 o más, o vacío | vacío (un núcleo) | un núcleo     |
| `PRISMA_MAIN_THREAD` | `shared` o `dedicated`, o vacío | vacío (`shared`) | `shared`      |
| `PRISMA_CGNAT_WORKERS` | solo dígitos, 1 o más, o vacío | vacío (todos) | todos hacen ambas cosas |
| `PRISMA_CONTROL_PLANE_MEMORY_GB` | GB enteros, 1 o más, o vacío | vacío (un cuarto de la RAM) | un cuarto de la RAM |
| `PRISMA_BUFFERS_PER_NUMA` | solo dígitos, o vacío | vacío (16384 por worker) | 16384 por worker |

`PRISMA_LOW_LATENCY=1` desactiva los estados profundos de ahorro de energía del procesador, desactiva las páginas grandes transparentes y mantiene el gobernador de frecuencia en `performance`. Cuesta energía y compra ráfagas predecibles, y por eso viene en `1`: esto es un router. `0` deja los tres en paz, para un equipo que necesita ahorrar más de lo que necesita reenviar.


:::warning
**Escriba exactamente** `**1**` **o** `**0**`**.** Un valor como `true` o `sim` se rechaza: el equipo indica qué variable rechazó, con qué valor, y cuáles son los dos valores que acepta y qué hace cada uno. Sigue con `1` y el arranque continúa.

Esto es deliberado: un error de tipeo **no** puede costar el ajuste del equipo. Una configuración ignorada se arregla editando una línea; un arranque que falla en medio del ajuste deja la línea de comandos del kernel diciendo una cosa y el archivo del dataplane diciendo otra, y entonces el equipo reenvía a medias sin ningún contador que lo muestre.

:::

**¿Cambió** `**tune.env**`**? Solo vale en el arranque siguiente.** El aislamiento de núcleos, las hugepages y los estados de energía son línea de comandos del kernel. Cuando el archivo ya dice una cosa y el equipo todavía ejecuta la línea antigua, el self-test de arranque lo dice con todas las letras, en lugar de dar el equipo por ajustado.

**Y lee lo que hizo el kernel, no lo que se pidió.** Un parámetro se puede perder en tres lugares (`update-grub` no se ejecutó, el equipo no se reinició, o el kernel lo vio y lo rechazó), y el equipo que pidió páginas de 1 GB en un procesador que no tiene páginas de 1 GB pasó toda su vida diciendo que estaba ajustado. Son dieciséis reglas leídas de vuelta de la máquina en funcionamiento, cada una en uno de cuatro estados: **vigente**, **pedido y no vigente**, **no se puede leer en este equipo** y **no se pidió aquí**. Los cuatro están descritos, con las líneas que imprime el equipo, en *Operación y diagnóstico*.

## El self-test verifica los núcleos que recibió el dataplane

El equipo calcula el plan de núcleos en cada arranque y lo graba en un archivo que el dataplane lee al iniciar. Dos cosas pueden salirse de lugar sin ningún síntoma:

* el dataplane puede estar ejecutándose desde **antes** del último cambio de plan, y en ese caso sigue en los núcleos con los que arrancó mientras la pantalla reporta el plan nuevo;
* la línea de comandos del kernel puede no haberlo acompañado, y en ese caso un hilo de reenvío comparte el núcleo con todo lo demás.

En los dos casos el equipo arranca, reenvía, responde a todo y entrega una fracción de la capacidad. **No hay contador que lo delate**: un hilo que nunca duerme marca 100% de uso tenga o no el núcleo para sí solo.

Por eso el self-test no verifica el archivo: verifica dónde colocó realmente el kernel los hilos del dataplane y qué núcleos está aislando realmente el kernel, y reclama cuando los tres no concuerdan. Reiniciar el dataplane resuelve el primer caso; el segundo requiere un `update-grub` y un reboot.

# Cómo calcula el equipo la memoria del dataplane

En cada arranque, `prisma-routing-tune` decide cuántas páginas de 1 GB (hugepages) reserva el kernel para VPP. Es memoria que sale de Linux y queda dedicada al dataplane. Dentro de ella está el **heap principal**, que es la memoria del CGNAT: todas las sesiones, las dos tablas de sesiones y los registros de suscriptores viven allí.

## Darle al CGNAT una memoria propia

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] memory 8
prisma# [edit nat / cgnat] exit
prisma# [edit nat] exit
prisma# [edit] commit
```

El `commit` indica cuántas sesiones admite ese número y que **vale a partir del próximo reboot**: la memoria se reserva mediante la línea de comandos del kernel, y hasta entonces el dataplane tiene la que tenía en el arranque. Ejecute `save` y reinicie en su ventana. `no memory` vuelve a la estimación.

**Cómo elegir el número.** Lo que decide es el número de **sesiones** abiertas al mismo tiempo, no el de suscriptores. Medido: cerca de 300 bytes por sesión con millones de ellas, y menos de 1 KB por suscriptor. 200 mil suscriptores con 100 sesiones cada uno son 20 millones de sesiones, unos **6 GB**. Con 300 sesiones cada uno, unos 17 GB.

`show cgnat memory` muestra el tamaño del heap, cuánto está en uso, cuántas sesiones caben todavía, cuántas se rechazaron por falta de memoria y si el número dado ya está vigente.

## La cuenta

**1. La memoria del CGNAT.** El número que usted dio. Sin él, una estimación: `PRISMA_SUBSCRIBERS` × 13,5 KB, redondeado a GB, como mínimo 1. Con el valor predeterminado de 200000 da **3 GB**, unas 45 sesiones por suscriptor.

**2. Buffers de paquetes.** 1 GB por cada 4 workers, redondeando hacia arriba, como mínimo 1. En una máquina de 8 núcleos, 1 queda para el sistema y 7 se convierten en workers: **2 GB**.

**3. Margen.** **1 GB**, fijo, para el resto de la memoria física de VPP.

**4. Lo deseado** es la suma. Con 8 GB dados: 8 + 2 + 1 = **11 GB**.

**5. El techo según la RAM de la máquina.** Linux, Docker y FRR quedan fuera de las hugepages, con `máx(2, RAM ÷ 4)` GB. La RAM se toma en GB enteros, redondeada hacia abajo: una VM de "16 GB" muestra unos 15,6, que pasan a ser 15. Entonces quedan 3 GB para el sistema, el techo es 12, y el equipo reserva **mín(11, 12) = 11 GB**.

**6. El heap** es lo reservado, menos los buffers, menos el margen: 11 − 2 − 1 = **8 GB**, exactamente lo que se dio. **Ya no hay techo de 2 GB**: ese techo limitaba a cualquier equipo a unos 10 millones de sesiones, fuera cual fuera la RAM.

| RAM de la VM | Memoria del CGNAT pedida | Reservado | Heap del CGNAT | Sesiones, aproximadamente |
|--------------|--------------------------|-----------|----------------|---------------------------|
| 8 GB         | estimación (3 GB)        | 5 GB      | 2 GB           | 6 millones                |
| 16 GB        | 8 GB                     | 11 GB     | 8 GB           | 27 millones               |
| 32 GB        | 16 GB                    | 19 GB     | 16 GB          | 56 millones               |

Cuando el techo recorta lo deseado, el log del ajuste advierte cuántos GB faltaron. Por debajo de 2 GB el equipo no reserva nada y arranca en el perfil conservador, que funciona pero no tiene rendimiento.

**Cuando el heap se llena, el equipo rechaza la sesión nueva y la cuenta**, y el dataplane sigue en pie: las sesiones que ya existen siguen traduciendo.

# Memoria por estructura

| Estructura | Dimensionamiento | Orden de magnitud |
|------------|------------------|-------------------|
| Bitmap de puertos | `block-size` bits por suscriptor activo | 64 bytes con bloques de 512 → \~13 MB para 200 mil suscriptores |
| Sesiones   | un pool y dos tablas por worker | \~300 bytes por sesión, medido con 2 millones de sesiones |
| Propiedad de bloque (modo dinámico) | 4 bytes por bloque, por porción de worker |                   |

La tabla de sesiones se ajusta con:

```
nat
 cgnat
  session-table buckets <n> memory <MB>
  exit
 exit
```

El valor predeterminado es 16 mil buckets. **El número de** `**memory**` **no dimensiona nada:** las tablas, como todas las sesiones, quedan en el heap principal del dataplane, y es ese heap el que se llena.

**Quien decide la memoria es el número de sesiones, no el de suscriptores.** Medido: un suscriptor cuesta menos de 1 KB, y una sesión cerca de 300 bytes cuando son millones. 200 mil suscriptores con 100 sesiones cada uno son 20 millones de sesiones, unos 6 GB.

Cuando el heap se llena, el equipo **rechaza la sesión nueva y la cuenta**, y el dataplane sigue en pie: las sesiones que ya existen siguen traduciendo. El contador aparece como `no memory for a new session`. Antes de esto el dataplane se caía por completo, llevándose a todos los suscriptores con él.

# El segmento de contadores, que tira el dataplane cuando se llena

El dataplane publica todos sus contadores en un segmento de memoria de tamaño **fijo**. Cada interfaz que carga ocupa una parte de ese segmento (cerca de 2,4 kB, más 200 bytes por hilo del dataplane), y el segmento no crece.

Cuando se llena, el dataplane **no rechaza la interfaz siguiente: aborta**. El equipo pierde todo el reenvío, de una vez, y no escribe ninguna explicación antes de caerse.

El tamaño de fábrica es el del propio dataplane: **32 MB**. Cuánto cabe en él depende del número de hilos, porque cada hilo suma contadores a cada interfaz. Medido con subinterfaces VLAN: un equipo de un hilo abortó después de **7.800**, uno de tres hilos después de **5.100**, y uno de ocho hilos (lo que recibe una máquina de ocho núcleos) después de **3.300**. Con el segmento en 1 GB, un equipo llegó a 40.000 interfaces, que fue donde se detuvo la medición y no donde encontró un límite.

**Un equipo de CGNAT común no se acerca a eso.** Un suscriptor no es una interfaz: 200 mil suscriptores detrás de un pool comparten un puñado de puertos. Quien se acerca es quien crea **una interfaz por cliente**, una subinterfaz QinQ por suscriptor, por ejemplo, o una sesión PPPoE, que también es una interfaz.


:::info
**El equipo dimensiona ese segmento por sí solo, a partir de lo que su configuración hace cargar al dataplane.** En el arranque cuenta las interfaces que nombra el archivo de configuración, el lado Linux de cada par que el dataplane mantiene con Linux y el máximo de sesiones PPPoE que permite la configuración, y reserva para cada una **tres veces lo que pesan sus contadores** en los hilos que va a ejecutar el equipo: unos 7,9 kB en un hilo, 12,1 kB en ocho. Tres veces porque hacer crecer la tabla de contadores exige la copia vieja y la nueva al mismo tiempo, y el aborto medido llegó a 2,5 veces el peso.

Si esas interfaces caben en los 32 MB de fábrica, **no escribe nada**: reservar menos de lo que el dataplane ya da gratis solo le quitaría contadores a su equipo. Y nunca le da al segmento más de un cuarto de la memoria que mantiene el plano de control: una configuración que pediría más recibe ese techo, y el ajuste de arranque dice cuántas interfaces admite.

:::

Fuera de las sesiones PPPoE, cuyo máximo declara la configuración, lo que ningún archivo de configuración tiene es la interfaz creada **mientras el equipo está en ejecución**. Si su topología crea interfaces en tiempo de ejecución, el número es suyo y está en la sección siguiente.

**El tamaño nuevo solo vale en el próximo inicio del dataplane.** El segmento se lee cuando arranca y nunca crece después: si usted crea miles de subinterfaces en un equipo que ya está encendido, gastan el segmento con el que arrancó ese equipo. El cálculo entra en el archivo de configuración del dataplane en el arranque siguiente, y vale a partir de ahí.

El self-test de arranque imprime cuánto del segmento está gastado, sobre cuántas interfaces, y **quién eligió el tamaño**:

```
  [ ok ] statistics segment: 4.20M of 31.99M used, 3 interface(s)
        size: VPP's own default, which this product does not set
```

Hay tres respuestas posibles en esa última línea: el valor predeterminado del dataplane, `chosen by prisma-routing-tune from this box's configuration` cuando fue el equipo el que lo calculó, y `set in /etc/prisma-routing/vpp-base.conf` cuando fue usted.

Pasada la mitad, esto se convierte en una advertencia. Pasados tres cuartos se convierte en una **falla**, con lo que sucede cuando se llena y qué hacer al respecto. Tres cuartos, y no noventa por ciento, porque el último tramo no es aprovechable: la tabla de contadores es un solo bloque de memoria, y crecer significa copiarla completa, lo que exige la copia vieja y la nueva al mismo tiempo.

## Cómo elegir el tamaño usted mismo

En `/etc/prisma-routing/vpp-base.conf`, que es el archivo base que usted edita:

```
statseg {
  size 256M
}
```

Reinicie el equipo después. El ajuste automático de arranque **nunca sobrescribe** ese fragmento: agrega los bloques que calcula **después** de lo que hay en ese archivo, y cuando existe un `statseg` suyo no escribe ninguno: el número pasa a ser enteramente suyo, incluso si es menor de lo que pediría su cuenta.


:::warning
**Ese segmento es RAM que sale del resto.** No pida 1 GB en un equipo de 6 GB por precaución. Pida lo que pide el número de interfaces de su topología, y verifique en el self-test del arranque siguiente cuánto de eso se está usando realmente.

:::

# Cuántas direcciones públicas

En el **modo determinístico** todo suscriptor ocupa su bloque esté en línea o no. La cuenta es directa:

```
direcciones públicas = suscriptores × block-size ÷ 64512
```

(64512 es el rango útil de puertos, de 1024 a 65535.)

| Suscriptores | Bloque | Direcciones | Prefijo |
|-------------:|-------:|------------:|---------|
| 50\.000      | 512    | \~397       | `/23`   |
| 100\.000     | 512    | \~794       | `/22`   |
| 200\.000     | 512    | \~1.588     | `/21`   |
| 200\.000     | 256    | \~794       | `/22`   |
| 200\.000     | 1024   | \~3.175     | `/20`   |

El **modo dinámico** asigna solo a quien está usando, así que admite sobresuscripción y la necesidad baja bastante, a cambio de volver a generar log.

## En modo dinámico, dimensione según el número que garantiza el equipo

En un equipo con más de un worker, los bloques de un pool dinámico se **reparten entre los workers**: cada bloque pertenece a un solo worker, y cada suscriptor sale por un solo worker. Los dos repartos se calculan de formas diferentes, así que no siempre coinciden: un suscriptor puede recibir "sin bloque libre" mientras sobran bloques en la porción de otro worker.

Por eso `show cgnat pool` muestra **dos** números en un pool dinámico:

```
capacity     768 subscribers (usable 768)
             384 whatever the addresses are: blocks are sharded 2 ways and a subscriber draws only from its own worker's slice
```

* el primero es el **techo**: lo que atiende el pool si los suscriptores se distribuyen perfectamente entre los workers;
* el segundo es el **piso garantizado**: lo que atiende el pool independientemente de qué direcciones internas estén en línea.

**Dimensione según el segundo.** El primero depende de la suerte en el plan de direccionamiento, y la suerte no es un número de compra.

La segunda línea solo aparece cuando hay algo que perder. Un pool con bloques de sobra para toda dirección interna no pierde nada con el reparto, y en ese caso el equipo imprime solo el techo.

La distancia entre los dos se reduce rápido a medida que el pool crece en relación con el número de workers. Medido, con los suscriptores distribuidos aleatoriamente por el rango interno:

| Bloques en el pool | Workers | Garantizado | Atendidos en la práctica | Pérdida |
|-------------------:|--------:|------------:|-------------------------:|--------:|
| 12                 | 2       | 6           | \~10,6                   | 11%     |
| 48                 | 4       | 12          | \~43,3                   | 10%     |
| 192                | 4       | 48          | \~183                    | 4,5%    |
| 768                | 8       | 96          | \~742                    | 3,4%    |
| 7\.936             | 8       | 992         | \~7.848                  | 1,1%    |

Y la pérdida **desaparece** cuando las direcciones internas se entregan en secuencia desde el inicio del rango, que es el caso de un pool DHCP que se llena de abajo hacia arriba. Un plan de direccionamiento contiguo no pierde nada; un plan con huecos pierde lo de la columna de la derecha.


:::warning
La conclusión práctica: en modo dinámico, **un pool pequeño con muchos workers es el peor caso**. Un pool de 12 bloques con 8 workers garantiza un único suscriptor. Si el pool es pequeño por elección (y en el modo dinámico suele serlo, ya que existe justamente para ahorrar IPv4), prefiera menos workers o un pool más grande antes que descubrirlo con el cliente en la línea.

:::


:::info
Un bloque más pequeño ahorra direcciones y aprieta al suscriptor. 512 puertos atienden el uso residencial común con holgura; por debajo de 256, las aplicaciones que abren muchas conexiones simultáneas (un navegador con muchas pestañas, un cliente de torrent) empiezan a toparse con el límite. Haga seguimiento con `show cgnat top` antes de reducir.

:::

# Volumen de log

Premisas explícitas, para orden de magnitud y no para precisión: 200 mil suscriptores, modo dinámico, un bloque renegociado \~5 veces por día por suscriptor activo, \~40 bytes por registro con el encuadre IPFIX.

| Escenario | Registros/día | Volumen bruto/día | Un año |
|-----------|--------------:|------------------:|-------:|
| BPA (asignación + liberación) | \~2 millones  | \~80 MB           | decenas de GB |
| NAT por flujo (referencia) | \~40 millones | \~1,6 GB          | decenas de TB |
| Determinístico | 0             | 0                 | 0      |

## Los registros tienen su propio sistema de archivos

El instalador crea tres particiones (ESP, raíz y `/var/log/prisma-routing`) en lugar de dejar todo junto. Lo que queda en el tercer volumen es el **trail de configuración** y el **spill** del exportador: este equipo no almacena registros de conexión, los exporta, y quien los almacena durante el año que exige el Marco Civil (Ley 12.965/2014 de Brasil; los requisitos de retención varían según el país) es el colector.


:::warning
Si comparte el sistema de archivos con el resto, un log que se llena impide que la máquina grabe su propia configuración y su propio trail, porque está ocupada registrando quién se conectó a qué. Con un volumen propio, un log lleno es solo un log lleno.

:::

El tamaño predeterminado es aritmético y la cuenta aparece en pantalla: \~80 MB por día para 200 mil suscriptores en modo dinámico, o cerca de **29 GB por año**. El modo determinístico no registra ninguna asignación y casi no necesita espacio; por eso esto es una pregunta y no una constante. La raíz nunca queda con menos de 8 GB.

En una instalación desatendida, `prisma-routing.log-gb=` responde esa pregunta en la línea de comandos del kernel.

El volumen se monta con `nofail`: una máquina que no arranca porque desapareció el volumen de registros es una máquina a la que nadie puede entrar para descubrir el porqué. Por eso el self-test de arranque **reporta dónde están los registros**; sin eso, un equipo cuyo volumen no se montó arranca calladito grabando en la raíz.

Esa tabla es el argumento comercial del CGNAT por bloque en una línea: almacenar un año de registros pasa de decenas de terabytes a decenas de gigabytes, o a nada. Es un argumento sobre el disco del **colector**, no sobre el de este equipo.

# Cómo verificar

Después de instalar, en la consola:

* el self-test reporta RAM, conjunto de instrucciones, VPP, contadores de reinicio, estado del tuning, FRR y pools configurados;
* también verifica **los núcleos**: si el dataplane está en los núcleos que nombra su plan y si el kernel está aislando esos núcleos, e indica cuántos quedaron para el plano de control;
* **distingue advertencia de falla** y siempre sale con código cero, porque un diagnóstico no puede tirar el arranque;
* el contador de reinicios importa más de lo que parece: un servicio en bucle puede estar "activo" en el instante en que usted lo mira, y solo `NRestarts` cuenta la historia.


:::success
Self-test diciendo listo, sin advertencia de instrucción ausente y sin contador de reinicios subiendo: la máquina está dimensionada para lo que usted le pidió.

:::

# Vea también

* [Primeros pasos](/s/wiki-es/doc/primeros-pasos-1atRsTUyKZ): la configuración después de que el hardware está correcto
* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA): dónde se eligen el `block-size` y el modo
* [Ampliar la capacidad sin tirar suscriptores](/s/wiki-es/doc/ampliar-la-capacidad-sin-tirar-suscriptores-eHEJEg47aX): cuando cambie la cuenta anterior
