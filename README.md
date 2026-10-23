# Panel de control de CSL

Herramienta interna de Smartmation para monitorear el estado de las CSL que reportan a Nagios, cruzado con el inventario de AnyDesk. Ver [NagioDashboard.md](NagioDashboard.md) para el contexto completo del proyecto y las decisiones de diseño.

## Requisitos

- Python 3.11+ (probado con 3.12)
- Acceso SSH por llave `.pem` al servidor Nagios
- El CSV de inventario de AnyDesk (`data/inventory.csv`), accesible desde esta máquina

## Setup

1. Instalar dependencias:

   ```
   cd backend
   pip install -r requirements.txt
   ```

2. Copiar `.env.example` a `.env` (en la raíz del proyecto, no en `backend/`) y completar:

   ```
   NAGIOS_SSH_HOST=
   NAGIOS_SSH_PORT=22
   NAGIOS_SSH_USER=
   NAGIOS_SSH_KEY_PATH=       # ruta completa al .pem, nunca commitear el archivo
   NAGIOS_STATUS_DAT_PATH=/usr/local/nagios/var/status.dat
   NAGIOS_HOSTS_CFG_PATH=/usr/local/nagios/etc/objects/hosts/
   INVENTORY_CSV_PATH=./data/inventory.csv   # relativo a la raíz del proyecto, o ruta absoluta
   POLL_INTERVAL_MINUTES=10
   DATABASE_PATH=./data/dashboard.db
   ```

   `.env` está en `.gitignore` — nunca se commitea, igual que el `.pem`.

## Uso

Todos los comandos se corren desde `backend/`.

**Scripts de una sola corrida** (útiles para validar cada pieza por separado):

```
python check_status.py [--only-down]        # Fase 1: conecta y lista hosts desde status.dat
python match_hosts.py [--only-down]          # Fase 2: cruza contra el inventario de AnyDesk
python poll_once.py [--only-down]            # Fase 3: guarda un snapshot y muestra la clasificación de patrón
```

**Scheduler** (polling automático periódico, guarda snapshots en SQLite cada `POLL_INTERVAL_MINUTES`):

```
python run_scheduler.py
```

**API + Dashboard web** (sirve la API y el frontend en `http://localhost:8000`):

```
uvicorn app.api:app --reload --port 8000
```

Documentación automática de la API (OpenAPI/Swagger) en `http://localhost:8000/docs`.

En producción, correr `run_scheduler.py` y `uvicorn app.api:app` como dos procesos separados y de larga duración — el scheduler alimenta la base, la API solo la lee.

**Despliegue actual (uso diario):** dos tareas programadas de Windows (`NagiosDashboard-Scheduler`, `NagiosDashboard-API`) arrancan ambos procesos al iniciar sesión, con reinicio automático si se caen. Cada una corre `python.exe` (no `pythonw.exe` — ver nota abajo) envuelto en `powershell.exe -WindowStyle Hidden`, con la salida redirigida a `backend/logs/*.log`. Para gestionarlas: `Get-ScheduledTask -TaskName "NagiosDashboard-*"`, `Start-ScheduledTask`/`Stop-ScheduledTask`, o `Unregister-ScheduledTask` para sacarlas.

> ⚠️ No cambiar `python.exe` por `pythonw.exe` en estas tareas para "ocultar la ventana": bajo `pythonw.exe`, `sys.stdout`/`sys.stderr` son `None`, y uvicorn se cae en silencio al intentar escribir su banner de arranque ahí. El wrapper de PowerShell oculto ya resuelve lo de la ventana visible sin ese problema.

## Tests

```
cd backend
pytest
```

Cubren el parser de `status.dat`, el algoritmo de matching, la persistencia SQLite, la clasificación de patrones, el scheduler y la API.

## Decisiones de diseño relevantes

- **El estado operativo vive en el inventario, no en los tags** (columna `Estado`: Producción / Testing / RMA / Refurbish / Baja). Antes, un tag de categoría especial decidía si un equipo aparecía en Equipos o en STOCK. Ese modelo no tenía paso de graduación: cuando un equipo salía del refurbish nadie le quitaba el tag, y como el tag lo escondía de Equipos tampoco había dónde ir a quitárselo — quedaba encerrado (caso Cardenas 2207). Ahora el estado es un dato propio del equipo, editable siempre desde "Editar" o desde STOCK, y los tags volvieron a ser etiquetas libres de organización. El tag sigue valiendo **solo** como respaldo para hosts sin fila en el inventario, donde no hay columna Estado que mirar. Migración de una sola corrida en `backend/migrate_estado_column.py` (hace backup antes de escribir).
- **Un chequeo en OK no es una alarma.** La pestaña Alarmas y el bloque del Dashboard muestran únicamente servicios que no están en OK. Los 7 tipos y sus umbrales salen de los scripts del agente NRDP (`NRDP_Monitoreo/Monitor-*.ps1`), no de Nagios: el servidor recibe el estado ya calculado. El catálogo está en `app/alarmas.py` y es documentación, no lógica — si se cambia un umbral en el agente hay que actualizarlo ahí también.
- **Bandeja de intervención: solo CRITICAL sostenida más de 48 h.** Se eligió deliberadamente dejar afuera los WARNING crónicos: hoy hay 26 alarmas de disco en advertencia y una bandeja que nace con decenas de entradas se deja de mirar a la semana. El umbral se mide con `last_state_change` de Nagios, así que funcionó desde el primer día sin esperar a acumular historial.
- **Historial de alarmas por transiciones, no por snapshot** (tabla `service_events`). Guardar los 7 servicios de ~228 equipos en cada ciclo de 10 minutos serían ~230.000 filas por día casi todas idénticas; registrando solo los cambios son unas decenas y la línea de tiempo queda igual de completa. Se acumula desde el 22/10/2026 — **no se puede reconstruir hacia atrás**, porque `service_status` siempre guardó solo el estado vigente.
- **Agentes mudos**: un equipo cuyos chequeos están *todos* en UNKNOWN no está sano — su agente NRDP dejó de reportar y Nagios muestra el último estado conocido del host. Se listan aparte en Alarmas porque de otro modo quedan invisibles: no figuran como caídos ni como alarma concreta.
- **La vista principal trae el historial en una sola consulta acotada a 8 días** (`get_histories_since`). Antes llamaba a `get_host_history` una vez por equipo y sin recorte: 229 consultas que materializaban las ~520.000 filas de `snapshots` en cada carga, unos 6 segundos. El clasificador de patrones nunca mira más de 7 días, así que todo lo demás era trabajo de descarte. Medido: 6,0 s → 1,14 s, con clasificación idéntica en los 229 equipos.

- **SSH + SFTP en vez de la API de Nagios**: `statusjson.cgi` requiere autenticación HTTP adicional; ya se cuenta con acceso SSH funcionando por llave, así que se lee `status.dat` directamente. Ver `NagioDashboard.md`.
- **La API nunca conecta en vivo a la EC2**: la lectura de `status.dat` por SFTP tarda decenas de segundos. La API sirve siempre el último snapshot guardado en SQLite; el polling en vivo es responsabilidad exclusiva del scheduler (`run_scheduler.py`).
- **El inventario es un CSV plano (`data/inventory.csv`), no el Excel multi-hoja original**: se migró para que la herramienta se pueda pasar a otra persona/máquina sin depender de OneDrive ni de una instalación de Excel. Las "hojas" (GCBA/Rosario/RESTO/STOCK) nunca aportaron información que no estuviera ya en la columna `Cliente`, así que colapsar a un solo archivo no pierde nada. El Excel original queda como respaldo (nunca se borra); la migración de una sola corrida está en `backend/migrate_excel_to_csv.py`. Se lee en vivo en cada request (sin caché): se puede editar a mano y los cambios se reflejan de inmediato sin reiniciar nada.
- **Nombre PC y Notas son editables desde el dashboard** (botón "Editar" por fila), y la escritura va directo al CSV — el usuario pidió tratarlo como la "base de datos" de estos dos campos en vez de editarlo a mano. **El RAK nunca es editable desde "Editar"** — sostiene todo el matching y el usuario fue explícito en que nunca debe tocarse por ese botón. La única vía que sí puede tocar el RAK es la pestaña separada **"Reemplazar"** (`replace_inventory_row` en `app/inventory_writer.py`), pensada para cuando se reemplaza físicamente el CSL de un sitio — es una acción deliberada y distinta, nunca una casilla más del editor común. Esa misma pestaña permite dar de alta un equipo nuevo en el inventario (para resolver hosts de la pestaña Inconsistencias que no tienen fila propia). Si el CSV está abierto en otro programa al guardar, se devuelve un 503 con mensaje claro en vez de fallar en silencio (ya pasó una vez con el Excel: el archivo estaba abierto en una ventana de Excel).
- **Pestaña "Insights"**: totales de hosts (Up/Down/Unreachable) y de servicios (Ok/Warning/Critical/Unknown) con porcentajes, al estilo de las cajas "Host/Service Status Totals" del propio Nagios — pedido explícito del jefe del usuario. Backend en `dashboard_service.get_insights()` / `GET /api/insights`.
- **Alarmas de servicio por PC**: además del estado del host, se guardan y muestran los checks de servicio de Nagios (CPU Load, Disk Space, etc. — los mismos 7 checks que aparecen en la vista de Nagios) con su estado OK/WARNING/CRITICAL/UNKNOWN. Se parsean del mismo `status.dat` (bloques `servicestatus`, ver `app/status_parser.py`) y se guardan en la tabla `service_status` como estado actual (upsert, no historial) en cada poll. En la tabla de Estado se ve un resumen compacto por fila; un clic abre el detalle completo.
- **Nagios Uploader: la herramienta prepara el cambio, la persona lo aplica** — regla no negociable del proyecto: "la herramienta nunca escribe, modifica ni elimina nada en el servidor Nagios". Alta y baja de RAKs en Nagios se resuelven generando el contenido exacto del archivo `.cfg` a crear (alta) o mostrando el `.cfg` actual como respaldo antes de borrar (baja) — ver `app/nagios_uploader.py` — pero la escritura/borrado real y el reload de Nagios los hace la persona a mano por SSH. El formato real de los `.cfg` (un archivo por host, `define host { use windows_powershell; host_name <RAK truncado a 15> }`) se confirmó leyendo el servidor.
- **Frontend sin build step**: HTML/CSS/JS plano servido como archivos estáticos por FastAPI (`backend/static/`), sin Node/npm/bundler. Alcanza para el número de vistas del spec y evita una cadena de herramientas nueva para mantener.
- **Escritorio Remoto = Chrome Remote Desktop, no RDP nativo**: el Excel no tiene columna de IP/dirección para RDP de Windows, y en la práctica el equipo ya usa Chrome Remote Desktop (CRD) para estas PCs. El botón "Escritorio Remoto" linkea a `https://remotedesktop.google.com/access/session/<crd_host_id>` (patrón confirmado a mano). `crd_host_id` es una columna opcional del inventario (`ID Chrome Remote Desktop`, detectada por encabezado); si falta, el botón queda deshabilitado con tooltip. Se armó [match_crd.py](backend/match_crd.py) como script de una sola corrida para cruzar el export de dispositivos de CRD (capturado desde el Network tab del navegador) contra el inventario y generar esa columna sin cargarla a mano equipo por equipo.
  - **Importante sobre el acceso**: el link de CRD solo funciona si quien hace clic tiene la sesión de Google iniciada en el navegador con la cuenta que tiene esos equipos vinculados (no es Google Workspace, es una cuenta compartida del equipo de soporte). No es algo que la herramienta pueda resolver — cada técnico necesita esa cuenta logueada en su navegador para que el botón funcione.
- **Botón de AnyDesk usa un esquema de protocolo personalizado**: Smartmation distribuye un cliente de AnyDesk con branding propio (no el genérico de anydesk.com), que registra en Windows un protocolo único en vez de `anydesk:` estándar — confirmado en el registro como `AnyDesk-cfd61e7b:` tras instalar el cliente de la empresa. El botón usa ese esquema (constante `ANYDESK_URI_SCHEME` en `backend/static/js/main.js`). Si alguna vez se redistribuye un cliente distinto, ese valor hay que actualizarlo a mano.
- **Tags personalizables por el usuario**: organización manual (no viene de Nagios ni del inventario), persistida en las tablas `tags`/`host_tags` de la misma SQLite (`app/tags_store.py`). Un equipo puede tener varios tags a la vez. Se gestionan desde el botón "+ Tags" del dashboard (crear/borrar), se asignan seleccionando filas con checkbox y aplicando un tag desde la barra que aparece, y se filtra por tag igual que por estado.
- **El botón "Actualizar" dispara un poll real** (`POST /api/poll`), no solo relee el snapshot ya guardado: al principio solo hacía esto último, y entre ciclos del scheduler (10 min) parecía no hacer nada. Ahora conecta a Nagios en el momento (tarda lo mismo que un poll normal, ~6-40s) y después refresca la vista con el resultado fresco.
