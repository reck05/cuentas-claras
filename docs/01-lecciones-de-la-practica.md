# 01 · Lo que enseña la práctica

Este proyecto parte de revisar el tipo de hojas de cálculo que se usan de verdad en la vida financiera de una empresa:
listas de petición de información de due diligence, modelos de estados financieros, controles de documentos y análisis
de ventas. Cada tipo deja una lección de diseño para el software. Aquí solo hay estructura y conclusiones: ningún
dato de ninguna empresa.

## 1. Listas de petición de información de due diligence financiera (IRL)

Son la mejor especificación posible del software: es exactamente lo que un comprador o un fondo pedirá a la startup el
día que quiera invertir o comprarla. Suelen ordenarse por área, fase (I/II) y prioridad (crítica/alta/media), con un
estado (pendiente/parcial/cerrado) y una fecha de entrega.

| Área | Qué piden (resumen) | Prioridad típica |
|---|---|---|
| General | Cuentas anuales con auditoría y memoria · **balance de sumas y saldos mensual a máximo detalle** de 3-4 años · **libros diarios** | Crítica |
| General | Información de gestión (PyG con KPIs) **y su reconciliación con contabilidad** · partes vinculadas · organigrama societario y capital | Alta |
| Cuenta de resultados | Ventas en € y unidades por producto, familia, línea, cliente, canal y zona, **conciliadas con contabilidad** · compras por proveedor · margen por línea | Alta |
| Cuenta de resultados | Plantilla detallada (fijo/variable, SS, bonus, jornada) · nóminas mensuales · autónomos · arrendamientos · subvenciones · **EBITDA ajustado con el detalle de los ajustes** | Alta / Media |
| Balance | Inmovilizado elemento a elemento cuadrado con contabilidad · **gastos activados y personal capitalizado con soporte** · capex de mantenimiento frente a expansión y capex comprometido | Media |
| Balance | Existencias valoradas y obsolescencia · anticipos · **antigüedad de clientes y proveedores con fecha de factura y vencimiento** · política de provisión de insolvencias · factoring, leasing, renting | Alta |
| Balance | **Conciliaciones bancarias de todas las cuentas** · deuda financiera (importe inicial, vencimiento, tipo, límite) · CIRBE · avales fuera de balance · litigios · movimientos de fondos propios · planes de opciones | Alta |

**Lección:** las tres peticiones críticas (cuentas anuales, sumas y saldos mensual, diarios) salen directamente del libro
mayor. Si el libro está limpio desde el día 1, la fase I de una due diligence se responde en horas, no en semanas. Todo
lo demás exige capturar ciertos datos *en el momento*: dimensiones analíticas en cada venta, vencimiento en cada factura,
marca de vinculado en cada tercero, marca de no recurrente en cada gasto, horas por proyecto si se activa I+D. Eso
convierte el checklist en el diseño del software → [`data/checklist_dd.csv`](../data/checklist_dd.csv).

## 2. Modelos de estados financieros

Los analistas pasan el balance y la PyG oficiales del PGC a una **vista de gestión**:

- **PyG de gestión:** Ingresos → aprovisionamientos → margen bruto → personal → otros gastos de explotación → **EBITDA** → amortizaciones → EBIT → financiero → impuestos → beneficio neto, con % sobre ventas y TACC.
- **Balance resumido**, **deuda financiera neta** y DFN/EBITDA, **capex** y % sobre ventas, **capital circulante** y % sobre ventas.
- **Rentabilidad:** ROA, ROE, ROIC.
- **Flujo de caja:** EBITDA → impuestos → variación de circulante → capex → flujo de caja libre → financiación → variación de tesorería, cuadrado contra la tesorería del balance.
- **Días:** periodo medio de inventario, de cobro (con las ventas brutas de IVA) y de pago, y ciclo de conversión de caja.
- **Checks** que deben dar cero.

**Lección 1:** en hojas de cálculo es habitual encontrar checks que no dan cero, divisiones por cero o un resultado mal
enlazado sin que nadie lo note. Un check que alguien tiene que mirar no es un control: **los controles tienen que ser
automáticos y bloquear el cierre** (ver [03](03-sumas-y-saldos-y-cuadre.md)).

**Lección 2:** el paso del PGC a la vista de gestión se rehace a mano en cada hoja. En el software va **fijado en el plan
de cuentas** (columna `epigrafe_gestion` de [`plan_cuentas_pgc.csv`](../data/plan_cuentas_pgc.csv)), así la PyG de gestión y
la contable salen del mismo libro y no pueden no cuadrar. Los KPIs están en [`data/kpis.csv`](../data/kpis.csv).

## 3. Control de documentos recurrentes

El mejor patrón para controlar un flujo de documentos (recibos, facturas, liquidaciones) tiene tres piezas:

- **Controles con estado OK/FALLA por documento:** la suma del detalle es el total impreso; los porcentajes pactados se cumplen; el saldo arrastrado es el saldo anterior. Y un estado global del modelo.
- **Alertas analíticas, no acusaciones:** importe frente a la mediana histórica del concepto, concepto recurrente que falta este mes, concepto que aparece por primera vez.
- **Cola de preguntas** con prioridad, evidencia, importe, documento fuente y estado, y trazabilidad de cada cifra a su documento.

**Lección:** es exactamente el patrón del módulo de cierre: *controles de cuadre + alertas + cola de revisión con evidencia
y enlace al documento*.

## 4. Análisis de ventas a partir de datos de caja

Un buen análisis de ventas por establecimiento sigue un pipeline: datos originales intocables → tabla maestra limpia →
calendario → panel → tendencias → día de la semana → días atípicos → previsión a 30/60/90 días → conclusiones, con una
hoja que explica qué se limpió y qué limitaciones tiene.

**Lección 1:** datos crudos inmutables y limpieza reproducible, igual que los extractos bancarios en el software.
**Lección 2:** la limitación más frecuente es *"solo hay ventas: ni márgenes, ni costes, ni personal"*. Si los costes no se
registran con las **mismas dimensiones** que las ventas (establecimiento, producto, canal), nunca se pasa de "cuánto se
vende" a "cuánto se gana". Por eso cada apunte lleva dimensiones analíticas desde el primer día.

## 5. Diagnóstico de madurez de la función financiera

Los diagnósticos de madurez de gestión preguntan, en su bloque de finanzas, por lo que hace una función financiera de
nivel alto. Cada pregunta se convierte en un objetivo del software:

| Pregunta típica | Objetivo del software |
|---|---|
| ¿Previsión de tesorería a 30/60/90 días con alertas? | Tesorería, burn y runway en el panel |
| ¿El cierre es solo contable o también analítico? | Cierre contable y analítico a la vez (mismo libro) |
| ¿Sobre qué ejes se analizan ingresos y márgenes? | Dimensiones analíticas en ingresos y costes |
| ¿Cuántos días tras fin de mes tarda el informe? | Cierre mensual en ≤ 5 días hábiles |
| ¿Se compara con presupuesto y se actúa? | Presupuesto vs real (pendiente, ver [05](05-arquitectura-y-roadmap.md)) |
| ¿Liquidez corriente? ¿Qué parte del EBITDA se convierte en caja? | KPIs automáticos |
| ¿Con quién se comparte el informe mensual? | Data room e informes exportables |
