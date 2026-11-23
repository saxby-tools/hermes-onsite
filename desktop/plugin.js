/**
 * hermes-hands: the Hermes Desktop half.
 *
 * Three features, each in its own labeled section so one can be
 * dropped without touching the others:
 *
 *   1. STATUS CHIP    where this session's tools run, and whether that
 *                     machine answers right now
 *   2. COMMANDS PANE  the commands the agent proposed, with Run and Copy
 *   3. RUN CARD       the same Run and Copy, inline in the transcript
 *
 * The desktop loader takes one plain ESM file. Only three imports
 * resolve, there is no build step, and UI is jsx() calls.
 *
 * Everything from the SDK is read off the namespace import below and
 * checked before use. A named import of something an older app does
 * not export would stop the whole file from loading.
 *
 * Install: copy this file to
 *   <hermes home>/desktop-plugins/hermes-hands/plugin.js
 * on the machine that runs Hermes Desktop. The folder name must stay
 * `hermes-hands`, because the app uses it to reach the plugin's
 * routes on the host.
 */

import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

/* ------------------------------------------------------------------ */
/* Constants                                                           */
/* ------------------------------------------------------------------ */

const ID = 'hermes-hands'
const NAME = 'Hands'
const DIRECTIVE = 'hands-run'
const PANE_ID = 'pane'

const POLL_MS = 5000
const REQUEST_TIMEOUT_MS = 8000
const COPIED_MS = 1500

// How the app reports a route that the host does not have.
const NOT_FOUND = '404'

const AREA_CHIP = 'statusBar.right'
const AREA_PANES = 'panes'
const AREA_DIRECTIVES = 'transcript.directives'

const CHIP_ORDER = 120
const PANE_WIDTH = '280px'

const TONE_UP = 'up'
const TONE_DOWN = 'down'
const TONE_UNKNOWN = 'unknown'

const TONE_COLORS = {
  [TONE_UP]: 'var(--ui-success, #3fb950)',
  [TONE_DOWN]: 'var(--ui-danger, #f85149)',
  [TONE_UNKNOWN]: 'var(--ui-text-tertiary, #8b949e)'
}

const REASONS = {
  override: 'set with /hands',
  pinned: 'this session started there',
  prefix: 'the folder is under one of its prefixes',
  path_map: 'the folder is in its path map',
  mirror: 'the folder is in its mirror',
  default: 'no folder rule matched'
}

const EVENTS_THAT_CHANGE_STATE = ['tool.complete', 'session.info', 'gateway.ready']

const SESSION_ATOMS = ['focusedStoredSessionId', 'focusedSessionId', 'activeSessionId']

const TEXT_SMALL = 'text-[0.6875rem]'
const TEXT_MUTED = 'text-(--ui-text-tertiary)'

const BUTTON_CLASS =
  'rounded border border-(--ui-border) px-2 py-0.5 text-[0.75rem] ' +
  'hover:bg-(--chrome-action-hover) disabled:opacity-50'

const CODE_CLASS =
  'block overflow-x-auto whitespace-pre rounded bg-(--ui-surface-sunken) ' +
  'px-2 py-1 font-mono text-[0.75rem]'

/* ------------------------------------------------------------------ */
/* Shared state                                                        */
/* ------------------------------------------------------------------ */

// A tiny store instead of the SDK's atom, so the file does not lean
// on one more export that an older app might lack.
function createStore(initial) {
  let value = initial
  const listeners = new Set()

  return {
    get: () => value,
    set: next => {
      value = next
      listeners.forEach(listener => listener(value))
    },
    subscribe: listener => {
      listeners.add(listener)

      return () => listeners.delete(listener)
    }
  }
}

function useStore(store) {
  const [value, setValue] = useState(store.get())

  useEffect(() => store.subscribe(setValue), [store])

  return value
}

const statusStore = createStore(null)
const proposalsStore = createStore([])

// Set in register(). The components need the plugin context to call
// the host, and they are created outside it.
let plugin = null

/* ------------------------------------------------------------------ */
/* Talking to the host                                                 */
/* ------------------------------------------------------------------ */

function joinClasses(...parts) {
  if (typeof sdk.cn === 'function') {
    return sdk.cn(...parts)
  }

  return parts.filter(Boolean).join(' ')
}

function readAtom(atom) {
  if (!atom) {
    return undefined
  }

  if (typeof atom.get !== 'function') {
    return undefined
  }

  return atom.get()
}

function hostState() {
  return sdk.host?.state ?? {}
}

// The app knows a session by more than one id. Which one matches the
// id Hermes uses differs by app version, so all of them are sent.
function sessionIds() {
  const state = hostState()
  const ids = SESSION_ATOMS.map(name => readAtom(state[name]))

  return ids.filter(id => typeof id === 'string' && id.length > 0)
}

function requestBody() {
  return {
    cwd: readAtom(hostState().cwd) ?? '',
    session_ids: sessionIds()
  }
}

// One gateway can serve several profiles, and each has its own
// plugins. The app sends a plugin's requests to whichever profile is
// active, which is not always the one the person thinks they are in.
function activeProfile() {
  const name = readAtom(hostState().profile)

  if (typeof name !== 'string') {
    return ''
  }

  return name.trim()
}

function describeMissingPlugin() {
  const profile = activeProfile()

  if (profile === '') {
    return 'The host has no hermes-hands plugin enabled.'
  }

  return (
    `The app asked the "${profile}" profile, ` +
    'which has no hermes-hands plugin enabled.'
  )
}

function describeError(error) {
  const text = String(error?.message ?? error ?? 'unknown error')

  if (text.includes(NOT_FOUND)) {
    return describeMissingPlugin()
  }

  return text
}

async function post(path) {
  return plugin.rest(path, {
    method: 'POST',
    body: requestBody(),
    timeoutMs: REQUEST_TIMEOUT_MS
  })
}

async function refresh() {
  if (!plugin) {
    return
  }

  try {
    statusStore.set(await post('/status'))
  } catch (error) {
    statusStore.set({ ok: false, error: describeError(error) })
  }

  try {
    const answer = await post('/proposals')

    proposalsStore.set(Array.isArray(answer?.items) ? answer.items : [])
  } catch (error) {
    // The chip already shows that the host is unreachable. The list
    // keeps what it had, so a blip does not blank the pane.
  }
}

function notify(kind, message) {
  sdk.host?.notify?.({ kind, message })
}

async function copyText(text) {
  if (typeof plugin?.os?.writeClipboard === 'function') {
    await plugin.os.writeClipboard(text)

    return true
  }

  if (globalThis.navigator?.clipboard?.writeText) {
    await globalThis.navigator.clipboard.writeText(text)

    return true
  }

  return false
}

// Sends `/run N` as if the user typed it. The agent then runs the
// command through the terminal tool, so Hermes' approval prompt
// still applies. Nothing is executed from here.
function runProposal(number, sessionId) {
  const submit = sdk.host?.composer?.submit

  if (typeof submit !== 'function') {
    notify('error', `Type /run ${number} in the chat to run it.`)

    return false
  }

  const sent = submit(sessionId ?? null, `/run ${number}`)

  if (!sent) {
    notify('error', `Could not reach the chat box. Type /run ${number}.`)

    return false
  }

  return true
}

/* ------------------------------------------------------------------ */
/* Small shared pieces                                                 */
/* ------------------------------------------------------------------ */

function toneOf(status) {
  if (!status) {
    return TONE_UNKNOWN
  }

  if (!status.ok) {
    return TONE_DOWN
  }

  if (!status.connected) {
    return TONE_DOWN
  }

  return TONE_UP
}

function shortFolder(folder) {
  if (typeof folder !== 'string') {
    return ''
  }

  const parts = folder.split('/').filter(Boolean)

  if (parts.length <= 2) {
    return folder
  }

  return `…/${parts.slice(-2).join('/')}`
}

function stateText(status) {
  if (!status) {
    return 'checking…'
  }

  if (!status.ok) {
    return 'not set up'
  }

  if (!status.connected) {
    return `not reachable (${status.detail || 'no answer'})`
  }

  if (status.kind === 'local') {
    return 'ready'
  }

  return `connected (${status.latency_ms} ms)`
}

function Dot({ tone }) {
  return jsx('span', {
    'aria-hidden': true,
    'data-tone': tone,
    style: {
      background: TONE_COLORS[tone],
      borderRadius: '50%',
      display: 'inline-block',
      height: '0.5rem',
      width: '0.5rem'
    }
  })
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)

  const onClick = async () => {
    const done = await copyText(text)

    if (!done) {
      notify('error', 'Could not copy. Select the text instead.')

      return
    }

    setCopied(true)
    globalThis.setTimeout(() => setCopied(false), COPIED_MS)
  }

  return jsx('button', {
    className: BUTTON_CLASS,
    'data-action': 'copy',
    onClick,
    type: 'button',
    children: copied ? 'Copied' : 'Copy'
  })
}

function RunButton({ number, sessionId }) {
  const [sent, setSent] = useState(false)

  const onClick = () => {
    if (runProposal(number, sessionId)) {
      setSent(true)
    }
  }

  return jsx('button', {
    className: BUTTON_CLASS,
    'data-action': 'run',
    disabled: sent,
    onClick,
    type: 'button',
    children: sent ? 'Sent' : 'Run'
  })
}

function CommandBlock({ number, command, why, sessionId }) {
  return jsxs('div', {
    className: 'flex flex-col gap-1 rounded border border-(--ui-border) p-2',
    'data-proposal': number,
    children: [
      jsxs('div', {
        className: joinClasses('flex items-center justify-between', TEXT_SMALL, TEXT_MUTED),
        children: [
          jsx('span', { children: `Proposed command #${number}` }),
          jsxs('span', {
            className: 'flex gap-1',
            children: [
              jsx(RunButton, { number, sessionId }),
              command ? jsx(CopyButton, { text: command }) : null
            ]
          })
        ]
      }),
      command
        ? jsx('code', { className: CODE_CLASS, children: command })
        : jsx('div', { className: TEXT_MUTED, children: `Run it with /run ${number}.` }),
      why ? jsx('div', { className: joinClasses(TEXT_SMALL, TEXT_MUTED), children: why }) : null
    ]
  })
}

/* ------------------------------------------------------------------ */
/* 1. STATUS CHIP                                                      */
/* ------------------------------------------------------------------ */

function chipLabel(status) {
  if (!status) {
    return 'hands'
  }

  if (!status.ok) {
    return 'hands: not set up'
  }

  return `hands: ${status.target}`
}

function chipTip(status) {
  if (!status) {
    return 'hermes-hands: checking where this session runs'
  }

  if (!status.ok) {
    return status.error || 'hermes-hands is not set up on the host.'
  }

  return [
    `Tools run on ${status.label}`,
    `State: ${stateText(status)}`,
    `Folder: ${status.cwd}`,
    `Why: ${REASONS[status.reason] ?? status.reason}`
  ].join('\n')
}

function HandsChip() {
  const status = useStore(statusStore)
  const tone = toneOf(status)

  const button = jsxs('button', {
    className: joinClasses(
      'inline-flex h-full items-center gap-1.5 px-1.5 transition-colors',
      TEXT_SMALL,
      TEXT_MUTED,
      'hover:bg-(--chrome-action-hover) hover:text-foreground'
    ),
    'data-hands-chip': tone,
    onClick: () => {
      sdk.host?.revealPane?.(`${ID}:${PANE_ID}`)
      refresh()
    },
    title: chipTip(status),
    type: 'button',
    children: [
      jsx(Dot, { tone }),
      jsx('span', { children: chipLabel(status) }),
      status?.ok ? jsx('span', { children: shortFolder(status.cwd) }) : null
    ]
  })

  if (typeof sdk.Tip !== 'function') {
    return button
  }

  return jsx(sdk.Tip, { label: chipTip(status), children: button })
}

/* ------------------------------------------------------------------ */
/* 2. COMMANDS PANE                                                    */
/* ------------------------------------------------------------------ */

function StatusLines({ status }) {
  if (!status) {
    return jsx('div', { className: TEXT_MUTED, children: 'Checking…' })
  }

  if (!status.ok) {
    return jsx('div', {
      className: 'whitespace-pre-wrap',
      children: status.error || 'hermes-hands is not set up on the host.'
    })
  }

  const rows = [
    ['Runs on', status.label],
    ['State', stateText(status)],
    ['Folder', status.cwd],
    ['Why', REASONS[status.reason] ?? status.reason]
  ]

  return jsx('dl', {
    className: 'grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5',
    children: rows.flatMap(([term, text]) => [
      jsx('dt', { className: TEXT_MUTED, children: term }, `${term}-t`),
      jsx('dd', { className: 'break-all', children: text }, `${term}-d`)
    ])
  })
}

function TargetList({ rows }) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return null
  }

  return jsx('ul', {
    className: 'flex flex-col gap-0.5',
    children: rows.map(row =>
      jsxs(
        'li',
        {
          className: 'flex items-center gap-1.5',
          children: [
            jsx(Dot, { tone: row.connected ? TONE_UP : TONE_DOWN }),
            jsx('span', { children: row.label }),
            row.default ? jsx('span', { className: TEXT_MUTED, children: '(default)' }) : null
          ]
        },
        row.name
      )
    )
  })
}

function HandsPane() {
  const status = useStore(statusStore)
  const proposals = useStore(proposalsStore)
  const pending = proposals.filter(item => item.status === 'pending')
  const sessionId = sessionIds()[0] ?? null

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3 text-[0.8125rem]',
    'data-hands-pane': true,
    children: [
      jsx('div', { className: 'font-medium', children: 'Hands' }),
      jsx(StatusLines, { status }),
      jsx(TargetList, { rows: status?.rows }),
      jsx('div', {
        className: joinClasses(TEXT_SMALL, TEXT_MUTED),
        children: 'Move this session with /hands <target>.'
      }),
      jsx('div', { className: 'font-medium', children: 'Proposed commands' }),
      pending.length === 0
        ? jsx('div', {
            className: TEXT_MUTED,
            children: 'Nothing is waiting. Commands the agent proposes show up here.'
          })
        : pending.map(item =>
            jsx(
              CommandBlock,
              {
                command: item.command,
                number: item.number,
                sessionId,
                why: item.why
              },
              item.number
            )
          )
    ]
  })
}

/* ------------------------------------------------------------------ */
/* 3. RUN CARD                                                         */
/* ------------------------------------------------------------------ */

// Attributes come from model output, so the number is checked and
// nothing else in them is trusted. The command text is looked up on
// the host by that number. It is never taken from the directive.
function parseNumber(attrs) {
  const raw = String(attrs?.n ?? '')

  if (!/^[0-9]{1,6}$/.test(raw)) {
    return null
  }

  return Number(raw)
}

function RunCard({ attrs, sessionId, streaming }) {
  const proposals = useStore(proposalsStore)
  const number = parseNumber(attrs)

  useEffect(() => {
    // A card can render before the poll that would know its command.
    if (!streaming) {
      refresh()
    }
  }, [streaming, number])

  if (number === null) {
    return null
  }

  const found = proposals.find(item => item.number === number)

  return jsx(CommandBlock, {
    command: found?.command ?? '',
    number,
    sessionId: sessionId === undefined ? null : sessionId,
    why: found?.why ?? ''
  })
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

export default {
  id: ID,
  name: NAME,
  description: 'Shows where this session runs and lets you run proposed commands.',
  register(ctx) {
    plugin = ctx

    ctx.onDispose?.(() => {
      plugin = null
    })

    // 1. STATUS CHIP
    ctx.register({
      id: 'chip',
      area: AREA_CHIP,
      order: CHIP_ORDER,
      render: () => jsx(HandsChip, {})
    })

    // 2. COMMANDS PANE
    ctx.register({
      id: PANE_ID,
      area: AREA_PANES,
      title: 'hands',
      data: { placement: 'right', width: PANE_WIDTH },
      render: () => jsx(HandsPane, {})
    })

    // 3. RUN CARD
    ctx.register({
      id: 'run-card',
      area: AREA_DIRECTIVES,
      data: {
        name: DIRECTIVE,
        render: props => jsx(RunCard, props)
      }
    })

    // Status comes from a live probe on the host each time. Nothing
    // here is cached between polls.
    ctx.setInterval(refresh, POLL_MS)

    EVENTS_THAT_CHANGE_STATE.forEach(type => {
      ctx.onEvent?.(type, () => refresh())
    })

    refresh()
  }
}
