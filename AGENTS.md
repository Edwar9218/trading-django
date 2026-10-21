# AGENTS.md — contexto técnico del proyecto

> Memoria técnica del proyecto para cualquier IA que trabaje acá.
> Basado en el código, la configuración y el historial de git reales.
> Lo que no pudo determinarse está marcado como **No determinado**.
>
> Última sincronización: commit `dd74dd2` (arranque con túnel privado) en la rama `feature/sr-fractal`.

---

## 1. Identidad del proyecto

**Nombre**: Tablero de Canales (`trading_django` en `config/celery.py`).

**Qué es**: aplicación web Django de análisis técnico de Forex para uso
personal/local. Lee velas de **MetaTrader 5** instalado en la misma PC,
calcula canales de soporte/resistencia, filtro de Kalman y un estado
S-P-N (Soporte / reSistencia / Neutro), y los muestra en dos pantallas:
un **tablero** multi-divisa y un **gráfico** detallado con indicadores.

**Problema que resuelve**: revisar el estado de muchas divisas ×
temporalidades sin abrir un gráfico por cada una, y sin esperar el
cálculo pesado al cargar la página.

**Decisión central que define la arquitectura**: el cálculo pesado lo
hace **Celery en segundo plano** cada 5 minutos y guarda un snapshot; el
navegador **solo lee** ese snapshot. Nunca dispara el cálculo al abrir la
página.

**Usuarios**: multi-usuario con login. Cada usuario tiene su watchlist,
sus dibujos, sus snapshots y sus preferencias de indicadores.

**Origen**: port de un proyecto Flask anterior (`servidor.py`,
`index.html`). Varios módulos conservan el contrato de la API vieja a
propósito — ver Decisiones técnicas.

---

## 2. Estado actual

### Funciona (probado end-to-end según README y código)
- Login / registro / logout multi-usuario, perfil automático por señal.
- Tablero: watchlist de divisas × temporalidades, snapshots S-P-N,
  polling cada 60 s, overlay de progreso, bloqueo de pestaña única.
- Tablero: **"+ Agregar" para divisas y para temporalidades**, validado
  contra MT5 antes de sumar nada (ver §5).
- Tablero: **medidor de fuerza del USD** (−100..100) con veredicto
  multi-temporalidad, calculado en el cliente.
- **Watchlist compartida tablero ↔ gráfico**: el buscador de Divisa y el
  selector de Timeframe del gráfico salen de lo que se sigue en el
  tablero y se actualizan sin recargar.
- Cálculo en Celery con sistema de generación (cancela trabajo obsoleto)
  y prioridades (lo manual pasa antes que el barrido automático).
- Gráfico: velas, 4 canales, Kalman, Smart Money Flow, "Cargar más
  historial", 2 pantallas, multi-temporalidad, zoom/scroll/crosshair
  sincronizados entre pantallas.
- Gráfico: **Modo Online / Modo Backtesting** con reproductor tipo bar
  replay y **vela en vivo** (sondeo cada 2 s) en Modo Online.
- Gráfico: botón para apagar/encender todos los indicadores de golpe.
- Indicadores de navegador: KN Smart TP/SL, S/R Avanzado, S/R Fractal,
  Secuencia estructural.
- Persistencia de dibujos por usuario + divisa + temporalidad.
- Preferencias de checkboxes persistidas por usuario (11 campos).
- Acceso desde otro equipo por un **túnel privado** (Microsoft Dev Tunnels):
  `arrancarTunnel.bat` lo levanta junto al resto. Probado por el dueño desde el
  celular con datos móviles, funcionando.

### Parcialmente implementado
- **Herramientas de dibujo**: ⚠️ **el README está desactualizado acá.**
  Dice que "ahora solo línea horizontal", pero el toolbar de
  `grafico.html` tiene las 5 herramientas (`trendline`, `hline`, `fib`,
  `brush`, `text`) con hit-test, drag y persistencia. Esa frase del
  README quedó del commit de la migración inicial y el port posterior
  (`c39297a`) la dejó obsoleta. **No tomar el README como pendiente
  real sin verificar.**
- **S/R Avanzado**: calculado y dibujado, pero con partes apagadas a
  propósito (`SR_MOSTRAR_BANDERAS = false` en `grafico.html:2615`; las
  flechas de ruptura, cruces de invalidación y niveles R→S/S→R se
  apagaron en el commit `2c7192d`). No borrar ese código: está desactivado
  por decisión, no por estar roto.
- **Tests**: tres suites JS (`chartview/tests_js/`; la de "Exportar caso" solo
  cubre los helpers puros, no el armado del JSON ni la descarga), tests Django en
  `chartview/tests.py` (`SRFractalTests`, `BotonTodosTests`) y en
  `dashboard/tests.py` (16 tests: validación contra MT5 con un MT5
  simulado, sincronización tablero↔gráfico, `api/watchlist/`). El resto
  de los `tests.py` son stubs vacíos de `startapp`.
- **Validación contra MT5 (`validar_item`)**: cubierta por tests con un
  MT5 simulado (`_FakeMt5` en `dashboard/tests.py`) y **probada a mano por
  el dueño contra un terminal real, funcionando**. Falta ejercitar con
  calma los casos límite (símbolos con sufijo del broker, MT5 cerrado).

### Pendiente / no hecho
- Endurecimiento para producción: `DEBUG=False`, `ALLOWED_HOSTS`, HTTPS,
  SECRET_KEY obligatoria (hay fallback inseguro en `settings.py`).
- Backtest estadístico real de la Secuencia estructural sobre histórico
  de varios símbolos (el motor ya devuelve los datos, falta la vista).
- Tests de `core/engine/` (la lógica más pesada del proyecto no tiene
  ninguno).

### Problemas conocidos
Ver sección 10.

---

## 3. Tecnologías

Detectadas en `requirements.txt`, `settings.py` y los templates.

| Capa | Tecnología | Versión declarada |
|---|---|---|
| Backend | Django | `>=6.0,<6.1` |
| Lenguaje | Python | No determinado (no hay `python_requires`) |
| Tareas async | Celery | `>=5.6` |
| Scheduler | django-celery-beat | `>=2.7` |
| Resultados | django-celery-results | `>=2.5` |
| Broker | **filesystem** por defecto; Redis opcional | redis `>=5.0` |
| Base de datos | SQLite | archivo `db.sqlite3` (gitignored) |
| Cálculo | pandas, numpy | sin pin |
| Gráficos PNG | matplotlib (backend `Agg`) | sin pin |
| Datos de mercado | MetaTrader5 | solo `sys_platform == "win32"` |
| Windows | pywin32 | solo win32 |
| Config | python-dotenv | `>=1.0` |
| Frontend charting | lightweight-charts | `4.1.3` (CDN unpkg) |
| Frontend | JS vanilla, sin framework ni build | — |
| Túnel (opcional) | CLI `devtunnel` (Microsoft Dev Tunnels, vista previa pública) | 1.0.2115 (la que usa el dueño) |

**Sin sistema de build de frontend**: no hay `package.json`, webpack ni
npm. Los `.js` se sirven como estáticos de Django y se prueban con
`node archivo.test.js` a secas.

**Plataforma**: pensado para **Windows** con MT5 instalado y abierto
(ver `arrancar.bat`, `apagar.bat`, `--pool=solo`). El código degrada con
gracia si MT5 no está (`Mt5Unavailable`), pero sin datos reales.

---

## 4. Arquitectura

### Organización

```
config/          settings, urls raíz, celery
core/engine/     TODA la lógica de cálculo — framework-agnostic
accounts/        login, registro, PerfilUsuario (preferencias)
dashboard/       tablero: watchlist, snapshots, tareas Celery
chartview/       gráfico detallado + APIs + indicadores JS
drawings/        persistencia de dibujos
templates/       base.html
broker/          cola de Celery en disco (in/out/processed, gitignored)
cargando.html    pantalla de bienvenida que abre arrancar.bat (ver §5)
arrancar.bat / apagar.bat                arranque y apagado en 3 ventanas CMD, sin túnel
arrancarTunnel.bat / apagarTunnel.bat    lo mismo + túnel privado (4 ventanas)
```

`core/` y `chartview/` no tienen migraciones propias con modelos:
`core/models.py` y `chartview/models.py` están vacíos a propósito.

### Capas y responsabilidades

1. **`core/engine/`** — Python puro (pandas/numpy). **No importa Django
   ni Flask.** Recibe parámetros, devuelve dicts serializables.
   - `analysis.py` — orquestador. `compute_analysis()` (gráfico),
     `compute_tf_row()` / `compute_multi_symbol_spn()` (tablero),
     `fetch_mt5_candles()`, `mt5_session()`, señal KN del tablero.
   - `auto_channels.py` — canales por pivotes + containment ratio, filtro
     de Kalman de 2 estados, breakouts. El archivo más grande (1675 líneas).
   - `smart_money_flow.py` — oscilador compuesto (momentum + CMF + MFI)
     con pivotes sobre el oscilador.
   - `channel_breakout_status.py` — clasificación S-P-N + distancia + rotura.
   - `trading_defaults.py` — defaults (ex `config.py` del proyecto Flask).
   - `mt5_export.py` — script CLI independiente. **No se importa como
     módulo** (hace `sys.exit(1)` al importarse sin MT5); `analysis.py`
     duplica sus dos tablas de mapeo a propósito.

2. **Vistas Django** — capa fina. Envuelven `core/engine` en
   `JsonResponse`. No contienen lógica de cálculo.

3. **Celery** (`dashboard/tasks.py`) — único lugar que dispara cálculo
   pesado. Escribe `TableroSnapshot`.

4. **Frontend** — JS vanilla en los templates + módulos puros en
   `chartview/static/chartview/`.

### Flujo de datos — tablero (el camino principal)

```
Celery beat (cada 5 min)
  └─> refrescar_todos_los_tableros()
        └─> refrescar_tablero_usuario(user_id, generacion_esperada)
              ├─ FASE 1: mt5_session() única, trae TODAS las velas
              ├─ FASE 2: ProcessPoolExecutor (SPN_CALC_WORKERS=min(4,cpus))
              │           calcula canales + S-P-N por (divisa, tf)
              └─ escribe TableroSnapshot (JSONField)

Navegador → GET /api/snapshot/ (solo SELECT, sin MT5) → tabla
```

### Flujo de datos — gráfico

```
Navegador → GET /grafico/api/datos/?symbol&timeframe&hasta
          → analysis.compute_analysis()  [síncrono, sí toca MT5]
          → { candles, channels, kalman, pivots, smf,
              posicion_canales, signals, ... }

Los indicadores (KN, S/R Avanzado, S/R Fractal, Secuencia) se calculan
en el NAVEGADOR sobre esas mismas velas — no piden nada más al servidor.

Modo Online → GET /grafico/api/vela_actual/ cada 2 s (fetch_vela_actual)
              → solo la última vela, aplicada con candleSerie.update()
Modo Backtesting → mismo /datos con `hasta` (fecha+hora); el reproductor
                   avanza vela a vela (piso de 300 ms entre pedidos)
```

### Flujo de datos — watchlist compartida (tablero ↔ gráfico)

```
Tablero: "+ Agregar" → POST /api/validar/ → analysis.validar_*_mt5()
         ├─ no existe → mensaje, NO se agrega nada
         └─ existe    → se tilda + guardarWatchlist() (POST /api/watchlist/)
                          └─ localStorage["watchlist_cambio"] = Date.now()

Gráfico: sincronizarWatchlist() → GET /grafico/api/watchlist/ (solo SELECT)
         se dispara por: evento "storage", foco/visibilidad de la pestaña,
         y cada 60 s. Reescribe el datalist de Divisa y el select de
         Timeframe de cada panel SIN tocar lo que el panel ya tiene elegido.
```

---

## 5. Funcionalidades

### Tablero (`dashboard/`)
- Watchlist de divisas × temporalidades por checkboxes.
- Tarjetas por divisa con mini-tabla (TF × 4 canales).
- **Optimización**: si el usuario solo *destilda*, no se dispara Celery
  (los datos que quedan ya son válidos). Solo *agregar* recalcula.
- **Pestaña única** (patrón WhatsApp Web): solo una pestaña queda activa,
  coordinada por `localStorage`, para no duplicar recálculos.
- Overlay de progreso durante el recálculo.
- **"+ Agregar" divisa / temporalidad** (`validar_item`): consulta a MT5
  antes de agregar. Divisa → `symbol_info()` (+ `symbol_select` si no está
  visible en Market Watch); si no existe, mensaje con símbolos parecidos
  del broker (ej. `EURUSD.m`). Temporalidad → comprueba la constante
  `TIMEFRAME_*` del paquete MetaTrader5; acepta `h4`, `4h`, `15m`, `mn1`.
  Si MT5 no está disponible responde **503 y no agrega nada**. Al validar
  bien, guarda la selección al instante (dispara el recálculo normal).
- Las temporalidades agregadas a mano (ej. `M5`) se muestran junto a las
  12 sugeridas, ordenadas de menor a mayor.
- **Medidor de fuerza del USD**: puntaje −100..100 desde las señales S-P-N
  y KN de `data.resultados`, ponderado por tipo de señal, canal, cercanía
  al borde y temporalidad (peso relativo a las TFs tildadas). Bono/castigo
  por consistencia entre pares y veredicto multi-temporalidad. Todo en el
  cliente (`home.html`), sin tocar el backend.
- Clic en una temporalidad de una divisa abre el gráfico con
  `?symbol=&timeframe=` ya cargados.

### Gráfico (`chartview/`)
- Velas + 4 canales (largo, largo inverso, corto, corto inverso) con
  % de calidad, Kalman, SMF, señales.
- 2 pantallas; fecha compartida. **Sincronización entre pantallas**
  (`sincronizarPantallas`): zoom/scroll/reset bidireccional en modo "Por
  fecha" (sirve con distinta temporalidad) o "Por velas", crosshair
  desactivable y botón ⇥ Alinear. Solo se propaga la interacción del
  usuario, no las cargas de datos. Preferencias en `localStorage`
  (`syncCfg`).
- **Modo Online / Backtesting**: "Hasta" es fecha+hora (`_parse_hasta`
  acepta ambos formatos) y está en **hora del servidor MT5**, no en la del
  computador (ver regla 15). El selector son 3 controles en 24 h (día,
  hora, minuto) que escriben en el input oculto `#fecha-global`, que sigue
  siendo la fuente de verdad. Reproductor ⏮ ▶/⏸ ⏭ con velocidad; se frena
  en la hora actual del servidor; salta los fines de semana (cierre
  viernes 24:00, apertura domingo 23:00, en hora del servidor) y reintenta
  hasta hallar una vela nueva.
- **Hora del servidor MT5** (`api/hora_servidor/` → `hora_servidor_mt5()`):
  mide el desfase con UTC usando el tick más reciente de varios símbolos
  líquidos. Con el mercado cerrado el tick es viejo y no sirve, así que
  cae en orden a: última medición válida → `MT5_SERVER_UTC_OFFSET_HORAS`
  del `.env` → estimación (GMT+3 con horario de verano de EE.UU., GMT+2
  sin él). El campo `fuente` (`tick`/`cache`/`configurado`/`estimado`) se
  muestra junto al selector como `GMT+3`, con "(estimado)" si corresponde.
- **Captura rápida de vela** (solo Backtesting): paseando el mouse, una
  etiqueta `dd/mm/aaaa HH:mm` muestra la vela bajo el cursor; un clic
  limpio (movimiento ≤ 4 px, no doble clic) la deja seleccionada (línea
  amarilla + aviso); **Enter** escribe su hora de apertura en `#fecha-global`
  y dispara su `change`, que ya recarga los paneles; **Esc** cancela. Se
  ignora con el replay corriendo, en Modo Online, con una herramienta de
  dibujo activa, al clicar un dibujo o con el foco en un campo/selector/
  botón. La selección se descarta si cambia divisa/temporalidad o si la
  vela ya no está cargada. Código: `activarCapturaRapida()` en
  `grafico.html` (bloque entre `// ── Captura rápida: inicio` y `fin`),
  con una sola llamada desde `init()` de `crearPanel`. Funciona también sobre las
  velas posteriores del futuro crudo (`futuroSerie` / `futuroVelas`, que esa llamada
  le entrega): clic + Enter sobre una de ellas vuelve a cortar el gráfico en esa vela.
- **Futuro crudo** (Backtesting, tras clic + Enter): el gráfico se corta en la
  vela elegida, con todos sus indicadores, y a la derecha aparecen las velas
  posteriores SIN indicadores (hasta 300, serie `futuroSerie` de colores
  apagados) para ver cómo siguió el precio. `futuroCrudo = {hasta}` lo fija
  Enter o el buscador manual de fecha/hora/minuto (`desdeControles`; el
  reproductor y ⏮ ⏭ escriben `.value` por código y NO lo activan); ambos respetan la casilla "Ver lo
  posterior" de la barra de Backtesting (`#chk-futuro`, `verFuturoCrudo`,
  recordada en `localStorage["ver_futuro_crudo"]`, encendida por defecto; al
  cambiarla se recarga el corte actual con o sin lo posterior, SIN mover el
  encuadre: `recargarPaneles({centrar:false})`; al entrar a Backtesting con la
  casilla activa ya se pide lo posterior, y al PAUSAR el replay con ⏸ se muestra
  lo posterior a la vela donde quedó, `mostrarPosteriorAlPausar`); `cargarDatos` lo atiende solo si el "Hasta" coincide, así cualquier
  otra carga (replay, otra fecha, otro modo) lo borra sola. Las velas
  posteriores NO entran en `loadedCandles` ni en ningún cálculo. Los
  indicadores no pueden proyectarse sobre esa zona: `limiteDerecha` recorta
  líneas de canales, KN y S/R (`hastaCorte`) y el canvas se recorta con `clip`
  en `redrawOverlay`. Endpoint: `api/velas_despues/?symbol&timeframe&desde=<time>&cantidad`
  (`fetch_mt5_newer_candles`); también sirve para pasar "qué pasó después" a
  mano junto con `api/datos/?...&hasta=`.
- **Encuadre al cargar** (`centrarEnUltimaVela`, opción `centrar` de `cargarDatos`
  y `recargarPaneles`): cada carga "de verdad" (botón Cargar, Enter, cambio
  Online/Backtesting, buscar fecha, clic + Enter, divisa/timeframe nuevos) deja
  la última vela analizada en el centro, conservando el zoom del usuario (160
  velas por defecto; si el ancho previo está fuera de 20-600 usa el defecto).
  Los pasos del reproductor y ⏮ ⏭ mantienen la posición. La vista se captura
  ANTES de vaciar/llenar `futuroSerie` (`vistaPrevia`), porque poner o quitar
  esas velas corre la escala de tiempo. OJO: los listeners de click/keydown le
  pasan el evento a `cargarDatos`; solo vale `opciones.centrar === true`.
- **Aviso "Cargando…" sobre el gráfico** (`mostrarVeloCarga` en `cargarDatos`):
  velo con spinner y el texto `Cargando SÍMBOLO TF · hasta fecha…`. Aparece
  solo si la carga pasa de 200 ms y nunca con el reproductor corriendo (si no,
  titilaría en cada vela). Es `pointer-events:none`, no estorba al ratón.
- **Exportar caso** (botón `#btn-exportar-caso`, `exportarCasos()` global +
  `exportarCaso()` por panel): descarga `caso_SIMBOLO_TF_AAAAMMDD_HHMM.json`
  (`formato: "caso-grafico/1"`) con un caso por pantalla visible. Cada caso
  tiene `meta`, `analisis` (solo lo sabido hasta la última vela: hasta 500
  velas, canales, Kalman, KN, S/R Avanzado, S/R Fractal, Secuencia) y
  `resultado_posterior` (aparte y marcado "MIRA AL FUTURO"): a 12/24/48 velas,
  máximo que subió, máximo que bajó y cierre, en precio, % y múltiplos de ATR
  (referencia: cierre de la última vela analizada). En Backtesting trae las
  velas posteriores aunque "Ver lo posterior" esté apagada; en Online no hay
  (`disponible: false` con el motivo). Si una de las 2 pantallas no se puede
  exportar, se exporta la otra y el motivo queda en `avisos`. Los helpers puros
  (`limpiarParaExport`, `atrSimple`, `resultadoPosterior`) viven entre los
  marcadores `// ── Exportar caso: helpers puros` y `// Fábrica de paneles`
  de `grafico.html`: el test los extrae de ahí, **no mover ni renombrar esos
  marcadores sin actualizar `exportar_caso.test.js`**. `estructuraUnicaSEQ()` la
  comparten el panel de Secuencia y el export, para que digan siempre lo mismo.
- **Pantalla de bienvenida al arrancar**: `arrancar.bat` abre
  `cargando.html` de inmediato y levanta servidor, worker y beat; la
  página sondea `http://localhost:8000/` y entra sola al tablero cuando el
  servidor responde. Si no existe, el `.bat` abre el tablero directo.
- **Acceso remoto por túnel privado** (`arrancarTunnel.bat` / `apagarTunnel.bat`):
  igual que `arrancar.bat` pero con una cuarta ventana, `Tunel - devtunnel`, que
  ejecuta `devtunnel host tablero.use2`. El túnel se crea una sola vez con
  `devtunnel create tablero` + `devtunnel port create tablero -p 8000 --protocol http`
  (devtunnel le agrega la región al nombre: el ID queda `tablero.use2`). Es
  **privado**: solo entra la cuenta de GitHub que lo creó. El link
  (`https://<id>-8000.use2.devtunnels.ms`) sale en la ventana del túnel; se arma con
  el ID del túnel, así que se espera estable mientras exista (sin verificar entre
  reinicios). En el `.env` hay que sumar `.devtunnels.ms` a `DJANGO_ALLOWED_HOSTS`.
  El `.bat` agrega `C:\devtunnel` al PATH de su proceso (el doble clic puede heredar un
  PATH viejo) y, si no encuentra la CLI, omite el túnel y arranca el resto. Los dos
  juegos de `.bat` usan los mismos títulos de ventana: `apagarTunnel.bat` apaga
  ambos modos, `apagar.bat` no cierra el túnel. No arrancar los dos juegos a la vez
  (puerto 8000 y cola de Celery).
- **Vela en vivo** (Modo Online): `api/vela_actual/` cada 2 s.
- **Zoom conservado** entre recargas (se captura justo antes de `setData`,
  relativo a la última vela; `fitContent()` solo al cambiar
  divisa/timeframe).
- **Precisión del eje de precio** con los `digits` reales de MT5
  (`symbol_info`), con respaldo inferido de la magnitud si el broker no
  los informa.
- **Layout compacto**: barra de navegación oculta (botón ⌂ la muestra),
  botón Filtros colapsable con contador, y Divisa/Timeframe/Cargar en la
  barra global con 1 pantalla. Botón "Cargar más historial" siempre
  visible.
- Botón **Apagar/Encender todo**: alterna los checkboxes disparando su
  propio evento `change` (no duplica lógica). "Pivot auto" queda afuera a
  propósito (es un modo de cálculo del servidor, no un dibujo).
- Divisa y Timeframe se alimentan de la watchlist del tablero; un
  `?timeframe=` que no se sigue se agrega al selector en vez de ignorarse.
- Multi-temporalidad en pestaña nueva.
- Dibujo persistido: trendline, hline, fibo, brush, texto.
- "Cargar más historial" (`api_velas_extra`, no recalcula canales).

### Indicadores que se calculan en el navegador

Todos comparten el mismo patrón: **módulos puros, causales, probados con
Node**, que reciben las velas ya cargadas.

| Indicador | Dónde vive | Preferencia | Qué hace |
|---|---|---|---|
| KN Smart TP/SL | inline en `grafico.html` (`calcularKN`) | `pref_kn_signals` | cruce EMA5/EMA13 + TP/SL por ATR (port de Pine) |
| S/R Avanzado | inline (`calcularSR`) | `pref_sr_avanzado` | pivotes confirmados, ruptura, retest R→S/S→R (parcialmente apagado) |
| S/R Fractal | `static/chartview/fractal_sr.js` | `pref_sr_fractal` | fractales multiescala agrupados en zonas con fuerza 0-100 — *dónde* está la barrera |
| Secuencia estructural | `static/chartview/secuencia_fractal.js` | `pref_secuencia` | máquina de estados fractal→ruptura→desplazamiento→imbalance→retesteo→continuación, score 0-100 — *qué hace* el precio tras romper |

**Secuencia estructural** (la más reciente, commit `f87138c`):
- Estados: `FRACTAL → BREAKOUT → DISPLACEMENT → IMBALANCE → RETEST →
  CONFIRMATION → COMPLETED`, más `INVALIDADO` con motivo explícito.
- Score = suma auditable de etapas (20/20/20/20/10/10).
- Multitemporalidad opcional (`htfMult`): estructura en velas agregadas,
  seguimiento en la base.
- `stats`: conteos, % de continuación, medianas en ATR, desglose por
  sesión y por motivo de invalidación.
- Panel lateral corto: muestra UNA sola estructura (la viva más avanzada, alcista
  o bajista, elegida entre `mejorAlcista`/`mejorBajista`; si no hay viva, la
  cerrada más reciente). Dos a la vez confundían al dueño. Lleva 6 puntos de
  avance (● hecha, ◐ la que falta, ○ pendiente) y UNA frase de lo que falta
  ("Falta retesteo: vuelta a …", "Falta nuevo máximo sobre …"). Puntaje,
  estadísticas, capas y alertas quedan bajo "más" (`seq_panel_mas`). Una
  estructura cerrada hace >5 velas se ve apagada, con su antigüedad. El motor
  sigue devolviendo `actual`, `mejorAlcista` y `mejorBajista`.
- Plazo de retesteo por temporalidad (`PLAZOS_POR_TF`): D1 = 60 velas; el resto
  usa `DEFAULTS` (120). Calibración (XAUUSD/EURUSD, recorrido vela a vela): solo D1 necesitaba
  ajuste. H1, H4 y M15 quedan con `DEFAULTS`: la estructura mostrada es siempre
  reciente (edad mediana del imbalance 2-24 velas) y 240 de plazo sale peor.

---

## 6. Datos y persistencia

**Base**: SQLite (`db.sqlite3`, gitignored). No hay configuración para
otro motor.

### Modelos

**`accounts.PerfilUsuario`** — OneToOne con `User`, creado por señal
`post_save` (`accounts/signals.py`).
- `tema_oscuro`, `creado`
- 11 preferencias de checkboxes: `pref_largo`, `pref_mediano`,
  `pref_corto`, `pref_relleno`, `pref_kalman`, `pref_auto_pivot`,
  `pref_tablero_canales`, `pref_kn_signals`, `pref_sr_avanzado`,
  `pref_sr_fractal`, `pref_secuencia`
- `watchlist_generacion` (`PositiveIntegerField`) — contador para
  cancelar tareas Celery obsoletas.

**`dashboard.DivisaSeguida`** — `(usuario, simbolo)` único, con `orden`.

**`dashboard.TemporalidadSeguida`** — `(usuario, timeframe)` único.
Acepta cualquier temporalidad de `_TF_MAP` (14: M1…MN1), no solo las 12
sugeridas de `ALL_TIMEFRAMES`. `guardar_watchlist` descarta las que MT5
no soporta.

**`dashboard.TableroSnapshot`** — el resultado ya calculado.
- `(usuario, simbolo, timeframe)` único; `datos` es `JSONField`.
- `timeframe="*"` es un **caso especial**: marca de error general de la
  divisa, no una temporalidad real. Tratarlo aparte en cualquier query.

**`drawings.Dibujo`** — `(usuario, simbolo, timeframe, tipo, datos)`.
`datos` guarda el JSON del canvas tal cual, sin reinterpretar.

### Migraciones

`accounts` va por la `0007`. Las `0002`→`0007` son casi todas "agregar
un `pref_*`". **Convención**: cada indicador nuevo con checkbox propio
agrega un `BooleanField(default=False)` y su migración.

`core` y `chartview` no tienen migraciones con modelos (vacías a propósito).

---

## 7. Reglas de negocio

Cosas que no deberían romperse:

1. **El navegador nunca dispara el cálculo pesado del tablero.** Solo lee
   `TableroSnapshot`. Si una vista empieza a llamar a `compute_*` de
   forma síncrona en el request del tablero, se rompe la razón de ser de
   toda la arquitectura.
2. **`core/engine/` no importa Django.** Es Python puro. Mantenerlo así.
3. **Las fórmulas de `auto_channels.py` / `smart_money_flow.py` /
   `channel_breakout_status.py` son un port sin cambios** del proyecto
   Flask. No "mejorarlas" sin pedirlo explícitamente.
4. **Los indicadores del gráfico son causales — no repintan.** Un evento
   aparece en la vela que lo confirmó y no se mueve después. Cualquier
   cambio que use datos futuros para decidir una señal histórica rompe la
   propiedad central y las pruebas lo detectan.
5. **Las métricas que sí miran al futuro** (MFE/MAE de backtest) van
   separadas, marcadas, y nunca alimentan un score o un estado.
6. **Un score alto no es una probabilidad de acierto.** Es confluencia
   estructural. No usar lenguaje de garantía en la UI ("seguro",
   "ganador", "garantizado").
7. **`timeframe="*"`** en `TableroSnapshot` = error de divisa, no un TF.
8. **El sistema de generación** (`watchlist_generacion`): toda tarea
   Celery que escriba snapshots debe chequearlo antes de escribir, o
   puede pisar resultados más nuevos.
9. **Una sola conexión MT5 a la vez**, serializada vía `mt5_session()`.
   El paralelismo es del *cálculo*, no del *fetch*.
10. **`mt5_export.py` no se importa como módulo** (hace `sys.exit(1)`).
11. **Nada se agrega a la watchlist sin confirmar que existe en MT5.** Si
    MT5 no responde, se avisa y no se agrega ("a ciegas" no).
12. **La watchlist del tablero es la fuente única** de divisas y
    temporalidades para ambas pantallas. El gráfico solo la lee
    (`api/watchlist/`); nunca la modifica.
13. **`api/validar/` y `api/watchlist/` no disparan cálculo pesado.**
    Validar toca MT5 (rápido); watchlist del gráfico es solo SELECT.
14. **El JSON nunca debe llevar NaN/inf**: `compute_analysis` pasa su
    resultado por `_limpiar_nan()` (→ `null`). Un NaN hace fallar
    `res.json()` en el navegador aunque el servidor responda 200.
15. **El `time` de cada vela y el selector "Hasta" están en la MISMA hora:
    la del servidor MT5** (no UTC real, no la del computador). MT5 guarda
    `time` como segundos desde 1970 contados con el reloj del bróker, y
    `_hasta_como_hora_servidor()` marca ese "hasta" como UTC a propósito
    para que `copy_rates_range` lo entienda igual. En el navegador, un
    `time` de vela se lee con `getUTC*` y **sin conversión de zona**; las
    fechas del selector se arman con esos mismos números. Convertir a otra
    zona desfasa la carga.
16. **`#fecha-global` es la fuente de verdad del "Hasta"** (input oculto,
    interceptado para que escribirlo desde código actualice los 3 controles
    visibles). Quien quiera "ir a una fecha" escribe ahí y dispara
    `change`; ese evento ya recarga los paneles. No crear otro camino de
    carga.

---

## 8. Convenciones

- **Idioma**: todo en español — nombres, comentarios, docstrings, UI.
  Mantenerlo.
- **Comentarios largos explicando el *porqué***, no el *qué*. Es el
  rasgo más marcado del código: bloques que explican la decisión, el
  límite técnico y lo que se descartó. Seguir ese estilo.
- **Cabeceras de módulo** con `"""..."""` o `/* ═══ */` describiendo qué
  hace y qué decisión lo justifica.
- **Separadores visuales**: `# ── Sección ──` y `# ═══` en Python,
  `// ──` y `/* ═══ */` en JS.
- **Commits**: `tipo(ámbito): descripción` en español — `feat`, `fix`,
  `perf`, `ajuste`, `hardening`. Los primeros commits del repo no siguen
  la convención; los recientes sí.
- **Endpoints**: `api_*` en `views.py`, rutas `api/<cosa>/`.
- **JS de indicadores**: IIFE con `module.exports` para Node y
  `root.<Nombre>` para el navegador; internals expuestos en `_internals`
  para poder probar cada función por separado.
- **Tests JS**: `chartview/tests_js/<modulo>.test.js`, `assert` nativo,
  sin framework. Se corren con `node <archivo>`.
- **Preferencias**: nombre corto en el JS (`"secuencia"`) mapeado al
  campo real en `_CAMPOS_PREFERENCIA` (`chartview/views.py`).

---

## 9. Decisiones técnicas

| Decisión | Por qué | Afecta |
|---|---|---|
| Snapshots + Celery en vez de cálculo al request | la página carga rápido sin importar cuánto tarde MT5 | toda la arquitectura del tablero |
| Broker Celery **filesystem** por defecto | cero instalación extra en una sola PC; Redis es opcional cambiando `.env` | `settings.py`, `broker/` |
| `--pool=solo` en el worker | obligatorio en Windows | `tasks.py`, README, `.bat` |
| `ProcessPoolExecutor` aparte para el cálculo | el paralelismo va en el cálculo, no en el fetch de MT5 | `tasks.py` |
| Límite de hilos BLAS a 1, **antes** de importar numpy | 4 procesos × N hilos se pisan entre sí; fijarlo tarde no tiene efecto | cabecera de `tasks.py` |
| `django.setup()` al tope de `tasks.py` | en Windows el pool re-importa el módulo y Django no está listo → `AppRegistryNotReady` | `tasks.py` |
| Sistema de generación | cortar trabajo obsoleto entre divisa y divisa; lo manual gana | `PerfilUsuario`, `tasks.py`, `dashboard/views.py` |
| Prioridades de Celery (0 manual / 9 barrido) | reordena lo que *espera* en la cola; no interrumpe lo que ya corre | `config/celery.py` |
| Indicadores calculados en el navegador | no cuestan nada al servidor, usan las velas ya cargadas | los 4 indicadores |
| Módulos JS puros con `_internals` | poder probar cada fase por separado con Node, sin navegador ni build | `fractal_sr.js`, `secuencia_fractal.js` |
| Causalidad estricta (sin repintado) | una señal histórica debe ser reproducible; si repinta, cualquier backtest miente | ambos indicadores fractales |
| Contrato de API idéntico al Flask original | el front-end no tuvo que cambiar cómo pide datos, solo la URL | `chartview/views.py` |
| `_TF_MAP` duplicado en `analysis.py` | evitar el `sys.exit(1)` de `mt5_export.py` al importarlo | `analysis.py` |
| Snapshot de error como `timeframe="*"` | marcar la divisa entera sin inventar un TF falso | `TableroSnapshot`, `api_snapshot` |
| Reemplazo completo de dibujos al guardar | más simple que sincronizar dibujo por dibujo | `api_dibujos_guardar` |
| `Cache-Control: no-store` en `api_snapshot` | el polling mostraba datos viejos por caché del navegador | `dashboard/views.py` |
| Pestaña única por `localStorage` | evitar que dos pestañas dupliquen el recálculo | `home.html` |
| Validar contra MT5 en un endpoint aparte (`api/validar/`), que no guarda | quien guarda sigue siendo `guardar_watchlist`; el flujo de generación/Celery no cambia | `dashboard/views.py` |
| Validar la temporalidad por la constante del paquete, no por el terminal | una temporalidad no vive en el broker; solo hace falta que exista en el API | `analysis.validar_timeframe_mt5` |
| `TIMEFRAMES_SOPORTADOS` (de `_TF_MAP`) para validar, `ALL_TIMEFRAMES` solo para sugerir | si no, una TF agregada a mano (M5) se descartaba en silencio al calcular | `analysis.py` |
| Gráfico se sincroniza por `storage` + foco + 60 s | `storage` solo avisa entre pestañas del mismo navegador; el sondeo es la red de seguridad | `grafico.html` |
| Caché de velas en memoria (TTL 5 s en vivo, máx. 30 entradas) | el replay no golpea el terminal MT5 en cada tick | `analysis.fetch_mt5_candles` |
| Vela en vivo por endpoint liviano en vez de recargar `/datos` | se ve el precio "vivir" sin costo ni parpadeo | `api/vela_actual/` |
| Botón "apagar todo" dispara `change` en cada checkbox | reutiliza la lógica de cada indicador; uno nuevo queda cubierto solo | `grafico.html` |
| "Hasta" en hora del servidor MT5, medida con ticks (`hora_servidor_mt5`) | pasar un datetime sin zona hacía que la librería lo tomara como hora local de la PC y desfasaba el backtesting (3 h en un servidor GMT+3) | `analysis.py`, `grafico.html` |
| Selector "Hasta" en 24 h con 3 controles sobre un input oculto | el `datetime-local` nativo muestra a. m./p. m. según Windows y no se puede forzar | `grafico.html` |
| Captura rápida: escribir `#fecha-global` + `change` en vez de llamar a `cargarDatos` | el `change` ya pausa el replay y recarga todos los paneles; llamar a `cargarDatos` cargaría solo uno y duplicaría lógica | `grafico.html` |
| Captura rápida sin checkbox ni interruptor | pedido expreso del dueño: no llenar el gráfico de checkboxes; Enter siempre pega y carga | `grafico.html` |
| Captura rápida solo escucha el mouse, nunca frena eventos ni usa el canvas de dibujo | el arrastre/zoom/paneo y los dibujos quedan exactamente como antes; la marca y la etiqueta son `div` con `pointer-events:none` | `grafico.html` |
| Casilla "Ver lo posterior" (única casilla de esta función) | pedido expreso del dueño para poder apagarlo; sigue sin haber casilla en la captura rápida en sí | `grafico.html` |
| Exportar caso en dos bloques (`analisis` / `resultado_posterior`) que nunca se mezclan | lo posterior solo sirve para medir; si se mezclara con lo causal, cualquier estudio posterior mentiría (regla 5) | `grafico.html` |
| Test de los helpers de Exportar caso extrae el código de `grafico.html` entre marcadores | no hay copia que se desactualice y no hace falta build ni framework | `exportar_caso.test.js` |
| Futuro crudo en una serie aparte + recorte de proyecciones de indicadores | las velas posteriores no deben tocar ningún cálculo ni verse "analizadas"; `limiteDerecha` se fija ANTES de dibujar para que nada se proyecte sobre ellas | `grafico.html` |
| `arrancar.bat` abre `cargando.html` antes de levantar nada | el navegador muestra algo de inmediato y entra solo cuando el servidor responde | `arrancar.bat`, `cargando.html` |
| Túnel privado con `devtunnel` para el acceso desde otra ciudad | no abre puertos del router ni deja el puerto público; solo entra la cuenta dueña del túnel. NUNCA `--allow-anonymous` | `arrancarTunnel.bat` |
| Dos juegos de `.bat` (con y sin túnel) con los mismos títulos de ventana | el dueño no siempre usa el túnel; con títulos iguales `apagarTunnel.bat` apaga ambos modos | `arrancar*.bat`, `apagar*.bat` |
| `arrancarTunnel.bat` suma `C:\devtunnel` al PATH de su proceso | el doble clic puede heredar un PATH viejo y no ver lo agregado después; las ventanas hijas lo heredan | `arrancarTunnel.bat` |
| Arranque desde VS Code (`tasks.json` con `runOn: folderOpen`) probado y descartado | con el sistema ya corriendo por `.bat`, abrir el proyecto en VS Code lanzaba un segundo servidor, worker/beat (tareas duplicadas) y túnel | historial (`c82c527` → `dd74dd2`) |

---

## 10. Problemas conocidos

1. **SECRET_KEY con fallback inseguro** en `settings.py` si no hay `.env`.
   Documentado, aceptable en local, bloqueante para producción.
2. **`ALLOWED_HOSTS` vacío por defecto** — solo sirve con `DEBUG=True`.
3. **Atado a Windows + MT5 abierto**. Sin MT5 no hay datos reales
   (`Mt5Unavailable`). En Linux/macOS el proyecto arranca pero el tablero
   queda en error.
4. **Con `--pool=solo` una tarea en curso no se puede interrumpir.** El
   corte por generación ocurre *entre* divisas, no a mitad de una.
   Documentado honestamente en `tasks.py`.
5. **Snapshots de error fantasma**: hubo dos arreglos (`1233912`,
   `8d132a9`) y queda una red de seguridad en `api_snapshot` que loguea
   un warning si reaparecen. Si aparece ese warning, algo lo reintrodujo.
6. **`core/engine/` sin tests.** Es la lógica más pesada y más delicada
   del proyecto.
7. **Umbrales de la Secuencia estructural calibrados solo parcialmente.**
   D1 usa `maxBarrasRetest: 60` (`PLAZOS_POR_TF`); H1/H4/M15 se revisaron con
   XAUUSD y EURUSD y no mostraron estructuras vencidas, así que siguen con los
   `DEFAULTS` (`cuerpoMult 1.5`, `rangoAtrMin 1.0`, `maxBarrasRuptura 40`,
   `maxBarrasRetest 120`). Otras divisas, W1/MN1 y historiales largos de
   intradía no se han medido.
8. **Estadísticas con muestras chicas.** `stats` sobre las velas cargadas
   de un solo símbolo da muestras de ~10 casos. En las pruebas hechas (200-830
   velas solapadas, un solo tramo de mercado) el acierto de la dirección a 12-24
   velas quedó entre 46% y 65%, sin ventaja demostrable. No concluir nada de ahí.
9. **Dependencias sin pin** (pandas, numpy, matplotlib). Una versión
   nueva puede romper el cálculo sin aviso.
10. **`grafico.html` tiene ~4630 líneas** con todo mezclado (CSS, HTML,
    JS de varios indicadores, replay, sincronización). Es el archivo más
    frágil de tocar. `home.html` ya va por 1354.
11. **matplotlib se importa siempre** en `auto_channels.py` aunque la web
    nunca genere PNG — herencia del script original.
12. **Sincronización del gráfico limitada al mismo navegador.** El aviso
    inmediato usa `localStorage`; entre navegadores/equipos solo llega por
    el sondeo de 60 s o al volver a la pestaña. No hay actualización en vivo
    (WebSocket).
13. **Símbolos con sufijo del broker** (`EURUSD.m`): `validar_item` no los
    resuelve solo; los sugiere y hay que escribirlos exactos.
14. **Atajo `?timeframe=`**: si la temporalidad no está en `_TF_MAP` se
    ignora (el selector no la agrega).
15. **El túnel depende de un servicio en vista previa pública** (Dev Tunnels: sin
    SLA, pensado para pruebas). Límites vistos con `devtunnel limits`: 5 GB de
    ancho de banda por usuario cada 30 días y 20 MB/s; un túnel sin uso caduca a
    los 30 días. El Modo Online (vela en vivo cada 2 s) y el reproductor de
    Backtesting (recarga `/datos` en cada paso) son lo que más consume. Alternativa
    evaluada, sin probar: Tailscale.
16. **Con el túnel el tablero es alcanzable desde internet** (privado, solo la
    cuenta dueña) y `settings.py` sigue con sus defaults: conviene `DJANGO_SECRET_KEY`
    propia en el `.env` y revisar `DEBUG`. Entrar por el túnel funcionó sin
    `CSRF_TRUSTED_ORIGINS` (no está en `settings.py`); si aparece un 403 de CSRF,
    agregarlo leyendo `DJANGO_CSRF_TRUSTED_ORIGINS` del `.env`.

---

## 11. Trabajo pendiente

Identificado en README, código y commits:

- [ ] Endurecer para producción: SECRET_KEY, `DEBUG=False`,
      `ALLOWED_HOSTS`, HTTPS.
- [ ] Corregir el README: la frase sobre herramientas de dibujo
      ("ahora solo línea horizontal") ya no es cierta.
- [ ] Vista de backtest agregado de la Secuencia estructural: correr
      `stats` sobre el watchlist completo y tabular por símbolo /
      temporalidad / sesión.
- [ ] Calibrar la Secuencia con más historia (meses de H1/M15, W1/MN1) y más pares.
- [ ] Tests de `core/engine/`.
- [ ] Pinear versiones de pandas/numpy/matplotlib.
- [ ] Evaluar partir `grafico.html` en archivos estáticos separados.
- [ ] Decidir si `validar_item` debe aceptar sufijos del broker.

---

## 12. Historial de cambios relevantes

Solo lo que ayuda a entender la evolución.

| Commit | Qué cambió |
|---|---|
| `ab57ea8` | **Migración inicial de Flask a Django**: tablero S-P-N, login multi-usuario, Celery |
| `c39297a` | Port completo de `grafico.html`: toolbar de dibujo, endpoints `api_velas_extra` / `api_config` / `api_ping`, persistencia de dibujos |
| `0286abe` | Preferencias de checkboxes persistidas por usuario |
| `d6da953` | **Paralelización del tablero** (ProcessPoolExecutor) + overlay de carga |
| `111b1a9` | Indicador KN Smart TP/SL |
| `2c7192d` | Indicador S/R Avanzado; se apagan banderas, flechas de ruptura y niveles R→S/S→R |
| `1233912`, `8d132a9` | Snapshots de error fantasma: fix + red de seguridad |
| `78dca31` | Bloqueo de pestaña única (patrón WhatsApp Web) |
| `1d65dda`, `0fff19a` | Señal KN en el tablero; distinguir rotura confirmada de potencial |
| `0bb027d`, `9bdf74f` | **S/R Fractal**: zonas por agrupación de fractales multiescala |
| `0493d44` | Optimización (sin descripción en el mensaje) |
| `f87138c` | **Secuencia estructural**: máquina de estados fractal→…→continuación, score 0-100, panel, alertas, `pref_secuencia` + migración `0007` |
| `3f97f0e` | Se agrega este `AGENTS.md` |
| `ad414e2` | Botón para apagar/encender todos los indicadores |
| `2927fef` | **Modo Backtesting** con reproductor tipo bar replay; "Hasta" pasa a fecha+hora |
| `6408af7` | **Vela en vivo** (`api/vela_actual/`), caché de velas MT5, precio con decimales completos |
| `8579db2` | Fix: `_limpiar_nan()` en `compute_analysis` (NaN rompía `res.json()`); se envían `digits` de MT5 |
| `b8a7ef7` | **Medidor de fuerza del USD** con veredicto multi-temporalidad en el tablero |
| `c3eb736`, `2f0b467` | Zoom conservado al recargar; layout compacto (Filtros colapsable, navegación oculta); se quita el toggle de info |
| `58300ef` | **Sincronización de zoom/scroll** entre las 2 pantallas (`sincronizarPantallas`) |
| `0e8ec72` | **"+ Agregar" temporalidades** y validación de divisas/temporalidades contra MT5 (`api/validar/`); **watchlist compartida** tablero↔gráfico (`api/watchlist/`, sincronización en vivo); tests en `dashboard/tests.py` |
| `8789fef` | **Pantalla de bienvenida** (`cargando.html`) que `arrancar.bat` abre mientras levanta el sistema |
| `c12aab2` | **"Hasta" en hora del servidor MT5** (`api/hora_servidor/`, `hora_servidor_mt5`) y selector en 24 h; el reproductor salta fines de semana en hora del servidor |
| `4a4d63d` | Secuencia: plazo de retesteo en D1 de 120 a 60 velas |
| `442fb4e` | Secuencia: panel corto con una sola estructura (puntos de avance + una frase, resto bajo "más"); calibración de H1/H4/M15 sin cambios |
| `e7abef1` | Backtesting: tras clic + Enter o buscando a mano fecha/hora se ven las velas posteriores crudas (`api/velas_despues/`) |
| `25f7a15` | Backtesting: casilla "Ver lo posterior" |
| `47cc083` | Encuadre: la última vela analizada queda centrada al cargar; lo posterior también al entrar a Backtesting y al pausar el replay |
| `458b5ac` | **Captura rápida de vela** en Backtesting: clic + Enter pega la fecha de la vela y carga (`activarCapturaRapida`) |
| `efa7301` | **Botón "Exportar caso"**: JSON por corte con el análisis y, aparte, el resultado posterior a 12/24/48 velas; refactor `estructuraUnicaSEQ()`; test `exportar_caso.test.js` |
| `3f2836e` | Captura rápida: clic + Enter también funciona sobre las velas posteriores (futuro crudo) |
| `c82c527`, `dd74dd2` | **Túnel privado**: `arrancarTunnel.bat` / `apagarTunnel.bat` (4 ventanas, `devtunnel host tablero.use2`); se prueba un arranque desde VS Code (`arrancar_vscode.bat` + `.vscode/tasks.json`) y se elimina en el mismo cambio |

**Ramas**: `master`, `feature/sr-fractal` (donde está lo más reciente),
`optimizacion-performance`.

---

## 13. Cómo correr y verificar

```bat
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py createsuperuser

:: tres procesos, cada uno en su terminal (o usar arrancar.bat; con túnel, arrancarTunnel.bat)
python manage.py runserver
celery -A config worker -l info --pool=solo
celery -A config beat -l info
```

Tests:

```bat
python manage.py test
node chartview/tests_js/fractal_sr.test.js
node chartview/tests_js/secuencia_fractal.test.js
node chartview/tests_js/exportar_caso.test.js
```

Las suites JS terminan con una línea `OK — …`. Las de los indicadores
fractales incluyen verificaciones de causalidad: recortan la historia en
la vela de cada evento y comprueban que el resultado sale idéntico.

---

## 14. Reglas para cualquier IA que trabaje en este proyecto

1. Leer este archivo antes de modificaciones importantes.
2. Analizar primero el código relacionado con la solicitud.
3. **No asumir que algo existe solo porque aparece acá o en el README.**
   Verificarlo en el código.
4. **El código es la fuente de verdad** cuando contradiga a la
   documentación (el README ya tiene al menos un punto desactualizado).
5. No eliminar funcionalidades sin autorización. Ojo con el código
   *desactivado a propósito* (`SR_MOSTRAR_BANDERAS`): apagado ≠ muerto.
6. No cambiar la arquitectura innecesariamente. En particular, no mover
   el cálculo pesado al request.
7. No agregar dependencias si algo existente alcanza. Este proyecto
   resuelve frontend sin framework y tests sin framework a propósito.
8. Respetar las convenciones: español, comentarios que explican el
   porqué, estilo de commits.
9. No duplicar lógica. Si algo ya está en `core/engine/`, usarlo.
10. Revisar efectos secundarios antes de modificar, sobre todo en
    `tasks.py` (orden de imports crítico) y `grafico.html` (monolítico).
11. Si un cambio afecta varios módulos, revisar sus relaciones primero.
12. Modificar solo los archivos necesarios.
13. No sobrescribir decisiones técnicas sin justificar el cambio.
14. **Ante una ambigüedad importante que pueda cambiar la arquitectura o
    el comportamiento, preguntar antes de actuar.**
15. Tras cambios significativos, evaluar si este archivo necesita
    actualizarse.
16. No tocar este archivo para registrar cambios chicos o irrelevantes.

### Regla específica del dominio

Este proyecto es una **herramienta de apoyo a decisiones de trading, no
un sistema de señales**. Al agregar o modificar indicadores:

- Distinguir siempre entre **estructura cumplida** y **probabilidad de
  acierto**. Un score alto significa lo primero.
- No usar lenguaje de garantía en la UI.
- No asumir que una estrategia es rentable: producir los datos para
  comprobarlo.
- Mantener la causalidad. Si algo repinta, cualquier backtest miente.

---

## 15. Comandos de contexto

### `LEE EL CONTEXTO`
Leer este archivo, usarlo como contexto, revisar archivos adicionales
cuando haga falta, y no asumir que está del todo actualizado.

### `REVISA EL CONTEXTO Y HAZ [TAREA]`
Leer este archivo → identificar las partes relacionadas → revisar el
código → tener en cuenta decisiones y restricciones → hacer la tarea →
verificar que no rompe nada → informar qué archivos se modificaron →
indicar si este archivo necesita actualizarse.

### `ACTUALIZA EL CONTEXTO`
Sincronizar este archivo con el estado real del proyecto: leerlo,
analizar los cambios desde la última sincronización (`git log`, archivos
modificados), identificar funcionalidades nuevas o eliminadas, cambios
arquitectónicos, dependencias nuevas, cambios de modelos, decisiones
técnicas nuevas, problemas resueltos y problemas nuevos. Actualizar
estado, pendientes e historial. Borrar lo obsoleto, conservar lo que
sigue siendo válido. No inventar. No registrar cambios triviales.
Mantenerlo conciso y útil.
