# Operar

Qué hacer con la máquina ya en producción: tareas del día a día, diagnóstico cuando algo falla, y la retención de registros que exige la ley.

| Página | Responde |
|--------|----------|
| [Recetas del día a día](/s/wiki-es/doc/recetas-del-dia-a-dia-maiZboGbqY) | Tareas concretas, con los comandos en orden |
| [Operación y diagnóstico](/s/wiki-es/doc/operacion-y-diagnostico-iHMcBJQ91I) | Por dónde empezar cuando alguien reclama, y el orden de arranque |
| [El equipo arrancó y no reenvía](/s/wiki-es/doc/el-equipo-arranco-y-no-reenvia-DtycFpWiAw) | Qué dice el autotest, dónde el equipo explica por qué se detuvo el dataplane, y cómo volver a reenviar |
| [Cuando se llena el bloque de un suscriptor](/s/wiki-es/doc/cuando-se-llena-el-bloque-de-un-suscriptor-LJuUIMODBQ) | Los tres rechazos, bloques extendidos y límites por suscriptor |
| [Comportamiento NAT y compatibilidad](/s/wiki-es/doc/comportamiento-nat-y-compatibilidad-QvEXCgxsQE) | Si los juegos, las cámaras y el peer-to-peer funcionan detrás de este CGNAT |
| [Protocolos y ALG](/s/wiki-es/doc/protocolos-y-alg-EQ2SXh1xAu) | Qué atraviesa por sí solo, qué necesita ALG y qué no está soportado |
| [Reenvío de puertos (port forward)](/s/wiki-es/doc/reenvio-de-puertos-port-forward-Px7qrbVWaq) | Abrir un puerto de entrada sin vender una dirección pública |
| [uRPF y orígenes falsificados](/s/wiki-es/doc/urpf-y-origenes-falsificados-E8NumaaxV4) | La defensa que impide que el log quede equivocado con toda confianza |
| [Ampliar la capacidad sin tirar suscriptores](/s/wiki-es/doc/ampliar-la-capacidad-sin-tirar-suscriptores-eHEJEg47aX) | El camino que cuesta una parada y el que no |
| [Llevar el equipo a una versión nueva](/s/wiki-es/doc/llevar-el-equipo-a-una-version-nueva-m1PIajBXEF) | `system upgrade`, el paquete a mano, y cuándo la reinstalación es inevitable |
| [Montar un par activo/standby](/s/wiki-es/doc/montar-un-par-activostandby-RHAskyjrZc) | Dos equipos, VRRP en los dos puertos, el archivo del par y cómo probar la conmutación |
| [Monitoreo y SNMP](/s/wiki-es/doc/monitoreo-y-snmp-zQ9bTfrh8z) | Qué ve su NMS, y qué falta para verlo todo |
| [Logging IPFIX y trail de auditoría](/s/wiki-es/doc/logging-ipfix-y-trail-de-auditoria-LHGHuvEHJy) | Adónde van los registros (IPFIX o syslog), qué contienen, y cómo probar que el trail no fue editado |
| [Adónde van los registros: colector, transporte y retención](/s/wiki-es/doc/adonde-van-los-registros-colector-transporte-y-retencion-mICkEG4K9V) | Por qué el equipo exporta y no almacena, qué transporte elegir, qué debe conservar el colector y qué sucede cuando se cae |

# Las tres reglas que evitan incidentes


1. **Cambiar el mapeo de un pool tira todas sus sesiones.** Prefijo, `block-size`, `ports`, `policy`, `mode`, `reserve` o VRF: el pool se reconstruye. Cuando suscriptores de un pool determinístico cambiarían de dirección o de bloque, el `commit` rechaza y muestra cuántos, hasta recibir `commit remap`; en los demás casos (pool dinámico, cambio de VRF, cambio de modo) aplica sin preguntar.
2. **Un cambio remoto va con** `**commit confirmed**`**.** Si corta su propio acceso, la máquina se recupera sola: quien deshace es el equipo, no su sesión, así que salir de la VTY o perder el SSH no cancela nada. Solo un nuevo `commit` confirma.
3. `**commit**` **no es** `**save**`**.** El primero hace que valga ahora; el segundo hace que sobreviva al arranque. Después de actualizar la versión, ejecute `save` antes de reiniciar: el equipo puede rechazar el archivo guardado en el formato antiguo y arrancar sin configuración.

# Vea también

* [Comandos show](/s/wiki-es/doc/comandos-show-yfCeHVInDX): la referencia de los comandos de diagnóstico
* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA): qué campos tiran sesiones y cuáles no
