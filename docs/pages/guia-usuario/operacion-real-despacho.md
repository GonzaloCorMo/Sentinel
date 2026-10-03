# Operación de despacho

## Flujo recomendado

1. Verificar estado de motor y conectividad MQTT en cabecera.
2. Seleccionar una emergencia prioritaria (alta primero).
3. Seleccionar ambulancia disponible.
4. Ejecutar `Asignar emergencia a ambulancia`.
5. Confirmar en mapa la ruta y seguimiento.

## Mapa operacional

- **Mapa** (sección *Mapa*, `/map`; aspecto tipo Google Maps en tema claro y oscuro, teselas vectoriales de OpenFreeMap): vista principal para despacho; aquí se colocan ambulancias, emergencias, hospitales, gasolineras y atascos haciendo clic en el mapa con el modo de colocación activo.
- **Sin datos en flota**: el mapa se muestra igual (centrado en la región activa; Santiago de Compostela por defecto) para poder colocar elementos o revisar la zona; no depende de que ya existan coordenadas en la simulación.
- Si el mapa aparece en gris o sin teselas, recarga la página o usa **Maximizar mapa** para forzar el redibujado.

## Buenas prácticas

- Priorizar emergencias `high`.
- Evitar asignar unidades con combustible bajo.
- Revisar panel IA antes de reasignaciones críticas.
- Supervisar estado de broker activo durante incidentes de red.

## Atajos operativos

- `Espacio`: play/pause simulación.
- `E`: crear emergencia demo.
- `R`, `H`, `M`, `B`: comandos rápidos para ambulancia seleccionada.

## Resolución de incidencias

- Si no puedes asignar: valida selección de ambulancia + emergencia.
- Si no hay ruta visible: comprobar el badge OSRM de la cabecera (ver [Runbook de resiliencia](../technical/runbook-resiliencia-operativa.md)).
- Si hay cortes de red: revisar broker activo y failover MQTT.
