import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Sparkles, Send, Plus, Search, MessageSquare, Settings2, PanelLeftClose, ShieldCheck, UserRound, Copy, Check, Menu, LogOut, Trash2, LoaderCircle, Sun, Moon, Wifi, WifiOff, FolderPlus, FileJson, FileText, Pencil, Square, Pin, PinOff, RefreshCw, Mic, MicOff, Volume2, Download, Upload, KeyRound, Paperclip, X, CopyPlus, Bookmark, BookmarkPlus } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import { request, streamPost, setUnauthorizedHandler } from './api.js'
import { MAX_ATTACHMENT_BYTES, MAX_MESSAGE_CHARS, addSavedPrompt, buildMessageWithAttachments, parseSavedPrompts, promptLabel, chatToJson, chatToMarkdown, isPending, looksLikeText, normalizeImport, plainTextForSpeech, safeFilename } from './utils.js'

const DEFAULT_MODEL = 'qwen2.5:3b'
const STORE = { token: 'clarity_token', theme: 'clarity_theme', instructions: 'clarity_instructions', temperature: 'clarity_temperature', prompts: 'clarity_prompts', autoTitle: 'clarity_autotitle' }
const starters = [
  { title: 'Build something', text: 'Help me plan a clean React project structure.' },
  { title: 'Understand a concept', text: 'Explain REST APIs with a simple example.' },
  { title: 'Solve a problem', text: 'Help me debug a Python or JavaScript error.' },
]
const SpeechRecognitionApi = typeof window !== 'undefined' ? (window.SpeechRecognition || window.webkitSpeechRecognition) : undefined
const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window

function readStore(key, fallback = '') {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function writeStore(key, value) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key) } catch { /* storage unavailable */ }
}
function downloadFile(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  URL.revokeObjectURL(url)
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

function CodeBlock({ children }) {
  const ref = useRef(null)
  const [done, setDone] = useState(false)
  async function copy() {
    try { await navigator.clipboard.writeText(ref.current?.innerText || ''); setDone(true); window.setTimeout(() => setDone(false), 1200) } catch { /* clipboard blocked */ }
  }
  return <div className="code-block">
    <button type="button" className="code-copy" onClick={copy}>{done ? <Check size={13}/> : <Copy size={13}/>}{done ? 'Copied' : 'Copy'}</button>
    <pre ref={ref}>{children}</pre>
  </div>
}
const markdownComponents = { pre: CodeBlock }

function AuthScreen({ dark, onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault(); setError(''); setBusy(true)
    try {
      const body = mode === 'register' ? form : { email: form.email, password: form.password }
      const data = await request(mode === 'register' ? '/api/auth/register' : '/api/auth/login', { method: 'POST', body: JSON.stringify(body) })
      onAuthenticated(data)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  return <div className={`auth-shell ${dark ? 'dark' : 'light'}`}>
    <div className="auth-card">
      <div className="brand auth-brand"><div className="brand-mark"><Sparkles size={19}/></div><span>clarity<span className="brand-dot">.</span></span></div>
      <div className="auth-intro"><div className="welcome-symbol"><Bot size={30}/></div><h1>{mode === 'login' ? 'Welcome back' : 'Create your workspace'}</h1><p>Private conversations, real AI, and your own saved chat history.</p></div>
      <form className="auth-form" onSubmit={submit}>
        {mode === 'register' && <label>Display name<input autoComplete="name" required maxLength={80} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Your name" /></label>}
        <label>Email address<input type="email" autoComplete="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} placeholder="you@example.com" /></label>
        <label>Password<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={mode === 'register' ? 8 : 1} maxLength={72} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder={mode === 'register' ? 'At least 8 characters' : 'Your password'} /></label>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <button className="modal-done" type="submit" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin"/> : null}{mode === 'login' ? 'Sign in' : 'Create account'}</button>
      </form>
      <p className="auth-switch">{mode === 'login' ? 'New to Clarity?' : 'Already have an account?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
      <div className="auth-foot"><ShieldCheck size={14}/> Passwords are hashed on the server. Use a unique password.</div>
    </div>
  </div>
}

function SettingsModal({ dark, setDark, autoTitle, setAutoTitle, instructions, setInstructions, temperature, setTemperature, apiOnline, user, token, onClose, onSignOut, onDeleteAccount, onImported }) {
  const [pwOpen, setPwOpen] = useState(false)
  const [pw, setPw] = useState({ current: '', next: '' })
  const [notice, setNotice] = useState({ kind: '', text: '' })
  const fileRef = useRef(null)

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function changePassword(e) {
    e.preventDefault(); setNotice({ kind: '', text: '' })
    try {
      await request('/api/auth/password', { token, method: 'POST', body: JSON.stringify({ current_password: pw.current, new_password: pw.next }) })
      setPw({ current: '', next: '' }); setPwOpen(false); setNotice({ kind: 'ok', text: 'Password updated.' })
    } catch (err) { setNotice({ kind: 'error', text: err.message }) }
  }

  async function exportAll() {
    setNotice({ kind: '', text: '' })
    try {
      const data = await request('/api/export', { token })
      downloadFile(`clarity-chat-export-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(data, null, 2), 'application/json')
      setNotice({ kind: 'ok', text: `Exported ${data.conversations.length} conversation(s).` })
    } catch (err) { setNotice({ kind: 'error', text: err.message }) }
  }

  async function importFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setNotice({ kind: '', text: '' })
    if (file.size > 8 * 1024 * 1024) { setNotice({ kind: 'error', text: 'That file is larger than 8 MB.' }); return }
    try {
      const payload = normalizeImport(JSON.parse(await file.text()))
      const result = await request('/api/import', { token, method: 'POST', body: JSON.stringify(payload) })
      setNotice({ kind: 'ok', text: `Imported ${result.imported} conversation(s).` })
      onImported()
    } catch (err) { setNotice({ kind: 'error', text: err instanceof SyntaxError ? 'That file is not valid JSON.' : err.message }) }
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <section className="settings-modal" role="dialog" aria-modal="true" aria-label="Preferences" onClick={e => e.stopPropagation()}>
      <div className="modal-heading"><div><span className="side-label">WORKSPACE</span><h2>Preferences</h2></div><button className="icon-btn" aria-label="Close preferences" onClick={onClose}>×</button></div>
      <p>Adjust your local workspace settings.</p>
      {notice.text && <div className={notice.kind === 'error' ? 'error-banner' : 'ok-banner'} role="status">{notice.text}</div>}
      <div className="setting-row"><div><strong>Appearance</strong><span>Switch between dark and light themes.</span></div><button className="toggle on" onClick={() => setDark(!dark)} aria-label="Toggle theme">{dark ? <Moon size={14}/> : <Sun size={14}/>}</button></div>
      <div className="setting-row"><div><strong>Auto-title new chats</strong><span>Let the model write a short title after the first answer.</span></div><button className={`toggle ${autoTitle ? 'on' : ''}`} role="switch" aria-checked={autoTitle} aria-label="Auto-title new chats" onClick={() => setAutoTitle(!autoTitle)}><span/></button></div>
      <div className="setting-block"><div><strong>Custom instructions</strong><span>Sent with every message, e.g. “Answer briefly and use TypeScript examples.” Saved in this browser.</span></div><textarea className="instructions-input" aria-label="Custom instructions" rows="4" maxLength={2000} value={instructions} onChange={e => setInstructions(e.target.value)} placeholder="Tell Clarity how you like answers to look…"/></div>
      <div className="setting-block"><div><strong>Creativity: {temperature.toFixed(1)}</strong><span>Lower is focused and predictable, higher is more varied.</span></div><input className="range-input" type="range" min="0" max="1.5" step="0.1" aria-label="Creativity (temperature)" value={temperature} onChange={e => setTemperature(Number(e.target.value))}/></div>
      <div className="setting-row"><div><strong>Your data</strong><span>Back up every conversation, or import a Clarity export.</span></div><div className="btn-group"><button className="tool-btn" title="Export all data" aria-label="Export all data" onClick={exportAll}><Download size={16}/></button><button className="tool-btn" title="Import conversations" aria-label="Import conversations" onClick={() => fileRef.current?.click()}><Upload size={16}/></button><input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={importFile}/></div></div>
      <div className="setting-row"><div><strong>AI provider</strong><span>Ollama local model · {apiOnline ? 'API reachable' : 'API not reachable'}</span></div><span className="status-chip">Local</span></div>
      <div className="setting-row"><div><strong>Signed in as</strong><span>{user.email}</span></div><div className="btn-group"><button className="tool-btn" title="Change password" aria-label="Change password" onClick={() => setPwOpen(!pwOpen)}><KeyRound size={16}/></button><button className="tool-btn" title="Sign out" aria-label="Sign out" onClick={onSignOut}><LogOut size={16}/></button></div></div>
      {pwOpen && <form className="pw-form" onSubmit={changePassword}>
        <label>Current password<input type="password" autoComplete="current-password" required maxLength={72} value={pw.current} onChange={e => setPw({ ...pw, current: e.target.value })}/></label>
        <label>New password<input type="password" autoComplete="new-password" required minLength={8} maxLength={72} value={pw.next} onChange={e => setPw({ ...pw, next: e.target.value })} placeholder="At least 8 characters"/></label>
        <button className="modal-done" type="submit">Update password</button>
      </form>}
      <div className="setting-row"><div><strong>Delete account</strong><span>Removes your account and every conversation.</span></div><button className="danger-btn" onClick={onDeleteAccount}>Delete</button></div>
      <button className="modal-done" onClick={onClose}>Done</button>
    </section>
  </div>
}

export default function App() {
  const [token, setToken] = useState(() => readStore(STORE.token))
  const [user, setUser] = useState(null)
  const [dark, setDark] = useState(() => readStore(STORE.theme, 'dark') !== 'light')
  const [instructions, setInstructions] = useState(() => readStore(STORE.instructions))
  const [temperature, setTemperature] = useState(() => { const v = Number(readStore(STORE.temperature, '0.7')); return Number.isFinite(v) ? Math.min(Math.max(v, 0), 1.5) : 0.7 })
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [conversations, setConversations] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [search, setSearch] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searchTick, setSearchTick] = useState(0)
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 680)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [apiOnline, setApiOnline] = useState(false)
  const [models, setModels] = useState([DEFAULT_MODEL])
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL)
  const [folderFilter, setFolderFilter] = useState('All folders')
  const [folderDraft, setFolderDraft] = useState('')
  const [speakingId, setSpeakingId] = useState(null)
  const [listening, setListening] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const attachRef = useRef(null)
  const [savedPrompts, setSavedPrompts] = useState(() => parseSavedPrompts(readStore(STORE.prompts)))
  const [promptsOpen, setPromptsOpen] = useState(false)
  const [autoTitle, setAutoTitle] = useState(() => readStore(STORE.autoTitle, '1') !== '0')
  const bottomRef = useRef(null)
  const composerRef = useRef(null)
  const abortRef = useRef(null)
  const recognitionRef = useRef(null)

  const resetSession = useCallback(() => {
    writeStore(STORE.token, '')
    setToken(''); setUser(null); setMessages([]); setConversations([]); setConversationId(null); setDraft(''); setAttachments([]); setEditingId(null); setSettingsOpen(false); setSearch('')
  }, [])
  const closeSettings = useCallback(() => setSettingsOpen(false), [])

  useEffect(() => { setUnauthorizedHandler(resetSession) }, [resetSession])
  useEffect(() => { writeStore(STORE.theme, dark ? 'dark' : 'light') }, [dark])
  useEffect(() => { writeStore(STORE.instructions, instructions) }, [instructions])
  useEffect(() => { writeStore(STORE.temperature, String(temperature)) }, [temperature])
  useEffect(() => { writeStore(STORE.autoTitle, autoTitle ? '1' : '0') }, [autoTitle])
  useEffect(() => { writeStore(STORE.prompts, savedPrompts.length ? JSON.stringify(savedPrompts) : '') }, [savedPrompts])
  useEffect(() => {
    if (!promptsOpen) return
    const onKey = e => { if (e.key === 'Escape') setPromptsOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [promptsOpen])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages])
  useEffect(() => { request('/api/health').then(() => setApiOnline(true)).catch(() => setApiOnline(false)) }, [])
  useEffect(() => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }, [draft])
  useEffect(() => {
    if (canSpeak) window.speechSynthesis.cancel()
    setSpeakingId(null)
  }, [conversationId])
  useEffect(() => () => { if (canSpeak) window.speechSynthesis.cancel(); recognitionRef.current?.abort?.() }, [])

  const refreshConversations = useCallback(async (activeToken = token) => {
    const rows = await request('/api/conversations', { token: activeToken })
    setConversations(rows)
    return rows
  }, [token])
  const refreshAll = useCallback(async () => { await refreshConversations(); setSearchTick(t => t + 1) }, [refreshConversations])

  const loadConversation = useCallback(async id => {
    try {
      const rows = await request(`/api/conversations/${id}/messages`, { token })
      setConversationId(id); setEditingId(null)
      setMessages(rows.map(m => ({ id: m.id, role: m.role, text: m.content, time: new Date(m.created_at).toLocaleString() })))
      setError('')
      if (window.innerWidth <= 680) setSidebarOpen(false)
      return rows
    } catch (e) { setError(e.message); return null }
  }, [token])

  useEffect(() => {
    if (!token) return
    request('/api/models', { token })
      .then(data => {
        const list = data.models?.length ? data.models : [DEFAULT_MODEL]
        setModels(list)
        setSelectedModel(current => (list.includes(current) ? current : (data.default || list[0])))
      }).catch(() => {})
  }, [token])

  useEffect(() => {
    if (!token) return
    request('/api/auth/me', { token })
      .then(async me => { setUser(me); await refreshConversations(token) })
      .catch(e => { if (e.status !== 401) setError(e.message) })
  }, [token, refreshConversations])

  // Full-text search across titles and message text (debounced).
  useEffect(() => {
    const q = search.trim()
    if (!token || !q) { setSearchResults(null); return }
    let active = true
    const timer = setTimeout(() => {
      request(`/api/search?q=${encodeURIComponent(q)}`, { token })
        .then(rows => { if (active) setSearchResults(rows) })
        .catch(() => { if (active) setSearchResults(null) })
    }, 300)
    return () => { active = false; clearTimeout(timer) }
  }, [search, token, searchTick])

  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setConversationId(null); setMessages([]); setError(''); setEditingId(null)
        composerRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  function handleAuthenticated(data) {
    writeStore(STORE.token, data.access_token)
    setUser(data.user); setToken(data.access_token); setMessages([]); setConversationId(null); setError('')
  }

  function newChat() { setConversationId(null); setMessages([]); setDraft(''); setAttachments([]); setEditingId(null); setError(''); composerRef.current?.focus() }
  const generationOptions = () => ({ model: selectedModel, system: instructions.trim() || null, temperature })

  async function syncAfterStop(text, wasNew, knownIds, baseCount, sentAttachments = []) {
    await sleep(500) // give the server a moment to save the partial answer
    try {
      const rows = await refreshConversations()
      const target = wasNew ? rows.find(c => !knownIds.has(c.id))?.id : conversationId
      if (target) {
        const loaded = await loadConversation(target)
        if (loaded && loaded.length > (wasNew ? 0 : baseCount)) return
      }
    } catch { /* fall through to restoring the draft */ }
    setMessages(prev => prev.filter(m => !isPending(m)))
    setDraft(text); setAttachments(sentAttachments)
  }

  async function sendMessage(value = draft) {
    const typed = value.trim()
    if ((!typed && attachments.length === 0) || busy) return
    const text = buildMessageWithAttachments(typed, attachments)
    if (text.length > MAX_MESSAGE_CHARS) { setError(`That message is ${text.length.toLocaleString()} characters; the limit is ${MAX_MESSAGE_CHARS.toLocaleString()}. Remove an attachment or shorten it.`); return }
    const sentAttachments = attachments
    setDraft(''); setAttachments([]); setError(''); setBusy(true)
    const stamp = Date.now()
    const botId = `pending-bot-${stamp}`
    const controller = new AbortController()
    abortRef.current = controller
    const wasNew = conversationId === null
    const knownIds = new Set(conversations.map(c => c.id))
    const baseCount = messages.length
    let createdId = null
    setMessages(prev => [...prev,
      { id: `pending-user-${stamp}`, role: 'user', text, time: 'Just now' },
      { id: botId, role: 'assistant', text: '', time: 'Just now' }])
    try {
      await streamPost('/api/chat/stream', { message: text, conversation_id: conversationId, ...generationOptions() }, {
        token, signal: controller.signal,
        onEvent: ev => {
          if (ev.type === 'token') {
            setMessages(prev => prev.map(m => (m.id === botId ? { ...m, text: m.text + ev.content } : m)))
          } else if (ev.type === 'error') {
            throw new Error(ev.detail)
          } else if (ev.type === 'done') {
            if (wasNew) createdId = ev.conversation.id
            setConversationId(ev.conversation.id)
            setMessages(prev => [
              ...prev.filter(m => !isPending(m)),
              { id: ev.user_message.id, role: 'user', text: ev.user_message.content, time: 'Just now' },
              { id: ev.assistant_message.id, role: 'assistant', text: ev.assistant_message.content, time: 'Just now' }])
          }
        },
      })
      try { await refreshAll() } catch { /* sidebar will refresh next time */ }
      if (createdId && autoTitle) generateTitle(createdId)
    } catch (e) {
      if (e.name === 'AbortError') await syncAfterStop(typed, wasNew, knownIds, baseCount, sentAttachments)
      else { setError(e.message); setMessages(prev => prev.filter(m => !isPending(m))); setDraft(typed); setAttachments(sentAttachments) }
    } finally { abortRef.current = null; setBusy(false) }
  }

  async function regenerate() {
    const last = messages[messages.length - 1]
    if (busy || !conversationId || last?.role !== 'assistant' || isPending(last)) return
    const previous = messages
    const botId = `pending-bot-${Date.now()}`
    const controller = new AbortController()
    abortRef.current = controller
    setError(''); setBusy(true)
    setMessages(prev => [...prev.slice(0, -1), { id: botId, role: 'assistant', text: '', time: 'Just now' }])
    try {
      await streamPost(`/api/conversations/${conversationId}/regenerate`, generationOptions(), {
        token, signal: controller.signal,
        onEvent: ev => {
          if (ev.type === 'token') {
            setMessages(prev => prev.map(m => (m.id === botId ? { ...m, text: m.text + ev.content } : m)))
          } else if (ev.type === 'error') {
            throw new Error(ev.detail)
          } else if (ev.type === 'done') {
            setMessages(prev => [...prev.filter(m => !isPending(m)),
              { id: ev.assistant_message.id, role: 'assistant', text: ev.assistant_message.content, time: 'Just now' }])
          }
        },
      })
      try { await refreshAll() } catch { /* ignore */ }
    } catch (e) {
      setMessages(previous) // the server keeps the old answer unless a new one completes
      if (e.name !== 'AbortError') setError(e.message)
    } finally { abortRef.current = null; setBusy(false) }
  }

  function startEdit(m) {
    if (busy || isPending(m)) return
    setEditingId(m.id); setEditText(m.text)
  }

  async function submitEdit(m) {
    const text = editText.trim()
    if (!text || busy || !conversationId || isPending(m)) return
    const index = messages.findIndex(x => x.id === m.id)
    if (index < 0) return
    const previous = messages
    const botId = `pending-bot-${Date.now()}`
    const controller = new AbortController()
    abortRef.current = controller
    setEditingId(null); setError(''); setBusy(true)
    setMessages([...messages.slice(0, index), { ...m, text }, { id: botId, role: 'assistant', text: '', time: 'Just now' }])
    try {
      await streamPost(`/api/conversations/${conversationId}/messages/${m.id}/edit`, { message: text, ...generationOptions() }, {
        token, signal: controller.signal,
        onEvent: ev => {
          if (ev.type === 'token') {
            setMessages(prev => prev.map(x => (x.id === botId ? { ...x, text: x.text + ev.content } : x)))
          } else if (ev.type === 'error') {
            throw new Error(ev.detail)
          } else if (ev.type === 'done') {
            setMessages(prev => [...prev.filter(x => !isPending(x)),
              { id: ev.assistant_message.id, role: 'assistant', text: ev.assistant_message.content, time: 'Just now' }])
          }
        },
      })
      try { await refreshAll() } catch { /* ignore */ }
    } catch (e) {
      setMessages(previous) // the server only changes the chat once a new answer completes
      if (e.name !== 'AbortError') setError(e.message)
    } finally { abortRef.current = null; setBusy(false) }
  }

  async function addAttachments(e) {
    const files = Array.from(e.target.files || [])
    e.target.value = ''
    if (!files.length) return
    setError('')
    const added = []
    const problems = []
    for (const file of files) {
      if (file.size > MAX_ATTACHMENT_BYTES) { problems.push(`${file.name} is larger than ${MAX_ATTACHMENT_BYTES / 1024} KB`); continue }
      try {
        const body = await file.text()
        if (!looksLikeText(body)) { problems.push(`${file.name} is not a text file`); continue }
        if (!body.trim()) { problems.push(`${file.name} is empty`); continue }
        added.push({ id: `${file.name}-${file.size}-${file.lastModified}`, name: file.name, text: body })
      } catch { problems.push(`${file.name} could not be read`) }
    }
    setAttachments(prev => {
      const known = new Set(prev.map(a => a.id))
      return [...prev, ...added.filter(a => !known.has(a.id))].slice(0, 5)
    })
    if (problems.length) setError(`Skipped: ${problems.join('; ')}.`)
  }

  async function generateTitle(id) {
    try {
      await request(`/api/conversations/${id}/title`, { token, method: 'POST', body: JSON.stringify({ model: selectedModel }) })
      await refreshAll()
    } catch { /* keep the automatic title */ }
  }

  async function renameFolder() {
    if (folderFilter === 'All folders') return
    const next = window.prompt('Rename folder', folderFilter)?.trim()
    if (!next || next === folderFilter) return
    try {
      await request('/api/folders', { token, method: 'PATCH', body: JSON.stringify({ old: folderFilter, new: next }) })
      setFolderFilter(next)
      await refreshAll()
    } catch (e) { setError(e.message) }
  }

  async function duplicateConversation() {
    if (!conversationId || busy) return
    try {
      const copy = await request(`/api/conversations/${conversationId}/duplicate`, { token, method: 'POST' })
      await refreshAll()
      await loadConversation(copy.id)
    } catch (e) { setError(e.message) }
  }

  function saveCurrentPrompt() {
    setSavedPrompts(prev => addSavedPrompt(prev, draft))
    setPromptsOpen(false)
  }

  function usePrompt(prompt) {
    setDraft(prompt.text); setPromptsOpen(false)
    composerRef.current?.focus()
  }

  async function deleteConversation(c) {
    if (!window.confirm(`Delete "${c.title}"? This cannot be undone.`)) return
    try {
      await request(`/api/conversations/${c.id}`, { token, method: 'DELETE' })
      setConversations(prev => prev.filter(x => x.id !== c.id))
      setSearchTick(t => t + 1)
      if (conversationId === c.id) newChat()
    } catch (e) { setError(e.message) }
  }

  async function updateConversation(c, changes) {
    try {
      await request(`/api/conversations/${c.id}`, { token, method: 'PATCH', body: JSON.stringify(changes) })
      await refreshAll()
    } catch (e) { setError(e.message) }
  }

  function renameConversation(c) {
    const title = window.prompt('Rename conversation', c.title)?.trim()
    if (title && title !== c.title) updateConversation(c, { title })
  }

  async function moveConversation(id, folder) {
    try {
      await request(`/api/conversations/${id}/folder`, { token, method: 'PATCH', body: JSON.stringify({ folder }) })
      await refreshAll()
    } catch (e) { setError(e.message) }
  }

  async function deleteAccount() {
    if (!window.confirm('Permanently delete your account and all conversations? This cannot be undone.')) return
    try { await request('/api/auth/me', { token, method: 'DELETE' }); resetSession() } catch (e) { setError(e.message) }
  }

  async function copyText(id, text) {
    try { await navigator.clipboard.writeText(text); setCopied(id); window.setTimeout(() => setCopied(null), 1200) }
    catch { setError('Clipboard access was blocked by the browser.') }
  }

  function toggleSpeak(m) {
    if (!canSpeak) return
    const synth = window.speechSynthesis
    if (speakingId === m.id) { synth.cancel(); setSpeakingId(null); return }
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(plainTextForSpeech(m.text))
    const finish = () => setSpeakingId(current => (current === m.id ? null : current))
    utterance.onend = finish; utterance.onerror = finish
    setSpeakingId(m.id)
    synth.speak(utterance)
  }

  function toggleVoice() {
    if (!SpeechRecognitionApi) return
    if (listening) { recognitionRef.current?.stop(); return }
    const recognition = new SpeechRecognitionApi()
    recognition.lang = navigator.language || 'en-US'
    recognition.interimResults = false
    recognition.continuous = false
    recognition.onresult = e => {
      const transcript = Array.from(e.results).map(r => r[0].transcript).join(' ').trim()
      if (transcript) setDraft(d => (d ? `${d} ` : '') + transcript)
    }
    recognition.onerror = () => setListening(false)
    recognition.onend = () => { setListening(false); composerRef.current?.focus() }
    recognitionRef.current = recognition
    try { recognition.start(); setListening(true) } catch { setListening(false) }
  }

  function exportChat(format) {
    const title = conversations.find(c => c.id === conversationId)?.title || 'Clarity chat'
    const data = { title, model: selectedModel, exportedAt: new Date().toISOString(), messages: messages.filter(m => !isPending(m)) }
    const name = safeFilename(title)
    if (format === 'json') downloadFile(`${name}.json`, chatToJson(data), 'application/json')
    else downloadFile(`${name}.md`, chatToMarkdown(data), 'text/markdown;charset=utf-8')
  }

  if (!user) return <AuthScreen dark={dark} onAuthenticated={handleAuthenticated} />

  const source = searchResults ?? conversations
  const visibleConversations = source.filter(c => folderFilter === 'All folders' || (c.folder || 'General') === folderFilter)
  const folders = [...new Set(conversations.map(c => c.folder || 'General'))]
  const currentTitle = conversations.find(c => c.id === conversationId)?.title || 'New conversation'
  const hasMessages = messages.length > 0
  const lastMessage = messages[messages.length - 1]
  const composedLength = buildMessageWithAttachments(draft, attachments).length
  const regenerateId = !busy && conversationId && lastMessage?.role === 'assistant' && !isPending(lastMessage) ? lastMessage.id : null

  return <div className={`app-shell ${dark ? 'dark' : 'light'}`}>
    {sidebarOpen && <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={19}/></div><span>clarity<span className="brand-dot">.</span></span><button className="icon-btn sidebar-hide" aria-label="Hide sidebar" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18}/></button></div>
      <button className="new-chat" onClick={newChat}><Plus size={17}/> New conversation <span className="shortcut">Ctrl K</span></button>
      <div className="side-search"><Search size={16}/><input aria-label="Search conversations" placeholder="Search titles and messages" value={search} onChange={e => setSearch(e.target.value)}/></div>
      <div className="side-label">WORKSPACE</div>
      <button className="nav-item active"><MessageSquare size={17}/> Chat <span className="nav-count">{conversations.length}</span></button>
      <button className="nav-item" onClick={() => setSettingsOpen(true)}><Settings2 size={17}/> Preferences</button>
      <div className="side-label recent-label">CONVERSATION FOLDERS</div>
      <div className="folder-row"><select className="folder-select" aria-label="Filter by folder" value={folderFilter} onChange={e => setFolderFilter(e.target.value)}><option>All folders</option>{folders.map(f => <option key={f}>{f}</option>)}</select>{folderFilter !== 'All folders' && <button className="icon-btn" title="Rename this folder" aria-label="Rename folder" onClick={renameFolder} disabled={busy}><Pencil size={15}/></button>}</div>
      <div className="folder-create"><input aria-label="Folder name" placeholder="Move this chat to folder…" value={folderDraft} onChange={e => setFolderDraft(e.target.value)} /><button className="icon-btn" title="Move current chat to this folder" disabled={!conversationId || !folderDraft.trim()} onClick={() => { moveConversation(conversationId, folderDraft.trim()); setFolderDraft('') }}><FolderPlus size={16}/></button></div>
      <div className="side-label recent-label">{searchResults ? 'SEARCH RESULTS' : 'SAVED CONVERSATIONS'}</div>
      <div className="conversation-list">
        {visibleConversations.length === 0 && <p className="empty-note">{search.trim() ? 'No matching conversations.' : 'Your conversations will appear here.'}</p>}
        {visibleConversations.map(c => <div className={`history-row ${conversationId === c.id ? 'selected' : ''} ${c.pinned ? 'pinned' : ''}`} key={c.id}>
          <button className="history-item" onClick={() => loadConversation(c.id)} disabled={busy}><span className="history-dot"/><span className="history-text"><span className="history-title">{c.title}</span>{c.snippet && <small>{c.snippet}</small>}</span></button>
          <button className="delete-chat pin-chat" title={c.pinned ? 'Unpin conversation' : 'Pin conversation'} aria-label={`${c.pinned ? 'Unpin' : 'Pin'} ${c.title}`} onClick={() => updateConversation(c, { pinned: !c.pinned })}>{c.pinned ? <PinOff size={14}/> : <Pin size={14}/>}</button>
          <button className="delete-chat" title="Rename conversation" aria-label={`Rename ${c.title}`} onClick={() => renameConversation(c)}><Pencil size={14}/></button>
          <button className="delete-chat" title="Delete conversation" aria-label={`Delete ${c.title}`} onClick={() => deleteConversation(c)}><Trash2 size={14}/></button>
        </div>)}
      </div>
      <div className="sidebar-bottom">
        <div className="plan-card"><div className="plan-icon">{apiOnline ? <Wifi size={17}/> : <WifiOff size={17}/>}</div><div><strong>{apiOnline ? 'API connected' : 'API unavailable'}</strong><span>{apiOnline ? 'Check Ollama for AI responses' : 'Start the backend server'}</span></div></div>
        <button className="profile" onClick={resetSession} title="Sign out"><div className="avatar user-avatar">{user.name.slice(0, 1).toUpperCase()}</div><div className="profile-text"><strong>{user.name}</strong><span>{user.email}</span></div><LogOut size={16}/></button>
      </div>
    </aside>}
    <main className="main-panel">
      <header className="topbar">
        <div className="top-left">{!sidebarOpen && <button className="icon-btn" onClick={() => setSidebarOpen(true)} aria-label="Show sidebar"><Menu size={19}/></button>}<span className="breadcrumb">Workspace</span><span className="crumb-slash">/</span><span className="current-crumb">{currentTitle}</span></div>
        <div className="top-actions">
          <label className="model-pill"><span className={`online-dot ${apiOnline ? '' : 'offline'}`}/> <select aria-label="Choose Ollama model" value={selectedModel} onChange={e => setSelectedModel(e.target.value)} disabled={busy}>{models.map(m => <option key={m} value={m}>{m}</option>)}</select></label>
          <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="Preferences"><Settings2 size={18}/></button>
          <button className="icon-btn" title="Duplicate this conversation" aria-label="Duplicate conversation" onClick={duplicateConversation} disabled={!conversationId || !hasMessages || busy}><CopyPlus size={17}/></button>
          <button className="icon-btn" title="Export Markdown" aria-label="Export Markdown" onClick={() => exportChat('md')} disabled={!hasMessages || busy}><FileText size={17}/></button>
          <button className="icon-btn" title="Export JSON" aria-label="Export JSON" onClick={() => exportChat('json')} disabled={!hasMessages || busy}><FileJson size={17}/></button>
        </div>
      </header>
      <section className="conversation">
        {!hasMessages && <div className="welcome-block"><div className="welcome-symbol"><Bot size={31}/><span><Sparkles size={14}/></span></div><div className="eyebrow"><span className="eyebrow-line"/> YOUR AI WORKSPACE <span className="eyebrow-line"/></div><h1>What can I help you<br/><span>make today?</span></h1><p className="welcome-subtitle">Real local AI, secure accounts, and saved conversations.<br/>A little clarity goes a long way.</p>
          <div className="prompt-grid">{starters.map(({ title, text }) => <button className="prompt-card" key={title} onClick={() => sendMessage(text)}><div className="prompt-icon"><Sparkles size={18}/></div><strong>{title}</strong><span>{text}</span><span className="prompt-arrow">↗</span></button>)}</div></div>}
        <div className="messages">
          {messages.map(m => <article className={`message ${m.role}`} key={m.id}>
            <div className={`avatar ${m.role === 'assistant' ? 'bot-avatar' : 'user-avatar'}`}>{m.role === 'assistant' ? <Sparkles size={16}/> : <UserRound size={16}/>}</div>
            <div className="message-body">
              <div className="message-meta"><strong>{m.role === 'assistant' ? 'Clarity' : 'You'}</strong><span>{m.time}</span></div>
              {editingId === m.id
                ? <div className="edit-box">
                  <textarea aria-label="Edit your message" rows="3" maxLength={MAX_MESSAGE_CHARS} autoFocus value={editText} onChange={e => setEditText(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') setEditingId(null); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitEdit(m) } }}/>
                  <div className="edit-actions"><button type="button" className="edit-cancel" onClick={() => setEditingId(null)}>Cancel</button><button type="button" className="edit-save" disabled={!editText.trim()} onClick={() => submitEdit(m)}>Save and resend</button></div>
                  <small>Answers after this message will be replaced. Ctrl/⌘ + Enter to save.</small>
                </div>
                : m.role === 'assistant' && !m.text && isPending(m)
                  ? <p className="thinking"><LoaderCircle size={16} className="spin"/> Asking the local model…</p>
                  : <div className="message-copy markdown-body"><ReactMarkdown rehypePlugins={[rehypeHighlight]} components={markdownComponents}>{m.text}</ReactMarkdown></div>}
              {m.role === 'user' && !isPending(m) && !busy && editingId !== m.id && conversationId && <div className="message-tools">
                <button onClick={() => startEdit(m)}><Pencil size={13}/> Edit</button>
              </div>}
              {m.role === 'assistant' && m.text && !isPending(m) && <div className="message-tools">
                <button onClick={() => copyText(m.id, m.text)}>{copied === m.id ? <Check size={13}/> : <Copy size={13}/>} {copied === m.id ? 'Copied' : 'Copy'}</button>
                {canSpeak && <button onClick={() => toggleSpeak(m)}>{speakingId === m.id ? <Square size={13}/> : <Volume2 size={13}/>} {speakingId === m.id ? 'Stop' : 'Read aloud'}</button>}
                {regenerateId === m.id && <button onClick={regenerate}><RefreshCw size={13}/> Regenerate</button>}
              </div>}
            </div>
          </article>)}
          <div ref={bottomRef}/>
        </div>
      </section>
      <div className="composer-area">
        {error && <div className="error-banner" role="alert">{error}</div>}
        <form className="composer" onSubmit={e => { e.preventDefault(); sendMessage() }}>
          {attachments.length > 0 && <div className="attachment-list">{attachments.map(a => <span className="attachment-chip" key={a.id}><Paperclip size={12}/><span className="attachment-name">{a.name}</span><button type="button" aria-label={`Remove ${a.name}`} onClick={() => setAttachments(prev => prev.filter(x => x.id !== a.id))}><X size={12}/></button></span>)}</div>}
          <textarea ref={composerRef} aria-label="Message Clarity" rows="1" maxLength={12000} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendMessage() }
            else if (e.key === 'ArrowUp' && !draft && !busy && conversationId) {
              const lastUser = [...messages].reverse().find(m => m.role === 'user' && !isPending(m))
              if (lastUser) { e.preventDefault(); startEdit(lastUser) }
            }
          }} placeholder="Message Clarity… (Enter to send)"/>
          <div className="composer-footer">
            <div className="composer-tools">
              <div className="prompt-wrap">
                <button type="button" className="tool-btn" onClick={() => setPromptsOpen(o => !o)} aria-label="Saved prompts" aria-expanded={promptsOpen} title="Saved prompts" disabled={busy}><Bookmark size={16}/></button>
                {promptsOpen && <div className="prompt-menu" role="menu" aria-label="Saved prompts">
                  <button type="button" className="prompt-save" onClick={saveCurrentPrompt} disabled={!draft.trim()}><BookmarkPlus size={14}/> Save current message as prompt</button>
                  {savedPrompts.length === 0 && <p className="empty-note">No saved prompts yet. Type a message, then save it here to reuse it.</p>}
                  {savedPrompts.map(p => <div className="prompt-item" key={p.id}>
                    <button type="button" role="menuitem" className="prompt-use" title={p.text} onClick={() => usePrompt(p)}>{promptLabel(p.text)}</button>
                    <button type="button" className="prompt-del" aria-label={`Delete saved prompt ${promptLabel(p.text, 20)}`} onClick={() => setSavedPrompts(prev => prev.filter(x => x.id !== p.id))}><X size={12}/></button>
                  </div>)}
                </div>}
              </div>
              <button type="button" className="tool-btn" onClick={() => attachRef.current?.click()} aria-label="Attach text files" title="Attach text or code files (up to 5, 100 KB each)" disabled={busy || attachments.length >= 5}><Paperclip size={16}/></button>
              <input ref={attachRef} type="file" multiple hidden accept="text/*,.md,.json,.csv,.js,.jsx,.ts,.tsx,.py,.java,.c,.cpp,.cs,.go,.rs,.rb,.php,.html,.css,.sql,.yml,.yaml,.xml,.sh,.log,.toml,.ini,.kt" onChange={addAttachments}/>
              {SpeechRecognitionApi && <button type="button" className={`tool-btn ${listening ? 'listening' : ''}`} onClick={toggleVoice} aria-label={listening ? 'Stop voice input' : 'Start voice input'} title={listening ? 'Stop voice input' : 'Voice input'} disabled={busy}>{listening ? <MicOff size={16}/> : <Mic size={16}/>}</button>}
              <span className={`composer-hint ${composedLength > MAX_MESSAGE_CHARS ? 'over-limit' : ''}`}>{busy ? 'Generating… press Stop to keep what you have' : listening ? 'Listening…' : composedLength > MAX_MESSAGE_CHARS * 0.8 ? `${composedLength.toLocaleString()} / ${MAX_MESSAGE_CHARS.toLocaleString()} characters` : 'Shift + Enter for a new line'}</span>
            </div>
            {busy
              ? <button className="send-btn stop-btn" type="button" aria-label="Stop generating" title="Stop generating" onClick={() => abortRef.current?.abort()}><Square size={14}/></button>
              : <button className="send-btn" type="submit" disabled={!draft.trim() && attachments.length === 0} aria-label="Send message"><Send size={16}/></button>}
          </div>
        </form>
        <div className="disclaimer"><ShieldCheck size={12}/> Local AI responses may be inaccurate. Verify important information.</div>
      </div>
    </main>
    {settingsOpen && <SettingsModal dark={dark} setDark={setDark} autoTitle={autoTitle} setAutoTitle={setAutoTitle} instructions={instructions} setInstructions={setInstructions} temperature={temperature} setTemperature={setTemperature} apiOnline={apiOnline} user={user} token={token} onClose={closeSettings} onSignOut={resetSession} onDeleteAccount={deleteAccount} onImported={refreshAll}/>}
  </div>
}
