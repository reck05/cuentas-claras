"""Comprobación mínima: python test_cuentas_claras.py (sin dependencias)."""
import csv
import sqlite3
from pathlib import Path

import clasificador
import motor

RAIZ = Path(__file__).parent
EJEMPLO = RAIZ / "data" / "ejemplo"

# Todos los CSV de data/ se leen y no tienen columnas de más (comas sin comillas).
for ruta in RAIZ.glob("data/**/*.csv"):
    with open(ruta, newline="", encoding="utf-8") as f:
        assert all(None not in fila for fila in csv.DictReader(f)), ruta

assert motor.centimos("0.1") + motor.centimos("0.2") == motor.centimos("0.3")
assert motor.euros(-123456) == "-1.234,56"

# El ejemplo cuadra: niveles 1 y 3 en OK, y la partida pendiente (555) sale como AVISO.
con = motor.abrir(EJEMPLO)
estado = {(cid, nombre): e for cid, nombre, e, _ in motor.controles(con, EJEMPLO)}
assert all(e == "OK" for (cid, _), e in estado.items() if cid in ("C01", "C02", "C03", "C04", "C06")), estado
assert [e for (cid, _), e in estado.items() if cid == "C05"] == ["AVISO"]
assert motor.saldo(con, "572") == 5295900

pyg = dict(motor.pyg_gestion(con))
assert pyg["EBITDA"] == 12500 and pyg["EBITDA ajustado (sin no recurrentes)"] == 52500 and pyg["Resultado neto"] == 7500

# Activo = PN + Pasivo (con el resultado dentro del PN).
activo = sum(motor.saldo(con, c) for c in ("217", "281", "472", "555", "572"))
pn_pasivo = -sum(motor.saldo(con, c) for c in ("100", "110", "4751", "477")) + pyg["Resultado neto"]
assert activo == pn_pasivo, (activo, pn_pasivo)

# Un asiento descuadrado se detecta.
con.execute("INSERT INTO asiento (id, fecha, concepto) VALUES (99, '2026-03-31', 'mal')")
con.execute("INSERT INTO apunte (asiento_id, cuenta, debe) VALUES (99, '629', 1000)")
assert [e for cid, _, e, _ in motor.controles(con, EJEMPLO) if cid == "C01"] == ["FALLA"]

# Un mes cerrado no admite apuntes.
con.execute("INSERT INTO periodo (mes, cerrado_en) VALUES ('2026-01', '2026-02-05')")
try:
    con.execute("INSERT INTO apunte (asiento_id, cuenta, debe) VALUES (1, '629', 1)")
    raise AssertionError("se pudo apuntar en un periodo cerrado")
except sqlite3.IntegrityError:
    pass

# Clasificador por reglas (sin IA): lo sensible sale marcado para revisar.
reglas = motor.leer_csv(RAIZ / "data" / "reglas_clasificacion.csv")
plan = motor.leer_csv(RAIZ / "data" / "plan_cuentas_pgc.csv")
salida = {m["id"]: m for m in clasificador.clasificar(motor.leer_csv(EJEMPLO / "banco.csv"), reglas, plan, False, 0.9)}
assert salida["1"]["cuenta"] == "476" and salida["3"]["cuenta"] == "4750" and salida["2"]["cuenta"] == "4751"
assert salida["7"]["cuenta"] == "430" and salida["9"]["cuenta"] == "171" and salida["9"]["revisar"] == "si"
assert salida["11"]["origen"] == "pendiente"
assert all(m["cuenta"] == "" or any(m["cuenta"].startswith(c["codigo"]) for c in plan) for m in salida.values())

print("OK")
