// Small JSON client for the Flask API (or, on the live demo site, its saved answers).

import { DEMO_SITE, demoApi, demoExportPath } from './demo-site.js';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** Where "Export CSV" downloads from. */
export function exportUrl(scope) {
  return DEMO_SITE ? demoExportPath(scope) : `/api/export.csv?scope=${scope}`;
}

export async function api(path, { method = 'GET', body, params } = {}) {
  if (DEMO_SITE) return demoApi(path, { method, params: params || {}, body });
  let url = `/api${path}`;
  if (params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') query.set(key, value);
    }
    const qs = query.toString();
    if (qs) url += `?${qs}`;
  }
  const options = { method, headers: { Accept: 'application/json' } };
  if (method !== 'GET') options.headers['X-Requested-With'] = 'cs2-tracker'; // CSRF guard expected by the server
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ApiError('Cannot reach the local server. Is app.py still running?', 0);
  }
  let data = null;
  try {
    data = await response.json();
  } catch { /* non-JSON response */ }
  if (!response.ok) {
    throw new ApiError((data && data.error) || `Request failed (HTTP ${response.status})`, response.status);
  }
  return data;
}
