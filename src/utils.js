// Pure helpers (kept free of React/DOM so they are easy to unit test).

/** Split a streamed NDJSON buffer into parsed events plus the unfinished remainder. */
export function parseNdjson(buffer) {
  const lines = buffer.split('\n')
  const rest = lines.pop() ?? ''
  const events = []
  for (const line of lines) {
    if (!line.trim()) continue
    try { events.push(JSON.parse(line)) } catch { /* ignore a malformed line */ }
  }
  return { events, rest }
}

export function safeFilename(title, fallback = 'clarity-chat') {
  const slug = String(title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60)
  return slug || fallback
}

const roleLabel = role => (role === 'assistant' ? 'Clarity' : 'You')

export function chatToMarkdown({ title, model, exportedAt, messages }) {
  const header = `# ${title}\n\n_Model: ${model} · Exported ${exportedAt}_\n\n`
  return header + messages.map(m => `## ${roleLabel(m.role)}\n\n${m.text}`).join('\n\n---\n\n') + '\n'
}

export function chatToJson({ title, model, exportedAt, messages }) {
  return JSON.stringify({
    title, model, exportedAt,
    messages: messages.map(({ role, text, time }) => ({ role, content: text, time })),
  }, null, 2)
}

export function isPending(message) {
  return String(message.id).startsWith('pending-')
}

/** Turn a markdown answer into text that sounds reasonable when read aloud. */
export function plainTextForSpeech(markdown) {
  return String(markdown || '')
    .replace(/```[\s\S]*?```/g, ' code block. ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/[*_~>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Accept either a full Clarity export or a single-chat JSON export and return the import payload. */
export function normalizeImport(data) {
  if (data && Array.isArray(data.conversations)) return { conversations: data.conversations }
  if (data && Array.isArray(data.messages)) {
    const messages = data.messages
      .map(m => ({ role: m.role, content: m.content ?? m.text ?? '' }))
      .filter(m => (m.role === 'user' || m.role === 'assistant') && String(m.content).trim())
    return { conversations: [{ title: String(data.title || 'Imported conversation').slice(0, 120), messages }] }
  }
  throw new Error('This file is not a Clarity Chat export.')
}

export const MAX_MESSAGE_CHARS = 12000
export const MAX_ATTACHMENT_BYTES = 100 * 1024

/** Pick a code fence longer than any run of backticks inside the text, so attachments cannot break out of it. */
function fenceFor(text) {
  const longest = Math.max(0, ...(String(text).match(/`+/g) || []).map(run => run.length))
  return '`'.repeat(Math.max(3, longest + 1))
}

/** Append attached text files to a message as labelled code blocks the model can read. */
export function buildMessageWithAttachments(text, attachments = []) {
  const typed = String(text || '').trim()
  if (!attachments.length) return typed
  const blocks = attachments.map(({ name, text: body }) => {
    const fence = fenceFor(body)
    return `Attached file: ${name}\n${fence}\n${body}\n${fence}`
  })
  return [typed, ...blocks].filter(Boolean).join('\n\n')
}

/** True when the file looks like readable text (not an image, archive or other binary). */
export function looksLikeText(content) {
  return !String(content).includes('\u0000')
}

export const MAX_SAVED_PROMPTS = 20

/** Read the saved-prompt list from storage text; anything malformed becomes an empty list. */
export function parseSavedPrompts(raw) {
  try {
    const data = JSON.parse(raw || '[]')
    if (!Array.isArray(data)) return []
    return data
      .filter(p => p && typeof p.id === 'string' && typeof p.text === 'string' && p.text.trim())
      .map(p => ({ id: p.id, text: p.text }))
      .slice(0, MAX_SAVED_PROMPTS)
  } catch { return [] }
}

/** Add a prompt to the front of the list, skipping blanks and exact duplicates, keeping at most MAX_SAVED_PROMPTS. */
export function addSavedPrompt(list, text, id = String(Date.now())) {
  const clean = String(text || '').trim()
  if (!clean || list.some(p => p.text === clean)) return list
  return [{ id, text: clean }, ...list].slice(0, MAX_SAVED_PROMPTS)
}

/** A short single-line label for a saved prompt. */
export function promptLabel(text, max = 48) {
  const flat = String(text).replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}
