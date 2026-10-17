# Entender el producto

Por qué el producto es así. Léalo cuando necesite decidir la arquitectura o evaluar si el CGNAT por bloque se ajusta a su caso.

| Página | Responde |
|--------|----------|
| [Prisma Routing — visión general](/s/wiki-es/doc/prisma-routing-vision-general-ehYILTAs3q) | Qué es el producto, de qué está hecho y qué todavía no existe |
| [Determinístico y BPA — conceptos y mapeo](/s/wiki-es/doc/deterministico-y-bpa-conceptos-y-mapeo-LgBbN0YDcl) | Determinístico (bloque por fórmula) y BPA (bloque asignado bajo demanda): la diferencia, la cuenta y lo que exige cada uno |
| [Alta disponibilidad](/s/wiki-es/doc/alta-disponibilidad-bjNhRjSh49) | El par activo/standby con VRRP: cómo armarlo, qué preserva la conmutación y qué corta |

# La idea en una frase

El CGNAT convencional registra cada flujo y el log se vuelve el elemento más caro del sistema. El CGNAT por bloque entrega a cada suscriptor un bloque contiguo de puertos, y el log baja a un registro por bloque con BPA, que asigna los bloques bajo demanda, o a cero con el determinístico, que calcula el bloque en lugar de asignarlo. BPA es solo el modo dinámico; el determinístico no es un tipo de BPA.

# Ver también

* [Logging IPFIX y trail de auditoría](/s/wiki-es/doc/logging-ipfix-y-trail-de-auditoria-LHGHuvEHJy) — la consecuencia práctica de esa elección
