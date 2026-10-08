# 04 · Autoclasificador con IA

**Objetivo:** que la gran mayoría de movimientos se contabilicen solos y bien, y que lo dudoso llegue a una persona
con la propuesta ya hecha y el motivo explicado.

**Principio:** *la IA propone, el libro mayor valida, la persona aprueba.* La IA nunca escribe directamente en la
contabilidad: lo que propone pasa por las mismas restricciones que un asiento manual (partida doble, cuenta existente,
periodo abierto, sin duplicados).

## 1. Por qué no "todo con IA"

Un modelo de lenguaje acierta mucho, pero no siempre, cuesta dinero por llamada y no es determinista. Las reglas son
gratis, exactas y auditables, pero solo cubren lo repetitivo. La solución es una **cascada**: cada capa resuelve lo que
puede y pasa el resto a la siguiente.

```
 Extracto / factura / nómina
          │
 0. Ingesta y normalización ── duplicados fuera (hash), datos crudos inmutables
          │
 1. Emparejamiento ─────────── movimiento ↔ factura abierta  → cobro/pago (430/400/410)
          │                    importe, NIF/IBAN, referencia, ventana de fechas; 1↔N y N↔1
 2. Reglas ─────────────────── contraparte / patrón → cuenta  (TGSS → 476, AEAT 303 → 4750...)
          │
 3. IA ─────────────────────── el resto: plan de cuentas + ejemplos validados de la empresa
          │                    → cuenta, confianza, ¿falta documento?, motivo
 4. Validación dura ────────── cuenta del plan, signo coherente, cuadra, periodo abierto
          │
 5. ¿confianza ≥ umbral y nada sensible? ── sí → contabilizado (origen = regla/ia)
          │                                  no → cola de revisión con la propuesta
 6. Corrección humana ──────── nuevo ejemplo para la IA y, si se repite, nueva regla
```

1. **Emparejamiento (conciliación).** Es la capa que más resuelve: la mayoría de cobros y pagos corresponden a facturas que ya están en el sistema. Si se empareja, la contrapartida es el tercero (430, 400, 410), no el gasto. Casos especiales: pagos agrupados (una transferencia, varias facturas), cobros parciales, pasarelas de pago (liquidan el neto: la comisión va a 626).
2. **Reglas.** Deterministas, editables por el usuario y aprendidas de las correcciones. Ejemplos en [`data/reglas_clasificacion.csv`](../data/reglas_clasificacion.csv). El orden importa: gana la primera que encaja.
3. **IA.** Recibe el movimiento, el plan de cuentas de la empresa y movimientos parecidos ya validados (few-shot). Devuelve una salida estructurada (JSON con esquema fijo): cuenta, confianza, si hace falta documento y el motivo.
4. **Validación.** La cuenta debe existir en el plan; un cargo contra una cuenta de ingresos es sospechoso; el asiento resultante debe cuadrar.
5. **Umbral.** Empezar con **todo a revisión** las primeras semanas para medir el acierto real; luego automatizar por encima de un umbral (p. ej. 0,95) solo en cuentas sin riesgo.
6. **Aprendizaje.** Cada corrección se guarda como ejemplo. Si la misma contraparte se corrige igual 2-3 veces, el sistema propone una regla.

## 2. Lo que la IA nunca decide sola (siempre a revisión)

- Activar como inmovilizado o llevar a gasto (I+D, equipos por encima del umbral de la política).
- Operaciones con socios, administradores y partes vinculadas.
- Préstamos: separar principal e intereses (se hace con el cuadro de amortización, no adivinando).
- Ampliaciones de capital, subvenciones, préstamos ENISA.
- IVA especial: inversión del sujeto pasivo (SaaS y publicidad de proveedores extranjeros: autorrepercutir IVA, 303 y 349), IVA no deducible, prorrata.
- Marcar un gasto como no recurrente (afecta al EBITDA ajustado, es decir, al precio).

En las reglas se marcan con `revisar = si`.

## 3. Facturas: extracción y validación

Las facturas en PDF se leen con un modelo con visión que extrae emisor, NIF, número, fecha, vencimiento, base, tipo de
IVA, cuota, retención y total. Antes de aceptar nada:

- Aritmética: base × tipo = cuota (±0,01) y total = base + IVA − retención.
- NIF con dígito de control válido; proveedor existente o alta propuesta.
- Fecha dentro de un periodo abierto.
- Duplicado: mismo NIF + número de factura.

Con la factura electrónica obligatoria (2027-2028) cada vez más facturas llegarán **estructuradas (XML)**: entonces la
extracción es determinista y la IA solo hace falta para lo que llega en PDF o papel.

## 4. Cómo medirlo antes de confiar en él

- **Conjunto de evaluación:** 200-500 movimientos que ya contabilizó la gestoría. Se pasa el clasificador y se compara cuenta por cuenta.
- **Métricas:** % resuelto automáticamente, % de acierto en lo automático, correcciones por cuenta, días de cierre, coste por movimiento.
- **Objetivo para activar la contabilización automática:** ≥ 90 % resuelto y ≥ 98 % de acierto en lo resuelto.

## 5. Modelo, coste y privacidad

- La aplicación usa Claude (`claude-opus-5-5`) con esfuerzo bajo (clasificar no necesita razonamiento largo), salida estructurada con esquema JSON y *fallback* automático en el servidor si el modelo declina una petición.
- **Coste:** las reglas se llevan la mayoría del volumen; a la IA solo llega el resto. Para lotes nocturnos, la Batch API cuesta la mitad. El plan de cuentas va en caché de prompt. Probar un modelo más barato solo si la evaluación demuestra que mantiene el acierto.
- **Privacidad (RGPD):** los conceptos bancarios incluyen nombres de empleados y de personas físicas. Seudonimizar antes de enviar (`EMPLEADO 001`), no enviar IBAN completos y firmar el contrato de encargado de tratamiento con el proveedor del modelo.

## 6. Cómo está implementado

En [`bancos.py`](../bancos.py) y la pantalla **Bancos** de la aplicación:

1. **Importar** un extracto Norma 43 (se comprueban los totales que trae el propio fichero) o un CSV de la banca online. Reimportar el mismo fichero no duplica movimientos.
2. **Proponer:** conciliación con facturas abiertas (importe exacto, sentido, nombre del tercero, número de factura) → reglas de la tabla `regla` → IA opcional.
3. **Revisar y contabilizar:** cada movimiento muestra la cuenta propuesta, el origen, la confianza y si pide revisión. Se acepta o se cambia la cuenta; al aprobar se genera el asiento (o el cobro/pago de la factura). "Contabilizar las propuestas sin dudas" aprueba de golpe las que no piden revisión.
4. **Aprender:** al aprobar se puede crear una regla ("el concepto contiene…") que pasa por delante de las genéricas, y los movimientos ya contabilizados son los ejemplos que recibe la IA.

En la demo, de 13 movimientos de abril: 2 se concilian solos con sus facturas, 6 se resuelven por reglas, 2 piden revisión
(préstamo ENISA y traspaso entre cuentas) y 3 pagos con tarjeta quedan para la IA o para la persona.

La llamada a Claude está probada contra el SDK oficial con un servidor simulado (forma de la petición y lectura de la
respuesta) y con un cliente falso en las pruebas; para usarla de verdad hace falta una clave de API.
