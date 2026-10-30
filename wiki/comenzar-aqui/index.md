# Comenzar aquí

Todo lo necesario para pasar de una máquina sin sistema a un CGNAT traduciendo tráfico. Lea en este orden si es su primera vez con el producto.

| Página | Responde |
|--------|----------|
| [Dimensionamiento y requisitos](/s/wiki-es/doc/dimensionamiento-y-requisitos-FuEOaBC4vd) | Cuánta máquina, cuántas IP públicas y cuánto log exige su caso |
| [Ejecutar Prisma Routing en una máquina virtual](/s/wiki-es/doc/ejecutar-prisma-routing-en-una-maquina-virtual-et1W8N6fwr) | Memoria, CPU, placas virtio, páginas de 1 GB, el agente de QEMU, apagar desde la VTY y qué mide una medición en una VM |
| [Instalación del appliance](/s/wiki-es/doc/instalacion-del-appliance-MgWUcARKzM) | Cómo instala la ISO, qué pregunta y cómo automatizarlo |
| [Acceso, login y roles](/s/wiki-es/doc/acceso-login-y-roles-ZiA9s69LH4) | Dónde cae usted al entrar, y quién puede cambiar qué |
| [Primeros pasos](/s/wiki-es/doc/primeros-pasos-1atRsTUyKZ) | Del disco vacío a suscriptores navegando, en diez pasos |
| [Configurar el CGNAT paso a paso](/s/wiki-es/doc/configurar-el-cgnat-paso-a-paso-v0ecpO8lbt) | De los dos puertos al primer suscriptor traducido, con la pantalla que muestra el equipo en cada paso |
| [Poner suscriptores PPPoE en el aire, paso a paso](/s/wiki-es/doc/poner-suscriptores-pppoe-en-el-aire-paso-a-paso-LS7Pm8pSyB) | Del puerto de acceso y la VLAN al primer suscriptor PPPoE autenticado en el RADIUS, con plan de ancho de banda, traducido por el CGNAT y rastreable en el historial |


:::info
**Antes de instalar, decida si el equipo se administrará por la red.** Un equipo nuevo se configura en la consola, y el dataplane se queda con todas las placas que tiene. Si debe ser alcanzable por `ssh` después del primer reinicio, indique en la instalación la placa de gestión: es la única que el dataplane no reclama. Un equipo de una sola placa entrega esa placa al dataplane de todos modos, y la dirección de gestión se va con ella.

:::

# Ver también

* [Recetas del día a día](/s/wiki-es/doc/recetas-del-dia-a-dia-maiZboGbqY) — una vez que esté en producción
* [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) — `system upgrade`, y qué hacer antes del próximo reinicio
* [Determinístico y BPA — conceptos y mapeo](/s/wiki-es/doc/deterministico-y-bpa-conceptos-y-mapeo-LgBbN0YDcl) — por qué el mapeo es un cálculo y no una tabla
