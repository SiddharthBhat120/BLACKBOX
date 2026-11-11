async function req(path, opts = {}) {
  const res = await fetch(path, opts)
  if (!res.ok) {
    let msg = `The server answered ${res.status}.`
    try { const b = await res.json(); if (b.detail) msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail) } catch { /* empty */ }
    throw new Error(msg)
  }
  return res.headers.get('content-type')?.includes('json') ? res.json() : res.text()
}
const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => req('/api/health'),
  incidents: () => req('/api/incidents'),
  incident: (id) => req(`/api/incidents/${id}`),
  create: (title) => req('/api/incidents', json('POST', { title })),
  runDemo: (scenario, seed) => req('/api/demo/run', json('POST', { scenario, seed })),
  importBundle: (id, file) => { const f = new FormData(); f.append('file', file); return req(`/api/incidents/${id}/import`, { method: 'POST', body: f }) },
  analysis: (id) => req(`/api/incidents/${id}/analysis`),
  truth: (id) => req(`/api/incidents/${id}/ground-truth`),
  events: (id, params) => req(`/api/incidents/${id}/events?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))}`),
  annotations: (id) => req(`/api/incidents/${id}/annotations`),
  annotate: (id, body) => req(`/api/incidents/${id}/annotations`, json('POST', body)),
  postmortem: (id) => req(`/api/incidents/${id}/postmortem`),
  regenerate: (id) => req(`/api/incidents/${id}/postmortem`, { method: 'POST' }),
  remove: (id) => req(`/api/incidents/${id}`, { method: 'DELETE' }),
  mdUrl: (id) => `/api/incidents/${id}/postmortem.md`,
}

export const fmtTime = (iso) => iso ? new Date(iso).toISOString().slice(11, 19) : '—'
export const fmtMin = (m) => m == null ? '—' : m < 1 ? `${Math.round(m * 60)} s` : `${m.toFixed(1)} min`
