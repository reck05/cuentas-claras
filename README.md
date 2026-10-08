# Cuentas Claras

Contabilidad y finanzas para startups españolas, pensada para que las cuentas estén **cuadradas y listas para una
due diligence desde el primer día**.

Cada euro entra una sola vez, con su documento, su cuenta del PGC y sus etiquetas analíticas. El sistema comprueba
solo que todo cuadra (internamente y contra el banco, Hacienda y la Seguridad Social), no deja cerrar un mes que no
cuadra y genera en un clic lo que pedirá un inversor o un comprador.

Funciona en local, con Python y sin instalar nada más.

## Qué hace

- **Libro mayor** con el plan de cuentas PGC PYMES: asientos que solo se guardan si cuadran, libro inmutable (se anula con el asiento inverso), importación del diario de la gestoría y dimensiones analíticas (línea de negocio, producto, canal, proyecto, no recurrente).
- **Bancos:** importa extractos **Norma 43** (comprobando los totales del propio fichero) o CSV de la banca online y propone la contrapartida de cada movimiento en cascada: **conciliación con facturas → reglas → IA (Claude, opcional)**. Una persona revisa y aprueba; el sistema aprende reglas nuevas.
- **Terceros y facturas:** NIF validado, subcuenta por tercero, marca de parte vinculada; registro de facturas emitidas y recibidas con su asiento (IVA, retenciones, inversión del sujeto pasivo), cobros y pagos, antigüedad de saldos.
- **Inmovilizado** con amortización mensual automática.
- **Controles de cuadre en tres niveles** (aritmético, coherencia interna, contra fuentes externas) y **cierre mensual** que bloquea el mes solo si ningún control falla.
- **Informes:** panel con tesorería, burn, runway, EBITDA y EBITDA ajustado, deuda financiera neta y periodos de cobro y pago; sumas y saldos, mayor, PyG mensual de gestión y balance.
- **Fiscal:** borradores de los modelos 303, 111 y 347, regularización del IVA y calendario de vencimientos.
- **Data room de due diligence:** un zip con diario, sumas y saldos mensual, PyG, balance, antigüedad de saldos, partes vinculadas, EBITDA ajustado, inmovilizado y controles.

## Empezar en un minuto

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/reck05/cuentas-claras.git
```

```bash
cd cuentas-claras
```

```bash
python app.py demo
```

```bash
python app.py --db demo.db servir
```

Abre http://127.0.0.1:8000. La demo es una startup ficticia con el primer trimestre importado de la gestoría y cerrado,
y abril a medias: facturas registradas, un extracto Norma 43 importado y 5 movimientos esperando revisión en **Bancos**.
Mientras estén pendientes, el control del extracto falla y abril no se puede cerrar; al contabilizarlos, cuadra.

## Usarlo con tu empresa

```bash
python app.py servir
```

Crea `cuentas_claras.db` (un fichero por empresa; elige otro con `--db`). Después:

1. **Panel:** nombre de la empresa y si es empresa emergente (Ley 28/2022).
2. **Terceros:** da de alta cada cuenta bancaria (tipo *banco*), clientes, proveedores, acreedores y socios.
3. **Diario:** importa el diario de la gestoría (CSV) o contabiliza a mano.
4. **Bancos:** importa el extracto, pulsa *Proponer*, revisa y contabiliza.
5. **Facturas:** registra las que emites y las que recibes.
6. **Controles:** registra los saldos de las fuentes externas (modelos 111 y 303, Seguridad Social...).
7. **Cierre:** cierra el mes. Si algo falla, te dice qué.
8. **Panel → Data room:** descarga el paquete para inversores.

La base de datos es un fichero: haz copia de seguridad de él. **No expongas el servidor a internet**: no tiene usuarios
ni contraseñas, está pensado para usarse en tu ordenador.

## IA (opcional)

La IA solo clasifica los movimientos bancarios que no cubren las facturas ni las reglas, y **nunca contabiliza sola**:
propone cuenta, confianza y motivo, y una persona aprueba.

```bash
pip install -r requirements.txt
```

Configura `ANTHROPIC_API_KEY` y marca *Usar IA* en **Bancos** (o usa `python app.py proponer --ia`). Se envían el plan de
cuentas, los conceptos de los movimientos pendientes y ejemplos ya contabilizados, con los IBAN y números largos
ocultos. Diseño completo en [`docs/04-autoclasificador-ia.md`](docs/04-autoclasificador-ia.md).

## Línea de comandos

| Orden | Qué hace |
|---|---|
| `python app.py demo` | Crea `demo.db` con la startup de ejemplo |
| `python app.py servir [--puerto 8000]` | Abre la aplicación web |
| `python app.py informe [--hasta AAAA-MM-DD]` | Sumas y saldos, PyG y controles en la terminal (sale con código 1 si algo falla) |
| `python app.py importar-diario fichero.csv` | Importa el diario de la gestoría |
| `python app.py importar-banco fichero [--cuenta 57200001]` | Importa un extracto Norma 43 o CSV |
| `python app.py proponer [--ia]` | Propone contrapartidas a los movimientos pendientes |
| `python app.py cerrar AAAA-MM` | Cierra el mes si ningún control falla |
| `python app.py dataroom salida.zip` | Exporta el data room |

Todas aceptan `--db` antes de la orden: `python app.py --db demo.db informe`.

## Límites de esta versión

- **Registra** facturas, no las **emite**: emite con tu programa de facturación (que debe cumplir Verifactu y la factura electrónica cuando sean obligatorias).
- Cobros y pagos completos (no parciales) y un tipo de IVA por factura.
- Los modelos fiscales son **borradores** para revisar con tu asesor, no presentaciones.
- Un extracto Norma 43 por cuenta bancaria.
- Un usuario y en local. Siguientes pasos en [`docs/05-arquitectura-y-roadmap.md`](docs/05-arquitectura-y-roadmap.md).

No sustituye a un asesor contable o fiscal.

## Estructura

| Archivo | Qué es |
|---|---|
| [`app.py`](app.py) | Aplicación web local y línea de comandos |
| [`motor.py`](motor.py) | Núcleo contable: libro mayor, terceros, facturas, inmovilizado, controles, cierre, informes, fiscal, data room |
| [`bancos.py`](bancos.py) | Extractos Norma 43 y CSV, conciliación, reglas, IA y aprobación |
| [`db/schema.sql`](db/schema.sql) | Modelo de datos (SQLite) |
| [`data/plan_cuentas_pgc.csv`](data/plan_cuentas_pgc.csv) | Plan de cuentas base con naturaleza, masa de balance y epígrafe de gestión |
| [`data/reglas_clasificacion.csv`](data/reglas_clasificacion.csv) | Reglas iniciales del clasificador bancario |
| [`data/controles_cuadre.csv`](data/controles_cuadre.csv) | Catálogo de controles y cuáles están implementados |
| [`data/checklist_dd.csv`](data/checklist_dd.csv) | Lo que pedirá una due diligence y qué módulo lo produce |
| [`data/kpis.csv`](data/kpis.csv) | KPIs con su fórmula |
| [`data/ejemplo/`](data/ejemplo) | Datos de la demo: diario, saldos externos, extracto Norma 43 y CSV de ejemplo |
| [`test_cuentas_claras.py`](test_cuentas_claras.py) | Pruebas: `python test_cuentas_claras.py` |

## Documentación

1. [Lo que enseña la práctica](docs/01-lecciones-de-la-practica.md): listas de due diligence, modelos financieros y controles.
2. [Qué necesita una startup](docs/02-que-necesita-una-startup.md): obligaciones legales y fiscales, calendario 2027-2028 y buenas prácticas.
3. [Sumas y saldos y cómo tenerlo todo cuadrado](docs/03-sumas-y-saldos-y-cuadre.md).
4. [Autoclasificador con IA](docs/04-autoclasificador-ia.md).
5. [Arquitectura, estado y siguientes pasos](docs/05-arquitectura-y-roadmap.md).

## Principios

1. **Partida doble garantizada:** un asiento descuadrado no se guarda.
2. **Importes en céntimos enteros**, nunca coma flotante.
3. **El libro no se borra:** se anula con el asiento inverso y queda rastro.
4. **Etiquetas analíticas desde el primer apunte.**
5. **La IA propone, el libro valida, la persona aprueba.**
6. **Mes cerrado = mes bloqueado**, y solo se cierra si cuadra.
7. **Lo que pedirá una due diligence dentro de cinco años se registra hoy.**
