"""Motor de cuadre de Cuentas Claras.

Carga el plan de cuentas y el libro diario en SQLite (en memoria), saca el balance
de sumas y saldos, la PyG de gestión y pasa los controles de cuadre.
Sale con código 1 si algún control FALLA.

Uso:  python motor.py data/ejemplo [--hasta 2026-03-31] [--nivel 3]
"""
import argparse
import csv
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).parent
DIMENSIONES = ("linea_negocio", "producto", "canal", "centro_coste", "proyecto")
EXPLOTACION = ("Ingresos", "Aprovisionamientos", "Otros ingresos de explotación", "Personal", "Otros gastos de explotación")


def centimos(texto: str) -> int:
    """'1234.5' -> 123450. Los importes viven en céntimos enteros, nunca en float."""
    return int((Decimal((texto or "").strip() or "0") * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def euros(c: int) -> str:
    s = f"{abs(c) // 100:,}".replace(",", ".") + f",{abs(c) % 100:02d}"
    return "-" + s if c < 0 else s


def leer_csv(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _asegurar_subcuenta(con: sqlite3.Connection, codigo: str) -> None:
    """Una subcuenta (43000001) hereda naturaleza y epígrafe de la cuenta más larga del plan de la que cuelga."""
    if con.execute("SELECT 1 FROM cuenta WHERE codigo = ?", (codigo,)).fetchone():
        return
    padre = con.execute(
        "SELECT codigo, grupo, naturaleza, masa, epigrafe_gestion FROM cuenta WHERE ? LIKE codigo || '%' "
        "ORDER BY length(codigo) DESC LIMIT 1", (codigo,)).fetchone()
    if not padre:
        raise ValueError(f"La cuenta {codigo} no está en el plan ni cuelga de ninguna cuenta del plan")
    con.execute("INSERT INTO cuenta VALUES (?, ?, ?, ?, ?, ?, ?)", (codigo, f"Subcuenta de {padre[0]}", *padre[1:], padre[0]))


def abrir(carpeta: Path) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript((RAIZ / "db" / "schema.sql").read_text(encoding="utf-8"))
    con.executemany(
        "INSERT INTO cuenta (codigo, nombre, grupo, naturaleza, masa, epigrafe_gestion) VALUES (?, ?, ?, ?, ?, ?)",
        [(c["codigo"], c["nombre"], int(c["codigo"][0]), c["naturaleza"], c["masa"], c["epigrafe_gestion"] or None)
         for c in leer_csv(RAIZ / "data" / "plan_cuentas_pgc.csv")])
    for l in leer_csv(carpeta / "diario.csv"):
        _asegurar_subcuenta(con, l["cuenta"])
        con.execute("INSERT OR IGNORE INTO asiento (id, fecha, concepto, origen) VALUES (?, ?, ?, 'importado')",
                    (int(l["asiento"]), l["fecha"], l["concepto"]))
        con.execute(
            f"INSERT INTO apunte (asiento_id, cuenta, debe, haber, {', '.join(DIMENSIONES)}, no_recurrente) "
            f"VALUES (?, ?, ?, ?, {', '.join('?' * len(DIMENSIONES))}, ?)",
            (int(l["asiento"]), l["cuenta"], centimos(l["debe"]), centimos(l["haber"]),
             *(l.get(d) or None for d in DIMENSIONES), int(l.get("no_recurrente") or 0)))
    return con


def saldo(con: sqlite3.Connection, cuenta: str, hasta: str | None = None) -> int:
    """Saldo (debe - haber) de una cuenta y sus subcuentas: deudor positivo, acreedor negativo."""
    return con.execute(
        "SELECT COALESCE(SUM(a.debe - a.haber), 0) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
        "WHERE a.cuenta LIKE ? || '%' AND (? IS NULL OR s.fecha <= ?)", (cuenta, hasta, hasta)).fetchone()[0]


def sumas_y_saldos(con: sqlite3.Connection, hasta: str | None = None, nivel: int | None = None) -> list[tuple]:
    """Filas (cuenta, nombre, naturaleza, sumas debe, sumas haber, saldo deudor, saldo acreedor)."""
    cta = f"substr(a.cuenta, 1, {int(nivel)})" if nivel else "a.cuenta"
    filas = []
    for codigo, debe, haber in con.execute(
            f"SELECT {cta} AS c, SUM(a.debe), SUM(a.haber) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            f"WHERE (? IS NULL OR s.fecha <= ?) GROUP BY c ORDER BY c", (hasta, hasta)):
        nombre, naturaleza = con.execute(
            "SELECT nombre, naturaleza FROM cuenta WHERE ? LIKE codigo || '%' ORDER BY length(codigo) DESC LIMIT 1",
            (codigo,)).fetchone() or ("", "DA")
        filas.append((codigo, nombre, naturaleza, debe, haber, max(debe - haber, 0), max(haber - debe, 0)))
    return filas


def pyg_gestion(con: sqlite3.Connection, hasta: str | None = None) -> list[tuple[str, int]]:
    """PyG de gestión sacada del mismo libro: la de gestión y la contable no pueden no cuadrar."""
    def suma(where: str) -> dict:
        return dict(con.execute(
            "SELECT c.epigrafe_gestion, SUM(a.haber - a.debe) FROM apunte a JOIN asiento s ON s.id = a.asiento_id "
            f"JOIN cuenta c ON c.codigo = a.cuenta WHERE c.grupo IN (6, 7) AND {where} AND (? IS NULL OR s.fecha <= ?) "
            "GROUP BY 1", (hasta, hasta)))
    t, nr = suma("1"), suma("a.no_recurrente = 1")
    ebitda = sum(t.get(e, 0) for e in EXPLOTACION)
    ebit = ebitda + t.get("Amortizaciones", 0) + t.get("Otros resultados", 0)
    return ([(e, t.get(e, 0)) for e in EXPLOTACION]
            + [("EBITDA", ebitda), ("EBITDA ajustado (sin no recurrentes)", ebitda - sum(nr.get(e, 0) for e in EXPLOTACION)),
               ("Amortizaciones", t.get("Amortizaciones", 0)), ("Otros resultados", t.get("Otros resultados", 0)),
               ("EBIT", ebit), ("Resultado financiero", t.get("Resultado financiero", 0)),
               ("Impuestos", t.get("Impuestos", 0)),
               ("Resultado neto", ebit + t.get("Resultado financiero", 0) + t.get("Impuestos", 0))])


def controles(con: sqlite3.Connection, carpeta: Path, hasta: str | None = None) -> list[tuple[str, str, str, str]]:
    """(id, control, OK|AVISO|FALLA, detalle). Catálogo completo en data/controles_cuadre.csv."""
    r = []
    malos = con.execute("SELECT asiento_id, debe, haber FROM v_asientos_descuadrados").fetchall()
    r.append(("C01", "Cada asiento cuadra (debe = haber)", "FALLA" if malos else "OK",
              "; ".join(f"asiento {a}: debe {euros(d)} / haber {euros(h)}" for a, d, h in malos)))
    bss = sumas_y_saldos(con, hasta)
    td, th = sum(f[3] for f in bss), sum(f[4] for f in bss)
    sd, sa = sum(f[5] for f in bss), sum(f[6] for f in bss)
    r.append(("C02", "Sumas debe = sumas haber", "OK" if td == th else "FALLA", f"{euros(td)} / {euros(th)}"))
    r.append(("C03", "Saldos deudores = saldos acreedores", "OK" if sd == sa else "FALLA", f"{euros(sd)} / {euros(sa)}"))
    raros = [f"{c} {n}: saldo {'acreedor' if s_a else 'deudor'} {euros(s_a or s_d)}"
             for c, n, nat, _, _, s_d, s_a in bss if (nat == "D" and s_a) or (nat == "A" and s_d)]
    r.append(("C04", "Saldos con el signo de su naturaleza", "AVISO" if raros else "OK", "; ".join(raros)))
    s555 = saldo(con, "555", hasta)
    r.append(("C05", "Partidas pendientes de aplicación (555) a cero", "AVISO" if s555 else "OK",
              f"quedan {euros(s555)} por identificar" if s555 else ""))
    externos = carpeta / "saldos_externos.csv"
    for e in leer_csv(externos) if externos.exists() else []:
        contable = sum(saldo(con, c.strip(), e["fecha"]) for c in e["cuentas"].split("+"))
        esperado = centimos(e["saldo_esperado"])
        bien = abs(contable - esperado) <= centimos(e.get("tolerancia") or "0.01")
        r.append(("C06", f"{e['fuente']} [{e['cuentas']} a {e['fecha']}]", "OK" if bien else "FALLA",
                  f"contabilidad {euros(contable)} / fuente {euros(esperado)}"))
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta", type=Path, help="carpeta con diario.csv (y opcionalmente saldos_externos.csv)")
    ap.add_argument("--hasta", help="fecha de corte AAAA-MM-DD")
    ap.add_argument("--nivel", type=int, help="agrupar cuentas a N dígitos (3 = cuentas, sin subcuentas)")
    a = ap.parse_args()
    con = abrir(a.carpeta)

    print(f"\nBALANCE DE SUMAS Y SALDOS{' a ' + a.hasta if a.hasta else ''}\n")
    print(f"{'Cuenta':<10}{'Nombre':<46}{'Debe':>13}{'Haber':>13}{'Saldo D':>13}{'Saldo A':>13}")
    filas = sumas_y_saldos(con, a.hasta, a.nivel)
    for c, n, _, d, h, sd, sa in filas:
        print(f"{c:<10}{n[:44]:<46}{euros(d):>13}{euros(h):>13}{euros(sd):>13}{euros(sa):>13}")
    print(f"{'TOTAL':<56}" + "".join(f"{euros(sum(f[i] for f in filas)):>13}" for i in (3, 4, 5, 6)))

    print("\nPYG DE GESTIÓN\n")
    for linea, importe in pyg_gestion(con, a.hasta):
        print(f"{linea:<46}{euros(importe):>13}")

    print("\nCONTROLES DE CUADRE\n")
    resultado = controles(con, a.carpeta, a.hasta)
    for cid, nombre, estado, detalle in resultado:
        print(f"[{estado:<5}] {cid} {nombre}" + (f"  ->  {detalle}" if detalle else ""))
    return 1 if any(estado == "FALLA" for _, _, estado, _ in resultado) else 0


if __name__ == "__main__":
    sys.exit(main())
