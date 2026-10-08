# 01 · Qué enseñan los Excels analizados

Se revisaron unos 45 libros Excel (todos los `.xlsx` de la carpeta de trabajo). La mayoría son bases de M&A
(compradores, PitchBook, contact logs) y no aportan nada a la contabilidad. Seis sí, y cada uno deja una lección
de diseño para el software.

> Confidencialidad: aquí no hay nombres de empresas, mandatos ni cifras de esos Excels. Solo su estructura y lo que se aprende de ella.

## 1. Dos listas de petición de información de due diligence financiera (IRL)

Son la mejor especificación posible del software: es exactamente lo que un comprador o un fondo le pedirá a la startup
el día que quiera invertir o comprar. Una tiene 40 peticiones y la otra 30, ordenadas por área, fase (I/II) y prioridad
(Crítica/Alta/Media), con estado (Pendiente/Parcial/Cerrado) y fecha de entrega.

| Área | Qué piden (resumen) | Prioridad |
|---|---|---|
| General | Cuentas anuales con auditoría y memoria · **balance de sumas y saldos mensual a máximo detalle** (10 dígitos) de 3-4 años · **libros diarios** | Crítica |
| General | Información de gestión (PyG con KPIs) **y su reconciliación con contabilidad** · partes vinculadas · organigrama societario y capital | Alta |
| Cuenta de resultados | Ventas en € y unidades por producto, familia, línea, cliente, canal y zona, **conciliadas con contabilidad** · compras por proveedor · margen por línea | Alta |
| Cuenta de resultados | Plantilla detallada (fijo/variable, SS, bonus, jornada) · nóminas mensuales · autónomos · arrendamientos · subvenciones · **EBITDA ajustado con detalle de ajustes** | Alta / Media |
| Balance | Inmovilizado elemento a elemento cuadrado con contabilidad · **gastos activados y personal capitalizado con soporte** · capex mantenimiento vs expansión y comprometido | Media |
| Balance | Existencias valoradas y obsolescencia · anticipos · **antigüedad de clientes y proveedores con fecha de factura y vencimiento** · política de provisión de insolvencias · factoring, leasing, renting | Alta |
| Balance | **Conciliaciones bancarias de todas las cuentas** · deuda financiera (importe inicial, vencimiento, tipo, límite) · CIRBE · avales fuera de balance · litigios · movimientos de fondos propios · planes de opciones | Alta |

**Lección:** las tres peticiones críticas (cuentas anuales, sumas y saldos mensual, diarios) salen directamente del libro
mayor. Si el libro está limpio desde el día 1, la Fase I de una due diligence se responde en horas, no en semanas.
Todo lo demás exige que ciertos datos se capturen *en el momento*: dimensiones analíticas en cada venta, vencimiento en
cada factura, marca de vinculado en cada tercero, marca de no recurrente en cada gasto, horas por proyecto si se activa I+D.
Eso es lo que convierte el checklist en el diseño del software → [`data/checklist_dd.csv`](../data/checklist_dd.csv).

## 2. Dos plantillas de estados financieros (desde SABI y desde cuentas oficiales)

Pasan del balance y la PyG oficiales del PGC a una **vista de gestión** en miles de euros:

- **PyG de gestión:** Ingresos → CMV → Margen bruto → Personal → Otros gastos de explotación → Otros ingresos y gastos → **EBITDA** → Amortizaciones → Provisiones → EBIT → Financiero → Extraordinario → Impuestos → Beneficio neto, con % sobre ventas y TACC.
- **Balance resumido**, **Deuda financiera neta** y DFN/EBITDA, **Capex** y % sobre ventas, **capital circulante** y % sobre ventas.
- **Rentabilidad:** ROA, ROE, ROIC (con un tipo impositivo del 25 %).
- **Cash flow:** EBITDA → impuestos → variación de circulante → capex → flujo de caja libre → financiación → dividendos → variación de tesorería, cuadrado contra la tesorería del balance.
- **Días:** PMI, PMC (con las ventas brutas de IVA al 21 %), PMP y ciclo de conversión de caja.
- **Checks:** "Check balance" y "Check original" que deben dar 0.

**Lección 1:** una de las dos plantillas tenía los checks distintos de 0, divisiones por cero (`#DIV/0!`) y el resultado
del ejercicio mal enlazado, y nadie lo había visto. Un check que hay que mirar no es un control: **los controles tienen
que ser automáticos y bloquear el cierre** (ver [03](03-sumas-y-saldos-y-cuadre.md)).

**Lección 2:** el paso PGC → vista de gestión se rehace a mano en cada Excel. En el software va **fijado en el plan de
cuentas** (columna `epigrafe_gestion` de [`plan_cuentas_pgc.csv`](../data/plan_cuentas_pgc.csv)), así la PyG de gestión
y la contable salen del mismo libro y no pueden no cuadrar. Los KPIs de estas plantillas están en [`data/kpis.csv`](../data/kpis.csv).

## 3. Análisis de recibos mensuales (15 meses de una comunidad)

Es el mejor ejemplo de **cómo se controla** un flujo de documentos:

- **Hoja de controles con estado PASS/FAIL por documento:** suma del detalle = total impreso; fondo de reserva = 10 % del gasto; cuota = alícuota × gasto; saldo arrastrado = saldo anterior. Un "MODEL STATUS" global.
- **Alertas analíticas, no acusaciones:** importe frente a la mediana histórica del concepto (×4, ×10, ×20), concepto recurrente que falta este mes, concepto que aparece por primera vez.
- **Cola de preguntas** con prioridad, evidencia, importe, documento fuente y estado (Pendiente/Respondida).
- Trazabilidad de cada cifra a su PDF de origen.

**Lección:** este es exactamente el patrón del módulo de cierre: *controles de cuadre + alertas de anomalías + cola de
preguntas con evidencia y enlace al documento*.

## 4. Análisis de ventas diarias (3 locales, ~170.000 tickets)

Un pipeline de datos bien hecho en Excel: hoja `LEEME` con qué se limpió y las limitaciones, **datos originales intocables**,
tabla maestra limpia, calendario, dashboard, tendencias con medias móviles, día de la semana, outliers (Z-score e IQR),
previsión 30/60/90 días con escenarios, maduración de locales nuevos e insights.

**Lección 1:** datos crudos inmutables y limpieza reproducible: igual que los extractos bancarios en el software.
**Lección 2:** su propia limitación lo dice: *"solo hay ventas, no márgenes, costes ni personal"*. Si los costes no se
registran con las **mismas dimensiones** que las ventas (local, producto, canal), nunca se puede pasar de "cuánto se
vende" a "cuánto se gana". Por eso cada apunte del software lleva dimensiones analíticas desde el primer día.

## 5. Cuestionario de madurez de gestión (197 preguntas, 22 bloques)

Los bloques *Finanzas*, *Working Capital* y *Costing* definen qué es una función financiera de nivel 5. Las preguntas se
convierten en objetivos del software:

| Pregunta del cuestionario | Objetivo del software |
|---|---|
| ¿Previsión de tesorería a 30/60/90 días con alertas automáticas? | Previsión de tesorería y runway con umbrales |
| ¿El cierre mensual es solo contable o también analítico? | Cierre contable y analítico a la vez (mismo libro) |
| ¿Sobre qué ejes se analizan ingresos y márgenes? ¿Qué costes se imputan? | Dimensiones analíticas en ingresos y costes |
| ¿Con qué frecuencia se cierra y cuántos días tras fin de mes tarda el informe? | Cierre mensual en ≤ 5 días hábiles |
| ¿Se compara con presupuesto y se toman medidas? | Presupuesto vs real con comentario de desviaciones |
| ¿Liquidez corriente? ¿Qué % del EBITDA se convierte en caja? | KPIs automáticos de liquidez y conversión de caja |
| ¿Con quién se comparte el informe mensual? | Informe mensual para socios/consejo/inversores |

## 6. Descartados

Bases de compradores y fondos, exportaciones de PitchBook, contact logs, fichas de compradores y bases de operaciones
de prensa: son de M&A, no de contabilidad. Lo único reutilizable es el formato SABI (balance y PyG en modelo abreviado),
que confirma qué epígrafes oficiales debe poder generar el software.
