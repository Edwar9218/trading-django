# AGENTS.md — contexto técnico del proyecto

> Memoria técnica del proyecto para cualquier IA que trabaje acá.
> Basado en el código, la configuración y el historial de git reales.
> Lo que no pudo determinarse está marcado como **No determinado**.
>
> Última sincronización: commit `f87138c` (rama `feature/sr-fractal`).

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
- Cálculo en Celery con sistema de generación (cancela trabajo obsoleto)
  y prioridades (lo manual pasa antes que el barrido automático).
- Gráfico: velas, 4 canales, Kalman, Smart Money Flow, "Cargar más
  historial", 2 pantallas, multi-temporalidad, crosshair sincronizado.
- Indicadores de navegador: KN Smart TP/SL, S/R Avanzado, S/R Fractal,
  Secuencia estructural.
- Persistencia de dibujos por usuario + divisa + temporalidad.
- Preferencias de checkboxes persistidas por usuario (11 campos).

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
- **Tests**: solo existen dos suites JS (`chartview/tests_js/`) y una
  clase de tests Django (`chartview/tests.py::SRFractalTests`). El resto
  de `tests.py` son los stubs vacíos de `startapp`.

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

### Gráfico (`chartview/`)
- Velas + 4 canales (largo, largo inverso, corto, corto inverso) con
  % de calidad, Kalman, SMF, señales.
- 2 pantallas con crosshair sincronizado; fecha compartida.
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
- Panel lateral, capas activables por separado, alertas conmutables.

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
7. **Umbrales de la Secuencia estructural sin calibrar** contra datos
   reales. Los defaults (`cuerpoMult 1.5`, `rangoAtrMin 1.0`,
   `maxBarrasRuptura 40`, `maxBarrasRetest 120`) están pensados para
   intradía; **en D1 son claramente largos** (120 velas = 120 días) y
   probablemente inflen el % de continuación.
8. **Estadísticas con muestras chicas.** `stats` sobre las velas cargadas
   de un solo símbolo da muestras de ~10 casos. No concluir nada de ahí.
9. **Dependencias sin pin** (pandas, numpy, matplotlib). Una versión
   nueva puede romper el cálculo sin aviso.
10. **`grafico.html` tiene 3419 líneas** con todo mezclado (CSS, HTML,
    JS de varios indicadores). Es el archivo más frágil de tocar.
11. **matplotlib se importa siempre** en `auto_channels.py` aunque la web
    nunca genere PNG — herencia del script original.

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
- [ ] Calibrar los umbrales de la Secuencia por temporalidad.
- [ ] Tests de `core/engine/`.
- [ ] Pinear versiones de pandas/numpy/matplotlib.
- [ ] Evaluar partir `grafico.html` en archivos estáticos separados.

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

**Ramas**: `master`, `feature/sr-fractal` (donde está lo más reciente),
`optimizacion-performance`.

---

## 13. Cómo correr y verificar

```bat
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py createsuperuser

:: tres procesos, cada uno en su terminal (o usar arrancar.bat)
python manage.py runserver
celery -A config worker -l info --pool=solo
celery -A config beat -l info
```

Tests:

```bat
python manage.py test
node chartview/tests_js/fractal_sr.test.js
node chartview/tests_js/secuencia_fractal.test.js
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
