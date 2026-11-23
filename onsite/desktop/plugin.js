/**
 * onsite: the Hermes Desktop half.
 *
 * Five features, each in its own labeled section so one can be
 * dropped without touching the others:
 *
 *   1. STATUS CHIP     where this session's tools run, and whether that
 *                      machine answers right now
 *   2. COMMANDS PANE   the commands the agent proposed, with Run and Copy
 *   3. RUN CARD        the same Run and Copy, inline in the transcript
 *   4. FOLDER BROWSER  pick a folder on a machine and start a session
 *                      in it, in the pane
 *   5. MACHINES PAGE   add, edit, test and remove machines, in Settings
 *
 * The desktop loader takes one plain ESM file. Only three imports
 * resolve, there is no build step, and UI is jsx() calls.
 *
 * Everything from the SDK is read off the namespace import below and
 * checked before use. A named import of something an older app does
 * not export would stop the whole file from loading.
 *
 * Install: copy this file to
 *   <hermes home>/desktop-plugins/onsite/plugin.js
 * on the machine that runs Hermes Desktop. The folder name must stay
 * `onsite`, because the app uses it to reach the plugin's
 * routes on the host.
 */

import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

/* ------------------------------------------------------------------ */
/* Constants                                                           */
/* ------------------------------------------------------------------ */

const ID = 'onsite'
const NAME = 'Onsite'
const DIRECTIVE = 'onsite-run'
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

const SETTINGS_PAGE_ID = 'machines'
const SETTINGS_PAGE_TITLE = 'Machines'
const SETTINGS_PAGE_ICON = 'remote'

const KIND_SSH = 'ssh'
const KIND_LOCAL = 'local'
const HOME_FOLDER = '~'

const INPUT_CLASS =
  'w-full rounded border border-(--ui-border) bg-transparent px-2 py-1 ' +
  'text-[0.8125rem]'
const ROW_CLASS =
  'flex w-full items-center gap-2 rounded px-2 py-1 text-left ' +
  'hover:bg-(--chrome-action-hover)'

const EMPTY_MACHINE = {
  name: '',
  kind: KIND_SSH,
  host: '',
  user: '',
  port: '',
  key: '',
  prefixes: '',
  mirror: '',
  isDefault: false,
  isNew: true
}
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
  override: 'set with /onsite',
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

// The profile that owns the chat in view. The app's Bots tab can show
// one profile's chat while its requests still go to another.
function focusedProfile() {
  const name = readAtom(hostState().focusedSessionProfile)

  if (typeof name !== 'string') {
    return ''
  }

  return name.trim()
}

function describeMissingPlugin() {
  const profile = activeProfile()
  const focused = focusedProfile()

  if (profile === '') {
    return 'The host has no onsite plugin enabled.'
  }

  const asked =
    `The app asked the "${profile}" profile, ` +
    'which has no onsite plugin enabled.'

  if (focused === '') {
    return asked
  }

  if (focused === profile) {
    return asked
  }

  return (
    `This chat belongs to the "${focused}" profile. ${asked} ` +
    `Pick "${focused}" in the Sessions tab to see where this chat runs.`
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
    return 'onsite'
  }

  if (!status.ok) {
    return 'onsite: not set up'
  }

  return `onsite: ${status.target}`
}

function chipTip(status) {
  if (!status) {
    return 'onsite: checking where this session runs'
  }

  if (!status.ok) {
    return status.error || 'onsite is not set up on the host.'
  }

  return [
    `Tools run on ${status.label}`,
    `State: ${stateText(status)}`,
    `Folder: ${status.cwd}`,
    `Why: ${REASONS[status.reason] ?? status.reason}`
  ].join('\n')
}

function OnsiteChip() {
  const status = useStore(statusStore)
  const tone = toneOf(status)

  const button = jsxs('button', {
    className: joinClasses(
      'inline-flex h-full items-center gap-1.5 px-1.5 transition-colors',
      TEXT_SMALL,
      TEXT_MUTED,
      'hover:bg-(--chrome-action-hover) hover:text-foreground'
    ),
    'data-onsite-chip': tone,
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
      children: status.error || 'onsite is not set up on the host.'
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

/* ------------------------------------------------------------------ */
/* 4. FOLDER BROWSER                                                   */
/* ------------------------------------------------------------------ */

async function send(path, body) {
  return plugin.rest(path, {
    method: 'POST',
    body,
    timeoutMs: REQUEST_TIMEOUT_MS
  })
}

function folderName(path) {
  const parts = String(path).split('/').filter(Boolean)

  if (parts.length === 0) {
    return '/'
  }

  return parts[parts.length - 1]
}

function joinFolder(path, name) {
  if (path === '/') {
    return `/${name}`
  }

  return `${path}/${name}`
}

function sshTargets(rows) {
  return (rows ?? []).filter(row => row.kind === KIND_SSH).map(row => row.name)
}

function canStartSessions() {
  if (typeof sdk.host?.request !== 'function') {
    return false
  }

  return typeof sdk.host?.openSession === 'function'
}

// Starts a new session in a folder on the host, the way Hermes' own
// chat plugin does. The folder is the host's stand-in for the folder
// on the machine, so the session runs there.
async function openSessionAt(cwd, title) {
  if (!canStartSessions()) {
    await copyText(cwd)
    notify('info', `Open this folder to work there. It is copied: ${cwd}`)

    return false
  }

  const request = { cwd, cwd_explicit: true }
  const profile = activeProfile()

  if (profile !== '') {
    request.profile = profile
  }

  const made = await sdk.host.request('session.create', request)

  // A new session has no saved row until it has a title or a prompt.
  await sdk.host.request('session.title', {
    session_id: made.session_id,
    title
  })
  await sdk.host.openSession(made.stored_session_id, { expectHistory: false })

  return true
}

function FolderRow({ label, onPick }) {
  return jsx('button', {
    type: 'button',
    className: ROW_CLASS,
    'data-onsite-folder': label,
    onClick: onPick,
    children: label
  })
}

function FolderBrowser({ rows }) {
  const names = sshTargets(rows)
  const [picked, setPicked] = useState('')
  const [listing, setListing] = useState(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  if (names.length === 0) {
    return null
  }

  const target = names.includes(picked) ? picked : names[0]

  const load = async path => {
    setBusy(true)
    setMessage('')

    try {
      const answer = await send('/browse', { target, path })

      if (answer?.ok) {
        setListing(answer)
      } else {
        setMessage(answer?.error || 'That folder could not be listed.')
      }
    } catch (error) {
      setMessage(describeError(error))
    }

    setBusy(false)
  }

  const openHere = async () => {
    setBusy(true)
    setMessage('')

    try {
      const answer = await send('/open', { target, path: listing.path })

      if (answer?.ok) {
        const title = `${folderName(listing.path)} on ${target}`

        await openSessionAt(answer.cwd, title)
      } else {
        setMessage(answer?.error || 'That folder could not be opened.')
      }
    } catch (error) {
      setMessage(describeError(error))
    }

    setBusy(false)
  }

  const choose = event => {
    setPicked(event.target.value)
    setListing(null)
    setMessage('')
  }

  return jsxs('div', {
    className: 'flex flex-col gap-1',
    'data-onsite-browser': true,
    children: [
      jsx('div', { className: 'font-medium', children: 'Open a folder' }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('select', {
            className: INPUT_CLASS,
            'aria-label': 'Machine',
            value: target,
            onChange: choose,
            children: names.map(name =>
              jsx('option', { value: name, children: name }, name)
            )
          }),
          jsx('button', {
            type: 'button',
            className: BUTTON_CLASS,
            'data-onsite-browse': true,
            disabled: busy,
            onClick: () => load(HOME_FOLDER),
            children: 'Browse'
          })
        ]
      }),
      message
        ? jsx('div', { className: TEXT_MUTED, role: 'alert', children: message })
        : null,
      listing
        ? jsxs('div', {
            className: 'flex flex-col',
            children: [
              jsx('div', {
                className: joinClasses(TEXT_SMALL, TEXT_MUTED, 'break-all'),
                'data-onsite-path': true,
                children: listing.path
              }),
              listing.parent
                ? jsx(FolderRow, {
                    label: '..',
                    onPick: () => load(listing.parent)
                  })
                : null,
              listing.folders.map(name =>
                jsx(
                  FolderRow,
                  {
                    label: name,
                    onPick: () => load(joinFolder(listing.path, name))
                  },
                  name
                )
              ),
              listing.more
                ? jsx('div', {
                    className: joinClasses(TEXT_SMALL, TEXT_MUTED),
                    children: 'More folders than fit here. Go deeper to narrow it.'
                  })
                : null,
              jsx('button', {
                type: 'button',
                className: joinClasses(BUTTON_CLASS, 'mt-1 self-start'),
                'data-onsite-open': true,
                disabled: busy,
                onClick: openHere,
                children: `Start a session in ${folderName(listing.path)}`
              })
            ]
          })
        : null
    ]
  })
}

function OnsitePane() {
  const status = useStore(statusStore)
  const proposals = useStore(proposalsStore)
  const pending = proposals.filter(item => item.status === 'pending')
  const sessionId = sessionIds()[0] ?? null

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3 text-[0.8125rem]',
    'data-onsite-pane': true,
    children: [
      jsx('div', { className: 'font-medium', children: 'Onsite' }),
      jsx(StatusLines, { status }),
      jsx(TargetList, { rows: status?.rows }),
      jsx('div', {
        className: joinClasses(TEXT_SMALL, TEXT_MUTED),
        children: 'Move this session with /onsite <target>.'
      }),
      jsx(FolderBrowser, { rows: status?.rows }),
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
/* 5. MACHINES PAGE                                                    */
/* ------------------------------------------------------------------ */

function machineToForm(name, block, isDefault) {
  return {
    name,
    kind: block?.kind === KIND_LOCAL ? KIND_LOCAL : KIND_SSH,
    host: block?.host ?? '',
    user: block?.user ?? '',
    port: block?.port === undefined ? '' : String(block.port),
    key: block?.key ?? '',
    prefixes: (block?.prefixes ?? []).join('\n'),
    mirror: block?.mirror ?? '',
    isDefault,
    isNew: false,
    // Settings this form does not show are kept as they were.
    kept: block ?? {}
  }
}

function formToTarget(form) {
  const prefixes = form.prefixes
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
  const target = { ...(form.kept ?? {}), kind: form.kind, prefixes }

  if (form.kind === KIND_LOCAL) {
    delete target.host
    delete target.user
    delete target.port
    delete target.key
    delete target.mirror

    return target
  }

  target.host = form.host.trim()
  target.user = form.user.trim()
  target.key = form.key.trim()
  target.mirror = form.mirror.trim()
  target.port = form.port.trim() === '' ? '' : Number(form.port)

  return target
}

function describeMachine(block) {
  if (block?.kind === KIND_LOCAL) {
    return 'this machine'
  }

  return `${block?.user ?? '?'}@${block?.host ?? '?'}`
}

function Field({ label, name, value, onChange, placeholder, disabled }) {
  return jsxs('label', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('span', { className: joinClasses(TEXT_SMALL, TEXT_MUTED), children: label }),
      jsx('input', {
        className: INPUT_CLASS,
        'data-onsite-field': name,
        value,
        placeholder,
        disabled,
        onChange: event => onChange(name, event.target.value)
      })
    ]
  })
}

function MachineForm({ form, note, busy, onChange, onSave, onTest, onCancel }) {
  const overSsh = form.kind === KIND_SSH

  return jsxs('div', {
    className: 'flex flex-col gap-2 rounded border border-(--ui-border) p-3',
    'data-onsite-form': true,
    children: [
      jsx(Field, {
        label: 'Name',
        name: 'name',
        value: form.name,
        placeholder: 'laptop',
        disabled: !form.isNew,
        onChange
      }),
      jsxs('label', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('span', {
            className: joinClasses(TEXT_SMALL, TEXT_MUTED),
            children: 'Where it is'
          }),
          jsxs('select', {
            className: INPUT_CLASS,
            'data-onsite-field': 'kind',
            value: form.kind,
            onChange: event => onChange('kind', event.target.value),
            children: [
              jsx('option', { value: KIND_SSH, children: 'Another machine, over SSH' }),
              jsx('option', { value: KIND_LOCAL, children: 'The machine Hermes runs on' })
            ]
          })
        ]
      }),
      overSsh
        ? jsx(Field, {
            label: 'Address',
            name: 'host',
            value: form.host,
            placeholder: 'laptop.example',
            onChange
          })
        : null,
      overSsh
        ? jsx(Field, {
            label: 'Log in as',
            name: 'user',
            value: form.user,
            placeholder: 'you',
            onChange
          })
        : null,
      overSsh
        ? jsx(Field, {
            label: 'Port (leave empty for 22)',
            name: 'port',
            value: form.port,
            placeholder: '22',
            onChange
          })
        : null,
      overSsh
        ? jsx(Field, {
            label: 'Key file (leave empty to use your SSH setup)',
            name: 'key',
            value: form.key,
            placeholder: '',
            onChange
          })
        : null,
      jsxs('label', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('span', {
            className: joinClasses(TEXT_SMALL, TEXT_MUTED),
            children: 'Folders that belong to it, one a line'
          }),
          jsx('textarea', {
            className: INPUT_CLASS,
            'data-onsite-field': 'prefixes',
            rows: 2,
            value: form.prefixes,
            placeholder: '/home/you/',
            onChange: event => onChange('prefixes', event.target.value)
          })
        ]
      }),
      jsxs('label', {
        className: 'flex items-center gap-2',
        children: [
          jsx('input', {
            type: 'checkbox',
            'data-onsite-field': 'isDefault',
            checked: form.isDefault,
            onChange: event => onChange('isDefault', event.target.checked)
          }),
          jsx('span', { children: 'Use it when no folder rule matches' })
        ]
      }),
      note
        ? jsx('div', { className: TEXT_MUTED, role: 'status', children: note })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('button', {
            type: 'button',
            className: BUTTON_CLASS,
            'data-onsite-save': true,
            disabled: busy,
            onClick: onSave,
            children: 'Save'
          }),
          overSsh
            ? jsx('button', {
                type: 'button',
                className: BUTTON_CLASS,
                'data-onsite-test': true,
                disabled: busy,
                onClick: onTest,
                children: 'Test connection'
              })
            : null,
          jsx('button', {
            type: 'button',
            className: BUTTON_CLASS,
            disabled: busy,
            onClick: onCancel,
            children: 'Cancel'
          })
        ]
      })
    ]
  })
}

function MachineRow({ name, block, isDefault, busy, onEdit, onRemove }) {
  return jsxs('div', {
    className: 'flex items-center gap-2',
    'data-onsite-machine': name,
    children: [
      jsxs('div', {
        className: 'min-w-0 flex-1',
        children: [
          jsx('span', { className: 'font-medium', children: name }),
          jsx('span', {
            className: TEXT_MUTED,
            children: `  ${describeMachine(block)}${isDefault ? '  (default)' : ''}`
          })
        ]
      }),
      jsx('button', {
        type: 'button',
        className: BUTTON_CLASS,
        'data-onsite-edit': name,
        disabled: busy,
        onClick: onEdit,
        children: 'Edit'
      }),
      jsx('button', {
        type: 'button',
        className: BUTTON_CLASS,
        'data-onsite-remove': name,
        disabled: busy || isDefault,
        title: isDefault ? 'Make another machine the default first.' : '',
        onClick: onRemove,
        children: 'Remove'
      })
    ]
  })
}

function describeTest(answer) {
  if (!answer?.ok) {
    return answer?.error || 'It could not be tested.'
  }

  if (answer.connected) {
    return `Connected in ${answer.latency_ms} ms.`
  }

  return `No connection: ${answer.detail || 'no answer'}`
}

function MachinesPage() {
  const [settings, setSettings] = useState(null)
  const [form, setForm] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // One request at a time. Each answer is the whole list again, so
  // the page always shows what the settings file holds.
  const ask = async (path, body, done) => {
    setBusy(true)

    try {
      done(await send(path, body))
    } catch (error) {
      setNote(describeError(error))
    }

    setBusy(false)
  }

  const showList = answer => {
    if (!answer?.ok) {
      setNote(answer?.error || 'The settings could not be read.')

      return
    }

    setSettings(answer)
    setForm(null)
    setNote(answer.problem || '')
  }

  useEffect(() => {
    ask('/targets', {}, showList)
  }, [])

  const change = (name, value) => setForm({ ...form, [name]: value })

  const save = () =>
    ask(
      '/targets/save',
      {
        name: form.name.trim(),
        target: formToTarget(form),
        default: form.isDefault
      },
      showList
    )

  const test = () =>
    ask(
      '/targets/test',
      { name: form.name.trim(), target: formToTarget(form) },
      answer => setNote(describeTest(answer))
    )

  const names = Object.keys(settings?.targets ?? {})

  return jsxs('div', {
    className: 'flex max-w-xl flex-col gap-3 text-[0.8125rem]',
    'data-onsite-machines': true,
    children: [
      jsx('div', {
        className: TEXT_MUTED,
        children:
          'The machines a session can run on. A session picks one from ' +
          'the folder it is opened in.'
      }),
      names.map(name =>
        jsx(
          MachineRow,
          {
            name,
            block: settings.targets[name],
            isDefault: settings.default === name,
            busy,
            onEdit: () => {
              setNote('')
              setForm(
                machineToForm(name, settings.targets[name], settings.default === name)
              )
            },
            onRemove: () => ask('/targets/remove', { name }, showList)
          },
          name
        )
      ),
      form
        ? jsx(MachineForm, {
            form,
            note,
            busy,
            onChange: change,
            onSave: save,
            onTest: test,
            onCancel: () => {
              setForm(null)
              setNote('')
            }
          })
        : jsx('button', {
            type: 'button',
            className: joinClasses(BUTTON_CLASS, 'self-start'),
            'data-onsite-add': true,
            disabled: busy,
            onClick: () => {
              setNote('')
              setForm({ ...EMPTY_MACHINE, isDefault: names.length === 0 })
            },
            children: 'Add a machine'
          }),
      !form && note
        ? jsx('div', { className: TEXT_MUTED, role: 'status', children: note })
        : null
    ]
  })
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

export default {
  id: ID,
  name: NAME,
  description:
    'Shows where a session runs, opens folders on your machines and ' +
    'runs the commands the agent proposes.',
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
      render: () => jsx(OnsiteChip, {})
    })

    // 2. COMMANDS PANE
    ctx.register({
      id: PANE_ID,
      area: AREA_PANES,
      title: 'onsite',
      data: { placement: 'right', width: PANE_WIDTH },
      render: () => jsx(OnsitePane, {})
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

    // 5. MACHINES PAGE. Older apps have no settings pages; the
    // machines can still be set up with `hermes onsite init`.
    ctx.registerSettingsPage?.({
      id: SETTINGS_PAGE_ID,
      title: SETTINGS_PAGE_TITLE,
      icon: SETTINGS_PAGE_ICON,
      render: () => jsx(MachinesPage, {})
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
