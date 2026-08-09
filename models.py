from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class RouteMonthly(db.Model):
    __tablename__ = 'route_monthly'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # 'cabotaje' | 'internacional'
    origin = db.Column(db.String(80), nullable=False)
    dest = db.Column(db.String(80), nullable=False)
    year = db.Column(db.String(4), nullable=False)
    month = db.Column(db.String(3), nullable=False)  # Ene, Feb, ...
    vuelos = db.Column(db.Integer)
    pax = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('tipo', 'origin', 'dest', 'year', 'month', name='uq_route_month'),
    )


class AirportMonthly(db.Model):
    __tablename__ = 'airport_monthly'
    id = db.Column(db.Integer, primary_key=True)
    airport = db.Column(db.String(80), nullable=False)
    year = db.Column(db.String(4), nullable=False)
    month = db.Column(db.String(3), nullable=False)
    pax_total = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('airport', 'year', 'month', name='uq_airport_month'),
    )


class Airport(db.Model):
    """Known airports/cities with coordinates. Seeded from geocode.py, editable via admin."""
    __tablename__ = 'airport'
    name = db.Column(db.String(80), primary_key=True)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    is_argentina = db.Column(db.Boolean, nullable=False, default=False)  # para el filtro "Argentina (todos los del país)"


class Aircraft(db.Model):
    """Catálogo de modelos de avión: consumo por hora, velocidad crucero, tipo (narrow/wide)
    y asientos por defecto. Seeded desde los valores que antes estaban hardcodeados en
    avion_model.py, editable via admin. Usado como fuente de verdad al elegir avión en
    rutas manuales, en vez de adivinar por texto libre.

    peso_operativo_kg y elasticidad_override son opcionales: sirven para afinar el ajuste
    por ocupación real que se simula en el frontend (map.html). Si no se cargan, ese ajuste
    cae a una aproximación genérica por tipo_fuselaje (ver PAX_WEIGHT_SHARE en map.html)."""
    __tablename__ = 'aircraft'
    name = db.Column(db.String(80), primary_key=True)  # ej. "Boeing 777-300ER"
    tipo_fuselaje = db.Column(db.String(10), nullable=False, default='narrow')  # 'narrow' | 'wide'
    consumo_hora_kg = db.Column(db.Float, nullable=False)
    velocidad_crucero_kmh = db.Column(db.Float, nullable=False)
    asientos_default = db.Column(db.Integer)
    peso_operativo_kg = db.Column(db.Float)  # OEW + combustible típico en vuelo, opcional
    elasticidad_override = db.Column(db.Float)  # % consumo por % peso, opcional (si no, usa el default global)


class UploadLog(db.Model):
    __tablename__ = 'upload_log'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())
    rows_routes = db.Column(db.Integer)
    rows_airports = db.Column(db.Integer)
    note = db.Column(db.Text)


class ManualRoute(db.Model):
    """Rutas cargadas manualmente por el usuario (no vienen de ANAC).
    Tienen avión y asientos confirmados; vuelos/pax son None (no hay tráfico real).
    Aparecen en el mapa como línea fija con vuelos=1 para que se dibujen."""
    __tablename__ = 'manual_route'
    id = db.Column(db.Integer, primary_key=True)
    origin = db.Column(db.String(80), nullable=False)
    dest = db.Column(db.String(80), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)   # 'cabotaje' | 'internacional'
    avion = db.Column(db.String(120), nullable=False)
    asientos = db.Column(db.Integer)
    consumo_kg_manual = db.Column(db.Float)  # opcional: si se carga, pisa el cálculo automático
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())
    note = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('origin', 'dest', name='uq_manual_route'),
    )


class ProyeccionRuta(db.Model):
    """Rutas hipotéticas cargadas desde la pestaña de Proyecciones (NO desde el admin).

    No representan tráfico real: son supuestos que el usuario agrega para ver cómo
    impactarían en el consumo nacional de Jet A-1 a futuro. Cualquiera con acceso al mapa
    puede crearlas, editarlas y borrarlas — por eso quedan en una tabla aparte de
    ManualRoute (que sí es admin) y nunca se mezclan con los datos de ANAC.

    signo = +1 para una ruta que se suma (apertura), -1 para una que se resta (cierre de
    una ruta existente), lo que permite modelar bajas además de altas."""
    __tablename__ = 'proyeccion_ruta'
    id = db.Column(db.Integer, primary_key=True)
    escenario = db.Column(db.String(60), nullable=False, default='Base')
    origin = db.Column(db.String(80), nullable=False)
    dest = db.Column(db.String(80), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)        # 'cabotaje' | 'internacional'
    vuelos_mes = db.Column(db.Integer, nullable=False, default=0)   # tramos por mes (ida+vuelta)
    pax_por_vuelo = db.Column(db.Float)                     # define qué avión se asigna
    avion_forzado = db.Column(db.String(10))                # código de flota.json, opcional
    signo = db.Column(db.Integer, nullable=False, default=1)        # +1 suma, -1 resta
    desde_anio = db.Column(db.Integer, nullable=False)
    desde_mes = db.Column(db.Integer, nullable=False)        # 1-12
    hasta_anio = db.Column(db.Integer)                      # None = sin fecha de fin
    hasta_mes = db.Column(db.Integer)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    nota = db.Column(db.Text)
    creado_por = db.Column(db.String(160))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class ProyeccionConfig(db.Model):
    """Supuestos globales de una proyección (crecimiento anual, horizonte, años base de
    estacionalidad). Una fila por escenario."""
    __tablename__ = 'proyeccion_config'
    escenario = db.Column(db.String(60), primary_key=True)
    crecimiento_anual_cabotaje = db.Column(db.Float, nullable=False, default=0.03)
    crecimiento_anual_intl = db.Column(db.Float, nullable=False, default=0.04)
    horizonte_meses = db.Column(db.Integer, nullable=False, default=24)
    anios_estacionalidad = db.Column(db.Integer, nullable=False, default=3)
    nota = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class ProyeccionExclusion(db.Model):
    """Ventanas de fechas que se excluyen del cálculo de estacionalidad y del nivel base de
    la proyección — pensadas para eventos puntuales que no reflejan demanda real (cierre de
    pista por obras, corte de suministro, falla operativa), no para tendencias genuinas.

    Es un mecanismo más fino que el checkbox "Excluir 2020-2021" del front (que descarta
    AÑOS COMPLETOS a nivel nacional para elegir sobre qué años se arma la estacionalidad):
    acá se excluyen MESES puntuales, opcionalmente atados a una sola aeroplanta, sin tocar
    la elección de años base.

    aeropuerto=None significa "aplica a nivel nacional y a cualquier aeroplanta" (para un
    evento sistémico, ej. un corte de suministro que afectó a todo el país). Con un nombre
    puntual, solo afecta la vista de esa aeroplanta — un cierre de pista en Córdoba no tiene
    por qué torcer la estacionalidad de Ezeiza."""
    __tablename__ = 'proyeccion_exclusion'
    id = db.Column(db.Integer, primary_key=True)
    aeropuerto = db.Column(db.String(80))  # None/'' = todo el país
    desde_anio = db.Column(db.Integer, nullable=False)
    desde_mes = db.Column(db.Integer, nullable=False)   # 1-12
    hasta_anio = db.Column(db.Integer, nullable=False)
    hasta_mes = db.Column(db.Integer, nullable=False)   # 1-12
    motivo = db.Column(db.String(200), nullable=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_por = db.Column(db.String(160))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class MercadoMensual(db.Model):
    """Volumen REAL de Jet A-1 del mercado argentino, mes a mes y por empresa, cargado desde
    una planilla propia (no de ANAC ni del despacho interno).

    Es la pieza que faltaba para calibrar de verdad: el despacho de FuelSale es solo lo que
    despacha YPF, así que compararlo contra el total nacional que estima el modelo da un
    SHARE de mercado, no un error. Con el total del mercado acá, el cociente
    modelo/mercado_real sí es el error del modelo, y es lo que permite bajar ese desvío.

    total_m3 no se recalcula como la suma de las empresas: se guarda tal cual viene de la
    planilla, porque suele incluir operadores chicos que no están desagregados en columnas
    propias (la diferencia se expone como `otros_m3`)."""
    __tablename__ = 'mercado_mensual'
    id = db.Column(db.Integer, primary_key=True)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)          # 1-12
    total_m3 = db.Column(db.Float, nullable=False)
    ypf_m3 = db.Column(db.Float)
    axion_m3 = db.Column(db.Float)
    shell_m3 = db.Column(db.Float)
    nota = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('anio', 'mes', name='uq_mercado_mensual'),
    )

    @property
    def otros_m3(self):
        """Lo que el total no explica con las tres empresas desagregadas."""
        conocido = (self.ypf_m3 or 0) + (self.axion_m3 or 0) + (self.shell_m3 or 0)
        return max(0.0, (self.total_m3 or 0) - conocido)


class IndiceEconomico(db.Model):
    """Series mensuales de indicadores económicos externos, para usar como regresores en la
    estimación de elasticidad de la demanda (mismo rol que TipoCambioMensual, generalizado).

    `categoria` agrupa el tipo de indicador ('salario', y a futuro 'ipc' si se carga el
    índice de precios) y `nombre` distingue sub-series dentro de una categoría (el índice
    de salarios de INDEC trae varias: sector privado registrado, público, total registrado,
    no registrado, índice general). Con `categoria='salario'` y `categoria='ipc'` cargados
    a la vez, se puede calcular salario REAL (nominal / IPC) en vez de nominal — un índice
    nominal en un país con inflación acumulada varía casi en línea recta con el tiempo y
    queda casi colineal con la tendencia, lo que hace la elasticidad poco confiable; el
    salario real tiene subas y bajas genuinas, independientes de la tendencia, y por eso
    identifica mejor el efecto."""
    __tablename__ = 'indice_economico'
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(30), nullable=False)  # 'salario', 'ipc', ...
    nombre = db.Column(db.String(60), nullable=False)     # sub-serie dentro de la categoría
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)            # 1-12
    valor = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('categoria', 'nombre', 'anio', 'mes', name='uq_indice_economico'),
    )


class TipoCambioMensual(db.Model):
    """Serie mensual de tipo de cambio, cargada a mano para poder incorporarla como
    regresor en la proyección.

    `valor` es deliberadamente genérico: podés cargar el dólar nominal (ARS/USD) o, mejor,
    un índice de tipo de cambio REAL como el ITCRM del BCRA (ajustado por inflación, que es
    lo que de verdad mueve la decisión de viajar — un salto nominal con inflación pareja no
    cambia el poder de compra real). La app no asume cuál es: lo que importa para estimar la
    elasticidad es la variación relativa mes a mes, no la unidad."""
    __tablename__ = 'tipo_cambio_mensual'
    id = db.Column(db.Integer, primary_key=True)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)   # 1-12
    valor = db.Column(db.Float, nullable=False)
    nota = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('anio', 'mes', name='uq_tipo_cambio_mensual'),
    )


class AirlineMonthly(db.Model):
    """Vuelos [#], Pax [000] y Ocupación por aerolínea y por mes (cabotaje e
    internacional), extraído de la tabla "Vuelos [#], Pax [000], Ocupación y
    Mercado x Aerolínea" de los Informes Mensuales de ANAC (PDF)."""
    __tablename__ = 'airline_monthly'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)      # 'cabotaje' | 'internacional'
    aerolinea = db.Column(db.String(80), nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)          # 1-12
    vuelos = db.Column(db.Integer, nullable=False)
    pax_000 = db.Column(db.Integer, nullable=False)      # pasajeros en miles
    ocupacion = db.Column(db.Float, nullable=False)       # 0.0 - 1.0
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('tipo', 'aerolinea', 'anio', 'mes', name='uq_airline_monthly'),
    )


class AirlineLoadFactorSnapshot(db.Model):
    """Factor de Ocupación por Aerolínea del mes: solo trae el mes del informe
    (2025 vs 2026, no histórico de 12 meses), pero desagrega individualmente
    aerolíneas internacionales que en AirlineMonthly quedan dentro de 'Otros'
    (Copa, Iberia, Sky, Avianca, Lufthansa, United, Boliviana, Air Europa)."""
    __tablename__ = 'airline_load_factor_snapshot'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)      # 'cabotaje' | 'internacional'
    aerolinea = db.Column(db.String(80), nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)           # 1-12, mes del informe
    lf_2025 = db.Column(db.Float, nullable=False)          # 0.0 - 1.0
    lf_2026 = db.Column(db.Float, nullable=False)          # 0.0 - 1.0
    variacion_pp = db.Column(db.Integer, nullable=False)   # puntos porcentuales
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('tipo', 'aerolinea', 'anio', 'mes', name='uq_airline_lf_snapshot'),
    )


class AirlineUploadLog(db.Model):
    __tablename__ = 'airline_upload_log'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())
    registros_insertados = db.Column(db.Integer)
    registros_actualizados = db.Column(db.Integer)
    warnings = db.Column(db.Text)


class FuelSale(db.Model):
    """Ventas de combustible por vuelo (m³ despachados x precio USD/l = ingreso USD),
    agrupadas por VUELO+AEROPUERTO+LLAA+MES+AÑO. Viene de la hoja "Resumen" generada
    por la macro de Excel a partir del reporte de despacho. Se muestra en el mapa
    al hacer click sobre el círculo de un aeropuerto."""
    __tablename__ = 'fuel_sale'
    id = db.Column(db.Integer, primary_key=True)
    vuelo = db.Column(db.String(20), nullable=False)
    aeropuerto = db.Column(db.String(80), nullable=False)
    llaa = db.Column(db.String(80), nullable=False)          # línea aérea / cliente
    destino = db.Column(db.String(20))
    cant_vuelos = db.Column(db.Integer, nullable=False, default=0)
    volumen_m3 = db.Column(db.Float, nullable=False, default=0.0)
    precio_usd_l = db.Column(db.Float)
    ingreso_usd = db.Column(db.Float, nullable=False, default=0.0)
    mes = db.Column(db.Integer, nullable=False)               # 1-12
    anio = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('vuelo', 'aeropuerto', 'llaa', 'anio', 'mes', name='uq_fuel_sale'),
    )


class FuelSaleUploadLog(db.Model):
    __tablename__ = 'fuel_sale_upload_log'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())
    mes = db.Column(db.Integer)
    anio = db.Column(db.Integer)
    registros_insertados = db.Column(db.Integer)
    registros_actualizados = db.Column(db.Integer)
    warnings = db.Column(db.Text)
    
class AdminFile(db.Model):
    """Archivos sueltos (CSV/TXT) subidos desde /admin solo para guardarlos y poder
    bajarlos después desde otra máquina -- no se procesan ni se leen para nada, es un
    simple guardado/descarga. Se guardan en la base (no en disco): el filesystem de
    Render free tier es efímero y no sobrevive a un redeploy."""
    __tablename__ = 'admin_file'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer, nullable=False)
    content = db.Column(db.LargeBinary, nullable=False)
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())

class AirportAlias(db.Model):
    """Traduce nombres de aeropuerto que vienen de fuentes externas (ej. el Excel de
    despacho de combustible, en MAYÚSCULAS/sin tildes: 'COMODORO RIVADAVIA') al nombre
    canónico que usa el mapa en la tabla Airport (ej. 'Comod. Rivadavia'). Los casos de
    solo mayúsculas/tildes (EZEIZA -> Ezeiza) se resuelven solos por normalización y no
    necesitan alias; esta tabla es solo para abreviaturas realmente distintas."""
    __tablename__ = 'airport_alias'
    alias = db.Column(db.String(80), primary_key=True)   # se guarda ya normalizado (mayúsculas, sin tildes)
    airport_name = db.Column(db.String(80), nullable=False)  # debe existir en Airport.name
    created_at = db.Column(db.DateTime, server_default=db.func.now())
