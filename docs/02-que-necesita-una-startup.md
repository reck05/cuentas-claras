# 02 · Qué necesita una startup para tener las cuentas perfectas desde el principio

Pensado para una **sociedad limitada española** (lo más habitual en startups). Las cifras y fechas legales llevan su
fuente; cualquier cosa que dependa del caso concreto está marcada como *a validar con el asesor*.

## 1. Obligaciones que el software tiene que cubrir

### Contables y mercantiles
- **Libros obligatorios:** Libro Diario y Libro de Inventarios y Cuentas Anuales (Código de Comercio, arts. 25-28), legalizados telemáticamente en el Registro Mercantil en los 4 meses siguientes al cierre. Además, libro de actas y libro registro de socios.
- **Conservación:** libros, correspondencia y justificantes 6 años (CCom art. 30).
- **Plan contable:** PGC de PYMES (RD 1515/2007) si durante dos ejercicios seguidos se cumplen 2 de 3 límites: activo ≤ 4 M€, cifra de negocios ≤ 8 M€, plantilla media ≤ 50.
- **Cuentas anuales:** formular en 3 meses desde el cierre (LSC art. 253), aprobar en junta en 6 meses (art. 164) y depositar en el Registro Mercantil en el mes siguiente a la aprobación (art. 279).
- **Auditoría obligatoria** si durante dos ejercicios seguidos se superan 2 de 3: activo 2,85 M€, cifra de negocios 5,7 M€, 50 empleados (LSC art. 263). Una ronda con fondo suele exigirla antes por contrato.

### Fiscales (calendario tipo de una SL)

| Modelo | Qué | Cuándo |
|---|---|---|
| 303 | IVA | Trimestral: 1-20 de abril, julio y octubre; 4T hasta el 30 de enero |
| 111 / 115 | Retenciones de trabajo y profesionales / de alquileres | Trimestral, mismos plazos |
| 349 | Operaciones intracomunitarias (p. ej. SaaS de la UE) | Mensual o trimestral según volumen |
| 202 | Pagos fraccionados del impuesto sobre sociedades | 1-20 de abril, octubre y diciembre |
| 390 / 190 / 180 | Resúmenes anuales de IVA y retenciones | Enero |
| 347 | Operaciones con terceros > 3.005,06 € al año | Febrero |
| 200 | Impuesto sobre sociedades | 25 días naturales tras los 6 meses del cierre (julio si cierra en diciembre) |
| RLC/RNT | Seguridad Social | Mensual, mes siguiente |

- **SII** (libros de IVA en tiempo real): obligatorio si el volumen de operaciones supera 6.010.121,04 €; voluntario por debajo.

### Facturación: lo que cambia en 2027-2028
- **Factura electrónica B2B obligatoria** (Ley 18/2022 "Crea y Crece", RD 238/2026 y Orden HAC/1028/2026, BOE 5-oct-2026): desde el **6-oct-2027** para empresas que facturan más de 8 M€ y desde el **6-oct-2028** para el resto. Incluye informar del **estado de la factura** (aceptación, rechazo y pago). Para las de hasta 8 M€ el reporte de estados es voluntario hasta el 6-oct-2029.
- **Verifactu** (RD 1007/2023, sistemas de facturación verificables): tras el RDL 15/2025 las fechas eran 1-ene-2027 (sociedades) y 1-jul-2027 (resto). En octubre de 2026 la AEAT **anunció** un nuevo retraso al **6-oct-2028** para alinearlo con la factura electrónica. *A fecha de este documento es un anuncio: confirmar con la norma publicada.*
- **Implicación de diseño:** las facturas emitidas se generan desde el primer día en formato estructurado (estándar europeo EN 16931), con numeración correlativa sin huecos, y cada factura guarda su estado de cobro. Eso, además, es lo que necesita la antigüedad de saldos.

### Ventajas de la Ley de startups (Ley 28/2022) que hay que poder aplicar
Requieren la certificación de empresa emergente (ENISA). *Requisitos y plazos a validar con el asesor.*
- Impuesto sobre sociedades al **15 %** en el primer ejercicio con base imponible positiva y los tres siguientes (frente al 25 % general).
- Aplazamiento sin garantías de la cuota del impuesto de los primeros ejercicios con base positiva y exoneración de pagos fraccionados en los términos de la ley.
- Stock options: exención ampliada en el IRPF del empleado y diferimiento de la tributación.
- Inversores: deducción en IRPF del 50 % por inversión en empresas de nueva creación (base máxima 100.000 €).

Consecuencia para el software: el módulo fiscal tiene que saber si la sociedad es empresa emergente y desde cuándo.

## 2. Cómo se lleva bien la contabilidad (las prácticas que el software impone)

1. **Un solo libro.** La contabilidad de gestión no es otro Excel: es una vista del mismo libro mayor con etiquetas. La due diligence pide "información de gestión y su reconciliación con contabilidad"; si salen de la misma fuente, la reconciliación es automática.
2. **Subcuentas por tercero desde el día 1** (`4300xxxx` clientes, `4000xxxx` proveedores, `4100xxxx` acreedores) con NIF y marca de **vinculado**. Sin esto no hay antigüedad de saldos ni partes vinculadas.
3. **Dimensiones analíticas obligatorias** en ingresos y costes directos: línea de negocio, producto, canal, centro de coste, proyecto. Y la marca **no recurrente** para el EBITDA ajustado.
4. **Ningún asiento sin documento** (factura, nómina, extracto, contrato, escritura), guardado y con huella (hash) para detectar duplicados.
5. **Devengo, no caja.** Una suscripción anual cobrada por adelantado va a ingresos anticipados (485) y se reconoce cada mes; un seguro anual pagado, a gastos anticipados (480).
6. **Cierre mensual en ≤ 5 días hábiles** con checklist y controles ([03](03-sumas-y-saldos-y-cuadre.md)). Mes cerrado = mes bloqueado.
7. **Nada personal en la empresa.** Si pasa, va a la cuenta con socios (551) y se regulariza.
8. **Capital y cap table impecables.** Cada ampliación con su escritura: el dinero entra en 194 (capital pendiente de inscripción) hasta la inscripción y luego pasa a 100/110. Libro registro de socios y planes de opciones al día.
9. **Subvenciones y préstamos ENISA con expediente:** resolución, justificación, calendario y criterio de imputación (130-132, 740, 746, 171).
10. **I+D activable con soporte desde el primer día:** partes horarias por proyecto. Sin ellas no se puede activar (201/730), ni defender en una due diligence, ni aplicar la deducción por I+D+i.
11. **Registro de inmovilizado** elemento a elemento con amortización automática, y **registro de contratos** (alquileres, renting, leasing, préstamos, avales).
12. **Presupuesto anual + previsión continua.** Real vs presupuesto cada mes con comentario de las desviaciones, y previsión de tesorería a 13 semanas y 30/60/90 días con runway.

## 3. De la necesidad al módulo

| Necesidad | Módulo ([05](05-arquitectura-y-roadmap.md)) |
|---|---|
| Libro diario, mayor, sumas y saldos, cuentas anuales | M1 Libro mayor · M8 Reporting |
| Clientes, proveedores, facturas emitidas y recibidas, contratos | M2 Terceros y documentos |
| Extractos, conciliación, autoclasificación | M3 Bancos ([04](04-autoclasificador-ia.md)) |
| 303, 111, 115, 349, 347, 390, 190, 180, 200, calendario | M4 Fiscal |
| Nóminas y Seguridad Social | M5 Nóminas (integración con la gestoría o el software de nóminas) |
| Inmovilizado, préstamos, subvenciones, capital | M6 Activos y financiación |
| Checklist de cierre, controles, bloqueo | M7 Cierre y controles |
| PyG de gestión, KPIs, data room de due diligence | M8 Reporting |
| Presupuesto, previsión de tesorería, runway | M9 Planificación |
