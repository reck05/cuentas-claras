"""Cuentas Claras: aplicación web local y línea de comandos.

  python app.py demo                         crea demo.db con una startup de ejemplo
  python app.py servir [--db demo.db]        abre la aplicación en http://127.0.0.1:8000
  python app.py informe [--hasta AAAA-MM-DD] sumas y saldos, PyG y controles en la terminal
  python app.py importar-diario F.csv        importa el diario exportado de la gestoría
  python app.py importar-banco F [--cuenta]  importa un extracto Norma 43 o CSV
  python app.py proponer [--ia]              propone contrapartidas a los movimientos pendientes
  python app.py cerrar AAAA-MM               cierra el mes si ningún control falla
  python app.py dataroom salida.zip          exporta el data room de due diligence

Todas las órdenes aceptan --db (por defecto cuentas_claras.db).
"""
import argparse
import html
import re
import sys
import traceback
from datetime import date
from email.parser import BytesParser
from email.policy import default as politica_email
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import bancos
import motor
from motor import ErrorContable, centimos, euros, fin_de_mes

RAIZ = Path(__file__).parent
MAX_SUBIDA = 25 * 1024 * 1024


class H(str):
    """Fragmento de HTML ya escapado."""


def e(v) -> str:
    return v if isinstance(v, H) else html.escape("" if v is None else str(v))


def tabla(cabecera, filas, num=(), pie=None) -> H:
    def clase(i, c):
        return "n" if i in num else "f" if re.fullmatch(r"\d{4}-\d{2}-\d{2}( .*)?", str(c)) else ""
    th = "".join(f'<th class="{"n" if i in num else ""}">{e(c)}</th>' for i, c in enumerate(cabecera))
    cuerpo = "".join("<tr>" + "".join(f'<td class="{clase(i, c)}">{e(c)}</td>' for i, c in enumerate(f)) + "</tr>"
                     for f in filas)
    if pie:
        cuerpo += '<tr class="total">' + "".join(f'<td class="{"n" if i in num else ""}">{e(c)}</td>' for i, c in enumerate(pie)) + "</tr>"
    return H(f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{cuerpo or "<tr><td>Sin datos</td></tr>"}</tbody></table></div>')


def etiqueta(texto, clase=None) -> H:
    return H(f'<span class="tag {e(clase or str(texto).lower())}">{e(texto)}</span>')


def enlace(href, texto) -> H:
    return H(f'<a href="{e(href)}">{e(texto)}</a>')


def campo(nombre, rotulo, valor="", tipo="text", extra="") -> H:
    return H(f'<label>{e(rotulo)}<input name="{nombre}" type="{tipo}" value="{e(valor)}" {extra}></label>')


def selector(nombre, rotulo, opciones, elegido=None) -> H:
    ops = "".join(f'<option value="{e(v)}"{" selected" if str(v) == str(elegido) else ""}>{e(t)}</option>' for v, t in opciones)
    return H(f'<label>{e(rotulo)}<select name="{nombre}">{ops}</select></label>')


def formulario(accion, contenido, boton, multipart=False, metodo="post", clase="linea") -> H:
    enc = ' enctype="multipart/form-data"' if multipart else ""
    return H(f'<form class="{clase}" method="{metodo}" action="{accion}"{enc}>{contenido}<button>{e(boton)}</button></form>')


def redir(ruta, **params):
    return ("redir", ruta + ("?" + urlencode(params) if params else ""))


CSS = """
:root{--fondo:#f5f7f9;--papel:#fff;--tinta:#1c2632;--suave:#5c6877;--linea:#e2e6eb;--marca:#0f5c6e;--ok:#1d7a46;--aviso:#9a5b00;--falla:#b42318}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--tinta);background:var(--fondo)}
header{display:flex;gap:14px;align-items:baseline;padding:12px 20px;background:var(--marca);color:#fff}header span{opacity:.85}
nav{display:flex;flex-wrap:wrap;gap:2px;padding:6px 14px;background:var(--papel);border-bottom:1px solid var(--linea)}
nav a{padding:6px 10px;border-radius:6px;color:var(--suave);text-decoration:none}nav a:hover{background:var(--fondo)}nav a.activo{background:var(--marca);color:#fff}
main{max-width:1240px;margin:0 auto;padding:16px 20px 60px}h1{font-size:22px;margin:8px 0 14px}h2{font-size:16px;margin:26px 0 8px}
a{color:var(--marca)}.caja{background:var(--papel);border:1px solid var(--linea);border-radius:10px;padding:12px 14px;margin:10px 0}
table{width:100%;border-collapse:collapse;background:var(--papel);border:1px solid var(--linea)}
th,td{padding:6px 8px;border-bottom:1px solid var(--linea);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--suave);font-weight:600;background:#fafbfc}.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.f{white-space:nowrap;font-variant-numeric:tabular-nums}
tr.total td{font-weight:700;border-top:2px solid var(--tinta)}tr.sep td{background:#f0f3f6;font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.kpi{background:var(--papel);border:1px solid var(--linea);border-radius:10px;padding:12px}
.kpi small{display:block;color:var(--suave)}.kpi b{display:block;font-size:20px;margin:3px 0}.kpi span{font-size:12px;color:var(--suave)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;background:#eef1f4;white-space:nowrap}
.tag.ok,.tag.cobrada,.tag.pagada,.tag.abierto{background:#e3f4ea;color:var(--ok)}
.tag.aviso,.tag.revisar,.tag.vencida,.tag.pendiente{background:#fdf0dc;color:var(--aviso)}.tag.falla{background:#fde4e2;color:var(--falla)}
p.ok,p.error{padding:10px 14px;border-radius:8px;margin:8px 0}p.ok{background:#e3f4ea;color:var(--ok)}p.error{background:#fde4e2;color:var(--falla)}
form.linea{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin:6px 0}form.inline{display:inline-flex;gap:4px;margin:0}
label{display:flex;flex-direction:column;font-size:12px;color:var(--suave);gap:2px}label.check{flex-direction:row;align-items:center;gap:6px}
input,select,button{font:inherit;padding:5px 8px;border:1px solid #c7ced6;border-radius:6px;background:#fff;color:var(--tinta);max-width:100%}
input.importe{width:110px;text-align:right}input.cuenta{width:110px}button{background:var(--marca);color:#fff;border-color:var(--marca);cursor:pointer}
button.sec{background:#fff;color:var(--marca)}.muted{color:var(--suave)}.scroll{overflow-x:auto}.dos{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:800px){main{padding:12px}.dos{grid-template-columns:1fr}}
"""

NAV = [("/", "Panel"), ("/bancos", "Bancos"), ("/facturas", "Facturas"), ("/diario", "Diario"), ("/sumas-saldos", "Sumas y saldos"),
       ("/pyg", "PyG"), ("/balance", "Balance"), ("/fiscal", "Fiscal"), ("/cierre", "Cierre"), ("/controles", "Controles"),
       ("/terceros", "Terceros"), ("/inmovilizado", "Inmovilizado"), ("/plan", "Plan de cuentas"), ("/reglas", "Reglas")]


def pagina(con, ruta, titulo, cuerpo, q) -> str:
    nav = "".join(f'<a href="{h}"{" class=activo" if h == ruta else ""}>{e(t)}</a>' for h, t in NAV)
    avisos = (f'<p class="ok">{e(q["ok"])}</p>' if q.get("ok") else "") + (f'<p class="error">{e(q["error"])}</p>' if q.get("error") else "")
    cuentas = "".join(f'<option value="{e(c)}">{e(n)}</option>' for c, n in con.execute("SELECT codigo, nombre FROM cuenta ORDER BY codigo"))
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(titulo)} · Cuentas Claras</title><style>{CSS}</style></head><body>'
            f'<header><strong>Cuentas Claras</strong><span>{e(motor.config(con, "empresa", "Mi startup"))}</span></header>'
            f'<nav>{nav}</nav><main><h1>{e(titulo)}</h1>{avisos}{cuerpo}</main><datalist id="cuentas">{cuentas}</datalist></body></html>')


def cuentas_banco(con) -> list:
    return con.execute("SELECT codigo, codigo || ' · ' || nombre FROM cuenta c WHERE codigo LIKE '57%' "
                       "AND NOT EXISTS (SELECT 1 FROM cuenta h WHERE h.padre = c.codigo) ORDER BY codigo").fetchall()


def hoy() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- páginas

def p_panel(con, q, f, fi):
    mes = q.get("mes") or motor.ultimo_mes_con_datos(con)
    tiles = "".join(f'<div class="kpi"><small>{e(n)}</small><b>{e(v)}</b><span>{e(x)}</span></div>' for n, v, x in motor.kpis(con, mes))
    ctrl = motor.controles(con, fin_de_mes(mes))
    no_ok = [(cid, n, etiqueta(s), d) for cid, n, s, d in ctrl if s != "OK"]
    sin = con.execute("SELECT COUNT(*) FROM movimiento_banco WHERE asiento_id IS NULL").fetchone()[0]
    venc_c = [x for x in motor.antiguedad(con, "emitida", hoy()) if x["dias"] > 0]
    venc_p = [x for x in motor.antiguedad(con, "recibida", hoy()) if x["dias"] > 0]
    emergente = motor.config(con, "empresa_emergente") == "si"
    venc = motor.proximos_vencimientos(date.today(), 60, emergente)
    cuerpo = (formulario("/", campo("mes", "Mes", mes, "month"), "Ver", metodo="get")
              + f'<div class="kpis">{tiles}</div>'
              + '<div class="dos"><div><h2>Qué hay pendiente</h2>'
              + tabla(["", "Cuántos"], [(enlace("/bancos", "Movimientos bancarios sin contabilizar"), sin),
                                         (enlace("/facturas", "Facturas de clientes vencidas"), f"{len(venc_c)} · {euros(sum(x['total'] for x in venc_c))}"),
                                         (enlace("/facturas", "Facturas de proveedores vencidas"), f"{len(venc_p)} · {euros(sum(x['total'] for x in venc_p))}")], num=(1,))
              + f'<h2>Controles a {e(fin_de_mes(mes))}</h2>'
              + (tabla(["Id", "Control", "Estado", "Detalle"], no_ok) if no_ok else H('<p class="ok">Todo cuadra: los controles están en OK.</p>'))
              + f'<p>{enlace("/controles?hasta=" + fin_de_mes(mes), "Ver todos los controles")}</p></div>'
              + '<div><h2>Próximos vencimientos (60 días)</h2>' + tabla(["Fecha", "Obligación"], venc)
              + '<h2>Empresa</h2>' + formulario("/config", campo("empresa", "Nombre", motor.config(con, "empresa"))
                                               + H(f'<label class="check"><input type="checkbox" name="empresa_emergente" value="si"'
                                                   f'{" checked" if emergente else ""}> Empresa emergente (Ley 28/2022)</label>'), "Guardar")
              + f'<h2>Data room</h2>{formulario("/dataroom", campo("hasta", "Hasta", fin_de_mes(mes), "date"), "Descargar data room (.zip)", metodo="get")}'
              + '</div></div>')
    return pagina(con, "/", "Panel", cuerpo, q)


def a_config(con, q, f, fi):
    motor.guardar_config(con, "empresa", f.get("empresa", "").strip())
    motor.guardar_config(con, "empresa_emergente", "si" if f.get("empresa_emergente") else "no")
    return redir("/", ok="Datos de la empresa guardados")


def p_diario(con, q, f, fi):
    mes = q.get("mes") or motor.ultimo_mes_con_datos(con)
    anulados = {a for (a,) in con.execute("SELECT anula_a FROM asiento WHERE anula_a IS NOT NULL")}
    bloques = []
    for a in con.execute("SELECT * FROM asiento WHERE substr(fecha, 1, 7) = ? ORDER BY fecha, id", (mes,)).fetchall():
        filas = [(p["cuenta"], p["nombre"], euros(p["debe"]) if p["debe"] else "", euros(p["haber"]) if p["haber"] else "",
                  " · ".join(x for x in (p["linea_negocio"], p["producto"], p["canal"], p["proyecto"], "no recurrente" if p["no_recurrente"] else "") if x))
                 for p in con.execute("SELECT a.*, c.nombre FROM apunte a JOIN cuenta c ON c.codigo = a.cuenta WHERE asiento_id = ? ORDER BY a.id", (a["id"],))]
        estado = (etiqueta("anulado", "aviso") if a["id"] in anulados else
                  etiqueta("anulación", "aviso") if a["anula_a"] else
                  formulario("/diario/anular", H(f'<input type="hidden" name="id" value="{a["id"]}"><input name="motivo" placeholder="motivo" required>'),
                             "Anular", clase="inline") if not motor.mes_cerrado(con, a["fecha"]) and a["origen"] != "regularizacion" else etiqueta("mes cerrado", "ok"))
        confianza = f" · confianza {a['confianza']:.2f}" if a["confianza"] is not None else ""
        bloques.append(f'<div class="caja" id="a{a["id"]}"><b>#{a["id"]}</b> · {e(a["fecha"])} · {e(a["concepto"])} '
                       f'<span class="muted">· {e(a["origen"])}{" · " + e(a["aprobado_por"]) if a["aprobado_por"] else ""}'
                       f'{confianza}</span> {estado}'
                       + tabla(["Cuenta", "Nombre", "Debe", "Haber", "Dimensiones"], filas, num=(2, 3)) + "</div>")
    lineas = "".join(
        f'<tr><td><input class="cuenta" name="cuenta_{i}" list="cuentas"></td><td><input class="importe" name="debe_{i}" inputmode="decimal"></td>'
        f'<td><input class="importe" name="haber_{i}" inputmode="decimal"></td><td><input name="linea_negocio_{i}" size="10"></td>'
        f'<td><input name="producto_{i}" size="10"></td><td><input name="canal_{i}" size="8"></td><td><input name="proyecto_{i}" size="8"></td>'
        f'<td><input type="checkbox" name="no_recurrente_{i}" value="1"></td></tr>' for i in range(1, 7))
    nuevo = (f'<form method="post" action="/diario/nuevo" class="caja"><div class="linea" style="display:flex;gap:8px">'
             f'{campo("fecha", "Fecha", hoy(), "date", "required")}{campo("concepto", "Concepto", "", "text", "required size=50")}</div>'
             f'<table><thead><tr><th>Cuenta</th><th>Debe</th><th>Haber</th><th>Línea de negocio</th><th>Producto</th><th>Canal</th>'
             f'<th>Proyecto</th><th>No recurrente</th></tr></thead><tbody>{lineas}</tbody></table><p><button>Contabilizar</button> '
             f'<span class="muted">Solo se guarda si debe = haber y el mes está abierto.</span></p></form>')
    cuerpo = (formulario("/diario", campo("mes", "Mes", mes, "month"), "Ver", metodo="get")
              + "".join(bloques) + "<h2>Nuevo asiento</h2>" + nuevo + "<h2>Importar el diario de la gestoría (CSV)</h2>"
              + formulario("/diario/importar", H('<input type="file" name="fichero" accept=".csv,.txt" required>'), "Importar", multipart=True)
              + H('<p class="muted">Columnas: asiento, fecha, cuenta, concepto, debe, haber (y opcionalmente linea_negocio, producto, canal, '
                  'centro_coste, proyecto, no_recurrente). Separador ; o , y decimales con coma o punto. Si un asiento no cuadra no se importa nada.</p>'))
    return pagina(con, "/diario", "Libro diario", cuerpo, q)


def a_diario_nuevo(con, q, f, fi):
    lineas = [{"cuenta": f.get(f"cuenta_{i}", "").strip(), "debe": centimos(f.get(f"debe_{i}")), "haber": centimos(f.get(f"haber_{i}")),
               **{d: f.get(f"{d}_{i}", "").strip() for d in motor.DIMENSIONES}, "no_recurrente": bool(f.get(f"no_recurrente_{i}"))}
              for i in range(1, 7) if f.get(f"cuenta_{i}", "").strip()]
    aid = motor.crear_asiento(con, f.get("fecha"), f.get("concepto", ""), lineas, aprobado_por="usuario")
    return redir("/diario", mes=motor.fecha_iso(f["fecha"])[:7], ok=f"Asiento {aid} contabilizado")


def a_diario_anular(con, q, f, fi):
    nuevo = motor.anular_asiento(con, int(f["id"]), f.get("motivo", ""))
    return redir("/diario", mes=con.execute("SELECT substr(fecha, 1, 7) FROM asiento WHERE id = ?", (nuevo,)).fetchone()[0],
                 ok=f"Asiento {f['id']} anulado con el asiento {nuevo}")


def a_diario_importar(con, q, f, fi):
    if "fichero" not in fi:
        raise ErrorContable("Elige un fichero")
    n = motor.importar_diario(con, bancos.decodificar(fi["fichero"][1]))
    return redir("/diario", ok=f"{n} asientos importados")


def p_mayor(con, q, f, fi):
    cuenta = q.get("cuenta", "572").strip()
    hasta = q.get("hasta") or hoy()
    info = motor.cuenta_info(con, cuenta)
    saldo, filas = 0, []
    for p in con.execute("SELECT a.*, s.fecha, s.concepto FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
                         "WHERE a.cuenta LIKE ? || '%' AND s.fecha <= ? ORDER BY s.fecha, s.id, a.id", (cuenta, hasta)):
        saldo += p["debe"] - p["haber"]
        filas.append((p["fecha"], enlace(f"/diario?mes={p['fecha'][:7]}#a{p['asiento_id']}", f"#{p['asiento_id']}"), p["cuenta"], p["concepto"],
                      euros(p["debe"]) if p["debe"] else "", euros(p["haber"]) if p["haber"] else "", euros(saldo)))
    cuerpo = (formulario("/mayor", H(f'<label>Cuenta<input name="cuenta" list="cuentas" value="{e(cuenta)}"></label>') + campo("hasta", "Hasta", hasta, "date"),
                         "Ver", metodo="get")
              + H(f'<p class="muted">{e(info["nombre"]) if info else "Cuenta desconocida"} · saldo deudor positivo, acreedor negativo</p>')
              + tabla(["Fecha", "Asiento", "Cuenta", "Concepto", "Debe", "Haber", "Saldo"], filas, num=(4, 5, 6)))
    return pagina(con, "/mayor", f"Mayor de la cuenta {cuenta}", cuerpo, q)


def p_sumas_saldos(con, q, f, fi):
    hasta = q.get("hasta") or hoy()
    nivel = int(q["nivel"]) if q.get("nivel") else None
    filas = motor.sumas_y_saldos(con, hasta, nivel)
    pie = ["TOTAL", "", *(euros(sum(x[i] for x in filas)) for i in (3, 4, 5, 6))]
    cuerpo = (formulario("/sumas-saldos", campo("hasta", "Hasta", hasta, "date")
                         + selector("nivel", "Detalle", [("", "Subcuenta (máximo detalle)"), ("3", "3 dígitos"), ("4", "4 dígitos")], q.get("nivel", "")),
                         "Ver", metodo="get")
              + tabla(["Cuenta", "Nombre", "Sumas debe", "Sumas haber", "Saldo deudor", "Saldo acreedor"],
                      [(enlace(f"/mayor?cuenta={c}&hasta={hasta}", c), n, euros(d), euros(h), euros(sd), euros(sa)) for c, n, _, d, h, sd, sa in filas],
                      num=(2, 3, 4, 5), pie=pie))
    return pagina(con, "/sumas-saldos", "Balance de sumas y saldos", cuerpo, q)


def p_pyg(con, q, f, fi):
    anio = int(q.get("anio") or motor.ultimo_mes_con_datos(con)[:4])
    filas = []
    for linea, valores, total in motor.pyg_mensual(con, anio):
        filas.append((H(f"<b>{e(linea)}</b>") if linea in ("EBITDA", "EBIT", "Resultado neto") else linea, *map(euros, valores), euros(total)))
    meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    cuerpo = (formulario("/pyg", campo("anio", "Año", anio, "number"), "Ver", metodo="get")
              + tabla(["", *meses, "Total"], filas, num=tuple(range(1, 14)))
              + H('<p class="muted">PyG de gestión sacada del mismo libro que la contable (control C30). El EBITDA ajustado excluye lo marcado como no recurrente.</p>'))
    return pagina(con, "/pyg", f"Cuenta de resultados {anio}", cuerpo, q)


def p_balance(con, q, f, fi):
    hasta = q.get("hasta") or hoy()
    b = motor.balance(con, hasta)
    nombres = {"ANC": "Activo no corriente", "AC": "Activo corriente", "PN": "Patrimonio neto", "PNC": "Pasivo no corriente", "PC": "Pasivo corriente"}

    def lado(masas):
        filas = []
        for m in masas:
            filas.append((H(f"<b>{nombres[m]}</b>"), "", H(f"<b>{euros(b['totales'][m])}</b>")))
            filas += [(c, n, euros(i)) for c, n, i in b["masas"][m]]
        return filas
    cuerpo = (formulario("/balance", campo("hasta", "A fecha", hasta, "date"), "Ver", metodo="get")
              + H('<div class="dos"><div>') + tabla(["Activo", "", ""], lado(("ANC", "AC")), num=(2,), pie=["TOTAL ACTIVO", "", euros(b["activo"])])
              + H("</div><div>") + tabla(["Patrimonio neto y pasivo", "", ""], lado(("PN", "PNC", "PC")), num=(2,),
                                         pie=["TOTAL PN Y PASIVO", "", euros(b["pn_pasivo"])]) + H("</div></div>")
              + H(f'<p>{etiqueta("Cuadra" if b["activo"] == b["pn_pasivo"] else "NO cuadra", "ok" if b["activo"] == b["pn_pasivo"] else "falla")}</p>'))
    return pagina(con, "/balance", f"Balance de situación a {hasta}", cuerpo, q)


def p_bancos(con, q, f, fi):
    pendientes = con.execute("SELECT m.*, d.serie_numero FROM movimiento_banco m LEFT JOIN documento d ON d.id = m.propuesta_documento "
                             "WHERE m.asiento_id IS NULL ORDER BY m.fecha, m.id").fetchall()
    filas = []
    for m in pendientes:
        fid = f"m{m['id']}"
        origen = m["propuesta_origen"] or "sin proponer"
        confianza = " " + etiqueta(f"{m['propuesta_confianza']:.2f}", "muted") if m["propuesta_confianza"] is not None else ""
        filas.append((
            m["fecha"], m["concepto"], euros(m["importe"]),
            H(f'<form id="{fid}" method="post" action="/bancos/aprobar" class="inline"><input type="hidden" name="id" value="{m["id"]}">'
              f'<input class="cuenta" name="cuenta" list="cuentas" value="{e(m["propuesta_cuenta"] or "")}" required></form>'),
            H(f'{etiqueta(origen, "ok" if origen in ("conciliacion", "regla") else "aviso")}{confianza}'
              f'{" " + etiqueta("revisar") if m["propuesta_revisar"] else ""}'),
            m["propuesta_nota"] or "",
            H(f'<input form="{fid}" name="regla" placeholder="crear regla: texto del concepto" size="18"> <button form="{fid}">Contabilizar</button>')))
    hechos = [(p["fecha"], p["concepto"], euros(p["importe"]), p["cuenta"], enlace(f"/diario?mes={p['fecha'][:7]}#a{p['asiento_id']}", f"#{p['asiento_id']}"))
              for p in con.execute("SELECT m.*, (SELECT cuenta FROM apunte a WHERE a.asiento_id = m.asiento_id AND a.cuenta <> m.cuenta_banco LIMIT 1) AS cuenta "
                                   "FROM movimiento_banco m WHERE m.asiento_id IS NOT NULL ORDER BY m.fecha DESC, m.id DESC LIMIT 40")]
    seguras = sum(1 for m in pendientes if m["propuesta_cuenta"] and not m["propuesta_revisar"])
    cuerpo = ('<div class="caja"><b>1. Importar extracto</b> (Norma 43 o CSV de la banca online)'
              + formulario("/bancos/importar", H('<input type="file" name="fichero" required>') + selector("cuenta", "Cuenta", cuentas_banco(con)),
                           "Importar", multipart=True)
              + '</div><div class="caja"><b>2. Proponer contrapartidas</b> · conciliación con facturas → reglas → IA'
              + formulario("/bancos/proponer", H('<label class="check"><input type="checkbox" name="ia" value="1"> Usar IA (Claude) para lo que no '
                                                 'cubran facturas ni reglas · necesita ANTHROPIC_API_KEY</label>'), "Proponer")
              + f'</div><div class="caja"><b>3. Revisar y contabilizar</b> · {len(pendientes)} pendientes, {seguras} sin dudas'
              + formulario("/bancos/aprobar-seguras", "", f"Contabilizar las {seguras} propuestas sin dudas")
              + tabla(["Fecha", "Concepto", "Importe", "Cuenta", "Origen", "Nota", ""], filas, num=(2,)) + "</div>"
              + "<h2>Últimos contabilizados</h2>" + tabla(["Fecha", "Concepto", "Importe", "Contrapartida", "Asiento"], hechos, num=(2,)))
    return pagina(con, "/bancos", "Bancos", cuerpo, q)


def a_bancos_importar(con, q, f, fi):
    if "fichero" not in fi:
        raise ErrorContable("Elige un fichero")
    r = bancos.importar_extracto(con, fi["fichero"][0], fi["fichero"][1], f.get("cuenta", "572"))
    return redir("/bancos", ok=f"{r['formato']}: {r['nuevos']} movimientos nuevos, {r['repetidos']} ya estaban")


def a_bancos_proponer(con, q, f, fi):
    try:
        r = bancos.proponer(con, usar_ia=bool(f.get("ia")))
    except ImportError:
        raise ErrorContable("Para la IA instala el SDK: pip install anthropic") from None
    return redir("/bancos", ok=", ".join(f"{v} por {k}" for k, v in r.items()) or "No hay movimientos pendientes")


def a_bancos_aprobar(con, q, f, fi):
    aid = bancos.aprobar(con, int(f["id"]), f.get("cuenta"), texto_regla=f.get("regla", ""))
    return redir("/bancos", ok=f"Contabilizado en el asiento {aid}" + (" y regla creada" if f.get("regla", "").strip() else ""))


def a_bancos_seguras(con, q, f, fi):
    n, errores = bancos.aprobar_seguras(con)
    return redir("/bancos", ok=f"{n} movimientos contabilizados", **({"error": " · ".join(errores)} if errores else {}))


def p_facturas(con, q, f, fi):
    terceros = [(t["id"], f"{t['nombre']} ({t['tipo']})") for t in con.execute(
        "SELECT * FROM tercero WHERE tipo IN ('cliente', 'proveedor', 'acreedor') ORDER BY nombre")]
    nueva = formulario("/facturas/nueva", H("").join([
        selector("tipo", "Tipo", [("recibida", "Recibida"), ("emitida", "Emitida")]),
        selector("tercero_id", "Tercero", terceros), campo("serie_numero", "Número", "", "text", "required size=12"),
        campo("fecha", "Fecha", hoy(), "date", "required"), campo("vencimiento", "Vencimiento", hoy(), "date", "required"),
        campo("base", "Base imponible", "", "text", 'class="importe" inputmode="decimal" required'),
        selector("tipo_iva", "IVA %", [(21, "21"), (10, "10"), (4, "4"), (0, "0 / exenta")]),
        selector("retencion", "Retención %", [(0, "0"), (15, "15"), (7, "7"), (19, "19")]),
        H('<label>Cuenta<input class="cuenta" name="cuenta" list="cuentas" placeholder="629 / 705" required></label>'),
        H('<label class="check"><input type="checkbox" name="isp" value="1"> Inversión sujeto pasivo</label>'),
        campo("vida_meses", "Vida útil (meses, si es 2xx)", "", "number", 'style="width:90px"'),
        campo("linea_negocio", "Línea", "", "text", "size=8"), campo("producto", "Producto", "", "text", "size=8"),
        campo("canal", "Canal", "", "text", "size=8"),
        H('<label class="check"><input type="checkbox" name="no_recurrente" value="1"> No recurrente</label>')]), "Registrar factura")
    bloques = ""
    for tipo, titulo in (("emitida", "Facturas emitidas"), ("recibida", "Facturas recibidas")):
        filas = []
        for d in con.execute("SELECT d.*, t.nombre, p.fecha AS fecha_pago FROM documento d JOIN tercero t ON t.id = d.tercero_id "
                             "LEFT JOIN asiento p ON p.id = d.asiento_pago_id WHERE d.tipo = ? ORDER BY d.fecha DESC, d.id DESC LIMIT 200", (tipo,)):
            if d["fecha_pago"]:
                estado = etiqueta(f"{'cobrada' if tipo == 'emitida' else 'pagada'} {d['fecha_pago']}", "ok")
            else:
                dias = (date.today() - date.fromisoformat(d["vencimiento"])).days
                estado = H(etiqueta(f"vencida {dias} d", "vencida") if dias > 0 else etiqueta("pendiente")) + formulario(
                    "/facturas/liquidar", H(f'<input type="hidden" name="id" value="{d["id"]}"><input type="date" name="fecha" value="{hoy()}">'
                                            f'<select name="cuenta">{"".join(f"<option>{e(c)}</option>" for c, _ in cuentas_banco(con))}</select>'),
                    "Cobrar" if tipo == "emitida" else "Pagar", clase="inline")
            filas.append((d["serie_numero"], d["nombre"], d["fecha"], d["vencimiento"], euros(d["base"]), euros(d["cuota_iva"]),
                          euros(d["retencion"]), euros(d["total"]) + (" ISP" if d["isp"] else ""), d["cuenta"],
                          enlace(f"/diario?mes={d['fecha'][:7]}#a{d['asiento_id']}", f"#{d['asiento_id']}"), estado))
        bloques += f"<h2>{titulo}</h2>" + tabla(["Número", "Tercero", "Fecha", "Vence", "Base", "IVA", "Retención", "Total", "Cuenta", "Asiento", "Estado"],
                                                filas, num=(4, 5, 6, 7))
    tramos = ["No vencida", "1-30", "31-60", "61-90", "> 90"]
    aging = [(nombre, *[euros(sum(x["total"] for x in motor.antiguedad(con, tipo, hoy()) if x["tramo"] == t)) for t in tramos])
             for tipo, nombre in (("emitida", "Clientes"), ("recibida", "Proveedores y acreedores"))]
    cuerpo = ('<div class="caja"><b>Registrar factura</b> · genera el asiento; si la cuenta es 2xx da de alta el activo' + nueva
              + H('<p class="muted">Registra aquí las facturas que emites con tu programa de facturación y las que recibes. '
                  'Las rectificativas: anula el asiento de la factura en el diario y regístrala de nuevo. '
                  'Si no tienes terceros, créalos en <a href="/terceros">Terceros</a>.</p></div>')
              + "<h2>Antigüedad de saldos (hoy)</h2>" + tabla(["", *tramos], aging, num=(1, 2, 3, 4, 5)) + bloques)
    return pagina(con, "/facturas", "Facturas", cuerpo, q)


def a_facturas_nueva(con, q, f, fi):
    did = motor.registrar_factura(
        con, f.get("tipo", ""), int(f.get("tercero_id") or 0), f.get("serie_numero", ""), f.get("fecha"), f.get("vencimiento"),
        centimos(f.get("base")), f.get("tipo_iva", "21"), f.get("cuenta", "").strip(), f.get("retencion", "0"), bool(f.get("isp")),
        {d: f.get(d, "").strip() for d in ("linea_negocio", "producto", "canal")}, bool(f.get("no_recurrente")),
        vida_meses=int(f["vida_meses"]) if f.get("vida_meses") else None)
    return redir("/facturas", ok=f"Factura registrada (documento {did})")


def a_facturas_liquidar(con, q, f, fi):
    aid = motor.liquidar_factura(con, int(f["id"]), f.get("fecha"), f.get("cuenta", "572"))
    return redir("/facturas", ok=f"Cobro/pago contabilizado en el asiento {aid}")


def p_terceros(con, q, f, fi):
    filas = [(t["nombre"], t["nif"], t["tipo"], enlace(f"/mayor?cuenta={t['subcuenta']}", t["subcuenta"]),
              "sí" if t["vinculado"] else "", "sí" if t["extranjero"] else "", euros(motor.saldo(con, t["subcuenta"])))
             for t in con.execute("SELECT * FROM tercero ORDER BY tipo, nombre")]
    alta = formulario("/terceros/nuevo", H("").join([
        campo("nombre", "Nombre o razón social", "", "text", "required size=30"), campo("nif", "NIF", "", "text", "required size=12"),
        selector("tipo", "Tipo", [(t, t) for t in motor.PREFIJO_TERCERO]),
        H('<label class="check"><input type="checkbox" name="vinculado" value="1"> Parte vinculada</label>'),
        H('<label class="check"><input type="checkbox" name="extranjero" value="1"> Extranjero</label>')]), "Dar de alta")
    cuerpo = ('<div class="caja">' + alta + H('<p class="muted">Cada tercero recibe su subcuenta (4300xxxx clientes, 4000xxxx proveedores, '
                                               '4100xxxx acreedores, 5510xxxx socios, 4650xxxx empleados, 5720xxxx cuentas bancarias). '
                                               'El NIF español se valida con su dígito de control.</p></div>')
              + tabla(["Nombre", "NIF", "Tipo", "Subcuenta", "Vinculado", "Extranjero", "Saldo"], filas, num=(6,)))
    return pagina(con, "/terceros", "Terceros", cuerpo, q)


def a_terceros_nuevo(con, q, f, fi):
    motor.alta_tercero(con, f.get("nombre", ""), f.get("nif", ""), f.get("tipo", ""), bool(f.get("vinculado")), bool(f.get("extranjero")))
    return redir("/terceros", ok=f"{f.get('nombre')} dado de alta")


def p_inmovilizado(con, q, f, fi):
    mes_hoy = hoy()[:7]
    filas = []
    for a in con.execute("SELECT a.*, COALESCE((SELECT SUM(importe) FROM amortizacion m WHERE m.activo_id = a.id), 0) AS acum FROM activo a ORDER BY fecha_alta"):
        filas.append((a["descripcion"], a["cuenta"], a["fecha_alta"], euros(a["coste"]), a["vida_meses"], euros(a["acum"]), euros(a["coste"] - a["acum"])))
    cuerpo = ('<div class="caja"><b>Amortizar un mes</b> (el cierre de mes lo hace solo)'
              + formulario("/inmovilizado/amortizar", campo("mes", "Mes", mes_hoy, "month"), "Generar amortización") + "</div>"
              + '<div class="caja"><b>Alta de un activo ya contabilizado</b> (los comprados con factura se dan de alta al registrarla)'
              + formulario("/inmovilizado/nuevo", H("").join([
                  campo("descripcion", "Descripción", "", "text", "required size=28"),
                  H('<label>Cuenta (20x/21x)<input class="cuenta" name="cuenta" list="cuentas" required></label>'),
                  campo("fecha_alta", "Alta", hoy(), "date", "required"), campo("coste", "Coste", "", "text", 'class="importe" required'),
                  campo("vida_meses", "Vida útil (meses)", "48", "number", 'style="width:90px" required')]), "Dar de alta") + "</div>"
              + tabla(["Activo", "Cuenta", "Alta", "Coste", "Vida (meses)", "Amortizado", "Valor neto"], filas, num=(3, 4, 5, 6)))
    return pagina(con, "/inmovilizado", "Inmovilizado", cuerpo, q)


def a_inmovilizado_nuevo(con, q, f, fi):
    motor.alta_activo(con, f.get("descripcion", ""), f.get("cuenta", "").strip(), f.get("fecha_alta"), centimos(f.get("coste")),
                      int(f.get("vida_meses") or 0))
    return redir("/inmovilizado", ok="Activo dado de alta")


def a_inmovilizado_amortizar(con, q, f, fi):
    aid = motor.amortizar_mes(con, f.get("mes", ""))
    return redir("/inmovilizado", ok=f"Amortización contabilizada en el asiento {aid}" if aid else "No había nada que amortizar ese mes")


def p_fiscal(con, q, f, fi):
    ultimo = motor.ultimo_mes_con_datos(con)
    anio = int(q.get("anio") or ultimo[:4])
    t = int(q.get("t") or (int(ultimo[5:7]) - 1) // 3 + 1)
    b303, b111 = motor.borrador_303(con, anio, t), motor.borrador_111(con, anio, t)
    filas303 = ([(H("<b>IVA devengado</b>"), "", "")] + [(n, euros(b), euros(c)) for n, b, c in b303["devengado"]]
                + [(H("<b>IVA deducible</b>"), "", "")] + [(n, euros(b), euros(c)) for n, b, c in b303["deducible"]])
    dif = b303["resultado"] - b303["segun_libro"]
    m347 = motor.modelo_347(con, anio)
    cuerpo = (formulario("/fiscal", campo("anio", "Año", anio, "number") + selector("t", "Trimestre", [(i, f"{i}T") for i in range(1, 5)], t), "Ver", metodo="get")
              + '<div class="dos"><div>' + f"<h2>Modelo 303 · borrador {b303['periodo']}</h2>"
              + tabla(["", "Base", "Cuota"], filas303, num=(1, 2), pie=["Resultado (a ingresar si es positivo)", "", euros(b303["resultado"])])
              + H(f'<p>Según el libro (477 − 472 sin regularizar): {euros(b303["segun_libro"])} '
                  f'{etiqueta("cuadra", "ok") if not dif else etiqueta(f"diferencia {euros(dif)}: hay IVA en asientos sin factura", "aviso")}</p>')
              + formulario("/fiscal/regularizar-iva", H(f'<input type="hidden" name="anio" value="{anio}"><input type="hidden" name="t" value="{t}">'),
                           f"Regularizar IVA del {t}T (472/477 → 4750/4700)")
              + "</div><div>" + f"<h2>Modelo 111 · borrador {b111['periodo']}</h2>"
              + tabla(["", "Perceptores", "Base", "Retenciones"], [(n, p, euros(b), euros(r)) for n, p, b, r in b111["lineas"]],
                      num=(1, 2, 3), pie=["Total a ingresar", "", "", euros(b111["total"])])
              + H('<p class="muted">Trabajo: base = sueldos (640) del trimestre; retenciones = abonos a la 4751 que no vienen de facturas.</p>')
              + "</div></div>" + f"<h2>Modelo 347 · {anio}</h2>"
              + tabla(["NIF", "Tercero", "Clave", "1T", "2T", "3T", "4T", "Total"],
                      [(x["nif"], x["nombre"], x["clave"], *map(euros, x["trimestres"]), euros(x["total"])) for x in m347], num=(3, 4, 5, 6, 7))
              + H('<p class="muted">Terceros nacionales con más de 3.005,06 € en el año. Excluye inversión del sujeto pasivo y operaciones con retención. '
                  'Son borradores para revisar con el asesor, no presentaciones.</p>')
              + "<h2>Próximos vencimientos</h2>" + tabla(["Fecha", "Obligación"], motor.proximos_vencimientos(date.today(), 120,
                                                                                                     motor.config(con, "empresa_emergente") == "si")))
    return pagina(con, "/fiscal", "Fiscal", cuerpo, q)


def a_fiscal_iva(con, q, f, fi):
    aid = motor.regularizar_iva(con, int(f["anio"]), int(f["t"]))
    return redir("/fiscal", anio=f["anio"], t=f["t"], ok=f"IVA regularizado en el asiento {aid}")


def p_cierre(con, q, f, fi):
    primero = (con.execute("SELECT MIN(fecha) FROM asiento").fetchone()[0] or hoy())[:7]
    cerrados = {p["mes"]: p for p in con.execute("SELECT * FROM periodo WHERE cerrado_en IS NOT NULL")}
    meses = motor.meses_entre(primero, max(hoy()[:7], motor.ultimo_mes_con_datos(con)))
    mes = q.get("mes") or next((m for m in meses if m not in cerrados), meses[-1])
    lista = [(enlace(f"/cierre?mes={m}", m), etiqueta("cerrado", "ok") if m in cerrados else etiqueta("abierto", "aviso"),
              cerrados[m]["cerrado_en"] if m in cerrados else "") for m in reversed(meses)]
    ctrl = motor.controles(con, fin_de_mes(mes))
    acciones = (formulario("/cierre/reabrir", H(f'<input type="hidden" name="mes" value="{mes}"><input name="motivo" placeholder="motivo" required>'),
                           f"Reabrir {mes}") if mes in cerrados else
                formulario("/cierre/cerrar", H(f'<input type="hidden" name="mes" value="{mes}">'), f"Cerrar {mes}"))
    if mes.endswith("-12"):
        acciones += formulario("/cierre/regularizar", H(f'<input type="hidden" name="anio" value="{mes[:4]}">'), f"Regularizar ejercicio {mes[:4]} (6 y 7 → 129)")
    pasos = ["Importar extractos y contabilizar todos los movimientos bancarios", "Registrar facturas emitidas y recibidas del mes",
             "Provisionar facturas pendientes de recibir de gastos ya devengados (4009/4109)", "Contabilizar nóminas y Seguridad Social",
             "Amortizaciones (las genera el cierre)", "Periodificaciones (480/485)", "Revisar la antigüedad de clientes y dotar deterioro",
             "Intereses de préstamos y reclasificación a corto plazo", "IVA trimestral: regularizar 472/477 (en Fiscal)", "Vaciar la 555",
             "Registrar los saldos externos (extracto, 303, 111, TGSS) en Controles", "Cerrar: solo se bloquea si ningún control FALLA"]
    eventos = con.execute("SELECT * FROM evento ORDER BY id DESC LIMIT 15").fetchall()
    cuerpo = ('<div class="dos"><div>' + f"<h2>Cierre de {mes}</h2>" + acciones
              + tabla(["Id", "Control", "Estado", "Detalle"], [(c, n, etiqueta(s), d) for c, n, s, d in ctrl])
              + "<h2>Checklist de cierre</h2><ol>" + "".join(f"<li>{e(p)}</li>" for p in pasos) + "</ol></div><div>"
              + "<h2>Meses</h2>" + tabla(["Mes", "Estado", "Cerrado en"], lista)
              + "<h2>Rastro de auditoría</h2>" + tabla(["Fecha", "Tipo", "Detalle"], [(x["fecha"], x["tipo"], x["detalle"]) for x in eventos]) + "</div></div>")
    return pagina(con, "/cierre", "Cierre mensual", cuerpo, q)


def a_cierre_cerrar(con, q, f, fi):
    motor.cerrar_mes(con, f["mes"])
    return redir("/cierre", mes=f["mes"], ok=f"Mes {f['mes']} cerrado y bloqueado")


def a_cierre_reabrir(con, q, f, fi):
    motor.reabrir_mes(con, f["mes"], f.get("motivo", ""))
    return redir("/cierre", mes=f["mes"], ok=f"Mes {f['mes']} reabierto")


def a_cierre_regularizar(con, q, f, fi):
    aid = motor.regularizar_ejercicio(con, int(f["anio"]))
    return redir("/cierre", mes=f"{f['anio']}-12", ok=f"Ejercicio regularizado en el asiento {aid}")


def p_controles(con, q, f, fi):
    hasta = q.get("hasta") or hoy()
    externos = [(x["fecha"], x["cuentas"], euros(x["saldo"]), x["fuente"]) for x in con.execute("SELECT * FROM saldo_externo ORDER BY fecha DESC, id DESC")]
    cuerpo = (formulario("/controles", campo("hasta", "A fecha", hasta, "date"), "Ver", metodo="get")
              + tabla(["Id", "Control", "Estado", "Detalle"], [(c, n, etiqueta(s), d) for c, n, s, d in motor.controles(con, hasta)])
              + H('<p class="muted">FALLA bloquea el cierre del mes; AVISO hay que explicarlo. Catálogo completo en data/controles_cuadre.csv.</p>')
              + '<h2>Saldos externos (control de nivel 3)</h2><div class="caja">'
              + formulario("/controles/saldo", H("").join([
                  campo("cuentas", "Cuentas (p. ej. 57200001 o 472+477)", "", "text", "required size=16"), campo("fecha", "Fecha", hasta, "date", "required"),
                  campo("saldo", "Saldo según la fuente", "", "text", 'class="importe" required'),
                  campo("fuente", "Fuente", "", "text", 'required size=28 placeholder="Extracto, modelo 111, TGSS..."')]), "Registrar")
              + H('<p class="muted">Saldo deudor en positivo y acreedor en negativo: un banco con 1.000 € a favor es 1000; '
                  'un 111 pendiente de 300 € es -300. Los extractos Norma 43 lo registran solos.</p></div>')
              + tabla(["Fecha", "Cuentas", "Saldo", "Fuente"], externos, num=(2,)))
    return pagina(con, "/controles", "Controles de cuadre", cuerpo, q)


def a_controles_saldo(con, q, f, fi):
    motor.registrar_saldo_externo(con, f.get("cuentas", ""), f.get("fecha"), centimos(f.get("saldo")), f.get("fuente", ""))
    return redir("/controles", ok="Saldo externo registrado")


def p_plan(con, q, f, fi):
    filas = [(enlace(f"/mayor?cuenta={c['codigo']}", c["codigo"]), c["nombre"], c["naturaleza"], c["masa"] or "", c["epigrafe_gestion"] or "")
             for c in con.execute("SELECT * FROM cuenta ORDER BY codigo")]
    cuerpo = ('<div class="caja"><b>Nueva subcuenta</b>' + formulario("/plan/subcuenta", campo("codigo", "Código", "", "text", "required size=10")
                                                                     + campo("nombre", "Nombre", "", "text", "required size=40"), "Crear")
              + H('<p class="muted">Hereda naturaleza, masa y epígrafe de la cuenta de la que cuelga. Para terceros usa Terceros.</p></div>')
              + tabla(["Código", "Nombre", "Naturaleza", "Masa", "Epígrafe de gestión"], filas))
    return pagina(con, "/plan", "Plan de cuentas (PGC PYMES)", cuerpo, q)


def a_plan_subcuenta(con, q, f, fi):
    motor.crear_subcuenta(con, f.get("codigo", "").strip(), f.get("nombre", "").strip())
    return redir("/plan", ok=f"Subcuenta {f.get('codigo')} creada")


def p_reglas(con, q, f, fi):
    filas = [(r["orden"], r["patron"], r["sentido"], r["cuenta"], "sí" if r["revisar"] else "", r["nota"] or "",
              formulario("/reglas/borrar", H(f'<input type="hidden" name="id" value="{r["id"]}">'), "Borrar", clase="inline"))
             for r in con.execute("SELECT * FROM regla ORDER BY orden")]
    cuerpo = ('<div class="caja"><b>Nueva regla</b> · el concepto contiene un texto → cuenta (va antes que las genéricas)'
              + formulario("/reglas/nueva", H("").join([
                  campo("texto", "El concepto contiene", "", "text", "required size=24"),
                  H('<label>Cuenta<input class="cuenta" name="cuenta" list="cuentas" required></label>'),
                  selector("sentido", "Sentido", [("ambos", "Cargos y abonos"), ("cargo", "Cargos"), ("abono", "Abonos")]),
                  H('<label class="check"><input type="checkbox" name="revisar" value="1"> Pedir revisión siempre</label>')]), "Crear") + "</div>"
              + tabla(["Orden", "Patrón (expresión regular)", "Sentido", "Cuenta", "Revisar", "Nota", ""], filas))
    return pagina(con, "/reglas", "Reglas de clasificación", cuerpo, q)


def a_reglas_nueva(con, q, f, fi):
    bancos.crear_regla(con, f.get("texto", ""), f.get("cuenta", "").strip(), f.get("sentido", "ambos"), revisar=bool(f.get("revisar")))
    return redir("/reglas", ok="Regla creada")


def a_reglas_borrar(con, q, f, fi):
    with con:
        con.execute("DELETE FROM regla WHERE id = ?", (int(f["id"]),))
    return redir("/reglas", ok="Regla borrada")


def d_dataroom(con, q, f, fi):
    hasta = motor.fecha_iso(q.get("hasta") or hoy())
    return ("descarga", f"dataroom_{hasta}.zip", motor.exportar_dataroom(con, hasta))


RUTAS = {
    ("GET", "/"): p_panel, ("POST", "/config"): a_config,
    ("GET", "/diario"): p_diario, ("POST", "/diario/nuevo"): a_diario_nuevo, ("POST", "/diario/anular"): a_diario_anular,
    ("POST", "/diario/importar"): a_diario_importar, ("GET", "/mayor"): p_mayor, ("GET", "/sumas-saldos"): p_sumas_saldos,
    ("GET", "/pyg"): p_pyg, ("GET", "/balance"): p_balance,
    ("GET", "/bancos"): p_bancos, ("POST", "/bancos/importar"): a_bancos_importar, ("POST", "/bancos/proponer"): a_bancos_proponer,
    ("POST", "/bancos/aprobar"): a_bancos_aprobar, ("POST", "/bancos/aprobar-seguras"): a_bancos_seguras,
    ("GET", "/facturas"): p_facturas, ("POST", "/facturas/nueva"): a_facturas_nueva, ("POST", "/facturas/liquidar"): a_facturas_liquidar,
    ("GET", "/terceros"): p_terceros, ("POST", "/terceros/nuevo"): a_terceros_nuevo,
    ("GET", "/inmovilizado"): p_inmovilizado, ("POST", "/inmovilizado/nuevo"): a_inmovilizado_nuevo,
    ("POST", "/inmovilizado/amortizar"): a_inmovilizado_amortizar,
    ("GET", "/fiscal"): p_fiscal, ("POST", "/fiscal/regularizar-iva"): a_fiscal_iva,
    ("GET", "/cierre"): p_cierre, ("POST", "/cierre/cerrar"): a_cierre_cerrar, ("POST", "/cierre/reabrir"): a_cierre_reabrir,
    ("POST", "/cierre/regularizar"): a_cierre_regularizar,
    ("GET", "/controles"): p_controles, ("POST", "/controles/saldo"): a_controles_saldo,
    ("GET", "/plan"): p_plan, ("POST", "/plan/subcuenta"): a_plan_subcuenta,
    ("GET", "/reglas"): p_reglas, ("POST", "/reglas/nueva"): a_reglas_nueva, ("POST", "/reglas/borrar"): a_reglas_borrar,
    ("GET", "/dataroom"): d_dataroom,
}


# ---------------------------------------------------------------- servidor

class Manejador(BaseHTTPRequestHandler):
    db = "cuentas_claras.db"

    def do_GET(self):
        self._atender("GET")

    def do_POST(self):
        self._atender("POST")

    def log_message(self, formato, *args):  # silencioso salvo errores
        pass

    def _formulario(self, cuerpo: bytes) -> tuple[dict, dict]:
        tipo = self.headers.get("Content-Type", "")
        if tipo.startswith("multipart/form-data"):
            msg = BytesParser(policy=politica_email).parsebytes(f"Content-Type: {tipo}\r\n\r\n".encode() + cuerpo)
            campos, ficheros = {}, {}
            for parte in msg.iter_parts():
                nombre = parte.get_param("name", header="content-disposition")
                datos = parte.get_payload(decode=True) or b""
                if parte.get_filename():
                    if datos:
                        ficheros[nombre] = (parte.get_filename(), datos)
                else:
                    campos[nombre] = datos.decode("utf-8")
            return campos, ficheros
        return {k: v[-1] for k, v in parse_qs(cuerpo.decode("utf-8"), keep_blank_values=True).items()}, {}

    def _responder(self, codigo, cuerpo: bytes, tipo="text/html; charset=utf-8", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _atender(self, metodo):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        cuerpo = b""
        if metodo == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_SUBIDA:
                self.close_connection = True
                return self._responder(413, "El fichero supera 25 MB".encode("utf-8"))
            cuerpo = self.rfile.read(n)  # leer siempre el cuerpo antes de responder (si no, Windows corta la conexión)
            origen = self.headers.get("Origin")
            if origen and urlparse(origen).netloc != self.headers.get("Host"):  # otra web no puede mandar formularios aquí
                return self._responder(403, b"Origen no permitido")
        manejador = RUTAS.get((metodo, url.path))
        if not manejador:
            return self._responder(404, b"No encontrado")
        con = motor.abrir(self.db)
        try:
            f, fi = self._formulario(cuerpo) if metodo == "POST" else ({}, {})
            r = manejador(con, q, f, fi)
            if isinstance(r, str):
                self._responder(200, r.encode("utf-8"))
            elif r[0] == "redir":
                self._responder(303, b"", extra={"Location": r[1]})
            else:
                self._responder(200, r[2], "application/zip", {"Content-Disposition": f'attachment; filename="{r[1]}"'})
        except (ErrorContable, ValueError, KeyError) as ex:
            mensaje = str(ex) if isinstance(ex, ErrorContable) else f"Dato no válido: {ex}"
            if metodo == "POST":
                volver = urlparse(self.headers.get("Referer") or "/")
                destino = volver.path if ("GET", volver.path) in RUTAS else "/"
                previos = {k: v[-1] for k, v in parse_qs(volver.query).items() if k not in ("ok", "error")}
                self._responder(303, b"", extra={"Location": destino + "?" + urlencode({**previos, "error": mensaje})})
            else:
                self._responder(400, pagina(con, url.path, "No se puede mostrar", H(f'<p class="error">{e(mensaje)}</p>'), {}).encode("utf-8"))
        except Exception:
            traceback.print_exc()
            self._responder(500, "Error interno: revisa la terminal donde corre la aplicación".encode("utf-8"))
        finally:
            con.close()


def servidor(db: str, puerto: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    motor.abrir(db).close()  # crea la base si no existe
    manejador = type("ManejadorEmpresa", (Manejador,), {"db": db})
    return ThreadingHTTPServer((host, puerto), manejador)


# ---------------------------------------------------------------- demo

def crear_demo(ruta: str) -> None:
    """Startup ficticia: primer trimestre importado de la gestoría y cerrado; abril con facturas y extracto Norma 43."""
    Path(ruta).unlink(missing_ok=True)
    ej = RAIZ / "data" / "ejemplo"
    con = motor.abrir(ruta)
    motor.guardar_config(con, "empresa", "Startup Ejemplo SL")
    motor.guardar_config(con, "empresa_emergente", "si")
    banco = motor.alta_tercero(con, "Banco Ejemplo · cuenta principal", "A00000000", "banco")
    motor.alta_tercero(con, "Banco Ejemplo · cuenta de ahorro", "A11111119", "banco")
    principal = con.execute("SELECT subcuenta FROM tercero WHERE id = ?", (banco,)).fetchone()[0]
    motor.importar_diario(con, (ej / "diario.csv").read_text(encoding="utf-8"))
    motor.alta_activo(con, "Portátil desarrollo", "217", "2026-02-15", 120000, 48)
    motor.regularizar_iva(con, 2026, 1)
    for s in motor.leer_csv(ej / "saldos_externos.csv"):
        motor.registrar_saldo_externo(con, s["cuentas"], s["fecha"], centimos(s["saldo_esperado"]), s["fuente"])
    for mes in ("2026-01", "2026-02", "2026-03"):
        motor.cerrar_mes(con, mes, "demo")
    cliente = motor.alta_tercero(con, "Cliente Ejemplo SL", "B12345674", "cliente")
    asesora = motor.alta_tercero(con, "Asesora Fiscal Ejemplo", "12345678Z", "acreedor")
    nube = motor.alta_tercero(con, "Proveedor Cloud Europa SARL", "LU12345678", "acreedor", extranjero=True)
    socia = motor.alta_tercero(con, "Fundadora Ejemplo", "00000000T", "socio", vinculado=True)
    motor.registrar_factura(con, "emitida", cliente, "2026-002", "2026-04-02", "2026-04-30", 500000, 21, "705",
                            dimensiones={"linea_negocio": "SaaS", "producto": "Plan Pro", "canal": "Directo"})
    motor.registrar_factura(con, "recibida", nube, "INV-0412", "2026-04-12", "2026-04-12", 4000, 21, "629", isp=True)
    motor.registrar_factura(con, "recibida", asesora, "A-2026-04", "2026-04-30", "2026-05-15", 50000, 21, "623", retencion_pct=15)
    motor.crear_asiento(con, "2026-04-30", "Nómina abril (gestoría)", [
        {"cuenta": "640", "debe": 300000}, {"cuenta": "642", "debe": 96000}, {"cuenta": "4751", "haber": 45000},
        {"cuenta": "476", "haber": 115000}, {"cuenta": "465", "haber": 236000}], origen="importado")
    sub_socia = con.execute("SELECT subcuenta FROM tercero WHERE id = ?", (socia,)).fetchone()[0]
    motor.crear_asiento(con, "2026-04-18", "Dominio web pagado por la socia con su tarjeta",
                        [{"cuenta": "629", "debe": 1500}, {"cuenta": sub_socia, "haber": 1500}])
    bancos.importar_extracto(con, "extracto_abril.n43", (ej / "extracto_abril.n43").read_bytes(), principal)
    bancos.proponer(con)
    bancos.aprobar_seguras(con, "demo")
    con.close()


# ---------------------------------------------------------------- línea de comandos

def informe(con, hasta: str | None) -> int:
    print(f"\nBALANCE DE SUMAS Y SALDOS{' a ' + hasta if hasta else ''}\n")
    print(f"{'Cuenta':<10}{'Nombre':<46}{'Debe':>13}{'Haber':>13}{'Saldo D':>13}{'Saldo A':>13}")
    filas = motor.sumas_y_saldos(con, hasta)
    for c, n, _, d, h, sd, sa in filas:
        print(f"{c:<10}{n[:44]:<46}{euros(d):>13}{euros(h):>13}{euros(sd):>13}{euros(sa):>13}")
    print(f"{'TOTAL':<56}" + "".join(f"{euros(sum(x[i] for x in filas)):>13}" for i in (3, 4, 5, 6)))
    print("\nPYG DE GESTION\n")
    anio = (hasta or motor.ultimo_mes_con_datos(con))[:4]
    for linea, importe in motor.pyg_gestion(con, f"{anio}-01-01", hasta):
        print(f"{linea:<46}{euros(importe):>13}")
    print("\nCONTROLES\n")
    resultado = motor.controles(con, hasta)
    for cid, nombre, estado, detalle in resultado:
        print(f"[{estado:<5}] {cid} {nombre}" + (f"  ->  {detalle}" if detalle else ""))
    return 1 if any(s == "FALLA" for _, _, s, _ in resultado) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="cuentas_claras.db", help="base de datos de la empresa")
    comun = argparse.ArgumentParser(add_help=False)  # --db también vale después de la orden
    comun.add_argument("--db", default=argparse.SUPPRESS, help="base de datos de la empresa")
    sub = ap.add_subparsers(dest="orden", required=True)
    s = sub.add_parser("servir", parents=[comun])
    s.add_argument("--puerto", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1", help="no la expongas a internet: no tiene usuarios ni contraseña")
    sub.add_parser("demo", parents=[comun])
    s = sub.add_parser("informe", parents=[comun])
    s.add_argument("--hasta")
    s = sub.add_parser("importar-diario", parents=[comun])
    s.add_argument("fichero", type=Path)
    s = sub.add_parser("importar-banco", parents=[comun])
    s.add_argument("fichero", type=Path)
    s.add_argument("--cuenta", default="572")
    s = sub.add_parser("proponer", parents=[comun])
    s.add_argument("--ia", action="store_true")
    s = sub.add_parser("cerrar", parents=[comun])
    s.add_argument("mes")
    s = sub.add_parser("dataroom", parents=[comun])
    s.add_argument("salida", type=Path)
    s.add_argument("--hasta")
    a = ap.parse_args()

    if a.orden == "demo":
        db = a.db if a.db != "cuentas_claras.db" else "demo.db"
        crear_demo(db)
        print(f"Creada {db}. Ábrela con:  python app.py servir --db {db}")
        return 0
    if a.orden == "servir":
        srv = servidor(a.db, a.puerto, a.host)
        print(f"Cuentas Claras ({a.db}) en http://{a.host}:{a.puerto}  ·  Ctrl+C para salir")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    con = motor.abrir(a.db)
    try:
        if a.orden == "informe":
            return informe(con, a.hasta)
        if a.orden == "importar-diario":
            print(f"{motor.importar_diario(con, bancos.decodificar(a.fichero.read_bytes()))} asientos importados")
        elif a.orden == "importar-banco":
            r = bancos.importar_extracto(con, a.fichero.name, a.fichero.read_bytes(), a.cuenta)
            print(f"{r['formato']}: {r['nuevos']} nuevos, {r['repetidos']} repetidos")
        elif a.orden == "proponer":
            print(dict(bancos.proponer(con, usar_ia=a.ia)))
        elif a.orden == "cerrar":
            motor.cerrar_mes(con, a.mes)
            print(f"Mes {a.mes} cerrado")
        elif a.orden == "dataroom":
            a.salida.write_bytes(motor.exportar_dataroom(con, a.hasta))
            print(f"Data room guardado en {a.salida}")
    except ErrorContable as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
