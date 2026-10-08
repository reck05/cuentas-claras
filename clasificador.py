"""Autoclasificador de movimientos bancarios de Cuentas Claras.

Cascada: 1) reglas deterministas (data/reglas_clasificacion.csv) y 2) solo para lo que
queda, IA (Claude) con el plan de cuentas y los movimientos ya clasificados como ejemplos.
No contabiliza nada: escribe una propuesta que revisa una persona y luego valida motor.py.

Uso:  python clasificador.py data/ejemplo/banco.csv [--ia] [--umbral 0.9]
      (--ia necesita `pip install anthropic` y ANTHROPIC_API_KEY o `ant auth login`)
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).parent
MODELO = "claude-opus-5-5"
SISTEMA = """Eres el contable de una startup española que aplica el PGC de PYMES.
Para cada movimiento bancario propones la cuenta contrapartida: la otra pata del asiento (el banco 572 ya es una).
- Usa solo códigos del plan de cuentas que se te da.
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


def leer_csv(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def por_reglas(mov: dict, reglas: list[dict]) -> dict | None:
    """Primera regla que encaja (el orden del CSV importa)."""
    sentido = "abono" if float(mov["importe"]) > 0 else "cargo"
    for r in reglas:
        if r["sentido"] in (sentido, "ambos") and re.search(r["patron"], mov["concepto"], re.IGNORECASE):
            return {"cuenta": r["cuenta"], "origen": "regla", "confianza": "1", "revisar": r["revisar"], "nota": r["nota"]}
    return None


def por_ia(pendientes: list[dict], plan: list[dict], ejemplos: list[dict]) -> dict[str, dict]:
    import anthropic  # solo hace falta con --ia

    cuentas = "\n".join(f"{c['codigo']} {c['nombre']}" for c in plan)
    hist = "\n".join(f"{e['concepto']} | {e['importe']} -> {e['cuenta']}" for e in ejemplos) or "(ninguno)"
    movs = "\n".join(f"{m['id']} | {m['fecha']} | {m['concepto']} | {m['importe']}" for m in pendientes)
    resp = anthropic.Anthropic().beta.messages.create(
        model=MODELO,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # si el modelo declina, la API reintenta con el recomendado
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
        system=[{"type": "text", "text": SISTEMA + cuentas, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Movimientos ya clasificados por la empresa:\n{hist}\n\nClasifica:\n{movs}"}],
    )
    if resp.stop_reason != "end_turn":  # refusal o max_tokens: todo a revisión manual
        print(f"La IA no terminó ({resp.stop_reason}); los movimientos quedan pendientes.", file=sys.stderr)
        return {}
    texto = next(b.text for b in resp.content if b.type == "text")
    return {r["id"]: r for r in json.loads(texto)["movimientos"]}


def clasificar(movimientos: list[dict], reglas: list[dict], plan: list[dict], usar_ia: bool, umbral: float) -> list[dict]:
    salida, pendientes = [], []
    for m in movimientos:
        p = por_reglas(m, reglas)
        if p:
            salida.append({**m, **p})
        else:
            pendientes.append(m)
    if pendientes and usar_ia:
        codigos = {c["codigo"] for c in plan}
        propuestas = por_ia(pendientes, plan, salida)
        for m in pendientes:
            p = propuestas.get(m["id"])
            # La IA solo propone: cuenta fuera del plan -> pendiente; poca confianza o falta documento -> revisar.
            if p and any(p["cuenta"].startswith(c) for c in codigos):
                dudoso = p["confianza"] < umbral or p["requiere_documento"]
                salida.append({**m, "cuenta": p["cuenta"], "origen": "ia", "confianza": f"{p['confianza']:.2f}",
                               "revisar": "si" if dudoso else "no", "nota": p["motivo"]})
                continue
            salida.append({**m, "cuenta": "", "origen": "pendiente", "confianza": "", "revisar": "si",
                           "nota": "La IA no dio una cuenta válida del plan"})
    elif pendientes:
        salida += [{**m, "cuenta": "", "origen": "pendiente", "confianza": "", "revisar": "si",
                    "nota": "Sin regla; ejecuta con --ia o clasifícalo a mano"} for m in pendientes]
    return sorted(salida, key=lambda m: int(m["id"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("banco", type=Path, help="CSV con id, fecha, concepto, importe (+ abono, - cargo)")
    ap.add_argument("--ia", action="store_true", help="clasificar con Claude lo que no cubren las reglas")
    ap.add_argument("--umbral", type=float, default=0.9, help="confianza mínima para no pedir revisión")
    a = ap.parse_args()

    salida = clasificar(leer_csv(a.banco), leer_csv(RAIZ / "data" / "reglas_clasificacion.csv"),
                        leer_csv(RAIZ / "data" / "plan_cuentas_pgc.csv"), a.ia, a.umbral)
    destino = a.banco.with_name(a.banco.stem + "_propuesta.csv")
    campos = ["id", "fecha", "concepto", "importe", "cuenta", "origen", "confianza", "revisar", "nota"]
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(salida)

    for m in salida:
        print(f"{m['id']:>3} {m['concepto'][:42]:<43}{m['importe']:>11}  -> {m['cuenta'] or '?':<6}"
              f"{m['origen']:<10}{'REVISAR' if m['revisar'] == 'si' else ''}")
    cuenta = {o: sum(m["origen"] == o for m in salida) for o in ("regla", "ia", "pendiente")}
    print(f"\n{len(salida)} movimientos: {cuenta['regla']} por reglas, {cuenta['ia']} por IA, "
          f"{cuenta['pendiente']} pendientes, {sum(m['revisar'] == 'si' for m in salida)} a revisar.\nPropuesta: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
