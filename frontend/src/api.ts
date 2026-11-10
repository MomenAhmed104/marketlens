// Typed client for the backend API. Types mirror backend Pydantic schemas.

export interface Health {
  status: string
  products: number
  competitors: number
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path} returned HTTP ${res.status}`)
  return (await res.json()) as T
}

export const fetchHealth = () => getJson<Health>('/api/health')
