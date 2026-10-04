# Modelo de simulación

Qué hace realista la simulación y dónde están las aproximaciones. El código vive en `simulation/app/`: `placement.py`, `emergency_catalog.py`, `engine.py`, `routing.py` y `region_data/`.

## Dónde aparecen las cosas

Todo lo que genera el simulador cae sobre la red viaria real del grafo OSRM de la región:

| Elemento | Cómo se coloca |
|---|---|
| Emergencias automáticas y de escenario | Punto aleatorio alrededor del centro urbano (distribución normal de desviación `urban_sigma_m`, 2,2 km en Santiago). Se ajusta a la calle más cercana con OSRM `/nearest` y solo se acepta si está a menos de 45 m y la calle tiene nombre. Así no aparecen en el monte, en un río ni en una pista forestal. |
| Accidentes de tráfico, atropellos, incendios de vehículo | Igual, pero se prefieren avenidas y carreteras. |
| Emergencias manuales y de la PWA | La dirección del aviso se ajusta a la calle más cercana (máx. 80 m); si no hay ninguna, se respeta el punto. |
| Hospitales | Los reales de la región (`regions.py`). |
| Gasolineras | Las reales de OpenStreetMap (`region_data/santiago.json`, 37 estaciones), de la más cercana al centro hacia fuera. |
| Unidades | Salen de hospitales y bases reales: ambulancias de hospitales y bases de ambulancias, bomberos del parque de bomberos y policía de las comisarías. Siempre sobre la calzada. |
| Cortes de tráfico | Son un tramo de la calle real: se traza la ruta entre dos puntos de la misma vía y se ensancha 12 m a cada lado. Vale igual para los cortes que pone el operador con un clic y para los que generan las incidencias externas (accidente, obras, corte de carril, vertido). |
| Incidencias externas simuladas | En calles con nombre del área metropolitana; las de tráfico, en vías principales. |

`region_data/<región>.json` se extrae del mismo `.osm.pbf` que usa OSRM. Para Santiago se sacaron con `osmium tags-filter` los objetos `amenity=fuel`, `emergency=ambulance_station`, `amenity=fire_station` y `amenity=police`, descartando los duplicados a menos de 80 m.

## Emergencias

`emergency_catalog.py` define 28 tipos de llamada: dolor torácico, ictus, caída de persona mayor, accidente de tráfico, agresión, incendio en vivienda… Cada tipo lleva:

- **Frecuencia relativa** y un factor nocturno (22:00–7:00): de noche suben las agresiones, las intoxicaciones y los incendios, y casi desaparecen los accidentes laborales.
- **Reparto de gravedad** (crítica, alta, media o baja).
- **Tiempo medio de asistencia** en el lugar, con una variación lognormal que alarga los casos críticos.
- **Probabilidad de traslado** al hospital: una parada cardiorrespiratoria se traslada en torno al 55 %, un ictus casi siempre y una crisis de ansiedad pocas veces.
- **Descripción** del aviso con edad y sexo plausibles según el tipo.

Las emergencias automáticas siguen un proceso de Poisson. La tasa media es la configurada (por minuto simulado) y se modula por la hora local de la región: mínimo hacia las 4:00 y picos a media mañana y a última hora de la tarde.

Las cifras son aproximaciones razonables de la demanda de un servicio de emergencias urbano en España, no datos oficiales.

### Ciclo de una misión

1. **Sin asignar** → se elige la unidad. Entre las seis mejor puntuadas gana la que antes llega por carretera (OSRM `/table`).
2. **Hacia la emergencia**.
3. **En el lugar** durante el tiempo de asistencia del tipo y la gravedad.
4. Si hace falta traslado: **trasladando paciente** al hospital al que antes se llega. Si no: la unidad queda libre en el sitio.
5. **Transferencia en hospital** de 8 a 25 minutos, más corta en los críticos.
6. **Disponible**: vuelve a una zona de espera cuando no hay trabajo.

La emergencia se marca como atendida cuando la unidad sale del lugar.

Todos los tiempos son simulados. A velocidad 1× una asistencia dura lo mismo que en la realidad; usa 5× o 20× para ver ciclos completos.

## Vehículos

- **Velocidad por tramo**: cada ruta trae la velocidad de circulación de cada tramo según el perfil de coche de OSRM, que depende del tipo de vía, el límite y los giros. «Límite de la vía» en el panel se deriva de ella.
- **Servicio urgente**: hacia la emergencia, y en el traslado de pacientes moderados o críticos, la unidad circula un 20–30 % por encima de esa velocidad. El tope es la velocidad máxima del tipo de unidad.
- **Aceleración y frenada**: 2 m/s² y 3 m/s². Antes de un giro cerrado (más de 50°) la unidad baja a unos 22 km/h y frena para detenerse en el destino.
- **Atascos**: dentro de un corte, 9 km/h (18 km/h con sirena). Las rutas intentan evitarlos con desvíos.
- **Meteorología**: lluvia, viento y visibilidad de la estación más cercana reducen la velocidad hasta un 65 %. Las lecturas son reales, de las estaciones de MeteoGalicia en el área de Santiago (ver [Fuente de eventos](./fuente-de-eventos)).
- **Consumo**: unos 0,2 % de depósito por km en combustión (≈ 80 L y 16 L/100 km) y 0,5 % por km en eléctrico. En parado se consume algo, porque el motor sigue encendido por el equipamiento. Repostar o recargar lleva 5 minutos.

## Comunicaciones

- La cobertura depende de dónde está cada unidad: 5G a menos de 3 km del centro, 4G hasta 8 km y 3G más allá, con zonas de sombra ocasionales en el extrarradio. La señal, la latencia y la pérdida de paquetes salen de ahí.
- «Último contacto» es el último envío de datos que llegó de la unidad: no se actualiza si están caídos todos los canales o si la unidad está sin cobertura.
- Los mensajes de la central (`POST /api/comms/messages`) pasan por tres estados:
  1. **en cola**, si no hay canal o la unidad no tiene cobertura;
  2. **entregado**, en la siguiente ronda de comunicaciones con canal activo;
  3. **leído**, cuando el conductor pulsa «Recibido» en el panel del vehículo (`POST /api/comms/messages/{id}/ack`).

## Comprobarlo

```bash
python scripts/check_realism.py --seconds 120
```

Genera un escenario y verifica con OSRM que emergencias, unidades y lugares están sobre una calle. Después acelera la simulación y comprueba que las velocidades son plausibles y que aparecen las fases de una misión.
