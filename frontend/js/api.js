// API service layer: every call to the FastAPI backend goes through here.
export const API_BASE = window.BATS_API_BASE ?? "";  // the API serves this page, so same-origin by default

export class ApiError extends Error {
  constructor(status, rule, message) {
    super(message);
    this.status = status;
    this.rule = rule; // backend rule code, e.g. "unit_expired"; null for non-rule errors
  }
}

let token = null;
try { token = sessionStorage.getItem("bats_token"); } catch { /* storage unavailable: stay in-memory */ }

let onSessionExpired = () => {};
export const setSessionExpiredHandler = (fn) => { onSessionExpired = fn; };
export const hasToken = () => Boolean(token);

export function setToken(t) {
  token = t;
  try { t ? sessionStorage.setItem("bats_token", t) : sessionStorage.removeItem("bats_token"); } catch { /* ignore */ }
}

async function request(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let resp;
  try {
    resp = await fetch(API_BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the BATS server. Is the backend running?");
  }

  const data = await resp.json().catch(() => null);
  if (resp.ok) return data;

  const d = data && data.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) throw new ApiError(resp.status, d.rule, d.message);

  const message = Array.isArray(d) ? d.map((e) => e.msg).join("; ") : d || `Request failed (${resp.status})`;
  // A 401 with no rule code means our own session is gone (verifier failures use 403 + a rule).
  if (resp.status === 401 && path.startsWith("/api/") && path !== "/api/v1/auth/login") onSessionExpired();
  throw new ApiError(resp.status, null, message);
}

export const login = (username, password) =>
  request("/api/v1/auth/login", { method: "POST", body: { username, password } });
export const getMe = () => request("/api/v1/auth/me");
export const getHealth = () => request("/health");
export const getStats = () => request("/api/v1/stats");
export const getUnits = (params = {}) => {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  return request(`/api/v1/units?${qs}`);
};
export const getIssuableUnits = (requisitionId) => request(`/api/v1/units/issuable?requisition_id=${encodeURIComponent(requisitionId)}`);
export const getRequisitions = () => request("/api/v1/requisitions");
export const createRequisition = (body) => request("/api/v1/requisitions", { method: "POST", body });
export const getIssuances = () => request("/api/v1/issuances");
export const createIssuanceRequest = (body) => request("/api/v1/issuance-requests", { method: "POST", body });
export const getIssuanceRequests = () => request("/api/v1/issuance-requests");
export const approveRequest = (id, body) => request(`/api/v1/issuance-requests/${id}/approve`, { method: "POST", body });
export const declineRequest = (id) => request(`/api/v1/issuance-requests/${id}/decline`, { method: "POST" });
export const cancelRequest = (id) => request(`/api/v1/issuance-requests/${id}/cancel`, { method: "POST" });
export const getAuditLogs = () => request("/api/v1/audit-logs?limit=500");
export const verifyAuditChain = () => request("/api/v1/audit-logs/verify");
export const getHmsStatus = () => request("/mock-hms/status");
