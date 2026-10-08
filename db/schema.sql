-- Cuentas Claras · modelo de datos (SQLite). La versión Postgres está en docs/05-arquitectura-y-roadmap.md.
-- Importes en céntimos (INTEGER). Nunca REAL: 0.1 + 0.2 no es 0.3 y la contabilidad tiene que cuadrar al céntimo.
PRAGMA foreign_keys = ON;

CREATE TABLE config (
    clave  TEXT PRIMARY KEY,
    valor  TEXT
);

CREATE TABLE cuenta (
    codigo            TEXT PRIMARY KEY,                -- '572', '4751', '43000001' (subcuenta de un cliente)
    nombre            TEXT NOT NULL,
    grupo             INTEGER NOT NULL CHECK (grupo BETWEEN 1 AND 7),
    naturaleza        TEXT NOT NULL CHECK (naturaleza IN ('D', 'A', 'DA')),  -- saldo normal: deudor, acreedor o ambos
    masa              TEXT,                            -- ANC, AC, PN, PNC, PC, AC/PC, ING, GAS
    epigrafe_gestion  TEXT,                            -- línea de la PyG de gestión (Ingresos, Personal...)
    padre             TEXT REFERENCES cuenta (codigo)
);

CREATE TABLE tercero (
    id         INTEGER PRIMARY KEY,
    nif        TEXT UNIQUE,
    nombre     TEXT NOT NULL,
    tipo       TEXT NOT NULL CHECK (tipo IN ('cliente', 'proveedor', 'acreedor', 'socio', 'empleado', 'banco')),
    vinculado  INTEGER NOT NULL DEFAULT 0,             -- parte vinculada: lo pide toda due diligence
    extranjero INTEGER NOT NULL DEFAULT 0,             -- fuera de España: no va al 347, sí al 349 si es UE
    subcuenta  TEXT NOT NULL UNIQUE REFERENCES cuenta (codigo)  -- 4300xxxx, 4000xxxx, 4100xxxx, 5720xxxx...
);

CREATE TABLE periodo (
    mes          TEXT PRIMARY KEY,                     -- '2026-03'
    cerrado_en   TEXT,                                 -- NULL = abierto
    cerrado_por  TEXT
);

CREATE TABLE asiento (
    id            INTEGER PRIMARY KEY,
    fecha         TEXT NOT NULL,
    concepto      TEXT NOT NULL,
    origen        TEXT NOT NULL DEFAULT 'manual'
                  CHECK (origen IN ('manual', 'regla', 'ia', 'conciliacion', 'importado', 'factura', 'automatico', 'anulacion',
                                    'regularizacion')),
    confianza     REAL,                                -- si lo propuso la IA
    aprobado_por  TEXT,
    anula_a       INTEGER UNIQUE REFERENCES asiento (id),  -- un asiento solo se anula una vez
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
CREATE INDEX apunte_asiento ON apunte (asiento_id);
CREATE INDEX apunte_cuenta ON apunte (cuenta);

-- Libro inmutable: lo contabilizado no se edita ni se borra; se anula con un asiento inverso.
CREATE TRIGGER apunte_sin_update BEFORE UPDATE ON apunte
BEGIN SELECT RAISE(ABORT, 'los apuntes no se modifican: anula el asiento'); END;
CREATE TRIGGER apunte_sin_delete BEFORE DELETE ON apunte
BEGIN SELECT RAISE(ABORT, 'los apuntes no se borran: anula el asiento'); END;

-- Un mes cerrado no admite apuntes nuevos.
CREATE TRIGGER periodo_cerrado BEFORE INSERT ON apunte
WHEN EXISTS (SELECT 1 FROM periodo p JOIN asiento s ON p.mes = substr(s.fecha, 1, 7)
             WHERE s.id = NEW.asiento_id AND p.cerrado_en IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'periodo cerrado'); END;

-- Tiene que estar SIEMPRE vacía (la aplicación no deja guardar un asiento descuadrado).
CREATE VIEW v_asientos_descuadrados AS
SELECT asiento_id, SUM(debe) AS debe, SUM(haber) AS haber
FROM apunte GROUP BY asiento_id HAVING SUM(debe) <> SUM(haber);

CREATE TABLE documento (                              -- facturas emitidas y recibidas
    id                 INTEGER PRIMARY KEY,
    tipo               TEXT NOT NULL CHECK (tipo IN ('emitida', 'recibida')),
    serie_numero       TEXT NOT NULL,
    fecha              TEXT NOT NULL,
    vencimiento        TEXT NOT NULL,                  -- sin vencimiento no hay antigüedad de saldos
    tercero_id         INTEGER NOT NULL REFERENCES tercero (id),
    cuenta             TEXT NOT NULL REFERENCES cuenta (codigo),  -- 70x en emitidas, 6xx/2xx en recibidas
    base               INTEGER NOT NULL,
    tipo_iva           REAL NOT NULL,                  -- 21, 10, 4, 0
    cuota_iva          INTEGER NOT NULL,
    retencion          INTEGER NOT NULL DEFAULT 0,
    total              INTEGER NOT NULL,               -- base + cuota - retención (ISP: base)
    isp                INTEGER NOT NULL DEFAULT 0,     -- inversión del sujeto pasivo (SaaS/publicidad extranjeros)
    archivo            TEXT,
    asiento_id         INTEGER REFERENCES asiento (id),
    asiento_pago_id    INTEGER REFERENCES asiento (id),  -- NULL = pendiente de cobro/pago
    UNIQUE (tipo, tercero_id, serie_numero)            -- la misma factura no entra dos veces
);
CREATE UNIQUE INDEX factura_emitida_unica ON documento (serie_numero) WHERE tipo = 'emitida';

CREATE TABLE activo (                                 -- registro de inmovilizado
    id            INTEGER PRIMARY KEY,
    descripcion   TEXT NOT NULL,
    cuenta        TEXT NOT NULL REFERENCES cuenta (codigo),  -- 20x / 21x
    fecha_alta    TEXT NOT NULL,
    coste         INTEGER NOT NULL CHECK (coste > 0),
    vida_meses    INTEGER NOT NULL CHECK (vida_meses > 0),
    documento_id  INTEGER REFERENCES documento (id)
);

CREATE TABLE amortizacion (
    activo_id   INTEGER NOT NULL REFERENCES activo (id),
    mes         TEXT NOT NULL,
    importe     INTEGER NOT NULL,
    asiento_id  INTEGER NOT NULL REFERENCES asiento (id),
    PRIMARY KEY (activo_id, mes)
);

CREATE TABLE movimiento_banco (                       -- extracto importado tal cual (Norma 43 / CSV)
    id                   INTEGER PRIMARY KEY,
    cuenta_banco         TEXT NOT NULL REFERENCES cuenta (codigo),
    fecha                TEXT NOT NULL,
    concepto             TEXT NOT NULL,
    importe              INTEGER NOT NULL,             -- + abono, - cargo
    huella               TEXT NOT NULL UNIQUE,         -- reimportar el mismo extracto no duplica
    fichero              TEXT,
    propuesta_cuenta     TEXT,
    propuesta_origen     TEXT,                         -- conciliacion | regla | ia | pendiente
    propuesta_confianza  REAL,
    propuesta_revisar    INTEGER,
    propuesta_nota       TEXT,
    propuesta_documento  INTEGER REFERENCES documento (id),
    asiento_id           INTEGER REFERENCES asiento (id)  -- NULL = sin contabilizar
);

CREATE TABLE regla (
    id        INTEGER PRIMARY KEY,
    orden     INTEGER NOT NULL,                        -- gana la primera que encaja
    patron    TEXT NOT NULL,                           -- expresión regular sobre el concepto
    sentido   TEXT NOT NULL CHECK (sentido IN ('cargo', 'abono', 'ambos')),
    cuenta    TEXT NOT NULL,
    revisar   INTEGER NOT NULL DEFAULT 0,
    nota      TEXT
);

CREATE TABLE saldo_externo (                          -- lo que dice el mundo exterior (control nivel 3)
    id          INTEGER PRIMARY KEY,
    cuentas     TEXT NOT NULL,                         -- '572' o '472+477'
    fecha       TEXT NOT NULL,
    saldo       INTEGER NOT NULL,                      -- deudor positivo, acreedor negativo
    fuente      TEXT NOT NULL,
    tolerancia  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE evento (                                 -- rastro de auditoría
    id       INTEGER PRIMARY KEY,
    fecha    TEXT NOT NULL DEFAULT (datetime('now')),
    tipo     TEXT NOT NULL,
    detalle  TEXT
);
