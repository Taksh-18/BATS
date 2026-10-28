import * as api from "./api.js";

const $ = (id) => document.getElementById(id);
const BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];

const RULE_HINTS = {
  password_incorrect: "Your password is incorrect.",
  unit_unavailable: "That unit is not available for issue.",
  unit_expired: "That unit is expired or expires today, so it can't be issued.",
  same_staff: "A second staff member must approve. You can't approve your own request.",
  verification_declined: "Tick the confirmation box to approve.",
  blood_type_mismatch: "That unit's blood group isn't compatible with the requisition.",
  requisition_closed: "That requisition is already closed.",
  wrong_hospital: "Both staff members must belong to the requesting hospital.",
  request_expired: "That request timed out. The requester must raise a new one.",
  request_not_pending: "That request was already handled.",
  already_requested: "That unit already has a pending request.",
};

const TITLES = {
  dashboard: "Dashboard Overview",
  inventory: "Live Inventory & Search",
  requisitions: "Blood Requisitions",
  issuance: "Two-Person Verification Issuance",
  audit: "Tamper-Evident Audit Logs",
};

const PAGE_SIZE = 25;
const state = { me: null, tab: "dashboard", unitPage: { items: [], total: 0 }, offset: 0, requisitions: [], issuances: [], audit: [], stats: null, hms: null, search: "", requests: [] };
const can = (permission) => Boolean(state.me?.permissions?.includes(permission));

// Everything coming from the server goes through esc() before it touches innerHTML.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const unitLabel = (u) => `UNIT-${u.unit_id}`;

function toast(message, type = "success") {
  const el = document.createElement("div");
  el.className = `p-4 rounded-xl shadow-lg border text-xs font-semibold flex items-center space-x-3 transition transform translate-y-2 opacity-0 max-w-sm ${
    type === "success" ? "bg-emerald-900 text-white border-emerald-800" : "bg-medical-900 text-white border-medical-800"}`;
  const icon = document.createElement("i");
  icon.className = `fa-solid ${type === "success" ? "fa-circle-check text-emerald-400" : "fa-triangle-exclamation text-medical-400"} text-base`;
  const text = document.createElement("span");
  text.textContent = message;
  el.append(icon, text);
  $("toast-container").appendChild(el);
  setTimeout(() => el.classList.remove("translate-y-2", "opacity-0"), 10);
  setTimeout(() => { el.classList.add("opacity-0", "translate-y-2"); setTimeout(() => el.remove(), 300); }, 4500);
}

const errText = (err) => (err instanceof api.ApiError ? (RULE_HINTS[err.rule] ?? err.message) : "Unexpected error.");

function showMsg(id, message) {
  const el = $(id);
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

// ---------- Tabs ----------
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab-pane").forEach((el) => el.classList.toggle("hidden", el.id !== `tab-${tab}`));
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    const active = btn.dataset.tab === tab;
    btn.classList.toggle("bg-medical-600", active);
    btn.classList.toggle("text-white", active);
    btn.classList.toggle("shadow-md", active);
    btn.classList.toggle("text-slate-400", !active);
    btn.classList.toggle("hover:bg-slate-800", !active);
    btn.classList.toggle("hover:text-white", !active);
  });
  $("current-title").textContent = TITLES[tab];
}

// ---------- Rendering ----------
const chip = (text, cls) => `<span class="px-2.5 py-1 rounded-full text-[10px] font-semibold ${cls}">${esc(text)}</span>`;
const groupChip = (g) => `<span class="px-2 py-0.5 rounded-md text-xs font-bold bg-medical-50 text-medical-700">${esc(g)}</span>`;
const UNIT_STATUS_CLS = {
  Available: "bg-emerald-50 text-emerald-600 border border-emerald-100",
  Issued: "bg-blue-50 text-blue-600 border border-blue-100",
  Transfused: "bg-violet-50 text-violet-600 border border-violet-100",
  Reserved: "bg-amber-50 text-amber-600 border border-amber-100",
};
const unitStatusChip = (s) => chip(s, UNIT_STATUS_CLS[s] ?? "bg-rose-50 text-rose-600 border border-rose-100");

function renderStats() {
  const s = state.stats;
  if (!s) return;
  $("stat-total-units").textContent = s.total_units;
  $("stat-available-note").textContent = `${s.available_units} available now`;
  $("stat-active-reqs").textContent = s.active_requisitions;
  $("stat-critical-note").textContent = `${s.critical_requisitions} critical`;
  $("stat-issued").textContent = s.issuances;
  $("stat-expiring").textContent = s.expiring_soon;
  show("audit-banner", s.audit_chain_valid === false);
  const pending = $("hms-pending");
  pending.textContent = `${s.hms_pending} event${s.hms_pending === 1 ? "" : "s"} queued, retrying automatically`;
  pending.classList.toggle("hidden", s.hms_pending === 0);

  $("blood-matrix-grid").innerHTML = BLOOD_TYPES.map((g) => {
    const n = s.available_by_type[g] ?? 0;
    return `<div class="p-3.5 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between">
      <div><span class="text-xs font-semibold text-slate-400 uppercase">Group</span><h4 class="text-lg font-bold text-slate-800">${esc(g)}</h4></div>
      <div class="text-right"><span class="text-xl font-bold ${n > 0 ? "text-medical-600" : "text-slate-400"}">${n}</span><p class="text-[10px] text-slate-400">units</p></div>
    </div>`;
  }).join("");
}

function renderDashboardAudit() {
  $("dashboard-audit-list").innerHTML = state.audit.slice(0, 5).map((l) => `
    <div class="p-2.5 rounded-xl bg-slate-50 border border-slate-100 text-xs">
      <div class="flex items-center justify-between"><span class="font-bold text-slate-700">${esc(l.action)}</span>
        <span class="text-[10px] text-slate-400 font-mono">${esc(new Date(l.created_at).toLocaleTimeString([], { timeStyle: "short" }))}</span></div>
      <p class="text-[11px] text-slate-500 mt-0.5">${esc(l.details)}</p>
    </div>`).join("") || '<p class="text-xs text-slate-400">No activity yet.</p>';
}

function renderInventory() {
  const { items, total } = state.unitPage;
  $("inventory-table-body").innerHTML = items.map((u) => `
    <tr class="hover:bg-slate-50/80 transition">
      <td class="p-3"><div class="font-mono font-medium text-slate-700">${esc(unitLabel(u))}</div><div class="text-[10px] text-slate-400">${esc(u.batch_number)}</div></td>
      <td class="p-3">${groupChip(u.blood_type)}</td>
      <td class="p-3 text-slate-600">${esc(u.component)}</td>
      <td class="p-3 text-slate-500">${esc(u.collection_date)}</td>
      <td class="p-3 text-slate-500">${esc(u.expiry_date)}</td>
      <td class="p-3">${unitStatusChip(u.status)}</td>
      <td class="p-3 text-right">${u.status === "Available" && can("issuance:handle")
        ? `<button data-action="quick-issue" data-unit="${u.unit_id}" class="px-3 py-1 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-medium transition">Issue</button>`
        : '<span class="text-slate-400 text-xs italic">Locked</span>'}</td>
    </tr>`).join("") || '<tr><td colspan="7" class="p-6 text-center text-slate-400">No units match.</td></tr>';
  const from = total ? state.offset + 1 : 0;
  $("inventory-range").textContent = `Showing ${from}-${state.offset + items.length} of ${total}`;
  $("page-prev").disabled = state.offset === 0;
  $("page-next").disabled = state.offset + PAGE_SIZE >= total;
}

const unitQuery = () => ({
  blood_type: $("filter-group").value, component: $("filter-component").value,
  status: $("filter-status").value, q: state.search.trim(), limit: PAGE_SIZE, offset: state.offset,
});

async function loadUnits() {
  try {
    state.unitPage = await api.getUnits(unitQuery());
    renderInventory();
  } catch (err) { toast(errText(err), "error"); }
}

const URGENCY_CLS = { Critical: "bg-medical-100 text-medical-700", Urgent: "bg-amber-100 text-amber-700", Routine: "bg-slate-100 text-slate-600" };
const REQ_STATUS_CLS = { Fulfilled: "bg-emerald-50 text-emerald-600 border border-emerald-100", Cancelled: "bg-slate-100 text-slate-500 border border-slate-200" };
const isOpen = (r) => r.status === "Pending" || r.status === "Partially Fulfilled";

function renderRequisitions() {
  $("requisitions-table-body").innerHTML = state.requisitions.map((r) => `
    <tr class="hover:bg-slate-50/80 transition">
      <td class="p-3 font-mono font-medium text-slate-700">REQ-${r.requisition_id}</td>
      <td class="p-3"><div class="font-medium text-slate-800">${esc(r.staff_name)}</div><div class="text-[10px] text-slate-400">${esc(fmtTime(r.requisition_date))}</div></td>
      <td class="p-3">${groupChip(r.blood_type_requested)}</td>
      <td class="p-3 font-semibold text-slate-700">${r.issued_count} / ${r.quantity}</td>
      <td class="p-3">${chip(r.urgency_level, URGENCY_CLS[r.urgency_level] ?? URGENCY_CLS.Routine)}</td>
      <td class="p-3">${chip(r.status, REQ_STATUS_CLS[r.status] ?? "bg-amber-50 text-amber-600 border border-amber-100")}</td>
      <td class="p-3 text-right">${isOpen(r) && can("issuance:handle")
        ? `<button data-action="fulfill" data-req="${r.requisition_id}" class="px-3 py-1 bg-medical-600 hover:bg-medical-700 text-white rounded-lg text-xs font-medium transition">Fulfill</button>`
        : '<span class="text-slate-400 text-xs">Closed</span>'}</td>
    </tr>`).join("") || '<tr><td colspan="7" class="p-6 text-center text-slate-400">No requisitions yet.</td></tr>';
}

let selectToken = 0;
async function renderIssueSelects(want = {}) {
  if (!can("issuance:handle")) return;
  const reqSel = $("issue-req-select"), unitSel = $("issue-unit-select");
  const open = state.requisitions.filter(isOpen);
  const prevReq = want.reqId ?? Number(reqSel.value);
  reqSel.innerHTML = open.length
    ? open.map((r) => `<option value="${r.requisition_id}">REQ-${r.requisition_id}: ${esc(r.blood_type_requested)} x${r.quantity - r.issued_count} (${esc(r.urgency_level)})</option>`).join("")
    : '<option value="">No open requisitions</option>';
  if (open.some((r) => r.requisition_id === prevReq)) reqSel.value = prevReq;

  const req = open.find((r) => r.requisition_id === Number(reqSel.value));
  const myToken = ++selectToken;  // ignore stale responses if the selection changed meanwhile
  let usable = [];
  if (req) { try { usable = await api.getIssuableUnits(req.requisition_id); } catch { /* leave the list empty */ } }
  if (myToken !== selectToken) return;

  const prevUnit = want.unitId ?? Number(unitSel.value);
  unitSel.innerHTML = usable.length
    ? usable.map((u) => `<option value="${u.unit_id}">${esc(unitLabel(u))}: ${esc(u.blood_type)} ${esc(u.component)} (exp ${esc(u.expiry_date)})${u.exact_match ? "" : " - compatible substitute"}</option>`).join("")
    : '<option value="">No compatible available units</option>';
  if (usable.some((u) => u.unit_id === prevUnit)) unitSel.value = prevUnit;
}

const minutesLeft = (iso) => Math.max(0, Math.ceil((new Date(iso) - Date.now()) / 60000));

function renderRequests() {
  if (!can("issuance:handle")) return;
  $("requests-table-body").innerHTML = state.requests.map((r) => {
    const mine = r.requested_by_staff_id === state.me.staff_id;
    return `<tr class="hover:bg-slate-50/80 transition">
      <td class="p-3"><span class="font-semibold text-slate-700">UNIT-${r.unit_id}</span> ${groupChip(r.blood_type)}<div class="text-[10px] text-slate-400">${esc(r.component)}</div></td>
      <td class="p-3 text-slate-600">REQ-${r.requisition_id}</td>
      <td class="p-3 text-slate-700">${esc(mine ? "You" : r.requested_by_name)}</td>
      <td class="p-3 text-slate-500">${minutesLeft(r.expires_at)} min</td>
      <td class="p-3 text-right whitespace-nowrap">${mine
        ? `<span class="text-slate-400 mr-2">Waiting for a colleague</span><button data-action="cancel-request" data-id="${r.request_id}" class="px-3 py-1 rounded-lg border border-slate-300 text-slate-700 font-medium hover:bg-slate-50">Cancel</button>`
        : `<button data-action="open-approve" data-id="${r.request_id}" class="px-3 py-1 rounded-lg bg-medical-600 hover:bg-medical-700 text-white font-medium">Review</button>`}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="5" class="p-6 text-center text-slate-400">Nothing waiting for verification.</td></tr>';
}

function renderIssuances() {
  $("issuances-table-body").innerHTML = state.issuances.map((i) => `
    <tr class="hover:bg-slate-50/80 transition">
      <td class="p-3 font-mono font-medium text-slate-700">ISS-${i.issue_id}</td>
      <td class="p-3 text-slate-600">UNIT-${i.unit_id} (${esc(i.blood_type)}) / REQ-${i.requisition_id}</td>
      <td class="p-3 font-medium text-slate-700">${esc(i.issuer_name)}</td>
      <td class="p-3 font-medium text-emerald-700">${esc(i.verifier_name)}</td>
      <td class="p-3 text-slate-500">${esc(fmtTime(i.issue_date))}</td>
    </tr>`).join("") || '<tr><td colspan="5" class="p-6 text-center text-slate-400">No issuances yet.</td></tr>';
}

function renderAudit() {
  $("audit-table-body").innerHTML = state.audit.map((l) => `
    <tr class="hover:bg-slate-50/80 transition">
      <td class="p-3 text-slate-400">${l.audit_id}</td>
      <td class="p-3 text-slate-500 whitespace-nowrap">${esc(fmtTime(l.created_at))}</td>
      <td class="p-3 text-slate-700 font-semibold">${esc(l.actor_username)}</td>
      <td class="p-3 text-medical-600 font-bold">${esc(l.action)}</td>
      <td class="p-3 text-slate-600">${esc(l.details)}</td>
      <td class="p-3 text-slate-400 text-[11px]" title="${esc(l.hash)}">${esc(l.hash.slice(0, 12))}…</td>
    </tr>`).join("");
}

function renderHms() {
  const h = state.hms;
  if (!h) return;
  const badge = $("hms-badge");
  badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1 ${h.online ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"}`;
  badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${h.online ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}"></span> ${h.online ? "ONLINE" : "OFFLINE"}`;
  $("hms-endpoint").textContent = h.endpoint;
}

function renderUser() {
  const me = state.me;
  $("user-initials").textContent = me.full_name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  $("user-name").textContent = me.full_name;
  $("user-meta").textContent = `${me.role} | STF-${me.staff_id}`;
}

function renderAll() {
  renderStats(); renderDashboardAudit(); renderInventory(); renderRequisitions();
  renderIssueSelects(); renderRequests(); renderIssuances(); renderAudit(); renderHms();
}

async function loadAll() {
  // Only ask for what this role may see; the server enforces it, this just avoids pointless 403s.
  const [stats, unitPage, requisitions, issuances, audit, hms, requests] = await Promise.all([
    api.getStats(), api.getUnits(unitQuery()), api.getRequisitions(), api.getIssuances(),
    can("audit:read") ? api.getAuditLogs() : [], api.getHmsStatus().catch(() => null),
    can("issuance:handle") ? api.getIssuanceRequests() : [],
  ]);
  Object.assign(state, { stats, unitPage, requisitions, issuances, audit, hms, requests });
  renderAll();
}

async function refresh({ silent = false } = {}) {
  try { await loadAll(); } catch (err) { if (state.me && !silent) toast(errText(err), "error"); }
}

// The HMS can change units behind BATS's back (transfused, returned), so poll quietly while signed in.
// Skipped while a form field has focus so a refresh never disrupts typing.
setInterval(() => {
  const typing = document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (state.me && !typing && !document.hidden) refresh({ silent: true });
}, 10000);

// ---------- Actions ----------
const show = (id, on) => $(id).classList.toggle("hidden", !on);

function applyPermissions() {
  const hide = (el, hidden) => el?.classList.toggle("hidden", hidden);
  hide(document.querySelector('[data-tab="issuance"]'), !can("issuance:handle"));
  hide(document.querySelector('[data-tab="audit"]'), !can("audit:read"));
  hide($("audit-card"), !can("audit:read"));
  hide($("dashboard-issue-banner"), !can("issuance:handle"));
  document.querySelectorAll('[data-action="open-req"]').forEach((b) => hide(b, !can("requisition:create")));
  hide($("hms-link"), !can("issuance:handle"));
}

async function enterApp(staff) {
  state.me = staff;
  show("login-screen", false);
  show("app", true);
  renderUser();
  applyPermissions();
  switchTab("dashboard");
  await refresh();
}

function leaveApp(message) {
  api.setToken(null);
  state.me = null;
  show("app", false);
  show("login-screen", true);
  $("login-password").value = "";
  showMsg("login-error", message);
}

const actions = {
  tab: (el) => switchTab(el.dataset.tab),
  "open-req": () => { showMsg("req-error", ""); show("requisition-modal", true); },
  "close-req": () => show("requisition-modal", false),
  "open-info": () => show("info-modal", true),
  "close-info": () => show("info-modal", false),
  logout: () => leaveApp(""),
  "quick-issue": (el) => {
    const unit = state.unitPage.items.find((u) => u.unit_id === Number(el.dataset.unit));
    const req = unit && state.requisitions.find((r) => isOpen(r) && r.blood_type_requested === unit.blood_type);
    switchTab("issuance");
    if (!req) { toast(`No open requisition for ${unit?.blood_type ?? "that group"}. Raise one first.`, "error"); }
    renderIssueSelects({ reqId: req?.requisition_id, unitId: unit?.unit_id });
  },
  "page-prev": () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadUnits(); },
  "page-next": () => { state.offset += PAGE_SIZE; loadUnits(); },
  "open-approve": (el) => {
    const r = state.requests.find((x) => x.request_id === Number(el.dataset.id));
    if (!r) return;
    state.approving = r;
    $("approve-summary").textContent = `UNIT-${r.unit_id} (${r.blood_type} ${r.component}) for REQ-${r.requisition_id}, requested by ${r.requested_by_name}.`;
    $("approve-password").value = "";
    $("approve-confirmed").checked = false;
    showMsg("approve-error", "");
    show("approve-modal", true);
    $("approve-password").focus();
  },
  "close-approve": () => { state.approving = null; $("approve-password").value = ""; show("approve-modal", false); },
  "cancel-request": async (el) => {
    try { await api.cancelRequest(Number(el.dataset.id)); toast("Request cancelled."); await refresh(); }
    catch (err) { toast(errText(err), "error"); await refresh({ silent: true }); }
  },
  "decline-request": async () => {
    const r = state.approving;
    if (!r) return;
    try {
      await api.declineRequest(r.request_id);
      actions["close-approve"]();
      toast("Request declined. Nothing was issued.");
      await refresh();
    } catch (err) { showMsg("approve-error", errText(err)); }
  },
  fulfill: (el) => { switchTab("issuance"); renderIssueSelects({ reqId: Number(el.dataset.req) }); },
  "verify-chain": async () => {
    try {
      const r = await api.verifyAuditChain();
      r.valid ? toast(`Audit chain intact (${r.checked} entries verified).`)
              : toast(`Audit chain BROKEN at entry #${r.first_bad_audit_id}.`, "error");
    } catch (err) { toast(errText(err), "error"); }
  },
  "export-audit": () => {
    // Prefix risky leading characters so spreadsheet apps don't execute cell content as a formula.
    const cell = (v) => { const s = String(v); return `"${(/^[=+\-@\t\r]/.test(s) ? "'" + s : s).replace(/"/g, '""')}"`; };
    const lines = ["ID,Timestamp,Actor,Action,Details,Hash"];
    for (const l of [...state.audit].reverse()) lines.push([l.audit_id, l.created_at, l.actor_username, l.action, l.details, l.hash].map(cell).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = "BATS_Audit_Log.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (el && actions[el.dataset.action]) actions[el.dataset.action](el);
});

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showMsg("login-error", "");
  try {
    const res = await api.login($("login-username").value.trim(), $("login-password").value);
    api.setToken(res.access_token);
    $("login-password").value = "";
    await enterApp(res.staff);
  } catch (err) { showMsg("login-error", errText(err)); }
});

$("new-req-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showMsg("req-error", "");
  try {
    const r = await api.createRequisition({
      blood_type_requested: $("req-blood-group").value,
      quantity: Number($("req-quantity").value),
      urgency_level: $("req-urgency").value,
    });
    show("requisition-modal", false);
    toast(`Requisition REQ-${r.requisition_id} raised.`);
    await refresh();
  } catch (err) { showMsg("req-error", errText(err)); }
});

$("issue-req-select").addEventListener("change", () => renderIssueSelects());

$("issuance-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showMsg("issue-error", "");
  const submit = $("issue-submit");
  submit.disabled = true;
  try {
    const r = await api.createIssuanceRequest({
      unit_id: Number($("issue-unit-select").value),
      requisition_id: Number($("issue-req-select").value),
    });
    toast(`Request sent for UNIT-${r.unit_id}. A colleague must approve it from their own sign-in.`);
    await refresh();
  } catch (err) {
    showMsg("issue-error", errText(err));
    if (err.rule) await refresh({ silent: true }); // a blocked attempt is audit-logged server-side
  } finally { submit.disabled = false; }
});

$("approve-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = state.approving;
  if (!r) return;
  showMsg("approve-error", "");
  const submit = $("approve-submit");
  submit.disabled = true;
  try {
    const res = await api.approveRequest(r.request_id, {
      password: $("approve-password").value, confirmed: $("approve-confirmed").checked,
    });
    actions["close-approve"]();
    toast(res.hms_synced
      ? `Unit ${res.unit_id} issued. Requisition is ${res.requisition_status}. HMS synced.`
      : `Unit ${res.unit_id} issued. The HMS is unreachable, so the update is queued and will retry automatically.`,
      res.hms_synced ? "success" : "error");
    await refresh();
  } catch (err) {
    showMsg("approve-error", errText(err));
    $("approve-password").value = "";
    if (err.rule && err.rule !== "password_incorrect") await refresh({ silent: true });
  } finally { submit.disabled = false; }
});

for (const id of ["filter-group", "filter-component", "filter-status"]) {
  $(id).addEventListener("change", () => { state.offset = 0; loadUnits(); });
}
let searchTimer;
$("global-search").addEventListener("input", (e) => {
  state.search = e.target.value;
  if (state.search && state.tab !== "inventory") switchTab("inventory");
  clearTimeout(searchTimer);  // wait for a pause in typing before hitting the server
  searchTimer = setTimeout(() => { state.offset = 0; loadUnits(); }, 250);
});

// ---------- Boot ----------
api.setSessionExpiredHandler(() => { if (state.me) leaveApp("Your session expired. Please sign in again."); });

(async () => {
  switchTab("dashboard");
  if (api.hasToken()) {
    try { await enterApp(await api.getMe()); return; } catch { api.setToken(null); }
  }
  show("login-screen", true);
})();
