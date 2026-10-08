"""Comprobación de Cuentas Claras: python test_cuentas_claras.py (sin dependencias ni llamadas reales a la IA)."""
import csv
import io
import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import app
import bancos
import motor
from motor import ErrorContable, centimos

RAIZ = Path(__file__).parent
EJ = RAIZ / "data" / "ejemplo"


def falla(fn, *a, **k):
    try:
        fn(*a, **k)
    except (ErrorContable, sqlite3.IntegrityError):
        return
    raise AssertionError(f"{fn.__name__}{a} tenía que fallar")


def estados(con, hasta, texto=""):
    return [s for _, n, s, _ in motor.controles(con, hasta) if texto in n]


# --- Los CSV del repositorio están bien formados
for ruta in RAIZ.glob("data/**/*.csv"):
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.reader(f))
    assert len({len(x) for x in filas if x and not ruta.name.startswith("extracto")}) <= 1, ruta

# --- Importes y NIF
assert centimos("1.234,56") == centimos("1234.56") == 123456 and centimos("1.234") == 123400
assert centimos("0.1") + centimos("0.2") == centimos("0.3")
falla(centimos, "1,234")  # tres decimales: no es dinero
assert motor.euros(-123456) == "-1.234,56"
for nif in ("12345678Z", "X1234567L", "B12345674", "A00000000"):
    motor.validar_nif(nif)
falla(motor.validar_nif, "12345678A")
falla(motor.validar_nif, "B12345675")

# --- Norma 43 y CSV de banca online
n43 = (EJ / "extracto_abril.n43").read_text(encoding="latin-1")
cuenta_n43 = bancos.leer_norma43(n43)[0]
assert len(cuenta_n43["movimientos"]) == 13 and cuenta_n43["saldo_final"] == 11173810
assert cuenta_n43["movimientos"][2]["concepto"] == "TRANSFERENCIA DE CLIENTE EJEMPLO SL FRA 2026-002"
lineas = n43.splitlines()
assert lineas[1][28:42] == "00000000035000"
lineas[1] = lineas[1][:28] + "00000000035001" + lineas[1][42:]
falla(bancos.leer_norma43, "\n".join(lineas))  # un céntimo tocado: los totales del propio fichero ya no cuadran
movs_csv = bancos.leer_csv_banco((EJ / "extracto_ejemplo.csv").read_text(encoding="utf-8"))
assert [m["importe"] for m in movs_csv] == [121000, -25000, -1500] and movs_csv[0]["fecha"] == "2026-05-02"

# --- Demo de principio a fin
tmp = Path(tempfile.mkdtemp())
db = str(tmp / "demo.db")
app.crear_demo(db)
con = motor.abrir(db)
assert motor.mes_cerrado(con, "2026-03-15") and not motor.mes_cerrado(con, "2026-04-15")
assert "FALLA" not in estados(con, "2026-03-31")
assert motor.saldo(con, "57200001", "2026-03-31") == 4708900 and motor.saldo(con, "4750", "2026-03-31") == -60900
q1 = dict(motor.pyg_gestion(con, "2026-01-01", "2026-03-31"))
assert q1["EBITDA"] == -779500 and q1["EBITDA ajustado (sin no recurrentes)"] == -739500 and q1["Amortizaciones"] == -3750

# Libro inmutable y meses cerrados
falla(motor.crear_asiento, con, "2026-02-10", "tarde", [{"cuenta": "629", "debe": 100}, {"cuenta": "57200001", "haber": 100}])
falla(motor.crear_asiento, con, "2026-04-10", "descuadrado", [{"cuenta": "629", "debe": 100}, {"cuenta": "57200001", "haber": 99}])
falla(con.execute, "UPDATE apunte SET debe = 1 WHERE id = 1")
falla(con.execute, "DELETE FROM apunte WHERE id = 1")

# Conciliación: el cobro y el pago de facturas se emparejaron solos
cobro = con.execute("SELECT * FROM movimiento_banco WHERE concepto LIKE 'TRANSFERENCIA%'").fetchone()
assert cobro["propuesta_origen"] == "conciliacion" and cobro["asiento_id"]
assert con.execute("SELECT asiento_pago_id FROM documento WHERE serie_numero = '2026-002'").fetchone()[0] == cobro["asiento_id"]
assert bancos.importar_extracto(con, "otra vez.n43", (EJ / "extracto_abril.n43").read_bytes(), "57200001")["nuevos"] == 0

# El banco no cuadra mientras haya movimientos sin contabilizar; al aprobarlos, sí
assert estados(con, "2026-04-30", "Norma 43") == ["FALLA"]
pend = {m["concepto"]: m["id"] for m in con.execute("SELECT * FROM movimiento_banco WHERE asiento_id IS NULL")}


def id_de(texto):
    return next(i for c, i in pend.items() if texto in c)


assert len(pend) == 5
falla(bancos.aprobar, con, id_de("TRASPASO"))  # la regla propone 572: no puede ser la propia cuenta
bancos.aprobar(con, id_de("TRASPASO"), "57200002")
bancos.aprobar(con, id_de("ENISA"))
bancos.aprobar(con, id_de("COWORKING"), "621")
bancos.aprobar(con, id_de("RESTAURANTE"), "627")
bancos.aprobar(con, id_de("RENFE"), "629", texto_regla="RENFE")
assert estados(con, "2026-04-30", "Norma 43") == ["OK"] and estados(con, "2026-04-30", "bancarios") == ["OK"]
assert tuple(con.execute("SELECT patron, cuenta FROM regla ORDER BY orden LIMIT 1").fetchone()) == ("RENFE", "629")

# Anulación: asiento inverso, una sola vez
dominio = con.execute("SELECT id FROM asiento WHERE concepto LIKE 'Dominio%'").fetchone()[0]
motor.anular_asiento(con, dominio, "pagado dos veces")
falla(motor.anular_asiento, con, dominio, "otra vez")
assert motor.saldo(con, "551") == 0

# Fiscal del 2T
b303 = motor.borrador_303(con, 2026, 2)
assert b303["resultado"] == 94500 and b303["segun_libro"] == 94500  # 1.050 + 8,40 (ISP) - 113,40
b111 = motor.borrador_111(con, 2026, 2)
assert b111["total"] == 52500 and b111["lineas"][1][3] == 7500
assert [x["nombre"] for x in motor.modelo_347(con, 2026)] == ["Cliente Ejemplo SL"]
assert motor.antiguedad(con, "recibida", "2026-05-31")[0]["tramo"] == "1-30"

# Cierre de abril: amortiza solo y bloquea
motor.cerrar_mes(con, "2026-04")
assert con.execute("SELECT importe FROM amortizacion WHERE mes = '2026-04'").fetchone()[0] == 2500
falla(motor.cerrar_mes, con, "2026-04")
motor.reabrir_mes(con, "2026-04", "prueba")
assert not motor.mes_cerrado(con, "2026-04-01")

# Regularización del ejercicio: la PyG no cambia y el balance sigue cuadrando
antes = dict(motor.pyg_gestion(con, "2026-01-01", "2026-12-31"))["Resultado neto"]
motor.regularizar_ejercicio(con, 2026)
assert dict(motor.pyg_gestion(con, "2026-01-01", "2026-12-31"))["Resultado neto"] == antes
assert motor.saldo(con, "6", "2026-12-31") == 0 and motor.saldo(con, "129", "2026-12-31") == -antes
b = motor.balance(con, "2026-12-31")
assert b["activo"] == b["pn_pasivo"] and estados(con, "2027-01-31", "regularizados") == ["OK"]
assert all(s != "FALLA" for s in estados(con, "2026-12-31"))

# Data room
z = zipfile.ZipFile(io.BytesIO(motor.exportar_dataroom(con, "2026-04-30")))
assert {"00_LEEME.txt", "01_libro_diario.csv", "02_sumas_y_saldos_mensual.csv", "07_partes_vinculadas.csv"} <= set(z.namelist())
assert "2026-03;57200001" in z.read("02_sumas_y_saldos_mensual.csv").decode("utf-8-sig")
assert motor.kpis(con, "2026-04")[0][0] == "Tesorería"
con.close()

# --- IA con un cliente simulado: valida la petición y que lo que propone pasa por las mismas reglas
class ClienteFalso:
    def __init__(self, propuestas):
        self.propuestas, self.peticion = propuestas, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    def _crear(self, **kw):
        self.peticion = kw
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"movimientos": self.propuestas}))])


con = motor.abrir()
bancos.importar_movimientos(con, "572", [{"fecha": "2026-05-03", "concepto": "PAGO TARJETA RENFE ES9121000418450200051332", "importe": -5000},
                                         {"fecha": "2026-05-04", "concepto": "PAGO TARJETA ALGO RARO", "importe": -1000},
                                         {"fecha": "2026-05-05", "concepto": "PAGO TARJETA OTRO", "importe": -2000}])
falso = ClienteFalso([{"id": "1", "cuenta": "629", "confianza": 0.97, "requiere_documento": False, "motivo": "Viaje"},
                      {"id": "2", "cuenta": "999", "confianza": 0.99, "requiere_documento": False, "motivo": "Inventada"},
                      {"id": "3", "cuenta": "627", "confianza": 0.6, "requiere_documento": True, "motivo": "Dudoso"}])
assert bancos.proponer(con, usar_ia=True, cliente=falso) == {"ia": 2, "pendiente": 1}
p = {m["id"]: m for m in con.execute("SELECT * FROM movimiento_banco")}
assert p[1]["propuesta_cuenta"] == "629" and not p[1]["propuesta_revisar"]
assert p[2]["propuesta_origen"] == "pendiente" and p[3]["propuesta_revisar"] == 1
assert falso.peticion["model"] == "claude-opus-5-5" and falso.peticion["output_config"]["format"]["type"] == "json_schema"
assert "ES9121000418450200051332" not in json.dumps(falso.peticion["messages"])  # el IBAN no sale de la empresa
con.close()

# --- Aplicación web
srv = app.servidor(db, 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
for ruta, _ in app.NAV:
    html = urllib.request.urlopen(base + ruta).read().decode("utf-8")
    assert "<h1>" in html and "Error interno" not in html, ruta
assert "Cliente Ejemplo SL" in urllib.request.urlopen(base + "/facturas").read().decode("utf-8")
assert urllib.request.urlopen(base + "/dataroom?hasta=2026-04-30").read()[:2] == b"PK"


def post(ruta, datos: bytes, tipo="application/x-www-form-urlencoded", origen=None):
    r = urllib.request.Request(base + ruta, data=datos, headers={"Content-Type": tipo, **({"Origin": origen} if origen else {})})
    return urllib.request.urlopen(r).read().decode("utf-8")


pagina = post("/terceros/nuevo", "nombre=Empleada Ejemplo&nif=X1234567L&tipo=empleado".encode())
assert "Empleada Ejemplo" in pagina
assert "NIF no v" in post("/terceros/nuevo", b"nombre=Mal&nif=12345678A&tipo=cliente")
try:
    post("/terceros/nuevo", b"nombre=Ataque&nif=00000001R&tipo=cliente", origen="http://otra-web.example")
    raise AssertionError("un formulario de otra web no puede escribir")
except urllib.error.HTTPError as ex:
    assert ex.code == 403
frontera = "----cuentasclaras"
cuerpo = (f"--{frontera}\r\nContent-Disposition: form-data; name=\"cuenta\"\r\n\r\n57200002\r\n"
          f"--{frontera}\r\nContent-Disposition: form-data; name=\"fichero\"; filename=\"extracto_ejemplo.csv\"\r\n"
          f"Content-Type: text/csv\r\n\r\n").encode() + (EJ / "extracto_ejemplo.csv").read_bytes() + f"\r\n--{frontera}--\r\n".encode()
assert "CSV: 3 movimientos nuevos" in post("/bancos/importar", cuerpo, f"multipart/form-data; boundary={frontera}")
srv.shutdown()

print("OK")
