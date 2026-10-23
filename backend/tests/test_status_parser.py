from app.status_parser import HostState, ServiceState, format_downtime, parse_service_statuses, parse_status_dat, parse_wwan_operator

VALID_SERVICE_BLOCK = """
servicestatus {
\thost_name=016ZBK223ZF5B7H
\tservice_description=CPU Load
\tcheck_command=check_dummy!3
\tcurrent_state=0
\tlast_state_change=1785360484
\tplugin_output=OK - CPU Load: 0%
\tlast_check=1787246883
\t}
"""

WARNING_SERVICE_BLOCK = """
servicestatus {
\thost_name=016ZBK223ZF5B7H
\tservice_description=Disk Space
\tcurrent_state=1
\tlast_state_change=1785360484
\tplugin_output=WARNING - Disk 85% full
\tlast_check=1787246883
\t}
"""

VALID_BLOCK = """
hoststatus {
\thost_name=0Y332CQX8842W1W
\tcurrent_state=1
\tlast_check=1755534729
\tlast_state_change=1742000000
\tplugin_output=UNKNOWN: CRITICAL: No se han recibido datos pasivos
\tlast_time_up=1741000000
\tlast_time_down=1742000000
\t}
"""

UP_BLOCK = """
hoststatus {
\thost_name=ANOTHERHOST123
\tcurrent_state=0
\tlast_check=1755534729
\tlast_state_change=1755500000
\tplugin_output=OK
\tlast_time_up=1755534729
\tlast_time_down=0
\t}
"""


def test_parses_valid_block():
    hosts = parse_status_dat(VALID_BLOCK)
    assert len(hosts) == 1
    host = hosts[0]
    assert host.host_name == "0Y332CQX8842W1W"
    assert host.current_state == HostState.DOWN
    assert host.last_state_change == 1742000000


def test_parses_multiple_blocks_ignoring_other_block_types():
    text = f"""
info {{
\tcreated=123
\t}}
{VALID_BLOCK}
servicestatus {{
\thost_name=IGNOREME
\t}}
{UP_BLOCK}
"""
    hosts = parse_status_dat(text)
    names = {h.host_name for h in hosts}
    assert names == {"0Y332CQX8842W1W", "ANOTHERHOST123"}


def test_discards_unclosed_trailing_block():
    truncated = VALID_BLOCK + "\nhoststatus {\n\thost_name=TRUNCATEDHOST\n\tcurrent_state=1\n"
    hosts = parse_status_dat(truncated)
    names = {h.host_name for h in hosts}
    assert names == {"0Y332CQX8842W1W"}


def test_discards_block_missing_required_field():
    broken = """
hoststatus {
\tcurrent_state=1
\tlast_state_change=1742000000
\t}
"""
    hosts = parse_status_dat(broken)
    assert hosts == []


def test_discards_block_with_invalid_state_value():
    broken = """
hoststatus {
\thost_name=BADSTATE
\tcurrent_state=NOT_A_NUMBER
\t}
"""
    hosts = parse_status_dat(broken)
    assert hosts == []


def test_up_host_has_zero_downtime():
    hosts = parse_status_dat(UP_BLOCK)
    assert hosts[0].downtime_seconds(now=1755600000) == 0
    assert hosts[0].downtime_human(now=1755600000) == "0m"


def test_down_host_downtime_seconds_and_human():
    hosts = parse_status_dat(VALID_BLOCK)
    host = hosts[0]
    now = 1742000000 + (34 * 86400) + (6 * 3600) + (23 * 60)
    assert host.downtime_seconds(now=now) == (34 * 86400) + (6 * 3600) + (23 * 60)
    assert host.downtime_human(now=now) == "34d 6h 23m"


def test_format_downtime_edge_cases():
    assert format_downtime(0) == "0m"
    assert format_downtime(59) == "0m"
    assert format_downtime(3600) == "1h 0m"
    assert format_downtime(86400) == "1d 0h 0m"


def test_parses_valid_service_block():
    services = parse_service_statuses(VALID_SERVICE_BLOCK)
    assert len(services) == 1
    s = services[0]
    assert s.host_name == "016ZBK223ZF5B7H"
    assert s.service_description == "CPU Load"
    assert s.current_state == ServiceState.OK
    assert s.plugin_output == "OK - CPU Load: 0%"


def test_parses_multiple_services_per_host():
    services = parse_service_statuses(VALID_SERVICE_BLOCK + WARNING_SERVICE_BLOCK)
    descriptions = {s.service_description for s in services}
    assert descriptions == {"CPU Load", "Disk Space"}
    warning = next(s for s in services if s.service_description == "Disk Space")
    assert warning.current_state == ServiceState.WARNING


def test_ignores_hoststatus_blocks_when_parsing_services():
    text = VALID_BLOCK + VALID_SERVICE_BLOCK
    services = parse_service_statuses(text)
    assert len(services) == 1
    assert services[0].host_name == "016ZBK223ZF5B7H"


def test_discards_service_block_missing_description():
    broken = """
servicestatus {
\thost_name=SOMEHOST
\tcurrent_state=0
\t}
"""
    assert parse_service_statuses(broken) == []


def test_discards_service_block_with_invalid_state():
    broken = """
servicestatus {
\thost_name=SOMEHOST
\tservice_description=CPU Load
\tcurrent_state=NOT_A_NUMBER
\t}
"""
    assert parse_service_statuses(broken) == []


# --- Operador de la SIM desde el servicio WWAN Radio ---
# Las salidas reales vienen en dos formatos según el módem del equipo.

def test_wwan_operator_formato_movistar():
    salida = ("Estado: Conectado Prov: AR - Movistar IMEI: 867869062027339 Signal: 100% "
              "RSSI: 31 (-51 dBm) [HW] EC200U OK FW: EC200UAUAAR03A08M08 Roaming: No")
    assert parse_wwan_operator(salida) == "Movistar"


def test_wwan_operator_formato_claro():
    salida = "OK - Prov: Claro AR IMEI: 867060030657577 Senal: 0% ( dBm) [HW] 0 FW: UC20GQBR03A14E1G"
    assert parse_wwan_operator(salida) == "Claro"


def test_wwan_operator_reconoce_otras_marcas():
    assert parse_wwan_operator("OK - Prov: COMCEL IMEI: 123456789012345") == "Comcel"
    assert parse_wwan_operator("OK - Prov: WOM IMEI: 123456789012345") == "Wom"


def test_wwan_operator_sin_dato():
    """Equipos sin interfaz WWAN o sin datos pasivos quedan sin operador."""
    assert parse_wwan_operator("UNKNOWN - Error NETSH MBN: No se detecto interfaz WWAN") is None
    assert parse_wwan_operator("UNKNOWN: CRITICAL: No se han recibido datos pasivos") is None
    assert parse_wwan_operator("OK - Prov: N/A IMEI: 0") is None
    assert parse_wwan_operator("") is None
