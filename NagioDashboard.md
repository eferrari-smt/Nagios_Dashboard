# Nagios CSL Dashboard — Contexto del Proyecto

## Qué es esto

Herramienta interna de Smartmation para monitorear el estado de las PCs concentradoras (CSL) que reportan a Nagios, cruzando esa información con el inventario de acceso remoto (AnyDesk / Escritorio Remoto) y el ambiente al que pertenece cada equipo.

**El problema que resuelve:** hoy el proceso es manual. Se copia el listado de hosts DOWN desde la web de Nagios, se cruza a mano contra una planilla Excel de AnyDesk para saber de qué equipo se trata y en qué ambiente está, y luego se busca el ID de AnyDesk para conectarse. Esto se repite varias veces al día y consume tiempo.

**El objetivo:** una pantalla única donde ver qué está caído, de qué equipo real se trata, en qué ambiente está, y poder conectarse remotamente con un clic.

## Contexto de dominio (importante para entender los datos)

Smartmation hace telegestión de luminarias. Las **CSL** (Controladores de Segmento de Luminarias) son PCs industriales (NUCs) instaladas en gabinetes en la calle. Cada una corre un software propio (CSL Manager) y reporta su estado a un servidor Nagios central mediante un agente **NRDP** (chequeos pasivos: la PC empuja su estado, Nagios no la consulta).

Los hosts en Nagios están nombrados con el **RAK** del equipo, que es un identificador alfanumérico. Los clientes principales son GCBA (Gobierno de la Ciudad de Buenos Aires) y Municipio de Rosario, más otros ambientes agrupados como "RESTO". También hay equipos en depósito ("STOCK").

## Stack y decisiones de arquitectura

- **Backend:** Python 3.11+ con FastAPI
- **Frontend:** por definir con el desarrollador — ver sección Frontend
- **Base de datos:** SQLite (histórico de estados)
- **Acceso a Nagios:** SSH con llave `.pem` usando `paramiko`
- **Scheduler:** APScheduler para el polling periódico

### Por qué SSH y no la API

Nagios Core 4.5.10 **no tiene API REST oficial**. Existe `statusjson.cgi`, pero requiere atravesar Apache con autenticación HTTP adicional. Como ya se cuenta con acceso SSH funcionando por llave `.pem`, se lee directamente el archivo `status.dat`. Un solo canal de acceso, menos superficie de falla.

`statusjson.cgi` queda como fallback opcional a implementar detrás de un flag de configuración, no como camino principal.

## Servidor Nagios

- **Tipo:** Nagios Core 4.5.10 (instalación desde source)
- **SO:** Amazon Linux 2023 en EC2 (`us-east-1`)
- **Host:** ver `NAGIOS_SSH_HOST` en el `.env` (no se versiona la dirección del servidor)
- **Usuario SSH:** `ec2-user`, puerto 22, autenticación por llave `.pem`
- **Archivo de estado:** `/usr/local/nagios/var/status.dat` (~2 MB, se refresca constantemente)
- **Definiciones de hosts:** `/usr/local/nagios/etc/objects/hosts/` (un `.cfg` por host)
- **Fallback CGI:** `/usr/local/nagios/sbin/statusjson.cgi` (existe y es ejecutable)

## Reglas de seguridad — NO NEGOCIABLES

1. **Acceso de solo lectura.** La herramienta nunca escribe, modifica ni elimina nada en el servidor Nagios. Solo lee `status.dat` y opcionalmente los `.cfg`. No ejecuta comandos de Nagios, no toca el command file, no reinicia servicios.

2. **Credenciales fuera del código.** Todo dato sensible va en `.env`, nunca hardcodeado. `.env` va en `.gitignore` desde el primer commit. Incluir un `.env.example` con las claves pero sin valores reales.

3. **La llave `.pem` no se copia al repo.** El `.env` guarda la *ruta* al archivo, no su contenido.

4. **Sin credenciales en logs.** Nunca loguear el contenido de la llave, rutas completas con datos de usuario, ni cadenas de conexión.

Variables de entorno esperadas:

```
NAGIOS_SSH_HOST=
NAGIOS_SSH_PORT=22
NAGIOS_SSH_USER=
NAGIOS_SSH_KEY_PATH=
NAGIOS_STATUS_DAT_PATH=/usr/local/nagios/var/status.dat
NAGIOS_HOSTS_CFG_PATH=/usr/local/nagios/etc/objects/hosts/
INVENTORY_XLSX_PATH=
POLL_INTERVAL_MINUTES=10
DATABASE_PATH=./data/dashboard.db
```

## Parseo de `status.dat`

El formato es texto plano con bloques delimitados. Los bloques relevantes son `hoststatus`:

```
hoststatus {
	host_name=0Y332CQX8842W1W
	current_state=1
	last_check=1755534729
	last_state_change=1742000000
	plugin_output=UNKNOWN: CRITICAL: No se han recibido datos pasivos...
	...
	}
```

Campos a extraer de cada `hoststatus`: `host_name`, `current_state`, `last_check`, `last_state_change`, `plugin_output`, `last_time_up`, `last_time_down`.

Mapeo de `current_state` para hosts: `0` = UP, `1` = DOWN, `2` = UNREACHABLE.

El downtime se calcula como `now - last_state_change` cuando el estado no es UP. Mostrarlo en formato legible (`34d 6h 23m`).

Escribir el parser de forma tolerante: el archivo puede estar siendo reescrito por Nagios en el momento de la lectura. Si un bloque viene incompleto, descartarlo y seguir, no abortar todo el parseo.

## Inventario de AnyDesk (Excel)

Planilla mantenida a mano. Estructura actual: varias hojas (`AnyDesk Inventory`, `RESTO`, `STOCK`), cada una con estas columnas:

| Columna | Contenido |
|---|---|
| Nombre PC | Nombre legible / ubicación (ej: `Av. Monroe 1231`, `Tocancipa 6 -TGACSL`) |
| RAK | Identificador del equipo, con variaciones de formato |
| ID AnyDesk | ID numérico para conectarse (ej: `1000000000`) |
| Cliente | Ambiente: `GCBA`, `RESTO`, `STOCK` |

El nombre de la hoja y la columna "Cliente" suelen coincidir, pero **la columna Cliente es la fuente de verdad** para el ambiente.

El parser debe ser tolerante a cambios: si se agrega una hoja nueva o se reordenan columnas, no debería romperse en silencio. Detectar columnas por encabezado, no por posición fija. Si falta una columna esperada, loguear un warning claro.

## Lógica de matching (crítica — ya validada manualmente)

Este es el corazón de la herramienta y donde está la complejidad real del dominio.

### El problema del truncado a 15 caracteres

Windows limita el nombre NetBIOS a **15 caracteres**. Cuando el agente NRDP reporta el hostname, envía solo los primeros 15. Pero en el Excel los RAK suelen estar completos (20 caracteres o más), y en Nagios algunos hosts fueron creados con el nombre largo.

Consecuencia: un host en Nagios llamado `MPC1BBAWT11MY260617` nunca va a matchear con lo que reporta la PC (`MPC1BBAWT11MY26`). Esto ya se identificó como causa raíz de hosts que aparecen DOWN sin estarlo realmente.

### Algoritmo de matching

1. **Normalizar** ambos lados: pasar a mayúsculas y eliminar todo lo que no sea alfanumérico. Los RAK en el Excel vienen con prefijos y sufijos inconsistentes (`TGACSL-612QWZ0Y2L44PKHX0MX9`, `-84X44840U58UTI2407YC`), así que la comparación debe ser por substring.

2. **Truncar el nombre de Nagios a 15 caracteres** normalizados y buscar ese fragmento como substring dentro del RAK normalizado del inventario.

3. **Fallback:** si no hay match por RAK, intentar contra el campo "Nombre PC" normalizado (algunos equipos de STOCK tienen el RAK como nombre y la columna RAK vacía).

4. **Resolución de duplicados:** un mismo RAK puede aparecer en más de una hoja. Cuando hay múltiples matches, **priorizar el que NO esté en STOCK** — el equipo activo es el que importa. Si todos los matches son STOCK, usar el primero.

5. **Sin match:** marcar el host como "no identificado" y mostrarlo igual en el dashboard, con una señal visual clara. No descartarlo silenciosamente. Estos casos son informativos: suelen ser equipos que no son CSL Windows (por ejemplo un NUC Linux) o entradas de inventario faltantes.

### Detección de duplicados en el inventario

El inventario tiene inconsistencias conocidas (un mismo equipo cargado en dos hojas con el mismo RAK e ID de AnyDesk). La herramienta debe **detectar y reportar** estos duplicados en una vista o sección aparte, sin intentar corregirlos automáticamente. Es información valiosa para limpiar la planilla.

## Histórico y clasificación de patrones

Cada corrida del poller guarda un snapshot del estado de cada host. Con eso, clasificar automáticamente:

- **Crónico:** DOWN de forma continua por más de 7 días. Requiere intervención en terreno, no se va a recuperar solo.
- **Intermitente:** cambió de estado más de N veces en los últimos 7 días (sugerido: 3). Típicamente problemas de conectividad, modem que se reinicia, alimentación inestable.
- **Reciente:** DOWN hace menos de 24 horas. Puede recuperarse solo.
- **Recuperado:** estaba DOWN en el snapshot anterior y ahora está UP.

Esta clasificación es la que aporta más valor por encima de mirar Nagios directamente — Nagios muestra el estado actual, no el patrón.

## Frontend

### Funcionalidad requerida

**Vista principal — tabla de estado:**
- Semáforo visual de estado (caído / activo)
- Nombre real del equipo (de la columna "Nombre PC"), no el RAK
- Ambiente (GCBA / RESTO / STOCK)
- Downtime en formato legible, ordenable
- Clasificación de patrón (crónico / intermitente / reciente)
- Host name tal como figura en Nagios (para poder buscarlo ahí si hace falta)

**Acciones de conexión remota — el punto central de la herramienta:**
- Botón que abre AnyDesk con el ID del equipo mediante el protocolo `anydesk:<ID>`
- Botón de Escritorio Remoto. Evaluar generar un archivo `.rdp` descargable con la dirección precargada, ya que el esquema `rdp://` no tiene soporte universal en navegadores. Documentar la decisión que se tome.
- Si el equipo no tiene ID de AnyDesk en el inventario, el botón debe estar deshabilitado con un tooltip explicando por qué, no ausente sin explicación.

**Filtros y búsqueda:**
- Por ambiente
- Por estado
- Por clasificación de patrón
- Buscador de texto libre (que matchee tanto nombre como RAK)

**Vista de histórico:**
- Timeline por equipo mostrando transiciones de estado
- Permite ver de un vistazo si un equipo es problemático de forma recurrente

**Vista de inconsistencias:**
- Hosts en Nagios sin match en el inventario
- Hosts con nombre de más de 15 caracteres (problema de truncado)
- Duplicados detectados en el inventario

**Indicadores de frescura:**
- Timestamp de la última actualización, siempre visible
- Estado de la conexión con Nagios (si el último poll falló, decirlo claramente en pantalla — no mostrar datos viejos como si fueran actuales)
- Botón de refresco manual además del automático

### Diseño

Antes de escribir el frontend, **leer el skill de frontend-design** disponible en el entorno para las convenciones de diseño y tokens. El criterio general: es una herramienta operativa que se va a mirar muchas veces al día, así que priorizar densidad de información legible y escaneo rápido por sobre decoración. El estado de un equipo tiene que leerse de un vistazo.

Preguntar al desarrollador qué framework prefiere antes de decidir. Si no hay preferencia, proponer una opción y justificarla.

## Fases de desarrollo

Construir en este orden, validando cada fase antes de seguir. **No avanzar a la siguiente sin confirmación.**

**Fase 1 — Conexión y parseo**
Conexión SSH a la EC2, lectura de `status.dat`, parser de bloques `hoststatus`. Salida: un script que imprima en consola la lista de hosts con su estado y downtime. Validar contra lo que muestra la web de Nagios.

**Fase 2 — Inventario y matching**
Lectura del Excel, implementación del algoritmo de matching, reporte de matches y no-matches. Validar manualmente que los cruces sean correctos antes de seguir.

**Fase 3 — Persistencia**
Esquema SQLite, guardado de snapshots, cálculo de clasificación de patrones.

**Fase 4 — API**
Endpoints FastAPI que expongan los datos cruzados. Documentación automática de OpenAPI.

**Fase 5 — Scheduler**
Polling periódico con APScheduler, manejo de errores de conexión, logging.

**Fase 6 — Frontend**
Recién acá la interfaz, sobre una API que ya funciona y datos ya validados.

## Convenciones de código

- Type hints en todo el código Python
- Docstrings en funciones no obvias, explicando el *por qué* cuando la lógica es específica del dominio (el matching en particular necesita comentarios que expliquen el problema del truncado)
- Logging estructurado, no `print()`
- Manejo explícito de errores en toda operación de red o I/O. La herramienta no debe caerse porque la EC2 no responde: debe degradar mostrando el último dato conocido y avisando que está desactualizado.
- Tests para la lógica de matching y el parser de `status.dat`. Son las dos piezas donde un bug pasa desapercibido y corrompe todo lo demás.
- README con instrucciones de setup y despliegue

## Contexto del usuario

Enzo Ferrari, soporte técnico N1/N2 en Smartmation, con objetivo de crecer hacia N3 (infraestructura, Relay Servers, firmware). Perfil técnico sólido en troubleshooting y operación, con menos experiencia en desarrollo de aplicaciones.

Al construir, explicar las decisiones de arquitectura y el *por qué* de cada elección, no solo el *qué*. Cuando haya varias formas de resolver algo, mencionar las alternativas y el criterio de la decisión. El objetivo es que quede una herramienta funcionando y también que se entienda cómo funciona para poder mantenerla y extenderla.

Preguntar antes de asumir. Si algo de esta especificación es ambiguo o parece incorrecto, plantearlo en vez de adivinar.
