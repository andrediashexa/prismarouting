# Reenvío de puertos (port forward)

Cómo abrir un puerto de entrada para un suscriptor —cámara, servidor pequeño, consola de videojuegos— sin venderle una dirección pública. Y por qué el equipo rechaza algunas solicitudes.

Un suscriptor detrás de CGNAT no es alcanzable desde afuera, y algunos necesitan serlo. La respuesta antigua del proveedor era "compre una dirección pública", lo que es una venta perdida justamente cuando no tiene ninguna, que es la razón por la que el CGNAT está ahí.

# Cómo hacerlo

```
configure
 nat
  cgnat
   pool residencial
    forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

```
show cgnat forward
```

Es un ajuste **aplicado sin reconstruir**: no tira la sesión de nadie.

También vale en un pool con `filter address-dependent`: el puerto reenviado queda fuera de ese filtrado, porque quien llega por él nunca fue contactado antes por el suscriptor; ese es el propósito del forward.

# La dirección pública no la elige usted

Usted indica el **puerto** público y nunca la **dirección**: la dirección es la del propio suscriptor, la que el mapeo ya le asigna.

Hay dos motivos, y ambos son propiedades que el resto del equipo ya garantiza:

| Motivo | Qué se rompería si fuera diferente |
|--------|------------------------------------|
| **Emparejamiento de dirección** (RFC 4787 REQ-2) | un suscriptor que recibe tráfico en una dirección y envía desde otra no está emparejado, y las aplicaciones que verifican de dónde vino la respuesta verían dos pares diferentes. Conviene decir hasta dónde lo garantiza el equipo: siempre, en un pool determinístico sin `reserve`, donde cada suscriptor tiene un solo bloque. En un pool dinámico, y en el bloque extra que sale de una `reserve`, cuando el suscriptor llena su bloque y recibe otro, el equipo **busca un bloque libre en su dirección pública** antes de tomar cualquiera - es una preferencia, no una regla, porque rechazar la extensión le costaría tráfico al cliente en vez de costar una dirección. La línea `RFC 4787 REQ-2 paired:` de `show cgnat blocks` muestra la cuenta de su propio pool. El reenvío sigue rechazado en cualquier dirección que no sea la primera del suscriptor, que es el lado conservador de esto |
| **La aritmética inversa** | es la que recupera al suscriptor a partir de (dirección pública, puerto) sin consultar una tabla. Un forward en otra dirección sería un mapeo que la fórmula no produce, y todo lector del trail calcularía la respuesta equivocada justamente para esos puertos |

Por eso el puerto público **debe caer dentro del bloque** que el pool asigna a ese suscriptor. Un forward fuera del bloque se rechaza, nombrando al suscriptor cuyo bloque habría tomado:

```
public port 2048 belongs to 100.64.0.9's block, not to 100.64.0.5's.
100.64.0.5 holds 200.150.0.17:1536-2047
```

Averigüe el bloque antes de elegir el puerto:

```
show cgnat subscriber 100.64.0.5
```


:::info
En la práctica el procedimiento es: consulte el bloque del suscriptor, elija un puerto **dentro** de él y configure el forward. El puerto público no es un número que usted inventa: es un número que usted saca de su bloque.

:::

## El mismo puerto sirve a un suscriptor por dirección pública

El pool tiene tantas direcciones públicas como usted le haya dado, y entrega **el mismo rango de puertos a un suscriptor de cada dirección**. Entonces `203.0.113.16:1100` y `203.0.113.17:1100` no son lo mismo: son dos tomas, en dos direcciones, que el pool ya entregó a dos clientes diferentes.

Por eso el puerto 1100 se puede vender una vez **en cada dirección pública** del pool:

```
203.0.113.16:1100 -> 10.0.0.4:8080
203.0.113.17:1100 -> 10.0.0.5:80
```

En un pool con cuatro direcciones públicas, esa es la diferencia entre vender ese puerto a un cliente y venderlo a cuatro. Usted no elige en qué dirección cae el forward: cae en la dirección del propio suscriptor, como siempre.

Lo que sigue rechazado es **la misma toma dos veces**: el mismo puerto, en la misma dirección pública, apuntando a dos lugares. El rechazo nombra a quien ya la tiene:

```
public port 1600 on 200.150.0.17 is already forwarded to 100.64.0.5:80
```

Y también lo contrario, dos forwards diferentes apuntando al mismo puerto del mismo suscriptor, porque un cliente que responde en dos puertos públicos deja de estar emparejado.

## Cómo eliminarlo

```
configure
 nat
  cgnat
   pool residencial
    no forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

`no forward tcp 6200` solo también sirve, mientras ese puerto esté reenviado en **una sola** dirección pública. Si está en más de una, el equipo lo rechaza y nombra a los suscriptores involucrados, en vez de eliminar el primero que encuentre. Eliminar el forward de un cliente que usted no mencionó es el tipo de cosa que uno descubre por teléfono:

```
error: forward tcp 6200 is configured for more than one subscriber
    on different public addresses: 100.64.0.5, 100.64.0.6
    name the subscriber: no forward tcp 6200 to <private-ip> <private-port>
```

Las direcciones de la segunda línea son los **suscriptores** - las direcciones privadas que usted mencionaría en el comando -, y no las públicas donde cayó el reenvío. Son ellas las que pide la tercera línea, por eso son ellas las que muestra la segunda.

El rechazo viene en tres líneas propias, dentro del ancho de la consola. Durante un tiempo salía en una sola frase, más ancha que la pantalla: la terminal la cortaba donde cayera, y lo que se perdía era justamente el final, que es donde está el comando que hay que escribir.

# En un pool dinámico, lo que queda fijado es el bloque

En un pool dinámico el bloque se entrega en el primer flujo y se devuelve después de que el suscriptor queda inactivo. Un puerto fijado dentro de él seguiría al bloque hacia el siguiente suscriptor: el forward seguiría funcionando y pasaría a alcanzar a **otro cliente**, lo que es peor que no funcionar, porque nada parece roto.

Por eso lo que queda fijado no es el puerto, es el **bloque**. Configurar un forward en un pool dinámico fija el bloque de ese suscriptor, y un bloque fijado **no se devuelve por inactividad ni se entrega a nadie más** mientras exista el forward. El comando es el mismo que en los demás pools: usted sigue indicando solo el puerto público.


:::info
Fijar es permanente por definición, y ese es el costo: ese bloque sale de la rotación del pool. `show cgnat blocks` cuenta los bloques fijados aparte de los bloques en uso, justamente para que usted sepa cuánto del pool no va a devolver ninguna inactividad.

:::

## Tres rechazos que va a encontrar

| Rechazo | Qué hacer |
|---------|-----------|
| el bloque está **con otro suscriptor ahora**; el rechazo nombra a quién | esperar a que ese cliente quede inactivo, o atender a este por otro puerto/pool. Tomar el bloque terminaría sus sesiones vivas |
| el suscriptor está **más allá de la cantidad de bloques del pool** | un pool dinámico está sobrevendido a propósito; para ese suscriptor no existe bloque. Amplíe el conjunto público o atiéndalo por un pool que lo alcance |
| el puerto está **fuera del bloque** de ese suscriptor | la misma regla de los pools determinísticos: consulte el bloque antes de elegir el puerto |

El rechazo por bloque ocupado aparece ya en el `commit check`, antes de cualquier cambio en el equipo.

## Después de un reinicio

El bloque vuelve fijado, y usted no necesita hacer nada para ello. Qué bloque está fijado no es un estado guardado aparte: se deduce del propio forward en la configuración, así que basta con que la configuración vuelva. No existe un segundo lugar que pueda quedar desactualizado.

## En el log

Fijar un bloque se registra como **asignación** y liberarlo como **liberación**, exactamente como cualquier otro bloque (RFC 8158). El evento es del bloque, no del forward: un segundo forward dentro de un bloque ya fijado no genera otro registro, y la liberación solo sale cuando se elimina el último forward del bloque **y** el bloque vuelve de hecho al pool. Si el suscriptor todavía tiene sesiones en él, el bloque se convierte en un bloque dinámico común y sale después, por inactividad, con su liberación. En modo dinámico el log es el único registro de quién retuvo un bloque, y un bloque retenido durante un año sin registro es un mapeo que nadie puede atribuir a un suscriptor después, lo que aquí es una obligación legal, no un capricho.

No hay un registro separado para el "fijar": el trail de configuración ya anota cada `forward` agregado o eliminado, con el operador que lo hizo, y el bloque fijado se deduce de él.

Una configuración se aplica con los pools primero y la exportación después, que es el orden de todo arranque. Por eso, cuando se activa la exportación, el equipo vuelve a anunciar como asignación todos los bloques que están fijados en ese instante; de lo contrario, el equipo recién reiniciado retendría cada uno de ellos sin registro alguno.

## Dónde verlo

```
show cgnat blocks                    cuántos bloques están fijados
show cgnat subscriber 100.64.0.5     indica que el bloque está fijado por un forward
```

# Cómo verificarlo

```
show cgnat forward
show cgnat subscriber 100.64.0.5
```

Después, desde afuera, alcance la dirección pública del suscriptor en el puerto configurado.


:::success
El forward listado en el pool, el puerto dentro del bloque del suscriptor y la conexión entrando desde afuera: listo, sin gastar una dirección pública.

:::

# Vea también

* [Configuración de pool](/s/wiki-es/doc/configuracion-de-pool-Y3cACG9QHA) — el campo `forward` y los demás aplicados sin reconstruir
* [Cuando se llena el bloque de un suscriptor](/s/wiki-es/doc/cuando-se-llena-el-bloque-de-un-suscriptor-LJuUIMODBQ) — el bloque del que debe salir el puerto
* [Recetas del día a día](/s/wiki-es/doc/recetas-del-dia-a-dia-maiZboGbqY) — la versión corta de esta tarea
