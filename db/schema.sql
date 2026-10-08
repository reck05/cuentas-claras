-- Cuentas Claras · modelo de datos del libro mayor.
-- SQLite para el prototipo; la versión Postgres está en docs/05-arquitectura-y-roadmap.md.
-- Importes en céntimos (INTEGER). Nunca REAL: 0.1 + 0.2 no es 0.3 y la contabilidad tiene que cuadrar al céntimo.
PRAGMA foreign_keys = ON;

CREATE TABLE cuenta (
    codigo            TEXT PRIMARY KEY,                -- '572', '4751', '43000001' (subcuenta de un cliente)
    nombre            TEXT NOT NULL,
    grupo             INTEGER NOT NULL CHECK (grupo BETWEEN 1 AND 7),
    naturaleza        TEXT NOT NULL CHECK (naturaleza IN ('D', 'A', 'DA')),  -- saldo normal: deudor, acreedor o ambos
    masa              TEXT,                            -- ANC, AC, PN, PNC, PC, ING, GAS
    epigrafe_gestion  TEXT,                            -- línea de la PyG de gestión (Ingresos, Personal...)
    padre             TEXT REFERENCES cuenta (codigo)
);

CREATE TABLE tercero (
    id         INTEGER PRIMARY KEY,
    nif        TEXT UNIQUE,
    nombre     TEXT NOT NULL,
    tipo       TEXT NOT NULL CHECK (tipo IN ('cliente', 'proveedor', 'acreedor', 'socio', 'empleado', 'administracion', 'banco')),
    vinculado  INTEGER NOT NULL DEFAULT 0,             -- parte vinculada: lo pide toda due diligence
    subcuenta  TEXT REFERENCES cuenta (codigo)        -- 4300xxxx, 4000xxxx, 4100xxxx...
);

CREATE TABLE documento (                              -- soporte de cada asiento
    id            INTEGER PRIMARY KEY,
    tipo          TEXT NOT NULL,                       -- factura_emitida, factura_recibida, nomina, extracto, contrato, escritura...
    serie_numero  TEXT,
    fecha         TEXT NOT NULL,
    vencimiento   TEXT,                                -- sin vencimiento no hay antigüedad de saldos
    tercero_id    INTEGER REFERENCES tercero (id),
    base          INTEGER,
    cuota_iva     INTEGER,
    retencion     INTEGER,
    total         INTEGER,
    archivo       TEXT,                                -- ruta o URL del PDF/XML
    hash          TEXT UNIQUE,                         -- el mismo archivo no entra dos veces
    UNIQUE (tercero_id, tipo, serie_numero)            -- la misma factura no entra dos veces
);

CREATE TABLE periodo (
    mes          TEXT PRIMARY KEY,                     -- '2026-03'
    cerrado_en   TEXT,
    cerrado_por  TEXT
);

CREATE TABLE asiento (
    id            INTEGER PRIMARY KEY,
    fecha         TEXT NOT NULL,
    concepto      TEXT NOT NULL,
    tipo          TEXT NOT NULL DEFAULT 'normal' CHECK (tipo IN ('apertura', 'normal', 'regularizacion', 'cierre')),
    documento_id  INTEGER REFERENCES documento (id),
    origen        TEXT NOT NULL DEFAULT 'manual' CHECK (origen IN ('manual', 'regla', 'ia', 'conciliacion', 'importado')),
    confianza     REAL,                                -- si lo propuso la IA
    aprobado_por  TEXT,
    creado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE apunte (
    id             INTEGER PRIMARY KEY,
    asiento_id     INTEGER NOT NULL REFERENCES asiento (id),
    cuenta         TEXT NOT NULL REFERENCES cuenta (codigo),
    debe           INTEGER NOT NULL DEFAULT 0 CHECK (debe >= 0),
    haber          INTEGER NOT NULL DEFAULT 0 CHECK (haber >= 0),
    -- Dimensiones analíticas: lo que pedirá la DD (ventas y márgenes por línea, producto, canal...).
    linea_negocio  TEXT,
    producto       TEXT,
    canal          TEXT,
    centro_coste   TEXT,
    proyecto       TEXT,
    no_recurrente  INTEGER NOT NULL DEFAULT 0,         -- 1 = se ajusta en el EBITDA ajustado
    CHECK ((debe > 0) <> (haber > 0))                  -- cada apunte va a un solo lado y nunca a cero
);

-- Un mes cerrado no admite apuntes nuevos.
CREATE TRIGGER periodo_cerrado BEFORE INSERT ON apunte
WHEN EXISTS (SELECT 1 FROM periodo p JOIN asiento s ON p.mes = substr(s.fecha, 1, 7)
             WHERE s.id = NEW.asiento_id AND p.cerrado_en IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'periodo cerrado');
END;

-- Tiene que estar SIEMPRE vacía. En Postgres se sustituye por un constraint trigger diferido (docs/05).
CREATE VIEW v_asientos_descuadrados AS
SELECT asiento_id, SUM(debe) AS debe, SUM(haber) AS haber
FROM apunte
GROUP BY asiento_id
HAVING SUM(debe) <> SUM(haber);

CREATE TABLE movimiento_banco (                       -- extracto importado tal cual (Norma 43 / PSD2); no se edita
    id            INTEGER PRIMARY KEY,
    cuenta_banco  TEXT NOT NULL REFERENCES cuenta (codigo),
    fecha         TEXT NOT NULL,
    concepto      TEXT NOT NULL,
    importe       INTEGER NOT NULL,                    -- + abono, - cargo
    saldo         INTEGER,                             -- saldo que informa el banco tras el movimiento
    asiento_id    INTEGER REFERENCES asiento (id),     -- NULL = sin conciliar
    UNIQUE (cuenta_banco, fecha, concepto, importe, saldo)
);
