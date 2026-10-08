# Cuentas Claras

Base para construir un software de contabilidad y finanzas para startups españolas que deje las cuentas
**cuadradas y listas para una due diligence desde el primer día**.

La idea en una línea: cada euro entra una sola vez, con su documento, su cuenta del PGC y sus etiquetas analíticas;
el sistema se cuadra solo y avisa de lo que no cuadra.

## Qué hay aquí

| Archivo | Qué es |
|---|---|
| [`docs/01-analisis-excels.md`](docs/01-analisis-excels.md) | Qué enseñan los Excels analizados (listas de due diligence, modelos de EEFF, controles, pipelines de datos) |
| [`docs/02-que-necesita-una-startup.md`](docs/02-que-necesita-una-startup.md) | Obligaciones legales y fiscales, calendario 2027-2028 (factura electrónica, Verifactu), Ley de startups y buenas prácticas |
| [`docs/03-sumas-y-saldos-y-cuadre.md`](docs/03-sumas-y-saldos-y-cuadre.md) | **Sumas y saldos y cómo tenerlo todo cuadrado:** los tres niveles de control y el cierre mensual |
| [`docs/04-autoclasificador-ia.md`](docs/04-autoclasificador-ia.md) | **Autoclasificador con IA:** cascada emparejamiento → reglas → IA → validación → revisión |
| [`docs/05-arquitectura-y-roadmap.md`](docs/05-arquitectura-y-roadmap.md) | Módulos, modelo de datos, stack y plan por fases |
| [`data/plan_cuentas_pgc.csv`](data/plan_cuentas_pgc.csv) | Plan de cuentas base (PGC PYMES) con naturaleza, masa de balance y epígrafe de gestión |
| [`data/controles_cuadre.csv`](data/controles_cuadre.csv) | Catálogo de 30 controles de cuadre |
| [`data/checklist_dd.csv`](data/checklist_dd.csv) | Lo que pedirá una due diligence y qué módulo lo produce |
| [`data/kpis.csv`](data/kpis.csv) | KPIs con su fórmula |
| [`data/reglas_clasificacion.csv`](data/reglas_clasificacion.csv) | Reglas iniciales del clasificador de movimientos bancarios |
| [`data/ejemplo/`](data/ejemplo) | Startup ficticia: diario del primer trimestre, saldos externos y extracto bancario |
| [`db/schema.sql`](db/schema.sql) | Modelo de datos del libro mayor |
| [`motor.py`](motor.py) | Sumas y saldos, PyG de gestión y controles de cuadre |
| [`clasificador.py`](clasificador.py) | Autoclasificador de movimientos bancarios (reglas + Claude opcional) |
| [`test_cuentas_claras.py`](test_cuentas_claras.py) | Comprobación de que todo lo anterior funciona |

## Probarlo

Requiere Python 3.10 o superior. Sin dependencias, salvo la IA (opcional).

```bash
python motor.py data/ejemplo
```

```bash
python clasificador.py data/ejemplo/banco.csv
```

```bash
python test_cuentas_claras.py
```

Para clasificar con IA lo que no cubren las reglas: `pip install -r requirements.txt`, configura `ANTHROPIC_API_KEY`
y añade `--ia`.

## Trabajar desde otro ordenador

```bash
git clone https://github.com/reck05/cuentas-claras.git
```

Después de cambiar algo: `git add -A`, `git commit -m "qué cambié"` y `git push`. En el otro ordenador, `git pull`
antes de empezar.

## Principios

1. **Partida doble en la base de datos:** un asiento descuadrado no puede existir.
2. **Importes en céntimos enteros**, nunca coma flotante.
3. **Ningún asiento sin documento** soporte.
4. **Etiquetas analíticas desde el primer apunte** (línea, producto, canal, proyecto, no recurrente).
5. **La IA propone, el libro mayor valida, la persona aprueba.**
6. **Mes cerrado = mes bloqueado.**
7. **Lo que pedirá una due diligence dentro de cinco años se registra hoy.**
