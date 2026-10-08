"""Motor contable de Cuentas Claras.

Libro mayor, terceros, facturas, inmovilizado, cierre, controles, informes, borradores
fiscales y data room de due diligence. SQLite y biblioteca estándar: sin dependencias.
Todos los importes viajan en céntimos (int).
"""
import calendar
import csv
import io
import re
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).parent
DIMENSIONES = ("linea_negocio", "producto", "canal", "centro_coste", "proyecto")
EXPLOTACION = ("Ingresos", "Aprovisionamientos", "Otros ingresos de explotación", "Personal", "Otros gastos de explotación")
PREFIJO_TERCERO = {"cliente": "430", "proveedor": "400", "acreedor": "410", "socio": "551", "empleado": "465", "banco": "572"}
FINANCIACION = ("10", "11", "13", "16", "17", "19", "52", "4708")  # caja que entra o sale por financiación, no por operar
DEUDA_FINANCIERA = ("160", "163", "170", "171", "172", "174", "520", "521", "524", "527")
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


class ErrorContable(ValueError):
    """Operación que rompería la contabilidad: se rechaza con un mensaje para la persona."""


# ---------------------------------------------------------------- importes y fechas

def centimos(valor) -> int:
    """'1.234,56' | '1234.56' | '1.234' -> céntimos. Nunca float."""
    s = str(valor if valor is not None else "").strip().replace(" ", "").replace("€", "")
    if not s:
        return 0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", s):
        s = s.replace(".", "")  # punto de miles: el dinero no tiene tres decimales
    try:
        d = Decimal(s)
    except Exception:
        raise ErrorContable(f"Importe no válido: {valor}") from None
    if d != d.quantize(Decimal("0.01")):
        raise ErrorContable(f"Importe con más de dos decimales: {valor}")
    return int(d * 100)


def euros(c: int | None) -> str:
    if c is None:
        return ""
    s = f"{abs(c) // 100:,}".replace(",", ".") + f",{abs(c) % 100:02d}"
    return "-" + s if c < 0 else s


def porcentaje(base: int, pct) -> int:
    return int((Decimal(base) * Decimal(str(pct)) / 100).quantize(Decimal("1"), ROUND_HALF_UP))


def fecha_iso(texto) -> str:
    """'2026-03-31' | '31/03/2026' | '31-03-26' -> '2026-03-31'."""
    s = str(texto).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y%m%d"):
        try:
            return datetime.strptime(s, formato).date().isoformat()
        except ValueError:
            pass
    raise ErrorContable(f"Fecha no válida: {texto}")


def fin_de_mes(mes: str) -> str:
    a, m = map(int, mes.split("-"))
    return f"{mes}-{calendar.monthrange(a, m)[1]:02d}"


def meses_entre(desde: str, hasta: str) -> list[str]:
    a, m = map(int, desde[:7].split("-"))
    fin, salida = hasta[:7], []
    while f"{a}-{m:02d}" <= fin:
        salida.append(f"{a}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return salida


def trimestre_fechas(anio: int, t: int) -> tuple[str, str]:
    return f"{anio}-{3 * t - 2:02d}-01", fin_de_mes(f"{anio}-{3 * t:02d}")


# ---------------------------------------------------------------- base de datos

def leer_csv(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def abrir(ruta=":memory:") -> sqlite3.Connection:
    """Abre (o crea con plan de cuentas y reglas) la base de datos de una empresa."""
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'cuenta'").fetchone():
        con.executescript((RAIZ / "db" / "schema.sql").read_text(encoding="utf-8"))
        con.executemany(
            "INSERT INTO cuenta (codigo, nombre, grupo, naturaleza, masa, epigrafe_gestion) VALUES (?, ?, ?, ?, ?, ?)",
            [(c["codigo"], c["nombre"], int(c["codigo"][0]), c["naturaleza"], c["masa"], c["epigrafe_gestion"] or None)
             for c in leer_csv(RAIZ / "data" / "plan_cuentas_pgc.csv")])
        con.executemany(
            "INSERT INTO regla (orden, patron, sentido, cuenta, revisar, nota) VALUES (?, ?, ?, ?, ?, ?)",
            [(i, r["patron"], r["sentido"], r["cuenta"], int(r["revisar"] == "si"), r["nota"])
             for i, r in enumerate(leer_csv(RAIZ / "data" / "reglas_clasificacion.csv"), 1)])
        con.commit()
    return con


def config(con, clave: str, defecto: str = "") -> str:
    fila = con.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    return fila[0] if fila and fila[0] is not None else defecto


def guardar_config(con, clave: str, valor: str) -> None:
    with con:
        con.execute("INSERT INTO config VALUES (?, ?) ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


def _evento(con, tipo: str, detalle: str) -> None:
    con.execute("INSERT INTO evento (tipo, detalle) VALUES (?, ?)", (tipo, detalle))


def cuenta_info(con, codigo: str):
    """La cuenta del plan más larga de la que cuelga `codigo` (o él mismo)."""
    return con.execute("SELECT * FROM cuenta WHERE ? LIKE codigo || '%' ORDER BY length(codigo) DESC LIMIT 1",
                       (codigo,)).fetchone()


def existe_cuenta(con, codigo: str) -> bool:
    return con.execute("SELECT 1 FROM cuenta WHERE codigo = ?", (codigo,)).fetchone() is not None


def _subcuenta(con, codigo: str, nombre: str | None = None) -> None:
    """Crea la subcuenta heredando naturaleza, masa y epígrafe de su cuenta madre."""
    if existe_cuenta(con, codigo):
        return
    madre = cuenta_info(con, codigo)
    if not madre or not codigo.isdigit():
        raise ErrorContable(f"La cuenta {codigo} no está en el plan ni cuelga de ninguna cuenta del plan")
    con.execute("INSERT INTO cuenta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (codigo, nombre or f"{madre['nombre']} ({codigo})", madre["grupo"], madre["naturaleza"],
                 madre["masa"], madre["epigrafe_gestion"], madre["codigo"]))


def crear_subcuenta(con, codigo: str, nombre: str) -> None:
    with con:
        if existe_cuenta(con, codigo):
            raise ErrorContable(f"La cuenta {codigo} ya existe")
        _subcuenta(con, codigo, nombre)


# ---------------------------------------------------------------- asientos

def mes_cerrado(con, fecha: str) -> bool:
    return con.execute("SELECT 1 FROM periodo WHERE mes = ? AND cerrado_en IS NOT NULL", (fecha[:7],)).fetchone() is not None


def _asiento(con, fecha, concepto, lineas, origen="manual", confianza=None, aprobado_por=None, anula_a=None,
             crear_subcuentas=False) -> int:
    """Valida y escribe un asiento. No confirma la transacción: lo hace quien llama."""
    fecha = fecha_iso(fecha)
    lineas = [l for l in lineas if l.get("debe") or l.get("haber")]
    if not str(concepto).strip():
        raise ErrorContable("El asiento necesita un concepto")
    if len(lineas) < 2:
        raise ErrorContable("Un asiento necesita al menos dos apuntes")
    for l in lineas:
        d, h = l.get("debe") or 0, l.get("haber") or 0
        if d < 0 or h < 0 or (d > 0) == (h > 0):
            raise ErrorContable(f"Cuenta {l['cuenta']}: cada apunte va al debe o al haber, en positivo y no a los dos")
        if not existe_cuenta(con, l["cuenta"]):
            if not crear_subcuentas:
                raise ErrorContable(f"La cuenta {l['cuenta']} no existe en el plan de cuentas")
            _subcuenta(con, l["cuenta"])
    td, th = sum(l.get("debe") or 0 for l in lineas), sum(l.get("haber") or 0 for l in lineas)
    if td != th:
        raise ErrorContable(f"Asiento descuadrado: debe {euros(td)} y haber {euros(th)}")
    if mes_cerrado(con, fecha):
        raise ErrorContable(f"El mes {fecha[:7]} está cerrado: reábrelo o contabiliza en un mes abierto")
    aid = con.execute("INSERT INTO asiento (fecha, concepto, origen, confianza, aprobado_por, anula_a) VALUES (?, ?, ?, ?, ?, ?)",
                      (fecha, str(concepto).strip(), origen, confianza, aprobado_por, anula_a)).lastrowid
    for l in lineas:
        con.execute(f"INSERT INTO apunte (asiento_id, cuenta, debe, haber, {', '.join(DIMENSIONES)}, no_recurrente) "
                    f"VALUES (?, ?, ?, ?, {', '.join('?' * len(DIMENSIONES))}, ?)",
                    (aid, l["cuenta"], l.get("debe") or 0, l.get("haber") or 0,
                     *((l.get(d) or None) for d in DIMENSIONES), int(bool(l.get("no_recurrente")))))
    return aid


def crear_asiento(con, fecha, concepto, lineas, **kw) -> int:
    with con:
        return _asiento(con, fecha, concepto, lineas, **kw)


def anular_asiento(con, asiento_id: int, motivo: str, usuario: str = "usuario") -> int:
    """El libro no se borra: se anula con el asiento inverso y se liberan los enlaces."""
    if not motivo.strip():
        raise ErrorContable("Indica el motivo de la anulación")
    with con:
        a = con.execute("SELECT * FROM asiento WHERE id = ?", (asiento_id,)).fetchone()
        if not a:
            raise ErrorContable(f"No existe el asiento {asiento_id}")
        if a["origen"] in ("anulacion", "regularizacion"):
            raise ErrorContable("Este asiento no se anula")
        if con.execute("SELECT 1 FROM asiento WHERE anula_a = ?", (asiento_id,)).fetchone():
            raise ErrorContable(f"El asiento {asiento_id} ya está anulado")
        doc = con.execute("SELECT * FROM documento WHERE asiento_id = ?", (asiento_id,)).fetchone()
        if doc and doc["asiento_pago_id"]:
            raise ErrorContable(f"La factura {doc['serie_numero']} está cobrada o pagada: anula antes el cobro o pago")
        lineas = [{"cuenta": p["cuenta"], "debe": p["haber"], "haber": p["debe"],
                   **{d: p[d] for d in DIMENSIONES}, "no_recurrente": p["no_recurrente"]}
                  for p in con.execute("SELECT * FROM apunte WHERE asiento_id = ?", (asiento_id,))]
        nuevo = _asiento(con, a["fecha"], f"Anulación del asiento {asiento_id}: {motivo.strip()}", lineas,
                         origen="anulacion", aprobado_por=usuario, anula_a=asiento_id)
        con.execute("UPDATE documento SET asiento_pago_id = NULL WHERE asiento_pago_id = ?", (asiento_id,))
        con.execute("UPDATE movimiento_banco SET asiento_id = NULL WHERE asiento_id = ?", (asiento_id,))
        con.execute("DELETE FROM amortizacion WHERE asiento_id = ?", (asiento_id,))
        if doc:
            con.execute("UPDATE activo SET documento_id = NULL WHERE documento_id = ?", (doc["id"],))
            con.execute("DELETE FROM documento WHERE id = ?", (doc["id"],))
        _evento(con, "anulacion", f"Asiento {asiento_id} anulado por {usuario}: {motivo.strip()}")
        return nuevo


def importar_diario(con, texto: str) -> int:
    """Diario exportado de la gestoría: asiento, fecha, cuenta, concepto, debe, haber [+ dimensiones, no_recurrente].
    Admite ';' o ',' y decimales con coma. Todo o nada: si un asiento falla, no se importa ninguno."""
    texto = texto.lstrip("﻿")
    cabecera = texto.splitlines()[0] if texto.strip() else ""
    delim = ";" if cabecera.count(";") > cabecera.count(",") else ","
    filas = list(csv.DictReader(io.StringIO(texto), delimiter=delim))
    faltan = {"asiento", "fecha", "cuenta", "concepto", "debe", "haber"} - set(filas[0] if filas else ())
    if faltan:
        raise ErrorContable(f"Faltan columnas en el diario: {', '.join(sorted(faltan))}")
    grupos: dict[str, list] = {}
    for f in filas:
        grupos.setdefault(f["asiento"], []).append(f)
    with con:
        for numero, lineas in grupos.items():
            try:
                _asiento(con, lineas[0]["fecha"], lineas[0]["concepto"],
                         [{"cuenta": l["cuenta"].strip(), "debe": centimos(l["debe"]), "haber": centimos(l["haber"]),
                           **{d: (l.get(d) or "").strip() for d in DIMENSIONES},
                           "no_recurrente": (l.get("no_recurrente") or "").strip() in ("1", "si", "sí", "x")}
                          for l in lineas], origen="importado", crear_subcuentas=True)
            except ErrorContable as e:
                raise ErrorContable(f"Asiento {numero} del fichero: {e}") from None
        _evento(con, "importacion", f"Diario importado: {len(grupos)} asientos")
    return len(grupos)


# ---------------------------------------------------------------- terceros

def validar_nif(nif: str, extranjero: bool = False) -> str:
    """DNI, NIE o CIF español con su dígito de control. Los NIF extranjeros solo se normalizan."""
    n = re.sub(r"[\s.-]", "", nif or "").upper()
    if extranjero:
        if len(n) < 4:
            raise ErrorContable("NIF extranjero demasiado corto")
        return n
    if re.fullmatch(r"\d{8}[A-Z]", n):
        ok = LETRAS_DNI[int(n[:8]) % 23] == n[8]
    elif re.fullmatch(r"[XYZ]\d{7}[A-Z]", n):
        ok = LETRAS_DNI[int(str("XYZ".index(n[0])) + n[1:8]) % 23] == n[8]
    elif re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", n):
        pares = sum(int(c) for c in n[2:8:2])
        impares = sum(sum(divmod(int(c) * 2, 10)) for c in n[1:8:2])
        control = (10 - (pares + impares) % 10) % 10
        ok = n[8] in (str(control), "JABCDEFGHI"[control])
    else:
        ok = False
    if not ok:
        raise ErrorContable(f"NIF no válido: {nif}")
    return n


def alta_tercero(con, nombre: str, nif: str, tipo: str, vinculado=False, extranjero=False) -> int:
    if tipo not in PREFIJO_TERCERO:
        raise ErrorContable(f"Tipo de tercero no válido: {tipo}")
    if not nombre.strip():
        raise ErrorContable("El tercero necesita un nombre")
    nif = validar_nif(nif, extranjero)
    prefijo = PREFIJO_TERCERO[tipo]
    with con:
        if con.execute("SELECT 1 FROM tercero WHERE nif = ?", (nif,)).fetchone():
            raise ErrorContable(f"Ya existe un tercero con NIF {nif}")
        ultimo = con.execute("SELECT MAX(CAST(substr(codigo, 4) AS INTEGER)) FROM cuenta WHERE codigo LIKE ? AND length(codigo) = 8",
                             (prefijo + "%",)).fetchone()[0] or 0
        subcuenta = f"{prefijo}{ultimo + 1:05d}"
        _subcuenta(con, subcuenta, nombre.strip())
        return con.execute("INSERT INTO tercero (nif, nombre, tipo, vinculado, extranjero, subcuenta) VALUES (?, ?, ?, ?, ?, ?)",
                           (nif, nombre.strip(), tipo, int(bool(vinculado)), int(bool(extranjero)), subcuenta)).lastrowid


# ---------------------------------------------------------------- facturas

def registrar_factura(con, tipo: str, tercero_id: int, serie_numero: str, fecha, vencimiento, base: int, tipo_iva,
                      cuenta: str, retencion_pct=0, isp=False, dimensiones: dict | None = None, no_recurrente=False,
                      archivo: str | None = None, vida_meses: int | None = None) -> int:
    """Registra una factura emitida o recibida y genera su asiento. Si la cuenta es de inmovilizado (2xx), da de alta el activo."""
    t = con.execute("SELECT * FROM tercero WHERE id = ?", (tercero_id,)).fetchone()
    if not t:
        raise ErrorContable("Elige un tercero")
    if tipo == "emitida" and t["tipo"] != "cliente":
        raise ErrorContable("Las facturas emitidas son a clientes")
    if tipo == "recibida" and t["tipo"] not in ("proveedor", "acreedor"):
        raise ErrorContable("Las facturas recibidas son de proveedores o acreedores")
    if tipo not in ("emitida", "recibida"):
        raise ErrorContable("Tipo de factura no válido")
    if not existe_cuenta(con, cuenta) or (tipo == "emitida" and not cuenta.startswith("7")) or (
            tipo == "recibida" and not cuenta[:1] in ("6", "2")):
        raise ErrorContable(f"Cuenta {cuenta} no válida para una factura {tipo} (emitidas: 7xx; recibidas: 6xx o 2xx)")
    if isp and tipo != "recibida":
        raise ErrorContable("La inversión del sujeto pasivo solo aplica a facturas recibidas")
    if base <= 0:
        raise ErrorContable("La base imponible tiene que ser positiva (las rectificativas se registran anulando)")
    if cuenta.startswith("2") and not vida_meses:
        raise ErrorContable("Para inmovilizado indica la vida útil en meses")
    fecha, vencimiento = fecha_iso(fecha), fecha_iso(vencimiento)
    cuota, retencion = porcentaje(base, tipo_iva), porcentaje(base, retencion_pct)
    total = base if isp else base + cuota - retencion
    dims = {**(dimensiones or {}), "no_recurrente": no_recurrente}
    sub = t["subcuenta"]
    if tipo == "emitida":
        lineas = [{"cuenta": sub, "debe": total}, {"cuenta": "473", "debe": retencion},
                  {"cuenta": cuenta, "haber": base, **dims}, {"cuenta": "477", "haber": cuota}]
    elif isp:
        lineas = [{"cuenta": cuenta, "debe": base, **dims}, {"cuenta": "472", "debe": cuota},
                  {"cuenta": "477", "haber": cuota}, {"cuenta": sub, "haber": base}]
    else:
        lineas = [{"cuenta": cuenta, "debe": base, **dims}, {"cuenta": "472", "debe": cuota},
                  {"cuenta": "4751", "haber": retencion}, {"cuenta": sub, "haber": total}]
    with con:
        aid = _asiento(con, fecha, f"Factura {tipo} {serie_numero} · {t['nombre']}", lineas, origen="factura")
        try:
            did = con.execute(
                "INSERT INTO documento (tipo, serie_numero, fecha, vencimiento, tercero_id, cuenta, base, tipo_iva, cuota_iva, "
                "retencion, total, isp, archivo, asiento_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tipo, serie_numero.strip(), fecha, vencimiento, tercero_id, cuenta, base, float(tipo_iva), cuota,
                 retencion, total, int(bool(isp)), archivo, aid)).lastrowid
        except sqlite3.IntegrityError:
            raise ErrorContable(f"La factura {serie_numero} ya está registrada") from None
        if cuenta.startswith("2"):
            _alta_activo(con, f"{serie_numero} · {t['nombre']}", cuenta, fecha, base, vida_meses, did)
        return did


def liquidar_factura(con, documento_id: int, fecha, cuenta_banco: str = "572", movimiento_id: int | None = None,
                     aprobado_por: str = "usuario") -> int:
    """Cobro (emitida) o pago (recibida) completo de una factura."""
    with con:
        d = con.execute("SELECT d.*, t.subcuenta, t.nombre FROM documento d JOIN tercero t ON t.id = d.tercero_id "
                        "WHERE d.id = ?", (documento_id,)).fetchone()
        if not d:
            raise ErrorContable("No existe la factura")
        if d["asiento_pago_id"]:
            raise ErrorContable(f"La factura {d['serie_numero']} ya está {'cobrada' if d['tipo'] == 'emitida' else 'pagada'}")
        if movimiento_id:
            m = con.execute("SELECT * FROM movimiento_banco WHERE id = ?", (movimiento_id,)).fetchone()
            if not m or m["asiento_id"]:
                raise ErrorContable("El movimiento no existe o ya está contabilizado")
            if abs(m["importe"]) != d["total"] or (m["importe"] > 0) != (d["tipo"] == "emitida"):
                raise ErrorContable("El importe del movimiento no coincide con el total de la factura")
            cuenta_banco, fecha = m["cuenta_banco"], m["fecha"]
        if d["tipo"] == "emitida":
            lineas = [{"cuenta": cuenta_banco, "debe": d["total"]}, {"cuenta": d["subcuenta"], "haber": d["total"]}]
        else:
            lineas = [{"cuenta": d["subcuenta"], "debe": d["total"]}, {"cuenta": cuenta_banco, "haber": d["total"]}]
        aid = _asiento(con, fecha, f"{'Cobro' if d['tipo'] == 'emitida' else 'Pago'} factura {d['serie_numero']} · {d['nombre']}",
                       lineas, origen="conciliacion" if movimiento_id else "manual", aprobado_por=aprobado_por)
        con.execute("UPDATE documento SET asiento_pago_id = ? WHERE id = ?", (aid, documento_id))
        if movimiento_id:
            con.execute("UPDATE movimiento_banco SET asiento_id = ? WHERE id = ?", (aid, movimiento_id))
        return aid


def facturas_pendientes(con, tipo: str, hasta: str | None = None) -> list:
    hasta = hasta or date.today().isoformat()
    return con.execute(
        "SELECT d.*, t.nombre, t.nif, t.subcuenta FROM documento d JOIN tercero t ON t.id = d.tercero_id "
        "LEFT JOIN asiento p ON p.id = d.asiento_pago_id "
        "WHERE d.tipo = ? AND d.fecha <= ? AND (p.id IS NULL OR p.fecha > ?) ORDER BY d.vencimiento",
        (tipo, hasta, hasta)).fetchall()


def antiguedad(con, tipo: str, hasta: str | None = None) -> list[dict]:
    """Antigüedad de saldos por factura: lo primero que mira una due diligence del circulante."""
    hasta = hasta or date.today().isoformat()
    salida = []
    for d in facturas_pendientes(con, tipo, hasta):
        dias = (date.fromisoformat(hasta) - date.fromisoformat(d["vencimiento"])).days
        tramo = "No vencida" if dias <= 0 else "1-30" if dias <= 30 else "31-60" if dias <= 60 else "61-90" if dias <= 90 else "> 90"
        salida.append({"tercero": d["nombre"], "factura": d["serie_numero"], "fecha": d["fecha"],
                       "vencimiento": d["vencimiento"], "total": d["total"], "dias": max(dias, 0), "tramo": tramo})
    return salida


# ---------------------------------------------------------------- inmovilizado

def _cuentas_amortizacion(cuenta: str) -> tuple[str, str]:
    return ("280", "680") if cuenta.startswith("20") else ("281", "681")


def _alta_activo(con, descripcion, cuenta, fecha_alta, coste, vida_meses, documento_id=None) -> int:
    if not cuenta[:2] in ("20", "21") or not existe_cuenta(con, cuenta):
        raise ErrorContable("El activo va en una cuenta 20x (intangible) o 21x (material)")
    if int(vida_meses or 0) <= 0 or coste <= 0:
        raise ErrorContable("Coste y vida útil tienen que ser positivos")
    return con.execute("INSERT INTO activo (descripcion, cuenta, fecha_alta, coste, vida_meses, documento_id) VALUES (?, ?, ?, ?, ?, ?)",
                       (descripcion.strip(), cuenta, fecha_iso(fecha_alta), coste, int(vida_meses), documento_id)).lastrowid


def alta_activo(con, descripcion, cuenta, fecha_alta, coste: int, vida_meses: int) -> int:
    """Alta en el registro de un activo ya contabilizado (p. ej. comprado antes de usar la aplicación)."""
    with con:
        return _alta_activo(con, descripcion, cuenta, fecha_alta, coste, vida_meses)


def cuota_amortizacion(activo, mes: str, acumulado: int) -> int:
    """Lineal por meses; el primer mes, en proporción a los días desde el alta."""
    alta = date.fromisoformat(activo["fecha_alta"])
    if mes < alta.isoformat()[:7]:
        return 0
    mensual = Decimal(activo["coste"]) / activo["vida_meses"]
    if mes == alta.isoformat()[:7]:
        dias = calendar.monthrange(alta.year, alta.month)[1]
        mensual = mensual * (dias - alta.day + 1) / dias
    return max(0, min(int(mensual.quantize(Decimal("1"), ROUND_HALF_UP)), activo["coste"] - acumulado))


def amortizar_mes(con, mes: str) -> int | None:
    """Genera (una vez) el asiento de amortización del mes para todo el inmovilizado."""
    lineas, filas = [], []
    for a in con.execute("SELECT * FROM activo WHERE fecha_alta <= ?", (fin_de_mes(mes),)).fetchall():
        if con.execute("SELECT 1 FROM amortizacion WHERE activo_id = ? AND mes = ?", (a["id"], mes)).fetchone():
            continue
        acumulado = con.execute("SELECT COALESCE(SUM(importe), 0) FROM amortizacion WHERE activo_id = ?", (a["id"],)).fetchone()[0]
        cuota = cuota_amortizacion(a, mes, acumulado)
        if cuota:
            acum, gasto = _cuentas_amortizacion(a["cuenta"])
            lineas += [{"cuenta": gasto, "debe": cuota}, {"cuenta": acum, "haber": cuota}]
            filas.append((a["id"], cuota))
    if not lineas:
        return None
    with con:
        aid = _asiento(con, fin_de_mes(mes), f"Amortización {mes}", lineas, origen="automatico", aprobado_por="sistema")
        con.executemany("INSERT INTO amortizacion VALUES (?, ?, ?, ?)", [(i, mes, c, aid) for i, c in filas])
        return aid


# ---------------------------------------------------------------- saldos e informes

def saldo(con, cuenta: str, hasta: str | None = None, desde: str | None = None) -> int:
    """Saldo (debe - haber) de una cuenta y sus subcuentas: deudor positivo, acreedor negativo."""
    return con.execute(
        "SELECT COALESCE(SUM(a.debe - a.haber), 0) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
        "WHERE a.cuenta LIKE ? || '%' AND (? IS NULL OR s.fecha <= ?) AND (? IS NULL OR s.fecha >= ?)",
        (cuenta, hasta, hasta, desde, desde)).fetchone()[0]


def sumas_y_saldos(con, hasta: str | None = None, nivel: int | None = None, desde: str | None = None) -> list[tuple]:
    """Filas (cuenta, nombre, naturaleza, sumas debe, sumas haber, saldo deudor, saldo acreedor)."""
    cta = f"substr(a.cuenta, 1, {int(nivel)})" if nivel else "a.cuenta"
    filas = []
    for codigo, debe, haber in con.execute(
            f"SELECT {cta} AS c, SUM(a.debe), SUM(a.haber) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            "WHERE (? IS NULL OR s.fecha <= ?) AND (? IS NULL OR s.fecha >= ?) GROUP BY c ORDER BY c",
            (hasta, hasta, desde, desde)).fetchall():
        info = cuenta_info(con, codigo)
        nombre = info["nombre"] if info and info["codigo"] == codigo else (info["nombre"] if info else "")
        filas.append((codigo, nombre, info["naturaleza"] if info else "DA", debe, haber, max(debe - haber, 0), max(haber - debe, 0)))
    return filas


def pyg_gestion(con, desde: str | None = None, hasta: str | None = None) -> list[tuple[str, int]]:
    """PyG de gestión sacada del mismo libro: la de gestión y la contable no pueden no cuadrar."""
    def suma(extra: str) -> dict:
        return dict(con.execute(
            "SELECT c.epigrafe_gestion, SUM(a.haber - a.debe) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            "JOIN cuenta c ON c.codigo = a.cuenta WHERE c.grupo IN (6, 7) AND s.origen <> 'regularizacion' "
            f"AND (? IS NULL OR s.fecha >= ?) AND (? IS NULL OR s.fecha <= ?) {extra} GROUP BY 1",
            (desde, desde, hasta, hasta)).fetchall())
    t, nr = suma(""), suma("AND a.no_recurrente = 1")
    ebitda = sum(t.get(e, 0) for e in EXPLOTACION)
    ebit = ebitda + t.get("Amortizaciones", 0) + t.get("Otros resultados", 0)
    return ([(e, t.get(e, 0)) for e in EXPLOTACION]
            + [("EBITDA", ebitda), ("EBITDA ajustado (sin no recurrentes)", ebitda - sum(nr.get(e, 0) for e in EXPLOTACION)),
               ("Amortizaciones", t.get("Amortizaciones", 0)), ("Otros resultados", t.get("Otros resultados", 0)),
               ("EBIT", ebit), ("Resultado financiero", t.get("Resultado financiero", 0)),
               ("Impuestos", t.get("Impuestos", 0)),
               ("Resultado neto", ebit + t.get("Resultado financiero", 0) + t.get("Impuestos", 0))])


def pyg_mensual(con, anio: int) -> list[tuple[str, list[int], int]]:
    meses = [pyg_gestion(con, f"{anio}-{m:02d}-01", fin_de_mes(f"{anio}-{m:02d}")) for m in range(1, 13)]
    total = dict(pyg_gestion(con, f"{anio}-01-01", f"{anio}-12-31"))
    return [(linea, [dict(m)[linea] for m in meses], total[linea]) for linea, _ in meses[0]]


def balance(con, hasta: str | None = None) -> dict:
    """Balance de situación por masas. El resultado aún no regularizado va dentro del patrimonio neto."""
    masas = {k: [] for k in ("ANC", "AC", "PN", "PNC", "PC")}
    resultado = 0
    for codigo, nombre, _, debe, haber, _, _ in sumas_y_saldos(con, hasta, nivel=None):
        info, s = cuenta_info(con, codigo), debe - haber
        if s == 0:
            continue
        if info["grupo"] in (6, 7):
            resultado -= s
            continue
        masa = info["masa"] or "AC/PC"
        if masa == "AC/PC":
            masa = "AC" if s > 0 else "PC"
        masas[masa].append((codigo, nombre, s if masa in ("ANC", "AC") else -s))
    if resultado:
        masas["PN"].append(("129", "Resultado del ejercicio (pendiente de regularizar)", resultado))
    tot = {k: sum(x[2] for x in v) for k, v in masas.items()}
    return {"masas": masas, "totales": tot, "activo": tot["ANC"] + tot["AC"], "pn_pasivo": tot["PN"] + tot["PNC"] + tot["PC"]}


def flujo_caja(con, desde: str, hasta: str) -> tuple[int, int]:
    """(operativo, financiación) de la tesorería (57x) en el periodo. + entra, - sale."""
    fin = " OR ".join(f"b.cuenta LIKE '{p}%'" for p in FINANCIACION)
    op = fn = 0
    for importe, es_fin in con.execute(
            "SELECT SUM(a.debe - a.haber), EXISTS (SELECT 1 FROM apunte b WHERE b.asiento_id = a.asiento_id "
            f"AND b.cuenta NOT LIKE '57%' AND ({fin})) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            "WHERE a.cuenta LIKE '57%' AND s.fecha BETWEEN ? AND ? GROUP BY 2", (desde, hasta)).fetchall():
        fn, op = (fn + importe, op) if es_fin else (fn, op + importe)
    return op, fn


def ultimo_mes_con_datos(con) -> str:
    fila = con.execute("SELECT MAX(fecha) FROM asiento").fetchone()[0]
    return (fila or date.today().isoformat())[:7]


def kpis(con, mes: str) -> list[tuple[str, str, str]]:
    """(KPI, valor, explicación) a final de `mes`."""
    hasta, anio = fin_de_mes(mes), mes[:4]
    caja = saldo(con, "57", hasta)
    primero = (con.execute("SELECT MIN(fecha) FROM asiento").fetchone()[0] or hasta)[:7]
    quemado = [-flujo_caja(con, f"{m}-01", fin_de_mes(m))[0] for m in [mes] + _meses_previos(mes, 2) if m >= primero]
    burn = sum(quemado) // len(quemado) if quemado else 0
    ytd = dict(pyg_gestion(con, f"{anio}-01-01", hasta))
    mes_pyg = dict(pyg_gestion(con, f"{mes}-01", hasta))
    deuda = -sum(saldo(con, c, hasta) for c in DEUDA_FINANCIERA)
    hace_un_anio = f"{int(anio) - 1}{hasta[4:]}"
    ventas_12m = dict(pyg_gestion(con, hace_un_anio, hasta))["Ingresos"]
    compras_12m = -sum(v for k, v in pyg_gestion(con, hace_un_anio, hasta) if k in ("Aprovisionamientos", "Otros gastos de explotación"))
    iva = Decimal(config(con, "iva_general", "21")) / 100 + 1
    b = balance(con, hasta)
    pc = b["totales"]["PC"]
    def dias(saldo_, flujo):
        return f"{int(Decimal(saldo_) / (Decimal(flujo) * iva) * 365)} días" if flujo > 0 else "n.a."
    return [
        ("Tesorería", euros(caja), f"Saldo de las cuentas 57x a {hasta}"),
        ("Burn neto medio (3 meses)", euros(burn) if burn > 0 else "Genera caja", "Caja que consume la operación al mes, sin financiación"),
        ("Runway", f"{caja / burn:.1f} meses".replace(".", ",") if burn > 0 else "Sin límite", "Tesorería / burn medio"),
        ("Ingresos del mes", euros(mes_pyg["Ingresos"]), mes),
        ("Ingresos acumulados del año", euros(ytd["Ingresos"]), f"{anio} hasta {hasta}"),
        ("EBITDA acumulado del año", euros(ytd["EBITDA"]), f"Ajustado: {euros(ytd['EBITDA ajustado (sin no recurrentes)'])}"),
        ("Margen bruto", f"{(ytd['Ingresos'] + ytd['Aprovisionamientos']) / ytd['Ingresos']:.0%}" if ytd["Ingresos"] else "n.a.",
         "(Ingresos + aprovisionamientos) / ingresos, año en curso"),
        ("Deuda financiera neta", euros(deuda - caja), "Negativa = caja neta"),
        ("Periodo medio de cobro", dias(saldo(con, "43", hasta), ventas_12m), "Clientes / ventas con IVA de 12 meses × 365"),
        ("Periodo medio de pago", dias(-saldo(con, "40", hasta) - saldo(con, "41", hasta), compras_12m), "Proveedores / compras y gastos con IVA × 365"),
        ("Liquidez corriente", f"{b['totales']['AC'] / pc:.2f}".replace(".", ",") if pc > 0 else "n.a.", "Activo corriente / pasivo corriente"),
    ]


def _meses_previos(mes: str, n: int) -> list[str]:
    a, m = map(int, mes.split("-"))
    salida = []
    for _ in range(n):
        a, m = (a - 1, 12) if m == 1 else (a, m - 1)
        salida.append(f"{a}-{m:02d}")
    return salida


# ---------------------------------------------------------------- IVA, cierre y regularización

def regularizar_iva(con, anio: int, trimestre: int) -> int:
    """Lleva 472 y 477 a 4750 (a ingresar) o 4700 (a compensar o devolver) al final del trimestre."""
    _, hasta = trimestre_fechas(anio, trimestre)
    soportado, repercutido = saldo(con, "472", hasta), -saldo(con, "477", hasta)
    if not soportado and not repercutido:
        raise ErrorContable("No hay IVA que regularizar")
    dif = repercutido - soportado
    lineas = [{"cuenta": "477", "debe": repercutido}, {"cuenta": "472", "haber": soportado},
              {"cuenta": "4750", "haber": dif} if dif > 0 else {"cuenta": "4700", "debe": -dif}]
    return crear_asiento(con, hasta, f"Regularización IVA {trimestre}T {anio}", lineas, origen="automatico", aprobado_por="sistema")


def regularizar_ejercicio(con, anio: int) -> int:
    """Salda los grupos 6 y 7 del año contra la 129 (resultado del ejercicio)."""
    hasta = f"{anio}-12-31"
    if con.execute("SELECT 1 FROM asiento WHERE origen = 'regularizacion' AND fecha = ?", (hasta,)).fetchone():
        raise ErrorContable(f"El ejercicio {anio} ya está regularizado")
    lineas = []
    for codigo, _, _, debe, haber, _, _ in sumas_y_saldos(con, hasta, desde=f"{anio}-01-01"):
        if codigo[0] in "67" and debe != haber:
            lineas.append({"cuenta": codigo, "haber": debe - haber} if debe > haber else {"cuenta": codigo, "debe": haber - debe})
    if not lineas:
        raise ErrorContable(f"No hay ingresos ni gastos en {anio}")
    resultado = sum(l.get("debe", 0) - l.get("haber", 0) for l in lineas)
    lineas.append({"cuenta": "129", "haber": resultado} if resultado > 0 else {"cuenta": "129", "debe": -resultado})
    return crear_asiento(con, hasta, f"Regularización del ejercicio {anio}", lineas, origen="regularizacion", aprobado_por="sistema")


def registrar_saldo_externo(con, cuentas: str, fecha, saldo_: int, fuente: str, tolerancia: int = 1) -> int:
    cuentas = cuentas.replace(" ", "")
    for c in cuentas.split("+"):
        if not cuenta_info(con, c):
            raise ErrorContable(f"La cuenta {c} no existe")
    if not fuente.strip():
        raise ErrorContable("Indica la fuente del saldo (extracto, modelo 303, TGSS...)")
    fecha = fecha_iso(fecha)
    repetido = con.execute("SELECT id FROM saldo_externo WHERE cuentas = ? AND fecha = ? AND saldo = ?", (cuentas, fecha, saldo_)).fetchone()
    if repetido:  # p. ej. reimportar el mismo extracto
        return repetido[0]
    with con:
        return con.execute("INSERT INTO saldo_externo (cuentas, fecha, saldo, fuente, tolerancia) VALUES (?, ?, ?, ?, ?)",
                           (cuentas, fecha_iso(fecha), saldo_, fuente.strip(), tolerancia)).lastrowid


def controles(con, hasta: str | None = None) -> list[tuple[str, str, str, str]]:
    """(id, control, OK | AVISO | FALLA, detalle). FALLA impide cerrar el mes. Catálogo en data/controles_cuadre.csv."""
    hasta = hasta or date.today().isoformat()
    mes = hasta[:7]
    r = []
    malos = con.execute("SELECT asiento_id, debe, haber FROM v_asientos_descuadrados").fetchall()
    r.append(("C01", "Cada asiento cuadra (debe = haber)", "FALLA" if malos else "OK",
              "; ".join(f"asiento {a}: debe {euros(d)} / haber {euros(h)}" for a, d, h in malos)))
    bss = sumas_y_saldos(con, hasta)
    td, th = sum(f[3] for f in bss), sum(f[4] for f in bss)
    sd, sa = sum(f[5] for f in bss), sum(f[6] for f in bss)
    r.append(("C02", "Sumas debe = sumas haber", "OK" if td == th else "FALLA", f"{euros(td)} / {euros(th)}"))
    r.append(("C03", "Saldos deudores = saldos acreedores", "OK" if sd == sa else "FALLA", f"{euros(sd)} / {euros(sa)}"))
    b = balance(con, hasta)
    r.append(("C03b", "Activo = patrimonio neto + pasivo", "OK" if b["activo"] == b["pn_pasivo"] else "FALLA",
              f"{euros(b['activo'])} / {euros(b['pn_pasivo'])}"))
    raros = [f"{c} {n}: saldo {'acreedor' if s_a else 'deudor'} {euros(s_a or s_d)}"
             for c, n, nat, _, _, s_d, s_a in bss if (nat == "D" and s_a) or (nat == "A" and s_d)]
    r.append(("C04", "Saldos con el signo de su naturaleza", "AVISO" if raros else "OK", "; ".join(raros)))
    s555 = saldo(con, "555", hasta)
    r.append(("C05", "Partidas pendientes de aplicación (555) a cero", "AVISO" if s555 else "OK",
              f"quedan {euros(s555)} por identificar" if s555 else ""))
    for e in con.execute("SELECT * FROM saldo_externo WHERE id IN (SELECT MAX(id) FROM saldo_externo WHERE fecha <= ? "
                         "GROUP BY cuentas, fuente) ORDER BY fecha", (hasta,)).fetchall():
        contable = sum(saldo(con, c, e["fecha"]) for c in e["cuentas"].split("+"))
        r.append(("C06", f"{e['fuente']} [{e['cuentas']} a {e['fecha']}]",
                  "OK" if abs(contable - e["saldo"]) <= e["tolerancia"] else "FALLA",
                  f"contabilidad {euros(contable)} / fuente {euros(e['saldo'])}"))
    huecos = []
    numeros: dict[str, list[int]] = {}
    for (sn,) in con.execute("SELECT serie_numero FROM documento WHERE tipo = 'emitida' AND fecha <= ?", (hasta,)):
        m = re.fullmatch(r"(.*?)(\d+)", sn)
        if m:
            numeros.setdefault(m.group(1), []).append(int(m.group(2)))
    for serie, ns in numeros.items():
        falta = sorted(set(range(min(ns), max(ns) + 1)) - set(ns))
        if falta:
            huecos.append(f"serie '{serie}': faltan {', '.join(map(str, falta[:10]))}")
    if numeros:
        r.append(("C09", "Numeración de facturas emitidas sin huecos", "AVISO" if huecos else "OK", "; ".join(huecos)))
    hasta_mes = mes if hasta == fin_de_mes(mes) else (_meses_previos(mes, 1)[0])
    faltan = []
    for a in con.execute("SELECT * FROM activo WHERE substr(fecha_alta, 1, 7) <= ?", (hasta_mes,)).fetchall():
        hechos = {m for (m,) in con.execute("SELECT mes FROM amortizacion WHERE activo_id = ?", (a["id"],))}
        acumulado = con.execute("SELECT COALESCE(SUM(importe), 0) FROM amortizacion WHERE activo_id = ?", (a["id"],)).fetchone()[0]
        pendientes = [m for m in meses_entre(a["fecha_alta"], fin_de_mes(hasta_mes)) if m not in hechos]
        if pendientes and acumulado < a["coste"]:
            faltan.append(f"{a['descripcion']}: {', '.join(pendientes[:6])}")
    if con.execute("SELECT 1 FROM activo").fetchone():
        r.append(("C11", "Amortizaciones al día", "AVISO" if faltan else "OK", "; ".join(faltan)))
    sin = con.execute("SELECT COUNT(*), COALESCE(SUM(importe), 0) FROM movimiento_banco WHERE asiento_id IS NULL AND fecha <= ?",
                      (hasta,)).fetchone()
    r.append(("C13", "Movimientos bancarios contabilizados", "AVISO" if sin[0] else "OK",
              f"{sin[0]} sin contabilizar ({euros(sin[1])})" if sin[0] else ""))
    for cid, tipo, prefijos, nombre in (("C15", "emitida", ("43",), "Clientes (43x) = facturas emitidas pendientes"),
                                        ("C16", "recibida", ("40", "41"), "Proveedores (40x+41x) = facturas recibidas pendientes")):
        if con.execute("SELECT 1 FROM documento WHERE tipo = ?", (tipo,)).fetchone():
            libro = sum(saldo(con, p, hasta) for p in prefijos) * (1 if tipo == "emitida" else -1)
            pend = sum(d["total"] for d in facturas_pendientes(con, tipo, hasta))
            r.append((cid, nombre, "OK" if libro == pend else "AVISO",
                      f"libro {euros(libro)} / facturas {euros(pend)}" + ("" if libro == pend else " (saldos sin factura: anticipos, saldos iniciales...)")))
    activos = con.execute("SELECT cuenta, SUM(coste) FROM activo WHERE fecha_alta <= ? GROUP BY cuenta", (hasta,)).fetchall()
    if activos:
        dif = [f"{c}: libro {euros(saldo(con, c, hasta))} / registro {euros(s)}" for c, s in activos if saldo(con, c, hasta) != s]
        r.append(("C21", "Inmovilizado = registro de activos", "AVISO" if dif else "OK", "; ".join(dif)))
    neto = dict(pyg_gestion(con, None, hasta))["Resultado neto"]
    reg = -con.execute("SELECT COALESCE(SUM(a.debe - a.haber), 0) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
                       "JOIN cuenta c ON c.codigo = a.cuenta WHERE c.grupo IN (6, 7) AND s.origen <> 'regularizacion' "
                       "AND s.fecha <= ?", (hasta,)).fetchone()[0]
    r.append(("C30", "PyG de gestión = PyG contable", "OK" if neto == reg else "FALLA", f"{euros(neto)} / {euros(reg)}"))
    anio = int(hasta[:4])
    previos = [a for (a,) in con.execute("SELECT DISTINCT CAST(substr(fecha, 1, 4) AS INTEGER) FROM asiento WHERE fecha < ?",
                                         (f"{anio}-01-01",))]
    sin_reg = [a for a in previos if any(f[0][0] in "67" and f[3] != f[4] for f in sumas_y_saldos(con, f"{a}-12-31", desde=f"{a}-01-01"))
               and not con.execute("SELECT 1 FROM asiento WHERE origen = 'regularizacion' AND fecha = ?", (f"{a}-12-31",)).fetchone()]
    if previos:
        r.append(("C31", "Ejercicios anteriores regularizados", "AVISO" if sin_reg else "OK",
                  f"sin regularizar: {', '.join(map(str, sin_reg))}" if sin_reg else ""))
    return r


def cerrar_mes(con, mes: str, usuario: str = "usuario") -> list:
    """Amortiza el mes si falta, pasa los controles y, si nada FALLA, bloquea el mes."""
    if mes_cerrado(con, f"{mes}-01"):
        raise ErrorContable(f"El mes {mes} ya está cerrado")
    amortizar_mes(con, mes)
    resultado = controles(con, fin_de_mes(mes))
    fallos = [f"{cid} {n}" for cid, n, e, _ in resultado if e == "FALLA"]
    if fallos:
        raise ErrorContable(f"No se puede cerrar {mes}: {'; '.join(fallos)}")
    with con:
        con.execute("INSERT INTO periodo (mes, cerrado_en, cerrado_por) VALUES (?, datetime('now'), ?) "
                    "ON CONFLICT (mes) DO UPDATE SET cerrado_en = excluded.cerrado_en, cerrado_por = excluded.cerrado_por",
                    (mes, usuario))
        _evento(con, "cierre", f"Mes {mes} cerrado por {usuario}")
    return resultado


def reabrir_mes(con, mes: str, motivo: str, usuario: str = "usuario") -> None:
    if not motivo.strip():
        raise ErrorContable("Indica el motivo de la reapertura (queda en el rastro de auditoría)")
    with con:
        if not con.execute("UPDATE periodo SET cerrado_en = NULL WHERE mes = ? AND cerrado_en IS NOT NULL", (mes,)).rowcount:
            raise ErrorContable(f"El mes {mes} no está cerrado")
        _evento(con, "reapertura", f"Mes {mes} reabierto por {usuario}: {motivo.strip()}")


# ---------------------------------------------------------------- fiscal

def borrador_303(con, anio: int, trimestre: int) -> dict:
    desde, hasta = trimestre_fechas(anio, trimestre)
    q = ("SELECT tipo_iva, SUM(base), SUM(cuota_iva) FROM documento WHERE fecha BETWEEN ? AND ? AND {} GROUP BY tipo_iva "
         "ORDER BY tipo_iva DESC")
    devengado = [(f"Régimen general {t:g} %", b, c) for t, b, c in con.execute(q.format("tipo = 'emitida'"), (desde, hasta))]
    devengado += [(f"Inversión del sujeto pasivo {t:g} %", b, c) for t, b, c in
                  con.execute(q.format("tipo = 'recibida' AND isp = 1"), (desde, hasta))]
    deducible = [("Bienes de inversión" if inv else "Operaciones corrientes", b, c) for inv, b, c in con.execute(
        "SELECT substr(cuenta, 1, 1) = '2', SUM(base), SUM(cuota_iva) FROM documento WHERE tipo = 'recibida' "
        "AND fecha BETWEEN ? AND ? GROUP BY 1", (desde, hasta))]
    total_dev, total_ded = sum(c for _, _, c in devengado), sum(c for _, _, c in deducible)
    # Repercutido - soportado según el libro (sin el asiento de regularización): (477 haber-debe) - (472 debe-haber).
    libro = con.execute(
        "SELECT COALESCE(SUM(a.haber - a.debe), 0) "
        "FROM apunte a JOIN asiento s ON s.id = a.asiento_id WHERE (a.cuenta LIKE '477%' OR a.cuenta LIKE '472%') "
        "AND s.fecha BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM apunte b WHERE b.asiento_id = s.id "
        "AND (b.cuenta LIKE '4750%' OR b.cuenta LIKE '4700%'))", (desde, hasta)).fetchone()[0]
    return {"periodo": f"{trimestre}T {anio}", "devengado": devengado, "deducible": deducible, "total_devengado": total_dev,
            "total_deducible": total_ded, "resultado": total_dev - total_ded, "segun_libro": libro}


def borrador_111(con, anio: int, trimestre: int) -> dict:
    desde, hasta = trimestre_fechas(anio, trimestre)
    pro = con.execute("SELECT COUNT(DISTINCT tercero_id), COALESCE(SUM(base), 0), COALESCE(SUM(retencion), 0) FROM documento "
                      "WHERE tipo = 'recibida' AND retencion > 0 AND fecha BETWEEN ? AND ?", (desde, hasta)).fetchone()
    # Retenciones practicadas en el trimestre: todo lo abonado a la 4751 (los pagos a Hacienda van al debe).
    ret_haber = con.execute("SELECT COALESCE(SUM(a.haber), 0) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
                            "WHERE a.cuenta LIKE '4751%' AND s.origen <> 'anulacion' AND s.fecha BETWEEN ? AND ?",
                            (desde, hasta)).fetchone()[0]
    trabajo = ret_haber - pro[2]
    return {"periodo": f"{trimestre}T {anio}",
            "lineas": [("Rendimientos del trabajo", "—", saldo(con, "640", hasta, desde), trabajo),
                       ("Actividades profesionales", pro[0], pro[1], pro[2])],
            "total": ret_haber}


def modelo_347(con, anio: int, umbral: int = 300506) -> list[dict]:
    """Terceros nacionales con más de 3.005,06 € en el año (sin ISP ni operaciones con retención: van a 349 y 190)."""
    salida = []
    for t in con.execute(
            "SELECT t.id, t.nombre, t.nif, d.tipo, SUM(d.total) AS total FROM documento d JOIN tercero t ON t.id = d.tercero_id "
            "WHERE t.extranjero = 0 AND d.isp = 0 AND d.retencion = 0 AND substr(d.fecha, 1, 4) = ? "
            "GROUP BY t.id, d.tipo HAVING SUM(d.total) > ? ORDER BY total DESC", (str(anio), umbral)).fetchall():
        trimestres = [con.execute("SELECT COALESCE(SUM(total), 0) FROM documento WHERE tercero_id = ? AND tipo = ? "
                                  "AND isp = 0 AND retencion = 0 AND fecha BETWEEN ? AND ?",
                                  (t["id"], t["tipo"], *trimestre_fechas(anio, q))).fetchone()[0] for q in range(1, 5)]
        salida.append({"nif": t["nif"], "nombre": t["nombre"], "clave": "B (ventas)" if t["tipo"] == "emitida" else "A (compras)",
                       "total": t["total"], "trimestres": trimestres})
    return salida


def proximos_vencimientos(hoy: date | None = None, dias: int = 60, emergente: bool = False) -> list[tuple[str, str]]:
    """Calendario fiscal y mercantil de una SL con ejercicio natural. Fin de semana -> lunes siguiente."""
    hoy = hoy or date.today()
    fijos = [(1, 30, "303, 111, 115 y 349 del 4T · resúmenes anuales 390, 190 y 180"),
             (2, 0, "347 operaciones con terceros del año anterior"),
             (3, 31, "Formular las cuentas anuales (3 meses tras el cierre)"),
             (4, 20, "303, 111, 115 y 349 del 1T · 202 primer pago fraccionado"),
             (4, 30, "Legalizar los libros en el Registro Mercantil (4 meses tras el cierre)"),
             (6, 30, "Aprobar las cuentas anuales en junta (6 meses tras el cierre)"),
             (7, 20, "303, 111, 115 y 349 del 2T"),
             (7, 25, "200 impuesto sobre sociedades"),
             (7, 30, "Depositar las cuentas anuales (1 mes tras aprobarlas)"),
             (10, 20, "303, 111, 115 y 349 del 3T · 202 segundo pago fraccionado"),
             (12, 20, "202 tercer pago fraccionado")]
    salida = []
    for anio in (hoy.year, hoy.year + 1):
        eventos = [(date(anio, m, d or calendar.monthrange(anio, m)[1]), t) for m, d, t in fijos]
        eventos += [(date(anio, m, calendar.monthrange(anio, m)[1]), "Seguridad Social: cotizaciones del mes anterior")
                    for m in range(1, 13)]
        for f, texto in eventos:
            while f.weekday() >= 5:
                f += timedelta(days=1)
            if emergente and "202" in texto:
                texto += " (revisar exoneración de empresa emergente)"
            if hoy <= f <= hoy + timedelta(days=dias):
                salida.append((f.isoformat(), texto))
    return sorted(salida)


# ---------------------------------------------------------------- data room

def _csv(cabecera: list[str], filas) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cabecera)
    w.writerows(filas)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM + ';' + coma decimal: se abre bien en Excel en español


def exportar_dataroom(con, hasta: str | None = None) -> bytes:
    """Zip con lo que pide la fase I de una due diligence financiera (ver data/checklist_dd.csv)."""
    hasta = hasta or date.today().isoformat()
    primero = con.execute("SELECT MIN(fecha) FROM asiento").fetchone()[0] or hasta
    f = {}
    f["01_libro_diario.csv"] = _csv(
        ["asiento", "fecha", "concepto", "origen", "cuenta", "nombre_cuenta", "debe", "haber", *DIMENSIONES, "no_recurrente"],
        [(p["asiento_id"], p["fecha"], p["concepto"], p["origen"], p["cuenta"], p["nombre"], euros(p["debe"]), euros(p["haber"]),
          *(p[d] or "" for d in DIMENSIONES), p["no_recurrente"]) for p in con.execute(
            "SELECT a.*, s.fecha, s.concepto, s.origen, c.nombre FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            "JOIN cuenta c ON c.codigo = a.cuenta WHERE s.fecha <= ? ORDER BY s.fecha, s.id, a.id", (hasta,))])
    f["02_sumas_y_saldos_mensual.csv"] = _csv(
        ["mes", "cuenta", "nombre", "sumas_debe", "sumas_haber", "saldo_deudor", "saldo_acreedor"],
        [(m, c, n, euros(d), euros(h), euros(sd), euros(sa)) for m in meses_entre(primero, hasta)
         for c, n, _, d, h, sd, sa in sumas_y_saldos(con, min(fin_de_mes(m), hasta))])
    anios = range(int(primero[:4]), int(hasta[:4]) + 1)
    f["03_pyg_gestion_mensual.csv"] = _csv(
        ["anio", "linea", *[f"{m:02d}" for m in range(1, 13)], "total"],
        [(a, linea, *map(euros, valores), euros(total)) for a in anios for linea, valores, total in pyg_mensual(con, a)])
    b = balance(con, hasta)
    f["04_balance.csv"] = _csv(["masa", "cuenta", "nombre", "importe"],
                               [(masa, c, n, euros(i)) for masa, filas in b["masas"].items() for c, n, i in filas])
    for nombre, tipo in (("05_antiguedad_clientes.csv", "emitida"), ("06_antiguedad_proveedores.csv", "recibida")):
        f[nombre] = _csv(["tercero", "factura", "fecha", "vencimiento", "total", "dias_vencida", "tramo"],
                         [(x["tercero"], x["factura"], x["fecha"], x["vencimiento"], euros(x["total"]), x["dias"], x["tramo"])
                          for x in antiguedad(con, tipo, hasta)])
    f["07_partes_vinculadas.csv"] = _csv(
        ["tercero", "nif", "tipo", "subcuenta", "movimientos_debe", "movimientos_haber", "saldo"],
        [(t["nombre"], t["nif"], t["tipo"], t["subcuenta"], euros(t["d"]), euros(t["h"]), euros(t["d"] - t["h"]))
         for t in con.execute("SELECT t.*, COALESCE(SUM(x.debe), 0) AS d, COALESCE(SUM(x.haber), 0) AS h FROM tercero t "
                              "LEFT JOIN (SELECT a.cuenta, a.debe, a.haber FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
                              "WHERE s.fecha <= ?) x ON x.cuenta = t.subcuenta WHERE t.vinculado = 1 GROUP BY t.id", (hasta,))])
    f["08_ebitda_ajustado.csv"] = _csv(
        ["fecha", "asiento", "concepto", "cuenta", "importe_ajustado"],
        [(p["fecha"], p["asiento_id"], p["concepto"], p["cuenta"], euros(p["debe"] - p["haber"])) for p in con.execute(
            "SELECT a.*, s.fecha, s.concepto FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            "WHERE a.no_recurrente = 1 AND s.fecha <= ? ORDER BY s.fecha", (hasta,))])
    f["09_inmovilizado.csv"] = _csv(
        ["activo", "cuenta", "fecha_alta", "coste", "vida_meses", "amortizacion_acumulada", "valor_neto"],
        [(a["descripcion"], a["cuenta"], a["fecha_alta"], euros(a["coste"]), a["vida_meses"], euros(a["acum"]),
          euros(a["coste"] - a["acum"])) for a in con.execute(
            "SELECT a.*, COALESCE((SELECT SUM(importe) FROM amortizacion m WHERE m.activo_id = a.id AND m.mes <= ?), 0) AS acum "
            "FROM activo a WHERE fecha_alta <= ?", (hasta[:7], hasta))])
    f["10_controles.csv"] = _csv(["id", "control", "estado", "detalle"], controles(con, hasta))
    f["11_conciliacion_bancaria_pendiente.csv"] = _csv(
        ["cuenta", "fecha", "concepto", "importe"],
        [(m["cuenta_banco"], m["fecha"], m["concepto"], euros(m["importe"])) for m in con.execute(
            "SELECT * FROM movimiento_banco WHERE asiento_id IS NULL AND fecha <= ? ORDER BY fecha", (hasta,))])
    leeme = (f"Data room financiero de {config(con, 'empresa', 'la empresa')} a {hasta}\n"
             f"Generado por Cuentas Claras el {date.today().isoformat()}. Importes en euros (coma decimal, separador ';').\n\n"
             + "\n".join(f"- {n}" for n in f) + "\n\nLos controles de cuadre (10) deben estar en OK o con el aviso explicado.\n")
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_LEEME.txt", leeme)
        for nombre, datos in f.items():
            z.writestr(nombre, datos)
    return salida.getvalue()
