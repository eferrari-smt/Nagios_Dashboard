const BASE = "";

async function getJSON(path) {
  const response = await fetch(`${BASE}${path}`);
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Sesión vencida.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Error ${response.status} en ${path}`);
  }
  return response.json();
}

async function sendJSON(path, method, payload) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload !== undefined ? JSON.stringify(payload) : undefined,
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Sesión vencida.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Error ${response.status} en ${path}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export function fetchMe() {
  return getJSON("/api/me");
}

export function changePassword(currentPassword, newPassword) {
  return sendJSON("/api/change-password", "POST", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export function logout() {
  return sendJSON("/api/logout", "POST");
}

export function fetchMeta() {
  return getJSON("/api/meta");
}

export function fetchInsights() {
  return getJSON("/api/insights");
}

export function triggerPoll() {
  return sendJSON("/api/poll", "POST");
}

export function editHost(hostName, { nombre_pc, notas, sim, empresa, ambiente, estado } = {}) {
  const payload = {};
  if (nombre_pc !== undefined) payload.nombre_pc = nombre_pc;
  if (notas !== undefined) payload.notas = notas;
  if (sim !== undefined) payload.sim = sim;
  if (estado !== undefined) payload.estado = estado;
  if (empresa !== undefined) payload.empresa = empresa;
  if (ambiente !== undefined) payload.ambiente = ambiente;
  return sendJSON(`/api/hosts/${encodeURIComponent(hostName)}`, "PATCH", payload);
}

export function replaceHost(hostName, { nombre_pc, rak, anydesk_id, crd_host_id, cliente }) {
  return sendJSON(`/api/hosts/${encodeURIComponent(hostName)}/replace`, "POST", {
    nombre_pc, rak, anydesk_id, crd_host_id: crd_host_id || null, cliente: cliente || null,
  });
}

export function fetchHosts() {
  return getJSON("/api/hosts");
}

export function fetchHostHistory(hostName, days = 7) {
  return getJSON(`/api/hosts/${encodeURIComponent(hostName)}/history?days=${days}`);
}

export function fetchHostNagiosInfo(hostName) {
  return getJSON(`/api/hosts/${encodeURIComponent(hostName)}/nagios-info`);
}

export function fetchHostReplacements(hostName) {
  return getJSON(`/api/hosts/${encodeURIComponent(hostName)}/replacements`);
}

export function fetchReplacements() {
  return getJSON("/api/replacements");
}

export function fetchPendingInventoryHosts() {
  return getJSON("/api/inventory/pending");
}

export function fetchTags() {
  return getJSON("/api/tags");
}

export function createTag(name, color, category) {
  return sendJSON("/api/tags", "POST", { name, color, category: category || null });
}

export function updateTagCategory(tagId, category) {
  return sendJSON(`/api/tags/${tagId}`, "PATCH", { category: category || null });
}

export function deleteTag(tagId) {
  return sendJSON(`/api/tags/${tagId}`, "DELETE");
}

export function assignTag(tagId, hostNames) {
  return sendJSON(`/api/tags/${tagId}/assign`, "POST", { host_names: hostNames });
}

export function unassignTag(tagId, hostNames) {
  return sendJSON(`/api/tags/${tagId}/unassign`, "POST", { host_names: hostNames });
}

export function prepareNagiosAdd(rak) {
  return sendJSON("/api/nagios-uploader/prepare-add", "POST", { rak });
}

export function prepareNagiosRemove(hostName) {
  return sendJSON("/api/nagios-uploader/prepare-remove", "POST", { host_name: hostName });
}

export function executeNagiosRemove(hostName) {
  return sendJSON("/api/nagios-uploader/execute-remove", "POST", { host_name: hostName });
}

export function registerHost({ rak, nombre_pc, cliente, anydesk_id, crd_host_id, notas }) {
  return sendJSON("/api/nagios-uploader/register", "POST", {
    rak,
    nombre_pc: nombre_pc || "",
    cliente: cliente || "",
    anydesk_id: anydesk_id || "",
    crd_host_id: crd_host_id || "",
    notas: notas || "",
  });
}

// --- Alarmas e historial ---

export function fetchAlarmas() {
  return getJSON("/api/alarmas");
}

export function fetchHostEventos(hostName, limit = 100) {
  return getJSON(`/api/hosts/${encodeURIComponent(hostName)}/eventos?limit=${limit}`);
}

// --- Estado operativo del inventario ---

export function fetchEstados() {
  return getJSON("/api/estados");
}

export function setInventoryEstado(rak, estado) {
  return sendJSON(`/api/inventory/${encodeURIComponent(rak)}/estado`, "PATCH", { estado });
}

export function deleteInventoryEntry(rak) {
  return sendJSON(`/api/inventory/${encodeURIComponent(rak)}`, "DELETE");
}
