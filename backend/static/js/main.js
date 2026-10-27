import {
  fetchMeta, fetchInsights, fetchHosts, fetchHostHistory,
  fetchHostNagiosInfo, fetchHostReplacements, fetchReplacements, fetchPendingInventoryHosts, fetchTags, createTag, deleteTag, updateTagCategory, assignTag, unassignTag,
  triggerPoll, editHost, replaceHost, fetchMe, logout, changePassword,
  prepareNagiosAdd, prepareNagiosRemove, executeNagiosRemove, registerHost,
  fetchAlarmas, fetchHostEventos, fetchEstados, setInventoryEstado, deleteInventoryEntry,
  fetchMotivos,
} from "./api.js";

const NEW_AMBIENTE_OPTION = "__nuevo__";
const AMBIENTE_ORDER_KEY = "nagiosDashboard.ambienteOrder";

// "Inaccesible" (UNREACHABLE de Nagios) no se muestra como categoría propia: a
// pedido del usuario se trata igual que "Caído" en toda la interfaz, para no
// abrir una tercera categoría que en la práctica significa lo mismo (el equipo
// no responde). Se sigue contando y mostrando, nunca se oculta el equipo.
const HOST_STATE_LABELS = { UP: "Activo", DOWN: "Caído", UNREACHABLE: "Caído" };
const SERVICE_STATE_LABELS = { OK: "OK", WARNING: "Advertencia", CRITICAL: "Crítico", UNKNOWN: "Desconocido" };
const MATCH_TYPE_LABELS = { rak: "RAK", nombre_pc: "Nombre PC", no_match: "Sin match" };
const TAG_CATEGORY_LABELS = { rma: "RMA", testing: "Testing", refurbish: "Refurbish" };
const EXCLUDED_TAG_CATEGORIES = ["rma", "testing", "refurbish"];

function isOperationallyExcluded(host) {
  // Tiene que decidir lo mismo que `is_operationally_excluded` del backend: manda
  // el estado operativo del inventario, no los tags. Cuando esto filtraba por tag
  // y el backend ya miraba el estado, el Dashboard escondía equipos que sí estaban
  // en producción y arrastraban un tag viejo — GCBA mostraba 165 en vez de 168.
  if (host.estado) return host.estado !== "Produccion";
  // Sin ficha en el inventario no hay estado que mirar: ahí sí vale el tag.
  return (host.tags || []).some((t) => EXCLUDED_TAG_CATEGORIES.includes(t.category));
}

function hostStateLabel(state) {
  return HOST_STATE_LABELS[state] || state;
}
// Color por ambiente: los conocidos tienen el suyo fijo; uno nuevo recibe una
// de las 8 clases por hash de su nombre, así el color no cambia entre recargas.
const AMBIENTE_CLASSES = {
  GCBA: "amb-1",
  ROSARIO: "amb-2",
  CELSA: "amb-3",
  AUSA: "amb-4",
  HIPODROMO: "amb-5",
  RESTO: "amb-6",
  STOCK: "amb-7",
  OFICINA: "amb-8",
};

function ambienteClass(ambiente) {
  const key = String(ambiente || "").trim().toUpperCase();
  if (AMBIENTE_CLASSES[key]) return AMBIENTE_CLASSES[key];
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % 8;
  return `amb-${hash + 1}`;
}

function ambienteChipHTML(ambiente) {
  if (!ambiente) return "—";
  return `<span class="amb ${ambienteClass(ambiente)}">${escapeAttr(ambiente)}</span>`;
}

function empresaChipHTML(empresa) {
  if (!empresa) return "";
  const slug = String(empresa).trim().toLowerCase();
  const variante = slug === "movistar" ? " empresa-movistar" : slug === "claro" ? " empresa-claro" : "";
  return ` <span class="empresa-tag${variante}">${escapeAttr(empresa)}</span>`;
}

// Mismo criterio que app/matching.py: solo alfanumérico en mayúsculas,
// truncado a 15 por el límite de NetBIOS. El RAK se guarda completo en el
// inventario; esto es solo el nombre con el que el equipo entra a Nagios.
function deriveHostName(rak) {
  return String(rak || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, NETBIOS_TRUNCATE_LEN);
}

function renderRakHint(inputId, hintId) {
  const raw = el(inputId).value.trim();
  const hint = el(hintId);
  if (!raw) {
    hint.innerHTML = "";
    return;
  }
  const limpio = String(raw).toUpperCase().replace(/[^A-Z0-9]/g, "");
  const host = deriveHostName(raw);
  const corto = limpio.length < NETBIOS_TRUNCATE_LEN;
  hint.innerHTML =
    `Se guarda completo en el inventario. En Nagios entra como <code>${escapeAttr(host)}</code>` +
    (corto
      ? ` <span class="rak-hint-warn">— ojo: son ${limpio.length} caracteres, ¿está completo?</span>`
      : "");
}

function hostStateClass(state) {
  return state === "UNREACHABLE" ? "down" : String(state).toLowerCase();
}
function isHostDown(state) {
  return state === "DOWN" || state === "UNREACHABLE";
}
function serviceStateLabel(state) {
  return SERVICE_STATE_LABELS[state] || state;
}
function matchTypeLabel(matchType) {
  return MATCH_TYPE_LABELS[matchType] || matchType;
}

const NETBIOS_TRUNCATE_LEN = 15;
const AUTO_REFRESH_MS = 30_000;

// Smartmation usa un cliente de AnyDesk personalizado (descargado de su propio
// portal), no el genérico de anydesk.com. Los builds personalizados registran
// su propio esquema de protocolo en vez de "anydesk:" — confirmado en registro
// de Windows (HKCR\AnyDesk-cfd61e7b) tras instalar el cliente. Si algún día se
// redistribuye un cliente distinto, este valor hay que actualizarlo a mano.
const ANYDESK_URI_SCHEME = "AnyDesk-cfd61e7b";

const state = {
  hosts: [],
  tags: [],
  selectedHosts: new Set(),
  editingHost: null,
  editingHostData: null,
  drawerHost: null,
  username: null,
  replaceHost: null,
  sort: { key: "current_state", dir: "asc" },
  filters: { search: "", ambiente: "", estado: "", patron: "", tagId: "" },
  alarmFilters: { search: "", severity: "", tipo: "" },
  alarmas: null,      // respuesta de /api/alarmas
  estados: [],        // opciones de estado operativo
  motivos: [],        // catálogo de motivos de reemplazo
  pendingHosts: [],
  stockFilters: { search: "", estado: "" },
  pendingFilters: { search: "" },
  ambienteOrder: loadAmbienteOrder(),
};

const patternLabels = {
  cronico: "Crónico",
  intermitente: "Intermitente",
  reciente: "Reciente",
  recuperado: "Recuperado",
  estable: "Estable",
};

function el(id) {
  return document.getElementById(id);
}

function formatEpoch(epochSeconds) {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString("es-AR");
}

function formatRelative(epochSeconds) {
  if (!epochSeconds) return "sin datos";
  const diffSeconds = Math.max(0, Math.floor(Date.now() / 1000) - epochSeconds);
  if (diffSeconds < 60) return "hace instantes";
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) return `hace ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `hace ${days}d ${hours % 24}h`;
}

// --- Frescura / header ---

async function refreshMeta() {
  try {
    const meta = await fetchMeta();
    el("lastUpdated").textContent = meta.last_polled_at
      ? `Última actualización: ${formatEpoch(meta.last_polled_at)} (${formatRelative(meta.last_polled_at)})`
      : "Última actualización: sin datos todavía";

    const badge = el("connectionBadge");
    if (meta.last_poll_success === null) {
      badge.textContent = "Todavía no se consultó a Nagios";
      badge.className = "badge unknown";
    } else if (meta.last_poll_success) {
      badge.textContent = "Conexión OK";
      badge.className = "badge ok";
    } else {
      badge.textContent = `Falló la última consulta: ${meta.last_poll_error || "error desconocido"}`;
      badge.className = "badge error";
    }
  } catch (err) {
    el("connectionBadge").textContent = "No se pudo consultar el estado";
    el("connectionBadge").className = "badge error";
  }
}

// --- Vista Resumen (Insights) ---

function pct(part, total) {
  if (!total) return "0%";
  return `${Math.round((part / total) * 100)}%`;
}

function statTileHTML(stateClass, label, count, total) {
  return `
    <div class="stat-tile stat-${stateClass}">
      <span class="stat-tile-value">${count}</span>
      <span class="stat-tile-label">${label}</span>
      <span class="stat-tile-pct">${pct(count, total)}</span>
    </div>`;
}

function heroTileHTML(value, label, toneClass) {
  return `
    <div class="hero-tile ${toneClass}">
      <span class="hero-tile-value">${value}</span>
      <span class="hero-tile-label">${label}</span>
    </div>`;
}

function totalsRowHTML(problems, allTypes) {
  return `
    <div class="insights-total-row"><span>Con problemas</span><span>${problems}</span></div>
    <div class="insights-total-row"><span>Total</span><span>${allTypes}</span></div>`;
}

function compositionBarHTML(segments, total) {
  if (!total) return "";
  const bars = segments
    .filter((s) => s.count > 0)
    .map((s) => `<div class="composition-segment stat-${s.cls}" style="width:${(s.count / total) * 100}%" title="${s.label}: ${s.count}"></div>`)
    .join("");
  return `<div class="composition-bar-track">${bars}</div>`;
}

async function loadInsights() {
  try {
    const i = await fetchInsights();
    const hostTotal = i.host_up + i.host_down + i.host_unreachable;
    const hostDown = i.host_down + i.host_unreachable;  // los inaccesibles cuentan como caídos
    const serviceTotal = i.service_ok + i.service_warning + i.service_critical + i.service_unknown;

    el("heroTiles").innerHTML =
      heroTileHTML(hostTotal, "Equipos monitoreados", "hero-neutral") +
      heroTileHTML(hostDown, "Equipos con problemas", "hero-down") +
      heroTileHTML(i.service_warning + i.service_critical + i.service_unknown, "Alarmas activas", "hero-warning");

    el("insightsHostTiles").innerHTML =
      statTileHTML("up", "Activos", i.host_up, hostTotal) +
      statTileHTML("down", "Caídos", hostDown, hostTotal);
    el("insightsHostBar").innerHTML = compositionBarHTML([
      { cls: "up", label: "Activos", count: i.host_up },
      { cls: "down", label: "Caídos", count: hostDown },
    ], hostTotal);
    el("insightsHostTotals").innerHTML = totalsRowHTML(hostDown, hostTotal);

    // Un check en OK no es una alarma: es la ausencia de una. Acá se cuentan
    // solo las alarmas reales, así el número grande significa "esto hay que
    // mirar" y no "cuántos chequeos corren".
    const alarmas = i.service_warning + i.service_critical + i.service_unknown;
    el("insightsServiceTiles").innerHTML =
      statTileHTML("critical", "Críticas", i.service_critical, alarmas) +
      statTileHTML("warning", "Advertencias", i.service_warning, alarmas) +
      statTileHTML("unknown", "Desconocidas", i.service_unknown, alarmas);
    el("insightsServiceBar").innerHTML = compositionBarHTML([
      { cls: "critical", label: "Críticas", count: i.service_critical },
      { cls: "warning", label: "Advertencias", count: i.service_warning },
      { cls: "unknown", label: "Desconocidas", count: i.service_unknown },
    ], alarmas);
    el("insightsServiceTotals").innerHTML = alarmas === 0
      ? `<p class="totals-row">Sin alarmas activas en ${serviceTotal} chequeos.</p>`
      : `<p class="totals-row"><strong>${alarmas}</strong> ${alarmas === 1 ? "alarma activa" : "alarmas activas"} sobre ${serviceTotal} chequeos.</p>`;
  } catch (err) {
    el("heroTiles").innerHTML = `<p class="empty-state">No se pudo cargar: ${err.message}</p>`;
  }
}

function breakdownRowHTML(label, count, maxCount, colorClass) {
  const widthPct = maxCount ? Math.max(4, (count / maxCount) * 100) : 0;
  return `
    <div class="breakdown-row">
      <span class="breakdown-label">${label}</span>
      <div class="breakdown-track"><div class="breakdown-fill ${colorClass}" style="width:${widthPct}%"></div></div>
      <span class="breakdown-count">${count}</span>
    </div>`;
}

function ambienteStatHTML(count, label, toneClass) {
  return `<span class="ambiente-stat ${toneClass}"><span class="ambiente-stat-value">${count}</span><span class="ambiente-stat-label">${label}</span></span>`;
}

function ambienteCardHTML(ambiente, stats) {
  return `
    <div class="ambiente-card" draggable="true" data-ambiente="${escapeAttr(ambiente)}" title="Arrastrá para reordenar">
      <div class="ambiente-card-title">${ambienteChipHTML(ambiente)}</div>
      <div class="ambiente-card-stats">
        ${ambienteStatHTML(stats.total, "Total", "")}
        ${ambienteStatHTML(stats.up, "Activos", "stat-up")}
        ${ambienteStatHTML(stats.down, "Caídos", "stat-down")}
      </div>
    </div>`;
}

function loadAmbienteOrder() {
  try {
    const raw = JSON.parse(localStorage.getItem(AMBIENTE_ORDER_KEY));
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function saveAmbienteOrder(order) {
  try {
    localStorage.setItem(AMBIENTE_ORDER_KEY, JSON.stringify(order));
  } catch {
    // localStorage no disponible (privado/bloqueado) — se pierde el orden guardado, no rompe nada más.
  }
}

function sortAmbienteEntries(entries) {
  const order = state.ambienteOrder;
  const known = entries
    .filter(([a]) => order.includes(a))
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  const unknown = entries
    .filter(([a]) => !order.includes(a))
    .sort((a, b) => b[1].down - a[1].down || a[0].localeCompare(b[0]));
  return [...known, ...unknown];
}

function wireAmbienteCardDragAndDrop() {
  const container = el("ambienteCards");
  let draggedAmbiente = null;

  container.querySelectorAll(".ambiente-card").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      draggedAmbiente = card.dataset.ambiente;
      e.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      card.classList.add("drag-over");
    });
    card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("drag-over");
      const targetAmbiente = card.dataset.ambiente;
      if (!draggedAmbiente || draggedAmbiente === targetAmbiente) return;

      const currentOrder = [...container.querySelectorAll(".ambiente-card")].map((c) => c.dataset.ambiente);
      const withoutDragged = currentOrder.filter((a) => a !== draggedAmbiente);
      withoutDragged.splice(withoutDragged.indexOf(targetAmbiente), 0, draggedAmbiente);

      state.ambienteOrder = withoutDragged;
      saveAmbienteOrder(withoutDragged);
      renderAmbienteBreakdown(state.hosts);
    });
  });
}

function renderAmbienteBreakdown(hosts) {
  const stats = {};
  hosts.forEach((h) => {
    if (isOperationallyExcluded(h)) return;
    const ambiente = h.ambiente || "Sin identificar";
    if (!stats[ambiente]) stats[ambiente] = { total: 0, up: 0, down: 0 };
    stats[ambiente].total++;
    if (h.current_state === "UP") stats[ambiente].up++;
    else if (isHostDown(h.current_state)) stats[ambiente].down++;
  });

  const container = el("ambienteCards");
  const entries = sortAmbienteEntries(Object.entries(stats));
  container.innerHTML = entries.length
    ? entries.map(([ambiente, s]) => ambienteCardHTML(ambiente, s)).join("")
    : '<p class="empty-state">Sin datos todavía.</p>';

  if (entries.length) wireAmbienteCardDragAndDrop();
}

function renderPatternBreakdown(hosts) {
  const counts = {};
  hosts.forEach((h) => {
    if (isOperationallyExcluded(h)) return;
    counts[h.pattern] = (counts[h.pattern] || 0) + 1;
  });
  const order = ["cronico", "intermitente", "reciente", "recuperado", "estable"];
  const container = el("patternBreakdown");
  const maxCount = Math.max(1, ...order.map((p) => counts[p] || 0));
  container.innerHTML = order
    .filter((p) => counts[p] > 0)
    .map((p) => breakdownRowHTML(patternLabels[p] || p, counts[p], maxCount, `breakdown-pattern-${p}`))
    .join("") || '<p class="empty-state">Sin datos todavía.</p>';
}

// --- Vista Estado ---

function populateAmbienteFilter(hosts) {
  const select = el("filterAmbiente");
  const current = select.value;
  const ambientes = [...new Set(hosts.map((h) => h.ambiente).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Ambiente: todos</option>' +
    ambientes.map((a) => `<option value="${a}">${a}</option>`).join("");
  select.value = current;
}

function applyFilters(hosts) {
  const { search, ambiente, estado, patron, tagId } = state.filters;
  const term = search.trim().toLowerCase();
  return hosts.filter((h) => {
    if (isOperationallyExcluded(h)) return false;
    if (ambiente && h.ambiente !== ambiente) return false;
    if (estado === "DOWN" && !isHostDown(h.current_state)) return false;
    if (estado && estado !== "DOWN" && h.current_state !== estado) return false;
    if (patron && h.pattern !== patron) return false;
    if (tagId && !h.tags.some((t) => String(t.id) === tagId)) return false;
    if (term) {
      const haystack = [h.host_name, h.nombre_pc, h.rak].filter(Boolean).join(" ").toLowerCase();
      if (!haystack.includes(term)) return false;
    }
    return true;
  });
}

function sortHosts(hosts) {
  const { key, dir } = state.sort;
  const factor = dir === "asc" ? 1 : -1;
  return [...hosts].sort((a, b) => {
    let va = a[key];
    let vb = b[key];
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va < vb) return -1 * factor;
    if (va > vb) return 1 * factor;
    return 0;
  });
}

function escapeAttr(str) {
  return String(str ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function actionsCellHTML(host) {
  const anydeskBtn = host.anydesk_id
    ? `<a class="action-btn" href="${ANYDESK_URI_SCHEME}:${host.anydesk_id}">AnyDesk</a>`
    : `<span class="action-btn disabled" title="Este equipo no tiene ID de AnyDesk en el inventario">AnyDesk</span>`;

  const crdBtn = host.crd_host_id
    ? `<a class="action-btn" href="https://remotedesktop.google.com/access/session/${host.crd_host_id}" target="_blank" rel="noopener">Escritorio Remoto</a>`
    : `<span class="action-btn disabled" title="Este equipo no tiene ID de Chrome Remote Desktop en el inventario">Escritorio Remoto</span>`;

  const editBtn = host.match_type !== "no_match"
    ? `<button class="action-btn edit-host-btn" data-host="${host.host_name}">Editar</button>`
    : `<span class="action-btn disabled" title="Este host no tiene fila en el inventario, no se puede editar">Editar</span>`;

  return `<div class="actions">${anydeskBtn}${crdBtn}${editBtn}</div>`;
}

function tagChipHTML(tag, host_name, removable) {
  const remove = removable
    ? `<button class="tag-remove" data-host="${host_name}" data-tag-id="${tag.id}" title="Quitar tag">×</button>`
    : "";
  return `<span class="tag-chip" style="background:${tag.color}">${tag.name}${remove}</span>`;
}

// --- Servicios por host (alarmas de Nagios) ---

function servicesSummaryHTML(host) {
  if (!host.services || host.services.length === 0) {
    return '<span class="services-summary-empty">—</span>';
  }
  const counts = { CRITICAL: 0, WARNING: 0, UNKNOWN: 0, OK: 0 };
  host.services.forEach((s) => { counts[s.current_state] = (counts[s.current_state] || 0) + 1; });

  const badges = ["CRITICAL", "WARNING", "UNKNOWN"]
    .filter((state) => counts[state] > 0)
    .map((state) => `<span class="state-pill state-${state.toLowerCase()}">${counts[state]} ${serviceStateLabel(state)}</span>`)
    .join(" ");

  const summary = badges || `<span class="state-pill state-ok">${counts.OK} OK</span>`;
  return `<button class="services-summary-btn" data-host="${host.host_name}" title="Ver detalle de servicios">${summary}</button>`;
}

function openServicesModal(host) {
  el("servicesModalTitle").textContent = `Servicios — ${host.nombre_pc || host.host_name}`;
  const list = el("servicesModalList");
  list.innerHTML = host.services.length
    ? host.services.map((s) => `
        <div class="service-row">
          <span class="state-pill state-${s.current_state.toLowerCase()}">${serviceStateLabel(s.current_state)}</span>
          <span class="service-name">${s.service_description}</span>
          <span class="service-output">${s.plugin_output || ""}</span>
        </div>`).join("")
    : '<p class="empty-state">Sin servicios reportados para este host.</p>';
  el("servicesModal").style.display = "block";
  el("servicesModalBackdrop").style.display = "block";
}

function closeServicesModal() {
  el("servicesModal").style.display = "none";
  el("servicesModalBackdrop").style.display = "none";
}

function renderFilterSummary(filtered) {
  const { ambiente, estado, patron, tagId, search } = state.filters;
  // Total sobre el que se compara: los equipos que la vista realmente maneja,
  // sin los excluidos por tag (RMA/Testing/Refurbish), que van a STOCK.
  const universo = state.hosts.filter((h) => !isOperationallyExcluded(h));

  el("filterCount").textContent = filtered.length;

  // El estado va como adjetivo del sustantivo ("equipos caídos"), no como
  // complemento ("equipos en caídos"); el resto de los filtros sí son
  // complementos.
  const plural = filtered.length !== 1;
  let etiqueta = plural ? "equipos" : "equipo";
  if (estado) etiqueta += ` ${hostStateLabel(estado).toLowerCase()}${plural ? "s" : ""}`;

  const complementos = [];
  if (ambiente) complementos.push(`en ${ambienteChipHTML(ambiente)}`);
  if (patron) complementos.push(`con patrón <strong>${patternLabels[patron] || patron}</strong>`);
  if (tagId) {
    const tag = state.tags.find((t) => String(t.id) === tagId);
    if (tag) complementos.push(`con tag <strong>${escapeAttr(tag.name)}</strong>`);
  }
  if (search.trim()) {
    complementos.push(`que ${plural ? "coinciden" : "coincide"} con <strong>"${escapeAttr(search.trim())}"</strong>`);
  }
  if (complementos.length) etiqueta += ` ${complementos.join(" · ")}`;

  el("filterLabel").innerHTML = etiqueta;

  // El desglose solo aporta si el conjunto es mixto: filtrando por estado
  // sería repetir lo que ya dice la etiqueta.
  const activos = filtered.filter((h) => h.current_state === "UP").length;
  const caidos = filtered.length - activos;
  el("filterSplit").innerHTML = filtered.length && !estado
    ? `<span class="up">${activos} activo${activos === 1 ? "" : "s"}</span>` +
      `<span class="down">${caidos} caído${caidos === 1 ? "" : "s"}</span>`
    : "";

  el("filterTotal").textContent = filtered.length === universo.length
    ? `${universo.length} equipos monitoreados`
    : `de ${universo.length} monitoreados`;
}

function renderHostsTable() {
  const filtered = sortHosts(applyFilters(state.hosts));
  const tbody = el("hostsTableBody");
  renderFilterSummary(filtered);

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-state">Ningún host coincide con los filtros.</td></tr>';
    updateBulkBar();
    return;
  }

  tbody.innerHTML = filtered.map((h) => {
    const truncatedFlag = h.host_name.length > NETBIOS_TRUNCATE_LEN
      ? '<span class="unmatched-flag" title="Nombre largo: puede sufrir truncado NetBIOS">⚠</span>' : "";
    const etiquetaNombre = h.nombre_pc
      ? escapeAttr(h.nombre_pc)
      : '<span class="unmatched-flag">sin identificar</span>';
    const nombre = `<button class="host-name-link" data-detail="${escapeAttr(h.host_name)}" title="Ver detalle del equipo">${etiquetaNombre}</button>`;
    const notaBadge = h.notas
      ? `<span class="note-badge" title="${escapeAttr(h.notas)}">nota</span>` : "";
    // El operador lo reporta Nagios aunque no haya ICCID cargado, así que se
    // muestra igual: sin él, la mayoría de los equipos no mostraría nada.
    const simCell = h.sim
      ? `<span class="host-name">${h.sim}</span>${empresaChipHTML(h.empresa)}`
      : (h.empresa ? empresaChipHTML(h.empresa).trim() : "—");
    const checked = state.selectedHosts.has(h.host_name) ? "checked" : "";
    return `
      <tr>
        <td><input type="checkbox" class="row-select" data-host="${h.host_name}" ${checked}></td>
        <td><span class="state-pill state-${hostStateClass(h.current_state)}">${hostStateLabel(h.current_state)}</span></td>
        <td>${nombre}${notaBadge}</td>
        <td>${ambienteChipHTML(h.ambiente)}</td>
        <td>${h.downtime_human}</td>
        <td><span class="pattern-tag pattern-${h.pattern}">${patternLabels[h.pattern] || h.pattern}</span></td>
        <td class="host-name">${h.host_name} ${truncatedFlag}</td>
        <td>${simCell}</td>
        <td>${servicesSummaryHTML(h)}</td>
        <td>${actionsCellHTML(h)}</td>
      </tr>`;
  }).join("");

  tbody.querySelectorAll(".services-summary-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const host = state.hosts.find((h) => h.host_name === btn.dataset.host);
      if (host) openServicesModal(host);
    });
  });
  tbody.querySelectorAll(".row-select").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) state.selectedHosts.add(cb.dataset.host);
      else state.selectedHosts.delete(cb.dataset.host);
      updateBulkBar();
    });
  });
  tbody.querySelectorAll(".tag-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await unassignTag(Number(btn.dataset.tagId), [btn.dataset.host]);
        await loadHosts();
      } catch (err) {
        alert(`No se pudo quitar el tag: ${err.message}`);
      }
    });
  });
  tbody.querySelectorAll(".edit-host-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const host = state.hosts.find((h) => h.host_name === btn.dataset.host);
      if (host) openEditModal(host);
    });
  });
  tbody.querySelectorAll(".host-name-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      const host = state.hosts.find((h) => h.host_name === btn.dataset.detail);
      if (host) openHostDrawer(host);
    });
  });

  updateBulkBar();
}

// --- Editar equipo (Nombre PC / Ambiente / SIM / Empresa / Notas) ---

function renderEditTags(host) {
  const actuales = el("editTagsActuales");
  actuales.innerHTML = host.tags.length
    ? host.tags.map((t) => tagChipHTML(t, host.host_name, true)).join(" ")
    : '<span class="block-hint">Sin tags.</span>';

  // Quitar un tag desde el modal, sin tener que ir a buscar el equipo a la tabla.
  actuales.querySelectorAll(".tag-remove-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await unassignTag(Number(btn.dataset.tagId), [btn.dataset.host]);
        await loadHosts();
        const fresco = state.hosts.find((h) => h.host_name === host.host_name);
        if (fresco) { state.editingHostData = fresco; renderEditTags(fresco); }
      } catch (err) {
        alert(`No se pudo quitar el tag: ${err.message}`);
      }
    });
  });

  const yaTiene = new Set(host.tags.map((t) => t.id));
  const disponibles = state.tags.filter((t) => !yaTiene.has(t.id));
  const selector = el("editTagAgregar");
  selector.innerHTML = disponibles.length
    ? '<option value="">+ Agregar un tag...</option>' +
      disponibles.map((t) => `<option value="${t.id}">${t.name}</option>`).join("")
    : '<option value="">No hay más tags para agregar</option>';
  selector.disabled = disponibles.length === 0;
}

function openEditModal(host) {
  state.editingHost = host.host_name;
  state.editingHostData = host;
  el("editHostLabel").textContent = `${host.nombre_pc || "sin identificar"} — host Nagios: ${host.host_name}`;
  el("editNombrePc").value = host.nombre_pc || "";
  el("editAmbiente").value = host.ambiente || "";
  el("editSim").value = host.sim || "";
  el("editEmpresa").value = host.empresa || "";
  el("editNotas").value = host.notas || "";

  el("editEstado").innerHTML = state.estados
    .map((e) => `<option value="${e.valor}">${e.etiqueta}</option>`).join("");
  el("editEstado").value = host.estado || "Produccion";

  renderEditTags(host);
  el("editModal").style.display = "block";
  el("editModalBackdrop").style.display = "block";
}

function closeEditModal() {
  state.editingHost = null;
  el("editModal").style.display = "none";
  el("editModalBackdrop").style.display = "none";
}

async function handleSaveEdit() {
  if (!state.editingHost) return;
  const nombrePc = el("editNombrePc").value.trim();
  const ambiente = el("editAmbiente").value.trim();
  const notas = el("editNotas").value.trim();
  const sim = el("editSim").value.trim();
  const empresa = el("editEmpresa").value.trim();
  try {
    const estado = el("editEstado").value;
    await editHost(state.editingHost, { nombre_pc: nombrePc, ambiente, notas, sim, empresa, estado });
    closeEditModal();
    await loadHosts();
  } catch (err) {
    alert(`No se pudo guardar: ${err.message}`);
  }
}

// --- Buscador de equipos (lo usan Histórico y Reemplazar) ---

function hostSearchLabel(host) {
  // El label incluye RAK y host de Nagios para poder encontrar por cualquiera de los tres.
  const parts = [host.nombre_pc, host.rak, host.host_name].filter(Boolean);
  return [...new Set(parts)].join(" — ");
}

function fillHostDatalist(datalistId, hosts) {
  const sorted = [...hosts].sort((a, b) => (a.nombre_pc || a.host_name).localeCompare(b.nombre_pc || b.host_name));
  el(datalistId).innerHTML = sorted
    .map((h) => `<option value="${escapeAttr(hostSearchLabel(h))}"></option>`)
    .join("");
}

function resolveHost(text, candidatos) {
  const term = String(text || "").trim().toLowerCase();
  if (!term) return null;
  // 1) el texto exacto que el datalist devuelve al elegir una opción
  const byLabel = candidatos.find((h) => hostSearchLabel(h).toLowerCase() === term);
  if (byLabel) return byLabel;
  // 2) alguien que escribió directamente un host de Nagios, un RAK o el nombre completo
  const exact = candidatos.find((h) =>
    h.host_name.toLowerCase() === term ||
    (h.rak || "").toLowerCase() === term ||
    (h.nombre_pc || "").toLowerCase() === term
  );
  if (exact) return exact;
  // 3) último recurso: una única coincidencia parcial
  const partial = candidatos.filter((h) => hostSearchLabel(h).toLowerCase().includes(term));
  return partial.length === 1 ? partial[0] : null;
}

function populateHistoryHostOptions(hosts) {
  fillHostDatalist("historyHostOptions", hosts);
}

function resolveHistoryHostName(text) {
  const host = resolveHost(text, state.hosts);
  return host ? host.host_name : null;
}

async function loadHosts() {
  try {
    state.hosts = await fetchHosts();
    populateAmbienteFilter(state.hosts);
    populateHistoryHostOptions(state.hosts);
    populateReplaceTabSelects(state.hosts);
    renderHostsTable();
    renderAmbienteBreakdown(state.hosts);
    renderPatternBreakdown(state.hosts);
    loadAlarmas();
    renderRmaTestingTab();
  } catch (err) {
    el("hostsTableBody").innerHTML =
      `<tr><td colspan="10" class="empty-state">No se pudieron cargar los hosts: ${err.message}</td></tr>`;
  }
}

// --- Tags ---

async function loadTags() {
  try {
    state.tags = await fetchTags();
  } catch (err) {
    state.tags = [];
  }
  populateTagFilterGroup();
  renderTagManageList();
  updateBulkBar();
}

function populateTagFilterGroup() {
  const group = el("tagFilterGroup");
  const currentActive = state.filters.tagId;
  const todosActive = currentActive === "" ? "active" : "";
  group.innerHTML = `<button class="state-filter-btn ${todosActive}" data-tag-id="">Todos</button>` +
    state.tags.map((t) => {
      const isActive = currentActive === String(t.id);
      return `<button class="state-filter-btn" data-tag-id="${t.id}" style="background:${isActive ? t.color : ""};border-color:${t.color};color:${isActive ? "white" : t.color}">${t.name}</button>`;
    }).join("");

  group.querySelectorAll(".state-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filters.tagId = btn.dataset.tagId;
      populateTagFilterGroup();
      renderHostsTable();
    });
  });
}

function updateBulkBar() {
  const bar = el("bulkBar");
  const count = state.selectedHosts.size;
  if (count === 0) {
    bar.style.display = "none";
    return;
  }
  bar.style.display = "flex";
  el("bulkCount").textContent = `${count} seleccionado(s)`;

  const container = el("bulkTagButtons");
  if (state.tags.length === 0) {
    container.innerHTML = '<span class="filters-label">Creá un tag primero (botón "+ Tags")</span>';
    return;
  }
  container.innerHTML = state.tags.map((t) =>
    `<button class="state-filter-btn" data-tag-id="${t.id}" style="border-color:${t.color};color:${t.color}">${t.name}</button>`
  ).join("");
  container.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await assignTag(Number(btn.dataset.tagId), Array.from(state.selectedHosts));
        state.selectedHosts.clear();
        await loadHosts();
      } catch (err) {
        alert(`No se pudo asignar el tag: ${err.message}`);
      }
    });
  });
}

function tagCategorySelectHTML(tag) {
  const options = ["", "rma", "testing", "refurbish"].map((cat) => {
    const label = cat === "" ? "Normal" : TAG_CATEGORY_LABELS[cat];
    const selected = (tag.category || "") === cat ? "selected" : "";
    return `<option value="${cat}" ${selected}>${label}</option>`;
  }).join("");
  return `<select class="input-select tag-category-select" data-tag-id="${tag.id}" title="Categoría">${options}</select>`;
}

function renderTagManageList() {
  const list = el("tagManageList");
  list.innerHTML = state.tags.length
    ? state.tags.map((t) => `
        <div class="tag-manage-row">
          ${tagChipHTML(t, "", false)}
          ${tagCategorySelectHTML(t)}
          <button class="tag-manage-delete" data-tag-id="${t.id}">Borrar</button>
        </div>`).join("")
    : '<p class="empty-state" style="padding:8px 0">Sin tags todavía.</p>';

  list.querySelectorAll(".tag-manage-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const tag = state.tags.find((t) => t.id === Number(btn.dataset.tagId));
      if (!confirm(`¿Borrar el tag "${tag?.name}"? Se va a quitar de todos los equipos que lo tengan.`)) return;
      try {
        await deleteTag(Number(btn.dataset.tagId));
        if (state.filters.tagId === btn.dataset.tagId) state.filters.tagId = "";
        await loadTags();
        await loadHosts();
      } catch (err) {
        alert(`No se pudo borrar el tag: ${err.message}`);
      }
    });
  });

  list.querySelectorAll(".tag-category-select").forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        await updateTagCategory(Number(select.dataset.tagId), select.value);
        await loadTags();
        await loadHosts();
      } catch (err) {
        alert(`No se pudo cambiar la categoría: ${err.message}`);
      }
    });
  });
}

function openTagModal() {
  el("tagModal").style.display = "block";
  el("tagModalBackdrop").style.display = "block";
}

function closeTagModal() {
  el("tagModal").style.display = "none";
  el("tagModalBackdrop").style.display = "none";
}

async function handleCreateTag() {
  const name = el("newTagName").value.trim();
  const color = el("newTagColor").value;
  const category = el("newTagCategory").value;
  if (!name) return;
  try {
    await createTag(name, color, category);
    el("newTagName").value = "";
    el("newTagCategory").value = "";
    await loadTags();
  } catch (err) {
    alert(`No se pudo crear el tag: ${err.message}`);
  }
}

// --- Vista Histórico ---

function renderTimeline(history) {
  if (history.length === 0) {
    return '<p class="empty-state">Sin historial en el rango elegido.</p>';
  }

  const start = history[0].polled_at;
  const end = Math.floor(Date.now() / 1000);
  const totalSpan = Math.max(1, end - start);

  const segments = history.map((snap, idx) => {
    const segEnd = idx + 1 < history.length ? history[idx + 1].polled_at : end;
    const widthPct = Math.max(0.3, ((segEnd - snap.polled_at) / totalSpan) * 100);
    return `<div class="timeline-segment state-${hostStateClass(snap.current_state)}" style="width:${widthPct}%" title="${hostStateLabel(snap.current_state)} desde ${formatEpoch(snap.polled_at)}"></div>`;
  }).join("");

  const transitions = [];
  for (let i = 0; i < history.length; i++) {
    if (i === 0 || history[i].current_state !== history[i - 1].current_state) {
      transitions.push(history[i]);
    }
  }
  const transitionsHTML = transitions.slice().reverse().map((snap) => `
    <div class="transition-row">
      <span class="transition-time">${formatEpoch(snap.polled_at)}</span>
      <span class="state-pill state-${hostStateClass(snap.current_state)}">${hostStateLabel(snap.current_state)}</span>
      <span>${snap.plugin_output || ""}</span>
    </div>
  `).join("");

  return `
    <div class="timeline-bar">${segments}</div>
    <div class="timeline-legend">
      <span class="legend-up">Activo</span>
      <span class="legend-down">Caído</span>
    </div>
    <div class="transitions-list">${transitionsHTML}</div>
  `;
}

// El motivo solo se dibuja si existe: los reemplazos anteriores a esta función
// no lo tienen y no hay que ensuciarlos con un "sin especificar".
function motivoChipHTML(r) {
  const texto = r.motivo_etiqueta || r.motivo;
  if (!texto) return "";
  return `<span class="motivo-chip" title="Motivo del reemplazo">${escapeAttr(texto)}</span>`;
}

async function loadReplacements() {
  try {
    const reemplazos = await fetchReplacements();
    el("replacementsList").innerHTML = reemplazos.length
      ? reemplazos.map((r) => `
          <div class="replacement-row">
            <span class="replacement-date">${formatEpoch(r.replaced_at)}</span>
            <span class="replacement-site">${escapeAttr(r.nombre_pc || r.new_host_name)}</span>
            ${r.ambiente ? ambienteChipHTML(r.ambiente) : ""}
            <span class="replacement-raks">
              <span class="replacement-old">${escapeAttr(r.old_rak)}</span>
              <span class="replacement-arrow">→</span>
              <span class="replacement-new">${escapeAttr(r.new_rak)}</span>
            </span>
            ${motivoChipHTML(r)}
          </div>`).join("")
      : '<p class="empty-state">Todavía no se registró ningún reemplazo.</p>';
  } catch (err) {
    el("replacementsList").innerHTML = `<p class="empty-state">No se pudo cargar: ${escapeAttr(err.message)}</p>`;
  }
}

async function loadHistory() {
  const rawText = el("historyHostSearch").value;
  const days = Number(el("historyRangeSelect").value);
  const content = el("historyContent");

  if (!rawText.trim()) {
    el("fichaEquipo").hidden = true;
    content.innerHTML = '<p class="empty-state">Buscá un equipo para ver su ficha y su historial de estado.</p>';
    return;
  }

  const hostName = resolveHistoryHostName(rawText);
  if (!hostName) {
    el("fichaEquipo").hidden = true;
    content.innerHTML = '<p class="empty-state">Ningún equipo coincide con esa búsqueda. Probá con el nombre, el RAK o el host de Nagios.</p>';
    return;
  }

  content.innerHTML = '<p class="empty-state">Cargando...</p>';
  renderFichaEquipo(hostName);
  try {
    const history = await fetchHostHistory(hostName, days);
    content.innerHTML = renderTimeline(history);
  } catch (err) {
    content.innerHTML = `<p class="empty-state">No se pudo cargar el historial: ${err.message}</p>`;
  }
}

// --- Ficha del equipo (pestaña Histórico) ---

function fichaDatoHTML(etiqueta, valor) {
  return `<div class="ficha-dato"><dt>${etiqueta}</dt><dd>${valor || "—"}</dd></div>`;
}

async function renderFichaEquipo(hostName) {
  const host = state.hosts.find((h) => h.host_name === hostName);
  const ficha = el("fichaEquipo");
  if (!host) { ficha.hidden = true; return; }
  ficha.hidden = false;

  el("fichaTitulo").textContent = host.nombre_pc || host.host_name;
  el("fichaSubtitulo").innerHTML =
    `Host en Nagios: <span class="host-name">${host.host_name}</span>` +
    (host.rak ? ` · RAK completo: <span class="host-name">${escapeAttr(host.rak)}</span>` : "");

  const estadoEtiqueta = (state.estados.find((e) => e.valor === host.estado) || {}).etiqueta || host.estado;
  el("fichaDatos").innerHTML =
    fichaDatoHTML("Estado actual",
      `<span class="state-pill state-${hostStateClass(host.current_state)}">${hostStateLabel(host.current_state)}</span>`) +
    fichaDatoHTML("Ambiente", ambienteChipHTML(host.ambiente)) +
    fichaDatoHTML("Situación", estadoEtiqueta) +
    fichaDatoHTML("Patrón", `<span class="pattern-tag pattern-${host.pattern}">${patternLabels[host.pattern] || host.pattern}</span>`) +
    fichaDatoHTML("Tiempo caído", host.downtime_human) +
    fichaDatoHTML("SIM", host.sim ? `${host.sim} ${empresaChipHTML(host.empresa)}` : empresaChipHTML(host.empresa)) +
    fichaDatoHTML("Tags", host.tags.length ? host.tags.map((t) => tagChipHTML(t, "", false)).join(" ") : "") +
    fichaDatoHTML("Notas", host.notas ? escapeAttr(host.notas) : "");

  // Reemplazos: la cadena completa del equipo, no solo el último cambio.
  try {
    const reemplazos = await fetchHostReplacements(hostName);
    el("fichaReemplazos").innerHTML = reemplazos.length
      ? reemplazos.map((r) => `
          <div class="replacement-row">
            <span class="replacement-date">${formatEpoch(r.replaced_at)}</span>
            <span class="replacement-raks">
              <span class="replacement-old">${escapeAttr(r.old_rak)}</span>
              <span class="replacement-arrow">→</span>
              <span class="replacement-new">${escapeAttr(r.new_rak)}</span>
            </span>
            ${motivoChipHTML(r)}
          </div>`).join("")
      : '<p class="empty-state">Este equipo nunca fue reemplazado.</p>';
  } catch (err) {
    el("fichaReemplazos").innerHTML = `<p class="empty-state">No se pudo cargar: ${escapeAttr(err.message)}</p>`;
  }

  const alarmasActivas = (host.services || []).filter((s) => s.current_state !== "OK");
  el("fichaAlarmas").innerHTML = alarmasActivas.length
    ? alarmasActivas.map((s) => `
        <div class="ficha-alarma">
          <span class="state-pill state-${s.current_state.toLowerCase()}">${serviceStateLabel(s.current_state)}</span>
          <span class="ficha-alarma-tipo">${s.service_description}</span>
          <span class="ficha-alarma-detalle">${escapeAttr((s.plugin_output || "").split("\n")[0])}</span>
        </div>`).join("")
    : '<p class="empty-state">Sin alarmas activas.</p>';

  try {
    const eventos = await fetchHostEventos(hostName, 60);
    el("fichaEventos").innerHTML = eventos.length
      ? eventos.map((e) => `
          <div class="ficha-evento">
            <span class="replacement-date">${formatEpoch(e.changed_at)}</span>
            <span class="ficha-alarma-tipo">${e.etiqueta}</span>
            <span class="ficha-evento-cambio">
              ${e.previous_state === null
                ? "alta"
                : `${serviceStateLabel(ESTADO_SERVICIO[e.previous_state])} → ${serviceStateLabel(ESTADO_SERVICIO[e.current_state])}`}
            </span>
          </div>`).join("")
      : '<p class="empty-state">Todavía no se registró ningún cambio de alarma en este equipo.</p>';
  } catch (err) {
    el("fichaEventos").innerHTML = `<p class="empty-state">No se pudo cargar: ${escapeAttr(err.message)}</p>`;
  }
}

// --- Vista Alarmas ---

// El estado llega como número desde la API (1=WARNING, 2=CRITICAL, 3=UNKNOWN);
// el 0 (OK) nunca se devuelve porque un chequeo en OK no es una alarma.
const ESTADO_ALARMA = { 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN" };

// En la línea de tiempo sí aparece el 0: volver a OK es una recuperación,
// y es justamente la transición que interesa ver después de un problema.
const ESTADO_SERVICIO = { 0: "OK", ...ESTADO_ALARMA };

async function loadAlarmas() {
  try {
    state.alarmas = await fetchAlarmas();
  } catch (err) {
    el("alarmsTableBody").innerHTML =
      `<tr><td colspan="7" class="empty-state">No se pudo cargar: ${err.message}</td></tr>`;
    return;
  }
  renderAlarms();
}

function renderIntervenciones(intervenciones) {
  const panel = el("intervencionPanel");
  panel.hidden = intervenciones.length === 0;
  if (!intervenciones.length) return;
  el("intervencionContador").textContent =
    `${intervenciones.length} ${intervenciones.length === 1 ? "alarma" : "alarmas"}`;

  el("intervencionList").innerHTML = intervenciones.map((a) => `
    <div class="intervencion-item">
      <div class="intervencion-cabecera">
        <span class="state-pill state-critical">${a.etiqueta}</span>
        <span class="intervencion-equipo">${a.nombre_pc || `<span class="unmatched-flag">${a.host_name}</span>`}</span>
        ${ambienteChipHTML(a.ambiente)}
        <span class="intervencion-tiempo">hace ${a.duracion_human}</span>
      </div>
      <div class="intervencion-pie">
        <p class="intervencion-detalle">${(a.plugin_output || "").split("\n")[0]}</p>
        <button class="action-btn alarm-view-btn" data-host="${a.host_name}">Ver equipo</button>
      </div>
    </div>`).join("");
}

function renderAgentesMudos(mudos) {
  const panel = el("agentesMudosPanel");
  panel.hidden = mudos.length === 0;
  if (!mudos.length) return;
  el("mudosContador").textContent =
    `${mudos.length} ${mudos.length === 1 ? "equipo" : "equipos"}`;

  el("agentesMudosList").innerHTML = mudos.map((m) => `
    <div class="mudos-item">
      <span class="state-pill state-${hostStateClass(m.estado_host)}">${hostStateLabel(m.estado_host)}</span>
      <span class="mudos-equipo">${m.nombre_pc || `<span class="unmatched-flag">${m.host_name}</span>`}</span>
      ${ambienteChipHTML(m.ambiente)}
      <button class="action-btn alarm-view-btn" data-host="${m.host_name}">Ver equipo</button>
    </div>`).join("");
}

function renderTipoCards(resumen, tipos) {
  const descripciones = Object.fromEntries(tipos.map((t) => [t.nombre, t]));
  const activo = state.alarmFilters.tipo;

  el("alarmTypeCards").innerHTML = resumen.map((r) => {
    const info = descripciones[r.tipo];
    const umbrales = info
      ? [info.umbral_warning && `Advertencia: ${info.umbral_warning}`,
         info.umbral_critical && `Crítico: ${info.umbral_critical}`].filter(Boolean).join(" · ")
      : "";
    const titulo = info ? `${info.descripcion}${umbrales ? `\n${umbrales}` : ""}` : r.etiqueta;
    return `
      <button class="alarm-type-card ${activo === r.tipo ? "activo" : ""} ${r.total === 0 ? "vacia" : ""}"
              data-tipo="${r.tipo}" title="${titulo.replace(/"/g, "&quot;")}">
        <span class="alarm-type-nombre">${r.etiqueta}</span>
        <span class="alarm-type-total">${r.total}</span>
        <span class="alarm-type-desglose">
          ${r.critical ? `<em class="sev-critical">${r.critical} crít.</em>` : ""}
          ${r.warning ? `<em class="sev-warning">${r.warning} adv.</em>` : ""}
          ${r.unknown ? `<em class="sev-unknown">${r.unknown} desc.</em>` : ""}
          ${r.total === 0 ? "sin alarmas" : ""}
        </span>
      </button>`;
  }).join("");

  el("alarmTypeCards").querySelectorAll(".alarm-type-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      // Volver a tocar el tipo activo quita el filtro.
      state.alarmFilters.tipo = state.alarmFilters.tipo === btn.dataset.tipo ? "" : btn.dataset.tipo;
      renderAlarms();
    });
  });
}

function renderAlarms() {
  const datos = state.alarmas;
  if (!datos) return;

  const all = datos.alarmas;
  const counts = { CRITICAL: 0, WARNING: 0, UNKNOWN: 0 };
  all.forEach((a) => { counts[ESTADO_ALARMA[a.estado]]++; });

  el("alarmSeverityTiles").innerHTML =
    heroTileHTML(datos.intervenciones.length, "Requieren intervención", "hero-down") +
    heroTileHTML(counts.CRITICAL, "Críticas", "hero-down") +
    heroTileHTML(counts.WARNING, "Advertencias", "hero-warning") +
    heroTileHTML(counts.UNKNOWN, "Desconocidas", "hero-unknown");

  renderIntervenciones(datos.intervenciones);
  renderAgentesMudos(datos.agentes_mudos);
  renderTipoCards(datos.resumen, datos.tipos);

  const { search, severity, tipo } = state.alarmFilters;
  const term = search.trim().toLowerCase();
  const filtered = all.filter((a) => {
    if (severity && ESTADO_ALARMA[a.estado] !== severity) return false;
    if (tipo && a.tipo !== tipo) return false;
    if (term) {
      const haystack = [a.nombre_pc, a.host_name, a.ambiente].filter(Boolean).join(" ").toLowerCase();
      if (!haystack.includes(term)) return false;
    }
    return true;
  });

  el("alarmFilterCount").textContent = filtered.length === all.length
    ? `${all.length} ${all.length === 1 ? "alarma" : "alarmas"}`
    : `${filtered.length} de ${all.length} ${all.length === 1 ? "alarma" : "alarmas"}`;

  const tbody = el("alarmsTableBody");
  tbody.innerHTML = filtered.length
    ? filtered.map((a) => {
        const sev = ESTADO_ALARMA[a.estado];
        return `
        <tr class="${a.requiere_intervencion ? "fila-intervencion" : ""}">
          <td><span class="state-pill state-${sev.toLowerCase()}">${serviceStateLabel(sev)}</span></td>
          <td>${a.nombre_pc || `<span class="unmatched-flag">${a.host_name}</span>`}</td>
          <td>${ambienteChipHTML(a.ambiente)}</td>
          <td>${a.etiqueta}</td>
          <td class="celda-duracion" data-segundos="${a.duracion_segundos}">${a.duracion_human}</td>
          <td>${(a.plugin_output || "").split("\n")[0]}</td>
          <td><button class="action-btn alarm-view-btn" data-host="${a.host_name}">Ver equipo</button></td>
        </tr>`;
      }).join("")
    : '<tr><td colspan="7" class="empty-state">Ninguna alarma con estos filtros.</td></tr>';

  document.querySelectorAll(".alarm-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const host = state.hosts.find((h) => h.host_name === btn.dataset.host);
      if (host) openServicesModal(host);
    });
  });
}

// --- Vista Pendientes (RMA / Testing / Refurbish) ---

function estadoSelectHTML(rak, estadoActual, extraClase) {
  // Varias filas de STOCK tienen el RAK cargado como nombre y la columna RAK
  // vacía; el backend resuelve ese caso, así que acá alcanza con no mandar vacío.
  if (!rak) return '<span class="block-hint">sin identificador</span>';
  const opciones = state.estados
    .map((e) => `<option value="${e.valor}" ${e.valor === estadoActual ? "selected" : ""}>${e.etiqueta}</option>`)
    .join("");
  return `<select class="input-select ${extraClase}" data-rak="${escapeAttr(rak)}">${opciones}</select>`;
}

function stockRowHTML(host) {
  const nombre = host.nombre_pc || `<span class="unmatched-flag">sin identificar</span>`;
  return `
    <tr>
      <td><span class="state-pill state-${hostStateClass(host.current_state)}">${hostStateLabel(host.current_state)}</span></td>
      <td>${nombre}</td>
      <td>${ambienteChipHTML(host.ambiente)}</td>
      <td class="host-name">${host.host_name}</td>
      <td>${estadoSelectHTML(host.rak || host.nombre_pc, host.estado, "stock-estado-select")}</td>
      <td>${actionsCellHTML(host)}</td>
    </tr>`;
}

function coincideBusqueda(equipo, termino) {
  if (!termino) return true;
  return [equipo.nombre_pc, equipo.host_name, equipo.ambiente, equipo.rak]
    .filter(Boolean).join(" ").toLowerCase().includes(termino);
}

function renderStockEstadoFiltros(fueraDeProduccion) {
  const grupo = el("stockEstadoGroup");
  const presentes = state.estados.filter((e) =>
    e.valor !== "Produccion" && fueraDeProduccion.some((h) => h.estado === e.valor));
  const actual = state.stockFilters.estado;
  grupo.innerHTML =
    `<button class="state-filter-btn ${actual === "" ? "active" : ""}" data-value="">Todos</button>` +
    presentes.map((e) =>
      `<button class="state-filter-btn ${actual === e.valor ? "active" : ""}" data-value="${e.valor}">${e.etiqueta}</button>`
    ).join("");

  grupo.querySelectorAll(".state-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.stockFilters.estado = btn.dataset.value;
      renderRmaTestingTab();
    });
  });
}

async function cambiarEstado(rak, estado, recargar) {
  try {
    await setInventoryEstado(rak, estado);
    await recargar();
  } catch (err) {
    alert(`No se pudo cambiar la situación: ${err.message}`);
  }
}

function renderStockTiles(fueraDeProduccion) {
  el("stockTiles").innerHTML = state.estados
    .filter((e) => e.valor !== "Produccion")
    .map((e) => heroTileHTML(
      fueraDeProduccion.filter((h) => h.estado === e.valor).length, e.etiqueta, "hero-neutral"))
    .join("") +
    heroTileHTML(state.pendingHosts.length, "Sin actividad en Nagios", "hero-unknown");
}

function renderRmaTestingTab() {
  // La situación sale del inventario, no de los tags: un equipo deja STOCK
  // cambiándole el desplegable, sin depender de que alguien le saque una etiqueta.
  const fueraDeProduccion = state.hosts.filter((h) => h.estado && h.estado !== "Produccion");

  renderStockTiles(fueraDeProduccion);
  renderStockEstadoFiltros(fueraDeProduccion);

  const termino = state.stockFilters.search.trim().toLowerCase();
  const filtrados = fueraDeProduccion.filter((h) =>
    (!state.stockFilters.estado || h.estado === state.stockFilters.estado) && coincideBusqueda(h, termino));

  el("stockFilterCount").textContent = filtrados.length === fueraDeProduccion.length
    ? `${fueraDeProduccion.length} ${fueraDeProduccion.length === 1 ? "equipo" : "equipos"}`
    : `${filtrados.length} de ${fueraDeProduccion.length} equipos`;

  el("stockTableBody").innerHTML = filtrados.length
    ? filtrados.map(stockRowHTML).join("")
    : '<tr><td colspan="6" class="empty-state">Ningún equipo fuera de producción con estos filtros.</td></tr>';

  document.querySelectorAll("#tab-rma-testing .edit-host-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const host = state.hosts.find((h) => h.host_name === btn.dataset.host);
      if (host) openEditModal(host);
    });
  });

  el("stockTableBody").querySelectorAll(".stock-estado-select").forEach((select) => {
    select.addEventListener("change", () => cambiarEstado(select.dataset.rak, select.value, loadHosts));
  });
}

function pendingInventoryRowHTML(p) {
  return `
    <tr>
      <td>${p.nombre_pc || "sin identificar"}</td>
      <td>${ambienteChipHTML(p.ambiente)}</td>
      <td class="host-name">${p.rak || p.host_name}</td>
      <td>${estadoSelectHTML(p.rak || p.nombre_pc, p.estado, "pending-estado-select")}</td>
      <td>
        <button class="action-btn danger pending-delete-btn"
                data-rak="${escapeAttr(p.rak || p.nombre_pc || "")}"
                data-nombre="${escapeAttr(p.nombre_pc || p.host_name)}"
                ${(p.rak || p.nombre_pc) ? "" : "disabled title=\"Sin identificador no se puede ubicar la fila\""}>Borrar</button>
      </td>
    </tr>`;
}

async function loadPendingInventoryHosts() {
  const tbody = el("pendingInventoryTableBody");
  try {
    state.pendingHosts = await fetchPendingInventoryHosts();
    renderPendingInventoryTable();
    renderStockTiles(state.hosts.filter((h) => h.estado && h.estado !== "Produccion"));
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No se pudo cargar: ${err.message}</td></tr>`;
  }
}

function renderPendingInventoryTable() {
  const tbody = el("pendingInventoryTableBody");
  const termino = state.pendingFilters.search.trim().toLowerCase();
  const filtrados = state.pendingHosts.filter((p) => coincideBusqueda(p, termino));

  el("pendingFilterCount").textContent = filtrados.length === state.pendingHosts.length
    ? `${state.pendingHosts.length} ${state.pendingHosts.length === 1 ? "equipo" : "equipos"}`
    : `${filtrados.length} de ${state.pendingHosts.length} equipos`;

  tbody.innerHTML = filtrados.length
    ? filtrados.map(pendingInventoryRowHTML).join("")
    : '<tr><td colspan="5" class="empty-state">Ningún equipo del inventario sin actividad en Nagios.</td></tr>';

  tbody.querySelectorAll(".pending-estado-select").forEach((select) => {
    select.addEventListener("change", () =>
      cambiarEstado(select.dataset.rak, select.value, loadPendingInventoryHosts));
  });

  tbody.querySelectorAll(".pending-delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      // Borra una fila del CSV y no se puede deshacer: se confirma siempre.
      if (!window.confirm(
        `¿Borrar "${btn.dataset.nombre}" del inventario?

` +
        "Esta acción no se puede deshacer. Si el equipo todavía está dado de alta en Nagios, " +
        "el sistema va a rechazar el borrado.")) return;
      try {
        await deleteInventoryEntry(btn.dataset.rak);
        await Promise.all([loadPendingInventoryHosts(), loadHosts()]);
      } catch (err) {
        alert(`No se pudo borrar: ${err.message}`);
      }
    });
  });
}

// --- Vista Reemplazar ---

function populateAmbienteSelect(select, ambientes, currentValue) {
  // La opción vacía va primero a propósito: sin ella el navegador deja
  // preseleccionado el primer ambiente de la lista y un alta sin tocar el
  // campo cargaría el equipo en un ambiente que nadie eligió.
  select.innerHTML =
    '<option value="">Elegí un ambiente...</option>' +
    ambientes.map((a) => `<option value="${a}">${a}</option>`).join("") +
    `<option value="${NEW_AMBIENTE_OPTION}">+ Agregar nuevo ambiente...</option>`;

  // El refresco automático repuebla estos selects cada 30s. Si el usuario está
  // en medio de crear un ambiente nuevo hay que preservar esa opción: al
  // perderla, el campo de texto quedaba visible con el nombre escrito pero el
  // alta se enviaba con el ambiente vacío y el backend la rechazaba.
  if (currentValue === NEW_AMBIENTE_OPTION || (currentValue && ambientes.includes(currentValue))) {
    select.value = currentValue;
  }
}

function wireAmbienteSelect(selectId, newInputId) {
  const select = el(selectId);
  const newInput = el(newInputId);
  select.addEventListener("change", () => {
    const isNew = select.value === NEW_AMBIENTE_OPTION;
    newInput.style.display = isNew ? "block" : "none";
    if (isNew) newInput.focus();
  });
}

function resolveAmbienteValue(selectId, newInputId) {
  const select = el(selectId);
  if (select.value === NEW_AMBIENTE_OPTION) return el(newInputId).value.trim();
  return select.value;
}

function populateReplaceTabSelects(hosts) {
  // Solo equipos con fila propia en el inventario: reemplazar uno sin match no
  // tendría qué actualizar.
  fillHostDatalist("replaceHostOptions", hosts.filter((h) => h.match_type !== "no_match"));

  const ambientes = [...new Set(hosts.map((h) => h.ambiente).filter(Boolean))].sort();
  populateAmbienteSelect(el("replaceAmbiente"), ambientes, el("replaceAmbiente").value);
  populateAmbienteSelect(el("nagiosAddAmbiente"), ambientes, el("nagiosAddAmbiente").value);
}

function handleReplaceHostSelectChange() {
  const form = el("replaceForm");
  const texto = el("replaceHostSearch").value;
  const aviso = el("replaceSearchHint");

  if (!texto.trim()) {
    state.replaceHost = null;
    form.style.display = "none";
    aviso.textContent = "";
    return;
  }

  const host = resolveHost(texto, state.hosts.filter((h) => h.match_type !== "no_match"));
  if (!host) {
    state.replaceHost = null;
    form.style.display = "none";
    aviso.textContent = "Ningún equipo coincide con esa búsqueda. Probá con el nombre, el RAK o el host de Nagios.";
    return;
  }

  state.replaceHost = host.host_name;
  aviso.textContent = "";
  el("replaceNombrePc").value = host.nombre_pc || "";
  el("replaceRak").value = host.rak || "";
  renderRakHint("replaceRak", "replaceRakHint");
  el("replaceAnydeskId").value = host.anydesk_id || "";
  el("replaceCrdId").value = host.crd_host_id || "";
  el("replaceAmbienteNew").style.display = "none";
  el("replaceAmbienteNew").value = "";
  const select = el("replaceAmbiente");
  select.value = host.ambiente && [...select.options].some((o) => o.value === host.ambiente) ? host.ambiente : "";
  form.style.display = "block";
}

async function handleSaveReplace() {
  const hostName = state.replaceHost;
  if (!hostName) return;
  const nombrePc = el("replaceNombrePc").value.trim();
  const rak = el("replaceRak").value.trim();
  const anydeskId = el("replaceAnydeskId").value.trim();
  const crdId = el("replaceCrdId").value.trim();
  const ambiente = resolveAmbienteValue("replaceAmbiente", "replaceAmbienteNew");
  const motivo = el("replaceMotivo").value;
  if (!nombrePc || !rak) {
    alert("Nombre PC y RAK son obligatorios.");
    return;
  }
  if (!motivo) {
    alert("Elegí el motivo del reemplazo.");
    el("replaceMotivo").focus();
    return;
  }
  if (!confirm(`Vas a reemplazar el RAK de "${nombrePc}". ¿Confirmás?`)) return;
  try {
    await replaceHost(hostName, { nombre_pc: nombrePc, rak, anydesk_id: anydeskId,
                                  crd_host_id: crdId, cliente: ambiente || undefined, motivo });
    alert("Reemplazo guardado.");
    el("replaceForm").style.display = "none";
    el("replaceHostSearch").value = "";
    el("replaceMotivo").value = "";
    state.replaceHost = null;
    await loadReplacements();
    await loadHosts();
  } catch (err) {
    alert(`No se pudo guardar el reemplazo: ${err.message}`);
  }
}


// --- Cambiar la propia contraseña ---

function openPasswordModal() {
  el("passwordModalUser").textContent = `Usuario: ${state.username || "—"}`;
  ["currentPassword", "newPassword", "repeatPassword"].forEach((id) => { el(id).value = ""; });
  el("passwordError").textContent = "";
  el("passwordModal").style.display = "block";
  el("passwordModalBackdrop").style.display = "block";
  el("currentPassword").focus();
}

function closePasswordModal() {
  el("passwordModal").style.display = "none";
  el("passwordModalBackdrop").style.display = "none";
}

async function handleSavePassword() {
  const actual = el("currentPassword").value;
  const nueva = el("newPassword").value;
  const repetida = el("repeatPassword").value;
  const error = el("passwordError");
  error.textContent = "";

  if (!actual || !nueva) {
    error.textContent = "Completá la contraseña actual y la nueva.";
    return;
  }
  if (nueva.length < 8) {
    error.textContent = "La contraseña nueva tiene que tener al menos 8 caracteres.";
    return;
  }
  if (nueva !== repetida) {
    error.textContent = "La contraseña nueva y su repetición no coinciden.";
    return;
  }

  const boton = el("savePasswordBtn");
  boton.disabled = true;
  try {
    await changePassword(actual, nueva);
    closePasswordModal();
    alert("Contraseña cambiada. Las sesiones abiertas en otros dispositivos se cerraron.");
  } catch (err) {
    error.textContent = err.message;
  } finally {
    boton.disabled = false;
  }
}

// --- Panel de detalle del equipo ---
// Abre al instante con lo que ya está cargado en state.hosts, y completa
// aparte el histórico y la fecha de alta en Nagios (esta última necesita una
// consulta SSH al servidor, así que tarda unos segundos).

function factsHTML(pairs) {
  const rows = pairs
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([label, value, mono]) =>
      `<dt>${label}</dt><dd${mono ? ' class="mono"' : ""}>${value}</dd>`)
    .join("");
  return `<dl class="drawer-facts">${rows}</dl>`;
}

function drawerServicesHTML(host) {
  const services = host.services || [];
  if (!services.length) return '<p class="drawer-empty">Este equipo no reporta servicios.</p>';
  const rank = { CRITICAL: 0, WARNING: 1, UNKNOWN: 2, OK: 3 };
  return [...services]
    .sort((a, b) => (rank[a.current_state] ?? 9) - (rank[b.current_state] ?? 9))
    .map((s) => `
      <div class="drawer-service">
        <span class="state-pill state-${s.current_state.toLowerCase()}">${serviceStateLabel(s.current_state)}</span>
        <span class="svc-name">${escapeAttr(s.service_description)}</span>
        <span class="svc-output">${escapeAttr(s.plugin_output || "sin detalle")}</span>
      </div>`).join("");
}

function drawerSimHTML(host) {
  if (!host.sim && !host.empresa) {
    return '<p class="drawer-empty">Este equipo no reporta operador ni tiene SIM cargada.</p>';
  }
  return factsHTML([
    ["Número", host.sim ? `<span class="host-name">${escapeAttr(host.sim)}</span>` : null],
    ["Operador", host.empresa ? empresaChipHTML(host.empresa).trim() : null],
  ]) + (host.sim ? "" : '<p class="drawer-empty" style="margin-top:8px">El operador lo reporta Nagios; el número de SIM se carga a mano.</p>');
}

function drawerReplacementsHTML(reemplazos) {
  if (!reemplazos.length) {
    return '<p class="drawer-empty">Este equipo nunca fue reemplazado.</p>';
  }
  return reemplazos.map((r) => `
    <div class="replacement-row">
      <span class="replacement-date">${formatEpoch(r.replaced_at)}</span>
      <span class="replacement-raks">
        <span class="replacement-old">${escapeAttr(r.old_rak)}</span>
        <span class="replacement-arrow">→</span>
        <span class="replacement-new">${escapeAttr(r.new_rak)}</span>
      </span>
      ${motivoChipHTML(r)}
    </div>`).join("");
}

function openHostDrawer(host) {
  state.drawerHost = host.host_name;
  el("drawerTitle").textContent = host.nombre_pc || "Sin identificar";
  el("drawerSubtitle").textContent = `${host.host_name}${host.ambiente ? ` · ${host.ambiente}` : ""}`;

  const tags = (host.tags || []).map((t) => tagChipHTML(t, host.host_name, false)).join(" ");
  const anydesk = host.anydesk_id
    ? `<a class="action-btn" href="${ANYDESK_URI_SCHEME}:${host.anydesk_id}">AnyDesk</a>` : "";
  const crd = host.crd_host_id
    ? `<a class="action-btn" href="https://remotedesktop.google.com/access/session/${host.crd_host_id}" target="_blank" rel="noopener">Escritorio Remoto</a>` : "";

  el("drawerBody").innerHTML = `
    <div class="drawer-section">
      <div class="drawer-headline">
        <span class="state-pill state-${hostStateClass(host.current_state)}">${hostStateLabel(host.current_state)}</span>
        <span class="pattern-tag pattern-${host.pattern}">${patternLabels[host.pattern] || host.pattern}</span>
      </div>
      ${factsHTML([
        ["Tiempo caído", host.current_state === "UP" ? "—" : escapeAttr(host.downtime_human)],
        ["Última revisión", formatEpoch(host.last_check)],
        ["Último poll", formatEpoch(host.polled_at)],
        ["Nagios", escapeAttr(host.plugin_output || "—")],
      ])}
    </div>

    <div class="drawer-section">
      <h3>Servicios</h3>
      ${drawerServicesHTML(host)}
    </div>

    <div class="drawer-section">
      <h3>Datos del equipo</h3>
      ${factsHTML([
        ["Ambiente", host.ambiente ? ambienteChipHTML(host.ambiente) : null],
        ["RAK", host.rak ? escapeAttr(host.rak) : null, true],
        ["Host en Nagios", escapeAttr(host.host_name), true],
        ["Cruce", matchTypeLabel(host.match_type)],
        ["Alta en Nagios", '<span id="drawerCfgDate">consultando el servidor...</span>'],
        ["Visto por el panel", '<span id="drawerFirstSeen">—</span>'],
        ["Notas", host.notas ? escapeAttr(host.notas) : null],
        ["Tags", tags || null],
      ])}
      ${anydesk || crd ? `<div class="drawer-actions">${anydesk}${crd}</div>` : ""}
    </div>

    <div class="drawer-section">
      <h3>SIM</h3>
      ${drawerSimHTML(host)}
    </div>

    <div class="drawer-section">
      <h3>Equipos anteriores en este sitio</h3>
      <div id="drawerReplacements"><p class="drawer-empty">Consultando...</p></div>
    </div>

    <div class="drawer-section">
      <h3>Histórico (últimos 7 días)</h3>
      <div id="drawerHistory"><p class="drawer-empty">Cargando historial...</p></div>
    </div>`;

  el("hostDrawer").hidden = false;
  el("hostDrawerBackdrop").hidden = false;
  el("drawerCloseBtn").focus();

  loadDrawerHistory(host.host_name);
  loadDrawerNagiosInfo(host.host_name);
  loadDrawerReplacements(host.host_name);
}

async function loadDrawerReplacements(hostName) {
  try {
    const reemplazos = await fetchHostReplacements(hostName);
    if (state.drawerHost !== hostName) return;
    el("drawerReplacements").innerHTML = drawerReplacementsHTML(reemplazos);
  } catch (err) {
    if (state.drawerHost !== hostName) return;
    el("drawerReplacements").innerHTML =
      `<p class="drawer-empty">No se pudo cargar: ${escapeAttr(err.message)}</p>`;
  }
}

async function loadDrawerHistory(hostName) {
  try {
    const history = await fetchHostHistory(hostName, 7);
    if (state.drawerHost !== hostName) return;  // se cerró o cambió de equipo
    el("drawerHistory").innerHTML = history.length
      ? renderTimeline(history)
      : '<p class="drawer-empty">Sin historial registrado en este período.</p>';
  } catch (err) {
    if (state.drawerHost !== hostName) return;
    el("drawerHistory").innerHTML = `<p class="drawer-empty">No se pudo cargar el historial: ${escapeAttr(err.message)}</p>`;
  }
}

async function loadDrawerNagiosInfo(hostName) {
  try {
    const info = await fetchHostNagiosInfo(hostName);
    if (state.drawerHost !== hostName) return;
    const cfg = el("drawerCfgDate");
    const seen = el("drawerFirstSeen");
    if (!cfg || !seen) return;
    if (info.ssh_error) {
      cfg.textContent = "no se pudo consultar el servidor";
    } else if (info.cfg_exists) {
      cfg.textContent = formatEpoch(info.cfg_modified_at);
    } else {
      cfg.textContent = "no tiene archivo .cfg en Nagios";
    }
    seen.textContent = info.first_seen_at ? formatEpoch(info.first_seen_at) : "—";
  } catch {
    if (state.drawerHost !== hostName) return;
    const cfg = el("drawerCfgDate");
    if (cfg) cfg.textContent = "no disponible";
  }
}

function closeHostDrawer() {
  const drawer = el("hostDrawer");
  if (drawer.hidden) return;
  state.drawerHost = null;
  drawer.classList.add("closing");
  const finish = () => {
    drawer.classList.remove("closing");
    drawer.hidden = true;
    el("hostDrawerBackdrop").hidden = true;
  };
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) finish();
  else setTimeout(finish, 180);
}

// --- Nagios Uploader (alta/baja real contra el servidor Nagios de producción) ---

let nagiosAddPlan = null;
let nagiosRemovePlan = null;

async function handleNagiosAddPreview() {
  const rak = el("nagiosAddRak").value.trim();
  if (!rak) { alert("Ingresá un RAK."); return; }
  try {
    const plan = await prepareNagiosAdd(rak);
    nagiosAddPlan = plan;
    el("nagiosAddPreviewLabel").textContent = `Se va a crear: ${plan.filename} (host_name: ${plan.host_name})`;
    el("nagiosAddPreviewContent").textContent = plan.content;
    el("nagiosAddPreview").style.display = "block";
    el("nagiosAddResult").innerHTML = "";
  } catch (err) {
    alert(`No se pudo generar el preview: ${err.message}`);
  }
}

async function handleNagiosAddConfirm() {
  if (!nagiosAddPlan) return;
  const ok = confirm(
    `Esto va a escribir ${nagiosAddPlan.filename} en el servidor Nagios de producción, validar la config y recargar el servicio. ¿Confirmás?`
  );
  if (!ok) return;
  try {
    const result = await registerHost({
      rak: el("nagiosAddRak").value.trim(),
      nombre_pc: el("nagiosAddNombrePc").value.trim(),
      cliente: resolveAmbienteValue("nagiosAddAmbiente", "nagiosAddAmbienteNew"),
      anydesk_id: el("nagiosAddAnydeskId").value.trim(),
      crd_host_id: el("nagiosAddCrdId").value.trim(),
    });
    const inventoryNote = result.inventory_row_created
      ? " Se cargó también en el inventario."
      : " El RAK ya estaba en el inventario, no se duplicó la fila.";
    el("nagiosAddResult").innerHTML =
      `<div class="nagios-uploader-result ok">Alta aplicada: ${escapeAttr(result.host_name)}. Nagios recargado.${inventoryNote}</div>`;
    el("nagiosAddPreview").style.display = "none";
    nagiosAddPlan = null;
    ["nagiosAddNombrePc", "nagiosAddAnydeskId", "nagiosAddCrdId", "nagiosAddAmbienteNew"].forEach((id) => { el(id).value = ""; });
    el("nagiosAddAmbienteNew").style.display = "none";
    await loadHosts();
  } catch (err) {
    el("nagiosAddResult").innerHTML = `<div class="nagios-uploader-result error">${escapeAttr(err.message)}</div>`;
  }
}

async function handleNagiosRemovePreview() {
  const hostName = el("nagiosRemoveHostName").value.trim();
  if (!hostName) { alert("Ingresá el host de Nagios."); return; }
  try {
    const plan = await prepareNagiosRemove(hostName);
    nagiosRemovePlan = plan;
    if (!plan.existed) {
      el("nagiosRemovePreviewLabel").textContent = `'${plan.host_name}' no tiene un .cfg en el servidor — no hay nada para borrar.`;
      el("nagiosRemovePreviewContent").textContent = "";
      el("nagiosRemoveConfirmBtn").style.display = "none";
    } else {
      el("nagiosRemovePreviewLabel").textContent = `Se va a borrar: ${plan.filename}. Contenido actual (respaldo):`;
      el("nagiosRemovePreviewContent").textContent = plan.current_content;
      el("nagiosRemoveConfirmBtn").style.display = "";
    }
    el("nagiosRemovePreview").style.display = "block";
    el("nagiosRemoveResult").innerHTML = "";
  } catch (err) {
    alert(`No se pudo generar el preview: ${err.message}`);
  }
}

async function handleNagiosRemoveConfirm() {
  if (!nagiosRemovePlan || !nagiosRemovePlan.existed) return;
  const ok = confirm(
    `Esto va a borrar ${nagiosRemovePlan.filename} del servidor Nagios de producción, validar la config y recargar el servicio. ¿Confirmás?`
  );
  if (!ok) return;
  try {
    const result = await executeNagiosRemove(el("nagiosRemoveHostName").value.trim());
    el("nagiosRemoveResult").innerHTML =
      `<div class="nagios-uploader-result ok">Baja aplicada: ${escapeAttr(result.host_name)}. Nagios recargado.</div>`;
    el("nagiosRemovePreview").style.display = "none";
    nagiosRemovePlan = null;
  } catch (err) {
    el("nagiosRemoveResult").innerHTML = `<div class="nagios-uploader-result error">${escapeAttr(err.message)}</div>`;
  }
}

// --- Wiring general ---

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${tabName}`));
  if (tabName === "insights") loadInsights();
  if (tabName === "alarmas") loadAlarmas();
  if (tabName === "rma-testing") { renderRmaTestingTab(); loadPendingInventoryHosts(); }
}

function wireEvents() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  el("refreshBtn").addEventListener("click", async () => {
    const btn = el("refreshBtn");
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "⟳ Consultando Nagios...";
    try {
      await triggerPoll();
    } catch (err) {
      alert(`No se pudo actualizar: ${err.message}`);
    } finally {
      await Promise.all([refreshMeta(), loadHosts()]);
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  el("downloadReportBtn").addEventListener("click", () => {
    window.location.href = "/api/reports/html";
  });

  el("searchInput").addEventListener("input", (e) => {
    state.filters.search = e.target.value;
    renderHostsTable();
  });
  el("filterAmbiente").addEventListener("change", (e) => {
    state.filters.ambiente = e.target.value;
    renderHostsTable();
  });
  document.querySelectorAll("#filterEstadoGroup .state-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filters.estado = btn.dataset.value;
      document.querySelectorAll("#filterEstadoGroup .state-filter-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderHostsTable();
    });
  });
  el("filterPatron").addEventListener("change", (e) => {
    state.filters.patron = e.target.value;
    renderHostsTable();
  });

  document.querySelectorAll("#hostsTable th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) {
        state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
      } else {
        state.sort = { key, dir: "asc" };
      }
      renderHostsTable();
    });
  });

  el("historyHostSearch").addEventListener("change", loadHistory);
  el("historyRangeSelect").addEventListener("change", loadHistory);

  el("toggleTagFiltersBtn").addEventListener("click", () => {
    const row = el("tagFiltersRow");
    const btn = el("toggleTagFiltersBtn");
    const open = row.style.display === "none";
    row.style.display = open ? "" : "none";
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "Tags ▴" : "Tags ▾";
  });

  el("selectAllCheckbox").addEventListener("change", (e) => {
    const visibleHosts = sortHosts(applyFilters(state.hosts));
    if (e.target.checked) visibleHosts.forEach((h) => state.selectedHosts.add(h.host_name));
    else visibleHosts.forEach((h) => state.selectedHosts.delete(h.host_name));
    renderHostsTable();
  });

  el("clearSelectionBtn").addEventListener("click", () => {
    state.selectedHosts.clear();
    renderHostsTable();
  });

  el("manageTagsBtn").addEventListener("click", openTagModal);
  el("closeTagModalBtn").addEventListener("click", closeTagModal);
  el("tagModalBackdrop").addEventListener("click", closeTagModal);
  el("createTagBtn").addEventListener("click", handleCreateTag);

  el("closeEditModalBtn").addEventListener("click", closeEditModal);
  el("editModalBackdrop").addEventListener("click", closeEditModal);
  el("saveEditBtn").addEventListener("click", handleSaveEdit);

  el("closeServicesModalBtn").addEventListener("click", closeServicesModal);
  el("servicesModalBackdrop").addEventListener("click", closeServicesModal);

  el("changePasswordBtn").addEventListener("click", openPasswordModal);
  el("closePasswordModalBtn").addEventListener("click", closePasswordModal);
  el("passwordModalBackdrop").addEventListener("click", closePasswordModal);
  el("savePasswordBtn").addEventListener("click", handleSavePassword);

  el("logoutBtn").addEventListener("click", async () => {
    await logout().catch(() => {});
    window.location.href = "/login";
  });

  el("drawerCloseBtn").addEventListener("click", closeHostDrawer);
  el("hostDrawerBackdrop").addEventListener("click", closeHostDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeHostDrawer();
  });

  el("replaceHostSearch").addEventListener("change", handleReplaceHostSelectChange);
  el("saveReplaceBtn").addEventListener("click", handleSaveReplace);
  wireAmbienteSelect("replaceAmbiente", "replaceAmbienteNew");
  wireAmbienteSelect("nagiosAddAmbiente", "nagiosAddAmbienteNew");

  el("replaceRak").addEventListener("input", () => renderRakHint("replaceRak", "replaceRakHint"));
  el("nagiosAddRak").addEventListener("input", () => renderRakHint("nagiosAddRak", "nagiosAddRakHint"));

  el("nagiosAddPreviewBtn").addEventListener("click", handleNagiosAddPreview);
  el("nagiosAddConfirmBtn").addEventListener("click", handleNagiosAddConfirm);
  el("nagiosRemovePreviewBtn").addEventListener("click", handleNagiosRemovePreview);
  el("nagiosRemoveConfirmBtn").addEventListener("click", handleNagiosRemoveConfirm);

  el("editTagAgregar").addEventListener("change", async (e) => {
    if (!e.target.value || !state.editingHostData) return;
    const host = state.editingHostData;
    try {
      await assignTag(Number(e.target.value), [host.host_name]);
      await loadHosts();
      const fresco = state.hosts.find((h) => h.host_name === host.host_name);
      if (fresco) { state.editingHostData = fresco; renderEditTags(fresco); }
    } catch (err) {
      alert(`No se pudo agregar el tag: ${err.message}`);
    }
  });

  el("stockSearchInput").addEventListener("input", (e) => {
    state.stockFilters.search = e.target.value;
    renderRmaTestingTab();
  });

  el("pendingSearchInput").addEventListener("input", (e) => {
    state.pendingFilters.search = e.target.value;
    renderPendingInventoryTable();
  });

  el("alarmSearchInput").addEventListener("input", (e) => {
    state.alarmFilters.search = e.target.value;
    renderAlarms();
  });
  document.querySelectorAll("#alarmSeverityGroup .state-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.alarmFilters.severity = btn.dataset.value;
      document.querySelectorAll("#alarmSeverityGroup .state-filter-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderAlarms();
    });
  });
}

async function loadMotivos() {
  try {
    state.motivos = await fetchMotivos();
  } catch {
    state.motivos = [];
  }
  // La opción vacía va primero: sin ella quedaría preseleccionado el primer
  // motivo y se guardaría una causa que nadie eligió.
  el("replaceMotivo").innerHTML =
    '<option value="">Elegí un motivo...</option>' +
    state.motivos.map((m) => `<option value="${m.valor}">${m.etiqueta}</option>`).join("");
}

async function loadEstados() {
  try {
    state.estados = await fetchEstados();
  } catch {
    // Sin esto el desplegable queda vacío, pero el resto del panel funciona igual.
    state.estados = [];
  }
}

async function init() {
  wireEvents();
  fetchMe().then((r) => {
    state.username = r.username || "";
    el("sessionUser").textContent = state.username;
  }).catch(() => {});
  await Promise.all([
    refreshMeta(), loadHosts(), loadTags(), loadInsights(), loadReplacements(), loadEstados(),
    loadMotivos(),
  ]);
  setInterval(() => {
    refreshMeta();
    loadHosts();
    if (document.getElementById("tab-insights").classList.contains("active")) loadInsights();
  }, AUTO_REFRESH_MS);
}

init();
