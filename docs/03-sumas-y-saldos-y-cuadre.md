# 03 · Sumas y saldos: qué hace falta para tenerlo todo cuadrado

## 1. Qué es y por qué es la pieza central

El **balance de sumas y saldos** (BSS) lista cada cuenta con el total de su debe, el total de su haber y su saldo
(deudor o acreedor) a una fecha. Es la radiografía de la contabilidad: de él salen el balance y la PyG, y es lo
primero que pide una due diligence, *mensual y a máximo detalle (subcuenta)*, de los últimos 3-4 años.

```
Cuenta  Nombre                      Debe       Haber     Saldo D    Saldo A
572     Bancos c/c                59.050,00   6.091,00  52.959,00       0,00
4751    HP acreedora retenciones       0,00     525,00       0,00     525,00
...
TOTAL                             75.806,00  75.806,00  59.625,00  59.625,00
```

`python motor.py data/ejemplo` genera este informe con datos de ejemplo.

## 2. Las tres leyes que siempre se cumplen

| | Ley | Cómo se garantiza en el software |
|---|---|---|
| L1 | Cada asiento cuadra: Σ debe = Σ haber | Restricción en la base de datos: un asiento descuadrado no se puede guardar (en el prototipo, vista `v_asientos_descuadrados` que debe estar vacía) |
| L2 | Por tanto, Σ sumas debe = Σ sumas haber y Σ saldos deudores = Σ saldos acreedores | Consecuencia de L1; se comprueba igualmente (controles C02, C03) |
| L3 | Activo = Patrimonio neto + Pasivo, con el resultado (grupo 7 − grupo 6) dentro del PN | Consecuencia de L1 si cada cuenta está bien asignada a su masa (columna `masa` del plan) |

**La trampa:** un BSS cuadrado **no** significa una contabilidad correcta. Cuadra igual aunque un gasto esté en la cuenta
equivocada, una factura se haya contabilizado dos veces (por los dos lados), falte un asiento o esté en el mes que no toca.
Por eso "estar cuadrado" tiene tres niveles.

## 3. Los tres niveles de "cuadrado"

### Nivel 1 · Aritmético (automático y bloqueante)
L1, L2 y L3. Si falla, no se puede cerrar el mes.

### Nivel 2 · Coherencia interna (automático, genera avisos)
- **Signo según naturaleza** (C04): cada cuenta tiene un saldo "normal". Si sale al revés, casi siempre es un error:

  | Saldo anómalo | Qué suele ser | Dónde debería ir |
  |---|---|---|
  | 572 Bancos acreedor | Descubierto o póliza de crédito | 5201 Crédito dispuesto |
  | 430 Clientes acreedor | Cobro duplicado o anticipo de cliente | 438 Anticipos de clientes |
  | 400/410 Proveedores deudor | Pago duplicado o anticipo | 407 Anticipos a proveedores |
  | 477 IVA repercutido deudor | Factura rectificativa mal hecha | Revisar facturas |
  | 465 Remuneraciones deudor | Nómina pagada dos veces o anticipo | 460 Anticipos de remuneraciones |

- **Cuentas transitorias a cero al cierre** (C05): 555 Partidas pendientes de aplicación; revisar 4009/4109 (facturas pendientes de recibir).
- **Periodo cerrado intocable** (trigger `periodo_cerrado`), fechas dentro del ejercicio, 129 solo tras la regularización, grupos 6 y 7 a cero tras el cierre.
- **Duplicados:** mismo tercero + número de factura, o mismo importe + fecha + concepto.
- **Aritmética de cada factura:** base × tipo = cuota (±0,01); total = base + IVA − retención; numeración emitida sin huecos.
- **Lo recurrente está:** amortización del mes, nóminas del mes, cuotas de préstamo del mes, periodificaciones del mes. Si falta algo que todos los meses aparece, aviso (el patrón del análisis de recibos: "concepto recurrente ausente").
- **Anomalías:** gasto de un concepto muy por encima de su mediana histórica (×3, ×5...), proveedor nuevo con importe alto.

### Nivel 3 · Contra el mundo exterior (lo que demuestra que las cuentas son verdad)
Cada cuenta importante se cuadra contra una fuente que no es la propia contabilidad:

| Cuenta | Se cuadra contra | Frecuencia |
|---|---|---|
| 572 Bancos (cada cuenta) | Extracto bancario: Norma 43 (AEB cuaderno 43) o API PSD2 | Diaria / mensual |
| 570 Caja | Arqueo | Mensual |
| 430 Clientes (subcuentas) | Facturas emitidas pendientes de cobro (antigüedad) | Mensual |
| 400/410 Proveedores y acreedores | Facturas recibidas pendientes de pago (antigüedad) | Mensual |
| 472 / 477 | Libros registro de IVA y modelo 303 | Trimestral |
| 4750 | 303 presentado y pagado | Trimestral |
| 4751 | Modelos 111 y 115 (y 190/180 al año) | Trimestral / anual |
| 476 | Liquidación de la Seguridad Social (RLC/RNT) | Mensual |
| 640 / 642 / 465 | Resumen de nóminas del mes | Mensual |
| 2xx / 28x | Registro de inmovilizado y cuadro de amortización | Mensual |
| 3xx | Inventario valorado | Mensual / anual |
| 17x / 52x | Cuadros de amortización de préstamos y CIRBE; parte a menos de 12 meses reclasificada a corto | Mensual |
| 100 / 110 / 194 | Escrituras inscritas y libro registro de socios | En cada operación |
| 130-132 / 4708 | Resolución y justificación de la subvención | En cada hito |
| 480 / 485 | Calendario de devengo de contratos | Mensual |
| Terceros > 3.005,06 € | Modelo 347 | Anual |
| Σ 303 = 390 · Σ 111 = 190 · Σ 115 = 180 | Resúmenes anuales | Anual |
| 6300 / 4752 / 473 | Modelo 200 | Anual |

El catálogo completo con 30 controles está en [`data/controles_cuadre.csv`](../data/controles_cuadre.csv). En el prototipo,
los controles de nivel 3 se alimentan de [`saldos_externos.csv`](../data/ejemplo/saldos_externos.csv): una línea por fuente
con las cuentas (se pueden sumar: `472+477`), la fecha y el saldo que dice la fuente (deudor positivo, acreedor negativo).

## 4. Qué datos necesita el sistema para poder cuadrarlo todo

1. **Plan de cuentas enriquecido:** código, naturaleza (D/A/DA), masa de balance, epígrafe de gestión y, más adelante, epígrafe oficial del modelo de cuentas anuales.
2. **Subcuentas por tercero** con NIF, tipo y marca de vinculado.
3. **Documento por asiento** con sus campos fiscales: base, tipo y cuota de IVA, retención, total, **vencimiento**.
4. **Extractos bancarios completos e inmutables** con el saldo que informa el banco tras cada movimiento (así se detectan huecos en la importación).
5. **Fuentes externas importables:** modelos AEAT presentados, liquidaciones de Seguridad Social, resumen de nóminas, cuadros de préstamos, CIRBE, inventario.
6. **Periodos** con fecha y responsable de cierre.
7. **Dimensiones analíticas** y marca de no recurrente en cada apunte.
8. **Auditoría de cada asiento:** quién o qué lo creó (manual, regla, IA, conciliación, importado), con qué confianza y quién lo aprobó.

Todo esto ya está en el modelo de datos: [`db/schema.sql`](../db/schema.sql).

## 5. Cierre mensual: el checklist (objetivo ≤ 5 días hábiles)

1. Importar extractos y **conciliar bancos**: ningún movimiento sin asiento.
2. Facturas emitidas del mes registradas, numeración sin huecos.
3. Facturas recibidas registradas; **provisionar** las pendientes de recibir de gastos ya devengados (4009/4109).
4. Nóminas y Seguridad Social del mes.
5. Amortizaciones del mes.
6. Periodificaciones (480/485).
7. Revisar antigüedad de clientes y dotar deterioro según la política.
8. Préstamos: intereses devengados y reclasificación a corto plazo (170 → 520) de lo que vence en 12 meses.
9. IVA (trimestral): regularizar 472/477 contra 4750/4700.
10. **Vaciar la 555.**
11. Pasar los controles de los tres niveles: todo en OK o con el aviso explicado.
12. **Bloquear el mes** y generar BSS, balance, PyG contable y de gestión, tesorería, KPIs e informe con desviaciones vs presupuesto.

## 6. Lo que esto significa para el producto

- La partida doble y el bloqueo de periodos van **en la base de datos**, no en la interfaz.
- Importes en **céntimos enteros**, nunca coma flotante.
- Los controles son **código que corre solo** cada vez que entra un dato, no celdas de "check" que alguien tiene que mirar.
- Cada aviso enlaza al apunte y al documento que lo provoca, y puede convertirse en pregunta con responsable y estado.
- El valor diferencial no es "llevar la contabilidad" (eso ya existe), sino **demostrar en todo momento que está bien**.
