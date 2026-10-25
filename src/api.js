import { parseNdjson } from './utils.js'

const API = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message, status) { super(message); this.name = 'ApiError'; this.status = status }
}

let onUnauthorized = null
/** Register a callback that runs when the server rejects the current session (HTTP 401). */
export function setUnauthorizedHandler(handler) { onUnauthorized = handler }

async function toApiError(response) {
  const data = await response.json().catch(() => ({}))
  // FastAPI sends a list of {msg} objects for validation errors and a string otherwise.
  const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join(', ') : data.detail
  return new ApiError(detail || `Request failed (${response.status})`, response.status)
}

function headers(token, extra) {
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(extra || {}) }
}

export async function request(path, { token, ...options } = {}) {
  let response
  try {
    response = await fetch(`${API}${path}`, { ...options, headers: headers(token, options.headers) })
  } catch {
    throw new ApiError('Cannot reach the server. Check that the backend is running.', 0)
  }
  if (response.status === 204) return null
  if (!response.ok) {
    const error = await toApiError(response)
    if (response.status === 401 && token && onUnauthorized) onUnauthorized()
    throw error
  }
  return response.json().catch(() => ({}))
}

/** POST to a streaming endpoint and call onEvent for every event ({type: token|done|error}). */
export async function streamPost(path, body, { token, signal, onEvent }) {
  let response
  try {
    response = await fetch(`${API}${path}`, { method: 'POST', headers: headers(token), body: JSON.stringify(body), signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError('Cannot reach the server. Check that the backend is running.', 0)
  }
  if (!response.ok) {
    const error = await toApiError(response)
    if (response.status === 401 && onUnauthorized) onUnauthorized()
    throw error
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseNdjson(buffer)
    buffer = parsed.rest
    parsed.events.forEach(onEvent)
  }
  buffer += decoder.decode()
  parseNdjson(buffer + '\n').events.forEach(onEvent)
}
