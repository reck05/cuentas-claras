"""Bancos de Cuentas Claras.

Importa extractos (Norma 43 o CSV), propone la contrapartida de cada movimiento en cascada
(1. conciliación con facturas abiertas, 2. reglas, 3. IA con Claude) y contabiliza lo que
una persona aprueba. La IA nunca escribe en el libro: solo propone.
"""
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter

import motor
from motor import ErrorContable, centimos, euros

MODELO = "claude-opus-5-5"
SISTEMA = """Eres el contable de una startup española que aplica el PGC de PYMES.
Para cada movimiento bancario propones la cuenta contrapartida: la otra pata del asiento (el banco ya es una).
- Usa solo códigos del plan de cuentas que se te da; si hay subcuenta del tercero, usa la subcuenta.
- Si el movimiento paga o cobra una factura ya registrada, la contrapartida es el tercero (400, 410, 430), no el gasto.
- Si falta información, baja la confianza y di qué falta. No inventes.
- confianza de 0 a 1: 0.95 o más solo si cualquier contable propondría lo mismo.
- requiere_documento = true si para contabilizarlo bien hace falta la factura o el contrato (IVA, retenciones, principal e intereses...).

Plan de cuentas:
"""
ESQUEMA = {
    "type": "object",
    "properties": {"movimientos": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "cuenta": {"type": "string"}, "confianza": {"type": "number"},
                       "requiere_documento": {"type": "boolean"}, "motivo": {"type": "string"}},
        "required": ["id", "cuenta", "confianza", "requiere_documento", "motivo"],
        "additionalProperties": False}}},
    "required": ["movimientos"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- lectura de extractos

def decodificar(datos: bytes) -> str:
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            pass
    return datos.decode("latin-1")


def _aammdd(s: str) -> str:
    return f"20{s[0:2]}-{s[2:4]}-{s[4:6]}"


def leer_norma43(texto: str) -> list[dict]:
    """Cuaderno 43 de la AEB (registros 11, 22, 23, 33, 88). Comprueba los totales que trae el propio fichero."""
    cuentas, actual, ultimo = [], None, None
    for n, linea in enumerate(texto.splitlines(), 1):
        if not linea.strip():
            continue
        linea = linea.ljust(80)
        registro = linea[:2]
        if registro == "11":
            actual = {"cuenta": linea[2:20], "desde": _aammdd(linea[20:26]), "hasta": _aammdd(linea[26:32]),
                      "saldo_inicial": (-1 if linea[32] == "1" else 1) * int(linea[33:47]), "movimientos": []}
            cuentas.append(actual)
        elif registro == "22":
            if actual is None:
                raise ErrorContable(f"Norma 43, línea {n}: movimiento antes de la cabecera de cuenta")
            ultimo = {"fecha": _aammdd(linea[10:16]), "importe": (-1 if linea[27] == "1" else 1) * int(linea[28:42]),
                      "concepto": "", "referencia": f"{linea[52:64].strip()} {linea[64:80].strip()}".strip()}
            actual["movimientos"].append(ultimo)
        elif registro == "23" and ultimo is not None:
            # Los dos campos de 38 posiciones son un mismo texto cortado: se pegan tal cual y se compactan los espacios.
            texto_23 = re.sub(r"\s+", " ", linea[4:80]).strip()
            ultimo["concepto"] = f"{ultimo['concepto']} {texto_23}".strip()
        elif registro == "33" and actual is not None:
            movs = actual["movimientos"]
            debe, haber = int(linea[25:39]), int(linea[44:58])
            actual["saldo_final"] = (-1 if linea[58] == "1" else 1) * int(linea[59:73])
            if (sum(-m["importe"] for m in movs if m["importe"] < 0) != debe or sum(m["importe"] for m in movs if m["importe"] > 0) != haber
                    or actual["saldo_inicial"] + sum(m["importe"] for m in movs) != actual["saldo_final"]):
                raise ErrorContable(f"Norma 43: los totales de la cuenta {actual['cuenta']} no cuadran: el fichero está incompleto")
        elif registro not in ("23", "88"):
            raise ErrorContable(f"Norma 43, línea {n}: registro desconocido '{registro}'. ¿Es un fichero Norma 43?")
    if not cuentas:
        raise ErrorContable("El fichero no tiene ninguna cuenta Norma 43")
    for c in cuentas:
        if "saldo_final" not in c:
            raise ErrorContable(f"Norma 43: falta el registro final (33) de la cuenta {c['cuenta']}")
        for m in c["movimientos"]:
            m["concepto"] = m["concepto"] or m["referencia"] or "(sin concepto)"
    return cuentas


def leer_csv_banco(texto: str) -> list[dict]:
    """CSV exportado de la banca online: busca la fila de cabecera con 'fecha' y 'concepto'/'descripción'
    y una columna 'importe' (o 'cargo'/'debe' y 'abono'/'haber')."""
    lineas = texto.lstrip("﻿").splitlines()
    inicio = next((i for i, l in enumerate(lineas) if "fecha" in l.lower() and re.search(r"concepto|descrip|detalle", l.lower())), None)
    if inicio is None:
        raise ErrorContable("No encuentro la cabecera del CSV (columnas 'fecha' y 'concepto' o 'descripción')")
    cab = lineas[inicio]
    delim = max(";,\t", key=cab.count)
    filas = list(csv.reader(io.StringIO("\n".join(lineas[inicio:])), delimiter=delim))
    nombres = [c.strip().lower() for c in filas[0]]

    def col(*claves, evitar=()):
        return next((i for i, c in enumerate(nombres) if any(k in c for k in claves) and not any(e in c for e in evitar)), None)
    i_fecha, i_concepto = col("fecha", evitar=("valor",)), col("concepto", "descrip", "detalle")
    i_importe, i_cargo, i_abono = col("importe", "cantidad"), col("cargo", "debe"), col("abono", "haber")
    if i_fecha is None or (i_importe is None and (i_cargo is None or i_abono is None)):
        raise ErrorContable("El CSV necesita fecha, concepto e importe (o cargo y abono)")
    salida = []
    for f in filas[1:]:
        if not any(x.strip() for x in f):
            continue
        if i_importe is not None:
            importe = centimos(f[i_importe])
        else:
            importe = centimos(f[i_abono]) - abs(centimos(f[i_cargo]))
        if importe:
            salida.append({"fecha": motor.fecha_iso(f[i_fecha]), "concepto": f[i_concepto].strip() if i_concepto is not None else "",
                           "importe": importe})
    return salida


def importar_extracto(con, nombre_fichero: str, datos: bytes, cuenta_banco: str = "572") -> dict:
    """Importa un Norma 43 o un CSV. Si es Norma 43 registra además su saldo final como control externo."""
    texto = decodificar(datos)
    es_n43 = texto.lstrip()[:2] == "11"
    if es_n43:
        cuentas = leer_norma43(texto)
        if len(cuentas) > 1:
            raise ErrorContable("El fichero trae varias cuentas: descarga e importa un fichero por cuenta")
        movimientos = cuentas[0]["movimientos"]
    else:
        movimientos = leer_csv_banco(texto)
    nuevos, repetidos = importar_movimientos(con, cuenta_banco, movimientos, nombre_fichero)
    if es_n43:
        c = cuentas[0]
        motor.registrar_saldo_externo(con, cuenta_banco, c["hasta"], c["saldo_final"], f"Extracto Norma 43 {nombre_fichero}")
    return {"nuevos": nuevos, "repetidos": repetidos, "formato": "Norma 43" if es_n43 else "CSV"}


def importar_movimientos(con, cuenta_banco: str, movimientos: list[dict], fichero: str = "") -> tuple[int, int]:
    if not cuenta_banco.startswith("57") or not motor.existe_cuenta(con, cuenta_banco):
        raise ErrorContable(f"{cuenta_banco} no es una cuenta de tesorería (57x) del plan")
    vistos, nuevos = Counter(), 0
    with con:
        for m in movimientos:
            clave = (cuenta_banco, m["fecha"], m["concepto"], m["importe"])
            vistos[clave] += 1  # dos comisiones iguales el mismo día son dos movimientos; reimportar el fichero no
            huella = hashlib.sha1(repr((*clave, vistos[clave])).encode()).hexdigest()
            nuevos += con.execute("INSERT OR IGNORE INTO movimiento_banco (cuenta_banco, fecha, concepto, importe, huella, fichero) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (*clave, huella, fichero)).rowcount
    return nuevos, len(movimientos) - nuevos


# ---------------------------------------------------------------- propuesta en cascada

def _conciliar(con, m, usados: set) -> dict | None:
    """1) ¿Paga o cobra una factura abierta? Importe exacto, sentido correcto y fecha no anterior a la factura."""
    tipo = "emitida" if m["importe"] > 0 else "recibida"
    candidatas = [d for d in motor.facturas_pendientes(con, tipo, m["fecha"])
                  if d["total"] == abs(m["importe"]) and d["id"] not in usados]
    if not candidatas:
        return None
    concepto = re.sub(r"[^A-Z0-9]", " ", m["concepto"].upper())

    def puntos(d):
        palabras = [p for p in re.sub(r"[^A-Z0-9]", " ", d["nombre"].upper()).split() if len(p) >= 4]
        numero = re.sub(r"[^A-Z0-9]", " ", d["serie_numero"].upper()).strip()
        return 2 * (numero in concepto) + any(p in concepto.split() for p in palabras) + (d["nif"] in concepto)
    candidatas.sort(key=lambda d: (-puntos(d), d["vencimiento"]))
    mejor = candidatas[0]
    unica = len(candidatas) == 1 or puntos(mejor) > puntos(candidatas[1])
    seguro = unica and puntos(mejor) > 0
    usados.add(mejor["id"])
    return {"cuenta": mejor["subcuenta"], "origen": "conciliacion", "confianza": 0.99 if seguro else 0.7,
            "revisar": not seguro, "documento": mejor["id"],
            "nota": f"Factura {mejor['serie_numero']} de {mejor['nombre']}"
                    + ("" if seguro else " (coincide el importe; compruébalo)" if unica else " (varias facturas con este importe: elige)")}


def _por_reglas(m, reglas) -> dict | None:
    """2) Primera regla que encaja (orden de la tabla)."""
    sentido = "abono" if m["importe"] > 0 else "cargo"
    for r in reglas:
        if r["sentido"] in (sentido, "ambos") and re.search(r["patron"], m["concepto"], re.IGNORECASE):
            return {"cuenta": r["cuenta"], "origen": "regla", "confianza": 1.0, "revisar": bool(r["revisar"]),
                    "documento": None, "nota": r["nota"] or ""}
    return None


def _seudonimizar(texto: str) -> str:
    """No mandar IBAN ni números largos (cuentas, DNI) al proveedor del modelo."""
    texto = re.sub(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b", "[IBAN]", texto)
    return re.sub(r"\d{8,}", "[NUM]", texto)


def por_ia(pendientes: list, plan: list, ejemplos: list, cliente=None) -> dict[str, dict]:
    """3) Claude propone cuenta, confianza y motivo. Salida con esquema JSON fijo."""
    if cliente is None:
        import anthropic  # solo hace falta para la IA
        cliente = anthropic.Anthropic()
    cuentas = "\n".join(f"{c['codigo']} {c['nombre']}" for c in plan)
    hist = "\n".join(f"{_seudonimizar(e['concepto'])} | {euros(e['importe'])} -> {e['cuenta']}" for e in ejemplos) or "(ninguno)"
    movs = "\n".join(f"{m['id']} | {m['fecha']} | {_seudonimizar(m['concepto'])} | {euros(m['importe'])}" for m in pendientes)
    resp = cliente.beta.messages.create(
        model=MODELO,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # si el modelo declina, la API reintenta con el modelo recomendado
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
        system=[{"type": "text", "text": SISTEMA + cuentas, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Movimientos ya contabilizados por la empresa:\n{hist}\n\nClasifica:\n{movs}"}],
    )
    if resp.stop_reason != "end_turn":  # refusal o max_tokens: todo a revisión manual
        print(f"La IA no terminó ({resp.stop_reason}); los movimientos quedan pendientes.", file=sys.stderr)
        return {}
    texto = next(b.text for b in resp.content if b.type == "text")
    return {str(r["id"]): r for r in json.loads(texto)["movimientos"]}


def ejemplos_validados(con, n: int = 40) -> list[dict]:
    """Movimientos ya contabilizados y su contrapartida: el few-shot de la IA y la memoria de la empresa."""
    return [dict(r) for r in con.execute(
        "SELECT m.concepto, m.importe, (SELECT cuenta FROM apunte p WHERE p.asiento_id = m.asiento_id "
        "AND p.cuenta NOT LIKE '57%' LIMIT 1) AS cuenta FROM movimiento_banco m WHERE m.asiento_id IS NOT NULL "
        "ORDER BY m.id DESC LIMIT ?", (n,))]


def proponer(con, usar_ia: bool = False, umbral: float = 0.9, cliente=None) -> Counter:
    """Calcula la propuesta de todos los movimientos sin contabilizar."""
    reglas = con.execute("SELECT * FROM regla ORDER BY orden").fetchall()
    pendientes = con.execute("SELECT * FROM movimiento_banco WHERE asiento_id IS NULL ORDER BY fecha, id").fetchall()
    usados, resto, cuenta = set(), [], Counter()
    with con:
        for m in pendientes:
            p = _conciliar(con, m, usados) or _por_reglas(m, reglas)
            if p:
                _guardar(con, m["id"], p)
                cuenta[p["origen"]] += 1
            else:
                resto.append(m)
        if resto and usar_ia:
            plan = [dict(c) for c in con.execute("SELECT codigo, nombre FROM cuenta ORDER BY codigo")]
            propuestas = por_ia([dict(m) | {"id": str(m["id"])} for m in resto], plan, ejemplos_validados(con), cliente)
            for m in list(resto):
                p = propuestas.get(str(m["id"]))
                # La IA solo propone: una cuenta fuera del plan (o el propio banco) se descarta y queda pendiente.
                if p and motor.existe_cuenta(con, p["cuenta"]) and p["cuenta"] != m["cuenta_banco"]:
                    dudoso = p["confianza"] < umbral or p["requiere_documento"]
                    _guardar(con, m["id"], {"cuenta": p["cuenta"], "origen": "ia", "confianza": round(p["confianza"], 2),
                                            "revisar": dudoso, "documento": None, "nota": p["motivo"]})
                    cuenta["ia"] += 1
                    resto.remove(m)
        for m in resto:
            _guardar(con, m["id"], {"cuenta": None, "origen": "pendiente", "confianza": None, "revisar": True, "documento": None,
                                    "nota": "Sin regla ni factura: elige la cuenta" + ("" if usar_ia else " o usa la IA")})
            cuenta["pendiente"] += 1
    return cuenta


def _guardar(con, mid: int, p: dict) -> None:
    con.execute("UPDATE movimiento_banco SET propuesta_cuenta = ?, propuesta_origen = ?, propuesta_confianza = ?, "
                "propuesta_revisar = ?, propuesta_nota = ?, propuesta_documento = ? WHERE id = ?",
                (p["cuenta"], p["origen"], p["confianza"], int(bool(p["revisar"])), p["nota"], p["documento"], mid))


# ---------------------------------------------------------------- aprobación

def crear_regla(con, texto: str, cuenta: str, sentido: str = "ambos", nota: str = "", revisar: bool = False) -> int:
    """Regla de la empresa: 'el concepto contiene <texto>' -> cuenta. Va por delante de las reglas genéricas."""
    if not texto.strip():
        raise ErrorContable("La regla necesita un texto que buscar en el concepto")
    if not motor.existe_cuenta(con, cuenta):
        raise ErrorContable(f"La cuenta {cuenta} no existe")
    with con:
        orden = (con.execute("SELECT MIN(orden) FROM regla").fetchone()[0] or 1) - 1
        return con.execute("INSERT INTO regla (orden, patron, sentido, cuenta, revisar, nota) VALUES (?, ?, ?, ?, ?, ?)",
                           (orden, re.escape(texto.strip()), sentido, cuenta, int(revisar), nota or "Regla de la empresa")).lastrowid


def aprobar(con, movimiento_id: int, cuenta: str | None = None, usuario: str = "usuario", texto_regla: str = "") -> int:
    """Contabiliza un movimiento con la cuenta propuesta (o la que diga la persona)."""
    m = con.execute("SELECT * FROM movimiento_banco WHERE id = ?", (movimiento_id,)).fetchone()
    if not m:
        raise ErrorContable("No existe el movimiento")
    if m["asiento_id"]:
        raise ErrorContable("El movimiento ya está contabilizado")
    cuenta = (cuenta or m["propuesta_cuenta"] or "").strip()
    if not cuenta:
        raise ErrorContable(f"Elige la cuenta para «{m['concepto']}»")
    if m["cuenta_banco"].startswith(cuenta):
        raise ErrorContable("La contrapartida no puede ser la propia cuenta bancaria: en un traspaso elige la subcuenta 57x de la otra cuenta")
    if m["propuesta_documento"] and cuenta == m["propuesta_cuenta"]:
        aid = motor.liquidar_factura(con, m["propuesta_documento"], m["fecha"], movimiento_id=m["id"], aprobado_por=usuario)
    else:
        if not motor.existe_cuenta(con, cuenta):
            raise ErrorContable(f"La cuenta {cuenta} no existe en el plan")
        propia = cuenta == m["propuesta_cuenta"] and m["propuesta_origen"] in ("regla", "ia")
        banco, otra = {"cuenta": m["cuenta_banco"]}, {"cuenta": cuenta}
        (banco if m["importe"] > 0 else otra)["debe"] = abs(m["importe"])
        (otra if m["importe"] > 0 else banco)["haber"] = abs(m["importe"])
        with con:
            aid = motor._asiento(con, m["fecha"], m["concepto"], [banco, otra], origen=m["propuesta_origen"] if propia else "manual",
                                 confianza=m["propuesta_confianza"] if propia else None, aprobado_por=usuario)
            con.execute("UPDATE movimiento_banco SET asiento_id = ? WHERE id = ?", (aid, m["id"]))
    if texto_regla.strip():
        crear_regla(con, texto_regla, cuenta, "abono" if m["importe"] > 0 else "cargo")
    return aid


def aprobar_seguras(con, usuario: str = "usuario") -> tuple[int, list[str]]:
    """Contabiliza todas las propuestas que no piden revisión."""
    hechos, errores = 0, []
    for m in con.execute("SELECT id, concepto FROM movimiento_banco WHERE asiento_id IS NULL AND propuesta_cuenta IS NOT NULL "
                         "AND propuesta_revisar = 0 ORDER BY fecha, id").fetchall():
        try:
            aprobar(con, m["id"], usuario=usuario)
            hechos += 1
        except ErrorContable as e:
            errores.append(f"{m['concepto']}: {e}")
    return hechos, errores
