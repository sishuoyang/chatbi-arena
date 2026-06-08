// Base URL of the FastAPI dashboard JSON API (dashboard/app.py).
// Override at build/run time with VITE_API_BASE, e.g. VITE_API_BASE=http://host:8000.
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export async function api(path) {
  const r = await fetch(API_BASE + path)
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}
