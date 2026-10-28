// Placeholder HMS screen. Talks only to the mock-HMS endpoints; the mock sends updates back to BATS
// server-to-server, so this page never needs a BATS login or the HMS API key.
import { API_BASE } from "./api.js";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const POLL_MS = 4000;

let events = [];
let online = true;
let busy = false;
let returning = null; // event being returned, while the dialog is open

async function call(path, method = "GET", body) {
  let resp;
  try {
    resp = await fetch(API_BASE + path, {
      method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the server.");
  }
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw new Error((data && typeof data.detail === "string" && data.detail) || `Request failed (${resp.status})`);
  return data;
}

function showError(message) {
  const el = $("error");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

const STATE_CHIP = {
  Received: "bg-amber-50 text-amber-700 border border-amber-200",
  Transfused: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  Returned: "bg-slate-100 text-slate-600 border border-slate-200",
};

function render() {
  for (const s of ["Received", "Transfused", "Returned"]) {
    $(`n-${s.toLowerCase()}`).textContent = events.filter((e) => e.state === s).length;
  }
  $("events").innerHTML = events.map((e) => `
    <tr class="hover:bg-sky-50/60">
      <td class="p-3 font-mono text-slate-700">${esc(e.hms_reference)}</td>
      <td class="p-3"><span class="font-semibold">UNIT-${esc(e.unit_id)}</span>
        <span class="ml-1 px-1.5 py-0.5 rounded bg-red-50 text-red-700 font-bold">${esc(e.blood_type)}</span></td>
      <td class="p-3 text-slate-600">REQ-${esc(e.requisition_id)}</td>
      <td class="p-3 text-slate-600">${esc(e.issued_by_staff_id)} / ${esc(e.verified_by_staff_id)}</td>
      <td class="p-3 text-slate-500">${esc(new Date(e.received_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</td>
      <td class="p-3"><span class="px-2.5 py-1 rounded-full text-[10px] font-semibold ${STATE_CHIP[e.state] ?? ""}">${esc(e.state)}</span></td>
      <td class="p-3 text-right whitespace-nowrap">${e.state === "Received" ? `
        <button data-action="transfuse" data-id="${e.event_id}" ${online ? "" : "disabled"} class="px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white font-medium">Transfused</button>
        <button data-action="return" data-id="${e.event_id}" ${online ? "" : "disabled"} class="ml-1 px-3 py-1 rounded-lg bg-white border border-slate-300 hover:bg-slate-50 disabled:opacity-40 text-slate-700 font-medium">Return</button>`
        : '<span class="text-slate-300">Recorded</span>'}</td>
    </tr>`).join("") || '<tr><td colspan="7" class="p-8 text-center text-slate-400">Nothing received yet. Issue a unit in BATS and it will appear here.</td></tr>';

  const badge = $("conn-badge");
  badge.textContent = online ? "Connected to BATS" : "HMS offline";
  badge.className = `px-2.5 py-1 rounded-full text-xs font-semibold ${online ? "bg-emerald-400/20 text-emerald-200" : "bg-red-400/25 text-red-100"}`;
}

async function refresh() {
  try {
    [events, { online }] = await Promise.all([call("/mock-hms/events"), call("/mock-hms/status")]);
    showError("");
  } catch (err) { showError(err.message); }
  render();
}

async function record(id, path, body) {
  if (busy) return;
  busy = true;
  try {
    await call(`/mock-hms/events/${id}/${path}`, "POST", body);
    showError("");
  } catch (err) { showError(err.message); }
  busy = false;
  await refresh();
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn || btn.disabled) return;
  const id = Number(btn.dataset.id);
  if (btn.dataset.action === "transfuse") record(id, "transfused");
  if (btn.dataset.action === "return") {
    returning = events.find((x) => x.event_id === id);
    $("return-unit").textContent = `UNIT-${returning.unit_id} (${returning.blood_type}), ${returning.hms_reference}`;
    $("cold-chain").checked = true;
    $("return-dialog").showModal();
  }
});

$("return-cancel").addEventListener("click", () => $("return-dialog").close());
$("return-form").addEventListener("submit", () => {
  if (returning) record(returning.event_id, "returned", { cold_chain_ok: $("cold-chain").checked });
  returning = null;
});

$("toggle-conn").addEventListener("click", async () => {
  try { await call("/mock-hms/toggle", "POST"); } catch (err) { showError(err.message); }
  refresh();
});

refresh();
setInterval(refresh, POLL_MS);
