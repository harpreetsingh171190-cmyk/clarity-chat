import { describe, expect, it } from 'vitest'
import { addSavedPrompt, buildMessageWithAttachments, parseSavedPrompts, promptLabel, chatToJson, chatToMarkdown, isPending, looksLikeText, normalizeImport, parseNdjson, plainTextForSpeech, safeFilename } from './utils.js'

describe('parseNdjson', () => {
  it('parses complete lines and keeps the partial remainder', () => {
    const { events, rest } = parseNdjson('{"type":"token","content":"Hi"}\n{"type":"to')
    expect(events).toEqual([{ type: 'token', content: 'Hi' }])
    expect(rest).toBe('{"type":"to')
  })
  it('skips blank and malformed lines', () => {
    const { events } = parseNdjson('\nnot json\n{"type":"done"}\n')
    expect(events).toEqual([{ type: 'done' }])
  })
  it('handles an event split across chunks', () => {
    const first = parseNdjson('{"type":"token","con')
    const second = parseNdjson(first.rest + 'tent":"x"}\n')
    expect(second.events).toEqual([{ type: 'token', content: 'x' }])
  })
})

describe('safeFilename', () => {
  it('creates a lowercase slug', () => expect(safeFilename('Hello, World! 2026')).toBe('hello-world-2026'))
  it('falls back when nothing usable remains', () => expect(safeFilename('???')).toBe('clarity-chat'))
  it('limits length', () => expect(safeFilename('a'.repeat(200)).length).toBe(60))
})

describe('export helpers', () => {
  const chat = {
    title: 'Demo', model: 'qwen2.5:3b', exportedAt: '2026-10-25T00:00:00.000Z',
    messages: [{ role: 'user', text: 'Hi', time: 't1' }, { role: 'assistant', text: 'Hello', time: 't2' }],
  }
  it('renders markdown with role headings', () => {
    const md = chatToMarkdown(chat)
    expect(md).toContain('# Demo')
    expect(md).toContain('## You\n\nHi')
    expect(md).toContain('## Clarity\n\nHello')
  })
  it('renders JSON with content fields', () => {
    const parsed = JSON.parse(chatToJson(chat))
    expect(parsed.messages[1]).toEqual({ role: 'assistant', content: 'Hello', time: 't2' })
  })
})

describe('isPending', () => {
  it('detects optimistic messages', () => {
    expect(isPending({ id: 'pending-1' })).toBe(true)
    expect(isPending({ id: 12 })).toBe(false)
  })
})

describe('plainTextForSpeech', () => {
  it('removes markdown syntax and replaces code blocks', () => {
    const text = plainTextForSpeech('# Title\n\nSome **bold** text and `inline`.\n\n```js\nconsole.log(1)\n```\n\nSee [docs](https://x.dev).')
    expect(text).toBe('Title Some bold text and inline. code block. See docs.')
  })
  it('handles empty input', () => expect(plainTextForSpeech(undefined)).toBe(''))
})

describe('normalizeImport', () => {
  it('passes full exports through', () => {
    const data = { conversations: [{ title: 'A', messages: [] }] }
    expect(normalizeImport(data)).toEqual({ conversations: data.conversations })
  })
  it('wraps a single-chat export and drops invalid messages', () => {
    const result = normalizeImport({ title: 'Chat', messages: [{ role: 'user', content: 'Hi' }, { role: 'assistant', text: 'Hello' }, { role: 'system', content: 'x' }, { role: 'user', content: '  ' }] })
    expect(result.conversations[0].title).toBe('Chat')
    expect(result.conversations[0].messages).toEqual([{ role: 'user', content: 'Hi' }, { role: 'assistant', content: 'Hello' }])
  })
  it('rejects unknown files', () => {
    expect(() => normalizeImport({ foo: 1 })).toThrow('not a Clarity Chat export')
    expect(() => normalizeImport(null)).toThrow()
  })
})

describe('attachments', () => {
  it('returns the typed text unchanged when nothing is attached', () => {
    expect(buildMessageWithAttachments('  hello  ', [])).toBe('hello')
  })

  it('appends each file in a labelled code block', () => {
    const out = buildMessageWithAttachments('Review this', [{ name: 'a.py', text: 'print(1)' }])
    expect(out).toBe('Review this\n\nAttached file: a.py\n```\nprint(1)\n```')
  })

  it('works with attachments only', () => {
    expect(buildMessageWithAttachments('', [{ name: 'n.txt', text: 'x' }])).toBe('Attached file: n.txt\n```\nx\n```')
  })

  it('uses a longer fence when the file contains backticks', () => {
    const out = buildMessageWithAttachments('', [{ name: 'doc.md', text: 'a\n```js\nb\n```' }])
    expect(out.split('\n')[1]).toBe('````')
    expect(out.endsWith('````')).toBe(true)
  })

  it('detects binary content', () => {
    expect(looksLikeText('plain text')).toBe(true)
    expect(looksLikeText('abc\u0000def')).toBe(false)
  })
})

describe('saved prompts', () => {
  it('adds new prompts first and ignores blanks and duplicates', () => {
    let list = addSavedPrompt([], '  Explain this  ', 'a')
    list = addSavedPrompt(list, 'Summarise', 'b')
    expect(list.map(p => p.text)).toEqual(['Summarise', 'Explain this'])
    expect(addSavedPrompt(list, 'Summarise', 'c')).toBe(list)
    expect(addSavedPrompt(list, '   ', 'd')).toBe(list)
  })

  it('keeps at most 20 prompts', () => {
    let list = []
    for (let i = 0; i < 25; i++) list = addSavedPrompt(list, `prompt ${i}`, String(i))
    expect(list).toHaveLength(20)
    expect(list[0].text).toBe('prompt 24')
  })

  it('survives bad storage content', () => {
    expect(parseSavedPrompts('not json')).toEqual([])
    expect(parseSavedPrompts('{"a":1}')).toEqual([])
    expect(parseSavedPrompts('[{"id":"1","text":"ok"},{"id":2,"text":"bad"},null]')).toEqual([{ id: '1', text: 'ok' }])
    expect(parseSavedPrompts(null)).toEqual([])
  })

  it('shortens long labels', () => {
    expect(promptLabel('a\n  b')).toBe('a b')
    expect(promptLabel('x'.repeat(100))).toHaveLength(48)
  })
})
