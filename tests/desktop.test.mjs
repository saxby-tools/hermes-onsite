/**
 * Tests for desktop/plugin.js, run with `node --test`.
 *
 * The file is loaded the way Hermes Desktop loads it: as plain ESM
 * whose three allowed imports are rewritten to stand-ins. React is
 * the real one and the page is a real DOM (jsdom). The SDK and the
 * host are fakes, so this shows the plugin's own logic is right. It
 * does not show the plugin inside the real app.
 *
 * React, react-dom and jsdom are borrowed from a Hermes checkout so
 * the repo needs no node_modules. Set HERMES_SRC to point at one.
 * Without it the tests are skipped, not failed.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUGIN_PATH = path.join(HERE, '..', 'onsite', 'desktop', 'plugin.js')

const DEFAULT_HERMES_SRC = path.join(os.homedir(), '.hermes', 'hermes-agent')
const HERMES_SRC = process.env.HERMES_SRC || DEFAULT_HERMES_SRC
const PNPM_DIR = path.join(HERMES_SRC, 'node_modules', '.pnpm')

const COPIED_RESET_MS = 1600

// What the plugin starts each of its console lines with, and the
// console levels it writes them at.
const LOG_TAG = '[onsite]'
const LOG_LEVELS = ['info', 'debug', 'warn']

const ALLOWED_IMPORTS = ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime']
const IMPORT_RE = /(from\s*)(['"])([^'"]+)\2/g

const UP = {
  ok: true,
  target: 'laptop',
  kind: 'ssh',
  label: 'laptop (dev@laptop.example)',
  connected: true,
  detail: '',
  latency_ms: 41,
  cwd: '/home/dev/Development/app',
  reason: 'path_map',
  rows: [
    { name: 'laptop', label: 'laptop (dev@laptop.example)', connected: true },
    { name: 'mac', label: 'mac (Hermes server)', connected: true, default: true }
  ]
}

const DOWN = { ...UP, connected: false, detail: 'connection refused' }

const PROPOSAL = {
  number: 1,
  command: 'echo hi > note.txt',
  why: 'Leaves a note.',
  status: 'pending'
}

function findPackage(prefix, name) {
  if (!fs.existsSync(PNPM_DIR)) {
    return null
  }

  const folder = fs.readdirSync(PNPM_DIR).find(entry => entry.startsWith(prefix))

  if (!folder) {
    return null
  }

  return path.join(PNPM_DIR, folder, 'node_modules', name)
}

const REACT_DOM = findPackage('react-dom@', 'react-dom')
const JSDOM = findPackage('jsdom@', 'jsdom')
const MISSING = !REACT_DOM || !JSDOM
const SKIP = MISSING ? `no Hermes checkout with React and jsdom at ${HERMES_SRC}` : false

function installDom(require) {
  const { JSDOM: Dom } = require(JSDOM)
  const dom = new Dom('<!doctype html><html><body></body></html>', {
    pretendToBeVisual: true
  })

  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true

  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: dom.window.navigator
  })

  return dom
}

// Keeps the plugin's console lines out of the test report and hands
// them to the test instead. Any other line is written as before.
function catchLogs() {
  const logs = []

  for (const level of LOG_LEVELS) {
    const write = console[level].wrappedByOnsiteTests ?? console[level]

    console[level] = (...parts) => {
      if (parts[0] !== LOG_TAG) {
        write.apply(console, parts)

        return
      }

      logs.push([level, ...parts.slice(1)])
    }

    console[level].wrappedByOnsiteTests = write
  }

  return logs
}

function atom(initial) {
  let value = initial

  return {
    get: () => value,
    set: next => {
      value = next
    }
  }
}

function createHost() {
  const calls = { submit: [], notify: [], reveal: [], rpc: [], opened: [] }

  const host = {
    state: {
      cwd: atom('/Users/dev/Development/app'),
      focusedStoredSessionId: atom('stored-1'),
      focusedSessionId: atom('runtime-1'),
      activeSessionId: atom('')
    },
    composer: {
      submit: (sessionId, text) => {
        calls.submit.push([sessionId, text])

        return true
      }
    },
    notify: message => calls.notify.push(message),
    revealPane: id => calls.reveal.push(id),
    request: async (method, params) => {
      calls.rpc.push([method, params])

      return { session_id: 'runtime-new', stored_session_id: 'stored-new' }
    },
    openSession: async (id, options) => {
      calls.opened.push([id, options])
    }
  }

  return { host, calls }
}

// Writes the plugin beside its stand-ins and imports it from there,
// which is what the real loader does with blob URLs.
async function loadPlugin(require, host) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'onsite-desktop-'))
  const reactPath = require.resolve('react')
  const jsxPath = require.resolve('react/jsx-runtime')

  globalThis.__ONSITE_TEST_HOST__ = host

  const shims = {
    '@hermes/plugin-sdk': [
      'export const host = globalThis.__ONSITE_TEST_HOST__',
      "export const cn = (...parts) => parts.filter(Boolean).join(' ')",
      ''
    ].join('\n'),
    react: [
      `import React from ${JSON.stringify(pathToFileURL(reactPath).href)}`,
      'export const { useEffect, useState } = React',
      'export default React',
      ''
    ].join('\n'),
    'react/jsx-runtime': [
      `import runtime from ${JSON.stringify(pathToFileURL(jsxPath).href)}`,
      'export const { jsx, jsxs } = runtime',
      ''
    ].join('\n')
  }

  const urls = {}

  for (const [index, name] of ALLOWED_IMPORTS.entries()) {
    const file = path.join(folder, `shim-${index}.mjs`)

    fs.writeFileSync(file, shims[name])
    urls[name] = pathToFileURL(file).href
  }

  const source = fs.readFileSync(PLUGIN_PATH, 'utf8')
  const rewritten = source.replace(IMPORT_RE, (whole, pre, quote, spec) => {
    if (!urls[spec]) {
      return whole
    }

    return `${pre}${quote}${urls[spec]}${quote}`
  })

  const target = path.join(folder, 'plugin.mjs')

  fs.writeFileSync(target, rewritten)

  return (await import(pathToFileURL(target).href)).default
}

function createContext(backend) {
  const made = {
    contributions: {}, intervals: [], events: {}, copied: [], requests: [],
    pages: [], stopped: []
  }

  const ctx = {
    register: item => {
      made.contributions[item.id] = item
    },
    rest: async (route, options) => {
      made.requests.push([route, options])

      if (backend.fail) {
        throw new Error(backend.fail)
      }

      if (route === '/status') {
        return backend.status
      }

      if (backend.routes[route]) {
        return backend.routes[route](options?.body)
      }

      return { items: backend.proposals }
    },
    registerSettingsPage: page => {
      made.pages.push(page)
    },
    setInterval: fn => {
      made.intervals.push(fn)

      return () => made.stopped.push(fn)
    },
    onEvent: (type, listener) => {
      made.events[type] = listener

      return () => {}
    },
    onDispose: () => {},
    os: {
      writeClipboard: async text => {
        made.copied.push(text)

        return true
      }
    }
  }

  return { ctx, made }
}

async function setup() {
  const require = createRequire(path.join(REACT_DOM, 'package.json'))

  installDom(require)

  const logs = catchLogs()
  const React = require('react')
  const { createRoot } = require('react-dom/client')
  const { host, calls } = createHost()
  const plugin = await loadPlugin(require, host)
  const backend = { status: UP, proposals: [], fail: '', routes: {} }
  const { ctx, made } = createContext(backend)

  const mount = async node => {
    const box = document.createElement('div')

    document.body.appendChild(box)

    await React.act(async () => {
      createRoot(box).render(node)
    })

    return box
  }

  const settle = async work => {
    await React.act(async () => {
      await work?.()
      await new Promise(resolve => setTimeout(resolve, 0))
    })
  }

  const click = async element => {
    await React.act(async () => {
      element.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
  }

  // React reads a field's value through its own setter, so the
  // element's native one is used and a real event is sent after it.
  const choose = async (element, value) => {
    const owner = Object.getPrototypeOf(element)
    const setValue = Object.getOwnPropertyDescriptor(owner, 'value').set

    await React.act(async () => {
      setValue.call(element, value)
      element.dispatchEvent(new window.Event('change', { bubbles: true }))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
  }

  await settle(() => plugin.register(ctx))

  return {
    React, backend, calls, choose, click, ctx, host, logs, made, mount,
    plugin, settle
  }
}

test('the file imports only what the desktop loader allows', () => {
  const source = fs.readFileSync(PLUGIN_PATH, 'utf8')
  const imports = [...source.matchAll(/^import .* from '([^']+)'$/gm)].map(m => m[1])

  assert.deepEqual(imports.sort(), [...ALLOWED_IMPORTS].sort())
  assert.equal(/\bimport\s*\(/.test(source.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')), false)
  assert.equal(/<[A-Z][A-Za-z]*[\s/>]/.test(source), false, 'no JSX syntax')
})

test('it registers a chip, a pane and a run card', { skip: SKIP }, async () => {
  const { made, plugin } = await setup()

  assert.equal(plugin.id, 'onsite')
  assert.equal(made.contributions.chip.area, 'statusBar.right')
  assert.equal(made.contributions.pane.area, 'panes')
  assert.equal(made.contributions['run-card'].area, 'transcript.directives')
  assert.equal(made.contributions['run-card'].data.name, 'onsite-run')
  assert.equal(made.intervals.length, 1)
})

test('it asks the host with a POST body, not a query string', { skip: SKIP }, async () => {
  const { made } = await setup()
  const [route, options] = made.requests[0]

  assert.equal(route, '/status')
  assert.equal(options.method, 'POST')
  assert.equal(options.body.cwd, '/Users/dev/Development/app')
  assert.deepEqual(options.body.session_ids, ['stored-1', 'runtime-1'])
})

test('the chip changes state when the target goes down and comes back', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())
  const chip = () => box.querySelector('[data-onsite-chip]')

  assert.equal(chip().getAttribute('data-onsite-chip'), 'up')
  assert.match(chip().textContent, /onsite: laptop/)
  assert.match(chip().textContent, /Development\/app/)
  assert.match(chip().getAttribute('title'), /connected \(41 ms\)/)

  backend.status = DOWN
  await settle(made.intervals[0])

  assert.equal(chip().getAttribute('data-onsite-chip'), 'down')
  assert.match(chip().getAttribute('title'), /not reachable \(connection refused\)/)

  backend.status = UP
  await settle(made.intervals[0])

  assert.equal(chip().getAttribute('data-onsite-chip'), 'up')
})

test('the chip says why a session\'s files cannot be shown', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())
  const chip = () => box.querySelector('[data-onsite-chip]')

  assert.doesNotMatch(chip().getAttribute('title'), /Files:/)

  backend.status = { ...UP, mount_error: 'this server has no `sshfs`.' }
  await settle(made.intervals[0])

  assert.match(chip().getAttribute('title'), /Files: this server has no `sshfs`\./)
})

test('the chip says so when the host has no plugin', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  backend.fail = 'HTTP 404'
  await settle(made.intervals[0])

  const chip = box.querySelector('[data-onsite-chip]')

  assert.equal(chip.getAttribute('data-onsite-chip'), 'down')
  assert.match(chip.textContent, /not set up/)
  assert.match(chip.getAttribute('title'), /no onsite plugin enabled/)
})

test('a server that does not answer is not called "not set up"', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  backend.fail = 'timed out after 8000 ms'
  await settle(made.intervals[0])

  const chip = box.querySelector('[data-onsite-chip]')

  assert.match(chip.textContent, /no answer/)
  assert.doesNotMatch(chip.textContent, /not set up/)
  assert.match(chip.title, /did not answer onsite: timed out/)
})

test('the chip names the profile that has no plugin', { skip: SKIP }, async () => {
  const { backend, host, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  // A gateway that serves several profiles: the app is still on the
  // default one, which does not have the plugin.
  host.state.profile = atom('default')
  backend.fail = 'HTTP 404'
  await settle(made.intervals[0])

  const title = box.querySelector('[data-onsite-chip]').getAttribute('title')

  assert.match(title, /asked the "default" profile/)
  assert.match(title, /no onsite plugin enabled/)
})

test('the chip says when the chat in view is another profile\'s', { skip: SKIP }, async () => {
  const { backend, host, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  // The Bots tab: the chat is the test profile's, but the app's
  // requests still go to the default one.
  host.state.profile = atom('default')
  host.state.focusedSessionProfile = atom('buster')
  backend.fail = 'HTTP 404'
  await settle(made.intervals[0])

  const title = box.querySelector('[data-onsite-chip]').getAttribute('title')

  assert.match(title, /This chat belongs to the "buster" profile/)
  assert.match(title, /asked the "default" profile/)
  assert.match(title, /Pick "buster" in the Sessions tab/)
})

test('clicking the chip opens the pane', { skip: SKIP }, async () => {
  const { calls, click, made, mount } = await setup()
  const box = await mount(made.contributions.chip.render())

  await click(box.querySelector('[data-onsite-chip]'))

  assert.deepEqual(calls.reveal, ['onsite:pane'])
})

test('the machine list is asked for only while the pane is open', { skip: SKIP }, async () => {
  const { made, mount, settle } = await setup()
  const asked = () => made.requests.filter(([route]) => route === '/status')

  assert.equal(asked()[0][1].body.rows, false)

  await mount(made.contributions.pane.render())
  await settle()

  assert.equal(asked()[asked().length - 1][1].body.rows, true)
})

test('the pane keeps its machine list when an answer comes without one', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.pane.render())

  await settle()

  const { rows, ...bare } = UP

  backend.status = bare
  await settle(made.intervals[0])

  assert.equal(rows.length, 2)
  assert.match(box.textContent, /mac \(Hermes server\)/)
})

test('the pane paints the same surface as the panes beside it', { skip: SKIP }, async () => {
  const { made, mount, settle } = await setup()
  const box = await mount(made.contributions.pane.render())

  await settle()

  const pane = box.querySelector('[data-onsite-pane]')

  assert.match(pane.className, /bg-\(--ui-sidebar-surface-background\)/)
})

test('a window out of sight does not ask the host', { skip: SKIP }, async () => {
  const { made, settle } = await setup()
  let state = 'hidden'

  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state
  })

  const before = made.requests.length

  await settle(made.intervals[0])
  await settle(made.events['tool.complete'])

  assert.equal(made.requests.length, before)

  state = 'visible'
  await settle(made.intervals[0])

  assert.ok(made.requests.length > before)
})

test('a host that does not allow /run greys Run out and says how to allow it', { skip: SKIP }, async () => {
  const { backend, calls, click, made, mount, settle } = await setup()

  backend.status = { ...UP, run_allowed: false }
  backend.proposals = [PROPOSAL]
  await settle(made.intervals[0])

  const box = await mount(made.contributions.pane.render())
  const run = box.querySelector('[data-action="run"]')
  const note = box.querySelector('[data-onsite-run-off]')

  assert.equal(run.disabled, true)
  assert.match(note.textContent, /Run is off for this profile/)
  assert.match(note.textContent, /allow_gateway_injection true/)

  await click(run)

  assert.deepEqual(calls.submit, [])
  assert.ok(box.querySelector('[data-action="copy"]'))
})

test('a host that says nothing about /run leaves Run as it was', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()

  backend.proposals = [PROPOSAL]
  await settle(made.intervals[0])

  const box = await mount(made.contributions.pane.render())

  assert.equal(box.querySelector('[data-action="run"]').disabled, false)
  assert.equal(box.querySelector('[data-onsite-run-off]'), null)
})

test('the pane lists proposed commands and Run sends /run N', { skip: SKIP }, async () => {
  const { backend, calls, click, made, mount, settle } = await setup()

  backend.proposals = [PROPOSAL, { ...PROPOSAL, number: 2, status: 'sent' }]
  await settle(made.intervals[0])

  const box = await mount(made.contributions.pane.render())
  const blocks = box.querySelectorAll('[data-proposal]')

  assert.equal(blocks.length, 1, 'only pending commands are listed')
  assert.match(blocks[0].textContent, /echo hi > note\.txt/)
  assert.match(blocks[0].textContent, /Leaves a note\./)
  assert.match(box.textContent, /laptop \(dev@laptop\.example\)/)

  const run = blocks[0].querySelector('[data-action="run"]')

  await click(run)

  assert.deepEqual(calls.submit, [['stored-1', '/run 1']])
  assert.equal(run.textContent, 'Sent')
  assert.equal(run.disabled, true)
})

test('Copy puts the exact command on the clipboard', { skip: SKIP }, async () => {
  const { backend, click, made, mount, settle } = await setup()

  backend.proposals = [PROPOSAL]
  await settle(made.intervals[0])

  const box = await mount(made.contributions.pane.render())

  const copy = box.querySelector('[data-action="copy"]')

  await click(copy)

  assert.deepEqual(made.copied, ['echo hi > note.txt'])
  assert.equal(copy.textContent, 'Copied')

  // The label resets on a timer. Waiting it out keeps the reset
  // inside the test instead of firing after it.
  await settle(() => new Promise(resolve => setTimeout(resolve, COPIED_RESET_MS)))

  assert.equal(copy.textContent, 'Copy')
})

test('the run card runs in the chat it was drawn in', { skip: SKIP }, async () => {
  const { backend, calls, click, made, mount, settle } = await setup()

  backend.proposals = [PROPOSAL]
  await settle(made.intervals[0])

  const render = made.contributions['run-card'].data.render
  const box = await mount(render({ attrs: { n: '1' }, sessionId: 'chat-7', streaming: false }))

  assert.match(box.textContent, /Proposed command #1/)
  assert.match(box.textContent, /echo hi > note\.txt/)

  await click(box.querySelector('[data-action="run"]'))

  assert.deepEqual(calls.submit, [['chat-7', '/run 1']])
})

test('a run card whose command was sent stays sent after a reload', { skip: SKIP }, async () => {
  const { backend, calls, click, made, mount, settle } = await setup()

  backend.proposals = [{ ...PROPOSAL, status: 'sent' }]
  await settle(made.intervals[0])

  const render = made.contributions['run-card'].data.render
  const box = await mount(render({ attrs: { n: '1' }, sessionId: 'chat-7', streaming: false }))
  const button = box.querySelector('[data-action="run"]')

  assert.equal(button.textContent, 'Sent')
  assert.equal(button.disabled, true)

  await click(button)

  assert.deepEqual(calls.submit, [])
})

test('the run card works on an app that sends no session id', { skip: SKIP }, async () => {
  const { calls, click, made, mount } = await setup()
  const render = made.contributions['run-card'].data.render
  const box = await mount(render({ attrs: { n: '3' }, streaming: false }))

  await click(box.querySelector('[data-action="run"]'))

  assert.deepEqual(calls.submit, [[null, '/run 3']])
})

test('the run card ignores attributes that are not a plain number', { skip: SKIP }, async () => {
  const { made, mount } = await setup()
  const render = made.contributions['run-card'].data.render

  for (const n of ['1; rm -rf /', '-1', '', 'one', '1234567']) {
    const box = await mount(render({ attrs: { n }, sessionId: null, streaming: false }))

    assert.equal(box.querySelector('[data-proposal]'), null, `n=${n}`)
  }
})

test('a failed send tells the user what to type instead', { skip: SKIP }, async () => {
  const { calls, click, made, mount } = await setup()
  const render = made.contributions['run-card'].data.render
  const box = await mount(render({ attrs: { n: '4' }, sessionId: 'gone', streaming: false }))

  globalThis.__ONSITE_TEST_HOST__.composer.submit = () => false

  const run = box.querySelector('[data-action="run"]')

  await click(run)

  assert.equal(run.textContent, 'Run')
  assert.match(calls.notify[0].message, /Type \/run 4/)
})

/* ------------------------------------------------------------------ */
/* 4. MACHINES IN THE PANE                                             */
/* ------------------------------------------------------------------ */

const TWO_MACHINES = {
  ...UP,
  rows: [
    {
      name: 'laptop',
      kind: 'ssh',
      label: 'laptop (dev@laptop.example)',
      connected: true,
      mount_error: ''
    },
    {
      name: 'mac',
      kind: 'local',
      label: 'mac (Hermes server)',
      connected: true,
      mount_error: ''
    }
  ]
}

const MOUNT = '/host/.local/share/onsite/mount/work/laptop'

async function openPaneMachines() {
  const made = await setup()

  made.backend.status = TWO_MACHINES
  made.backend.routes['/open'] = body => ({
    ok: true,
    target: body.target,
    cwd: `${MOUNT}/home/dev`,
    path: '/home/dev'
  })

  await made.settle(made.made.intervals[0])

  const box = await made.mount(made.made.contributions.pane.render())

  return { ...made, box }
}

test('only a machine reached over SSH gets a New session button', { skip: SKIP }, async () => {
  const { box } = await openPaneMachines()
  const buttons = [...box.querySelectorAll('[data-onsite-new-session]')]

  assert.deepEqual(
    buttons.map(button => button.getAttribute('data-onsite-new-session')),
    ['laptop']
  )
  assert.equal(buttons[0].textContent, 'New session')
  assert.equal(box.querySelectorAll('[data-onsite-machine-row]').length, 2)
})

test('the pane has no folder list of its own', { skip: SKIP }, async () => {
  const { box } = await openPaneMachines()

  assert.equal(box.querySelector('[data-onsite-browser]'), null)
  assert.equal(box.querySelector('select'), null)
  assert.doesNotMatch(box.textContent, /Open a folder|Browse/)
})

function sessionCalls(calls) {
  return calls.rpc.filter(([method]) => method.startsWith('session.'))
}

function projectCalls(calls) {
  return calls.rpc.filter(([method]) => method.startsWith('projects.'))
}

test('New session opens the machine where a login lands, on its mount', { skip: SKIP }, async () => {
  const { box, calls, click, made } = await openPaneMachines()

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const asked = made.requests.filter(([route]) => route === '/open')

  assert.deepEqual(asked.map(([, options]) => options.body), [{ target: 'laptop' }])
  assert.deepEqual(sessionCalls(calls), [
    ['session.create', { cwd: `${MOUNT}/home/dev`, cwd_explicit: true }],
    ['session.list', { title: 'dev on laptop' }],
    ['session.title', { session_id: 'runtime-new', title: 'dev on laptop' }]
  ])
  assert.deepEqual(calls.opened, [['stored-new', { expectHistory: false }]])
})

test('New session files the machine under the app\'s own Projects', { skip: SKIP }, async () => {
  const { box, calls, click } = await openPaneMachines()

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  assert.deepEqual(projectCalls(calls), [
    [
      'projects.create',
      { name: 'laptop', folders: [`${MOUNT}/home/dev`], primary_path: `${MOUNT}/home/dev` }
    ]
  ])
})

test('a project Hermes refuses does not stop the session', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const answer = host.request

  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method.startsWith('projects.')) {
      throw new Error("folder already belongs to project 'mine'")
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  assert.equal(calls.opened.length, 1)
  assert.equal(box.querySelector('[role="alert"]'), null)
})

// The steps the plugin wrote at one console level, with the time each
// took replaced, because that differs from run to run.
function stepsAt(logs, level) {
  return logs
    .filter(([at]) => at === level)
    .map(([, step]) => step.replace(/\d+ ms/, 'N ms'))
}

test('New session writes each step to the console, in order', { skip: SKIP }, async () => {
  const { box, click, logs } = await openPaneMachines()
  const before = logs.length

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const steps = stepsAt(logs.slice(before), 'info')
  const flow = steps.filter(step => !step.includes('after opening'))

  assert.deepEqual(flow, [
    'New session: clicked',
    'the app shows, before the click',
    'host /open: asking',
    'host /open: answered in N ms',
    'hermes projects.create: asking',
    'hermes projects.create: answered in N ms',
    'hermes session.create: asking',
    'hermes session.create: answered in N ms',
    'hermes session.title: asking',
    'hermes session.title: answered in N ms',
    'app openSession: asking',
    'app openSession: answered in N ms',
    'New session: finished'
  ])
})

test('after a session opens, the console says what the app shows', { skip: SKIP }, async () => {
  const { box, click, logs, settle } = await openPaneMachines()

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))
  await settle()

  const shown = logs.find(([, step]) => step === 'the app shows, 0 ms after opening')

  assert.ok(shown, 'a line right after the session opened')
  assert.deepEqual(shown[2], {
    cwd: '/Users/dev/Development/app',
    session_ids: ['stored-1', 'runtime-1'],
    profile: '',
    focused_profile: ''
  })
})

test('a refusal from Hermes is written to the console as a warning', { skip: SKIP }, async () => {
  const { box, click, host, logs } = await openPaneMachines()
  const answer = host.request

  host.request = async (method, params) => {
    if (method.startsWith('projects.')) {
      throw new Error('projects are turned off')
    }

    return answer(method, params)
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const warnings = logs.filter(([at]) => at === 'warn')

  assert.equal(warnings.length, 1)
  assert.match(warnings[0][1], /^hermes projects\.create: failed after \d+ ms$/)
  assert.equal(warnings[0][2], 'projects are turned off')
})

test('a folder that already has a project is not written as a fault', { skip: SKIP }, async () => {
  const { box, calls, click, host, logs } = await openPaneMachines()
  const answer = host.request

  host.request = async (method, params) => {
    if (method.startsWith('projects.')) {
      throw new Error("folder already belongs to project 'mine'")
    }

    return answer(method, params)
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const said = 'hermes projects.create: the folder already has a project'

  assert.equal(logs.filter(([at]) => at === 'warn').length, 0)
  assert.ok(stepsAt(logs, 'info').includes(said))
  assert.equal(calls.opened.length, 1)
})

test('a second click while a session is starting does not start another', { skip: SKIP }, async () => {
  const { React, box, calls, logs, settle } = await openPaneMachines()
  const button = box.querySelector('[data-onsite-new-session="laptop"]')

  // Both clicks land before React draws the button greyed out.
  await React.act(async () => {
    button.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    button.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
  })
  await settle()

  const created = sessionCalls(calls).filter(([method]) => method === 'session.create')

  assert.equal(created.length, 1)
  assert.ok(stepsAt(logs, 'info').includes('New session: already starting, click ignored'))
})

test('the poll writes at the level the inspector hides', { skip: SKIP }, async () => {
  const { logs } = await setup()
  const loud = stepsAt(logs, 'info').filter(step => step.startsWith('host /'))
  const quiet = stepsAt(logs, 'debug')

  assert.deepEqual(loud, [])
  assert.ok(quiet.includes('host /status: asking'))
  assert.ok(quiet.includes('host /proposals: answered in N ms'))
})

test('the console says when a session moves, not on every poll', { skip: SKIP }, async () => {
  const { backend, logs, made, settle } = await setup()
  const changes = () => logs.filter(([, step]) => step === 'status changed')

  assert.equal(changes().length, 1)

  await settle(made.intervals[0])
  await settle(made.intervals[0])

  assert.equal(changes().length, 1)

  backend.status = DOWN
  await settle(made.intervals[0])

  assert.equal(changes().length, 2)
  assert.equal(changes()[1][2].connected, false)
})

test('loading the plugin says which calls this app has', { skip: SKIP }, async () => {
  const { logs } = await setup()
  const loaded = logs.find(([, step]) => step === 'loaded')

  assert.deepEqual(loaded[2], {
    can_ask_hermes: true,
    can_open_sessions: true,
    has_settings_pages: true,
    has_events: true
  })
})

test('a second session on a machine gets a title that is free', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const taken = new Set(['dev on laptop', 'dev on laptop 2'])
  const answer = host.request

  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method === 'session.title' && taken.has(params.title)) {
      throw new Error(`Title '${params.title}' is already in use by session 1`)
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const titles = calls.rpc.filter(([method]) => method === 'session.title')

  assert.equal(titles[titles.length - 1][1].title, 'dev on laptop 3')
  assert.deepEqual(calls.opened, [['stored-new', { expectHistory: false }]])
  assert.equal(box.querySelector('[role="alert"]'), null)
})

test('the number for a new session is looked up, not tried in turn', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const taken = new Set(['dev on laptop'])
  const answer = host.request

  for (let number = 2; number <= 20; number += 1) {
    taken.add(`dev on laptop ${number}`)
  }

  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method === 'session.list') {
      return { sessions: taken.has(params.title) ? [{ id: 'old' }] : [] }
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const titles = calls.rpc.filter(([method]) => method === 'session.title')
  const lookups = calls.rpc.filter(([method]) => method === 'session.list')

  assert.deepEqual(titles.map(([, params]) => params.title), ['dev on laptop 21'])
  assert.ok(lookups.length <= 10, `${lookups.length} lookups`)
  assert.equal(box.querySelector('[role="alert"]'), null)
})

test('titles the lookup cannot see are still got past', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const archived = new Set(['dev on laptop'])
  const answer = host.request

  for (let number = 2; number <= 12; number += 1) {
    archived.add(`dev on laptop ${number}`)
  }

  // Hermes' lookup leaves an archived session out, and its title is
  // refused all the same.
  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method === 'session.list') {
      return { sessions: [] }
    }

    if (method === 'session.title' && archived.has(params.title)) {
      throw new Error(`Title '${params.title}' is already in use by session 1`)
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const titles = calls.rpc.filter(([method]) => method === 'session.title')

  assert.equal(titles[titles.length - 1][1].title, 'dev on laptop 13')
  assert.equal(calls.opened.length, 1)
  assert.equal(box.querySelector('[role="alert"]'), null)
})

test('an id with 404 in it is not read as a missing plugin', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  backend.fail = 'session 20261127_044021_3d3f87 is busy'
  await settle(made.intervals[0])

  const chip = box.querySelector('[data-onsite-chip]')

  assert.match(chip.textContent, /no answer/)
})

test('the pane says so when the server did not answer', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.pane.render())

  backend.fail = 'timed out after 8000 ms'
  await settle(made.intervals[0])

  assert.match(box.textContent, /did not answer onsite: timed out/)
})

test('a Hermes that cannot look titles up is tried from the first', { skip: SKIP }, async () => {
  const { box, calls, click, host, logs } = await openPaneMachines()
  const answer = host.request

  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method === 'session.list') {
      throw new Error('unknown method')
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const titles = calls.rpc.filter(([method]) => method === 'session.title')

  assert.deepEqual(titles.map(([, params]) => params.title), ['dev on laptop'])
  assert.equal(calls.opened.length, 1)
  assert.ok(stepsAt(logs, 'warn').includes('could not look for a free title'))
})

test('a title refused for another reason is said, not retried', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const answer = host.request

  host.request = async (method, params) => {
    const made = await answer(method, params)

    if (method === 'session.title') {
      throw new Error('title required')
    }

    return made
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  const titles = calls.rpc.filter(([method]) => method === 'session.title')

  assert.equal(titles.length, 1)
  assert.equal(calls.opened.length, 0)
  assert.match(box.querySelector('[role="alert"]').textContent, /title required/)
})

test('a session is started in the profile the app is on', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()

  host.state.profile = atom('buster')

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  assert.equal(calls.rpc[0][1].profile, 'buster')
})

// The app keeps its "show every profile" switch where a page keeps
// small settings. The test page has none unless one is put there.
function withAllProfilesSwitch(value) {
  globalThis.localStorage = { getItem: () => value }

  return () => {
    delete globalThis.localStorage
  }
}

test('on a named profile the app is told whose session it opens', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()

  host.state.profile = atom('buster')

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  // Without the profile, and with the list on that profile alone, the
  // real app empties its file tree and never fills it again.
  assert.deepEqual(calls.opened, [
    ['stored-new', { expectHistory: false, profile: 'buster', keepAllProfilesScope: true }]
  ])
})

test('on the default profile the list of profiles is left as it was', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()
  const button = box.querySelector('[data-onsite-new-session="laptop"]')

  host.state.profile = atom('default')

  await click(button)

  const putBack = withAllProfilesSwitch('true')

  await click(button)
  putBack()

  assert.deepEqual(calls.opened.map(([, how]) => how), [
    { expectHistory: false, profile: 'default', keepAllProfilesScope: false },
    { expectHistory: false, profile: 'default', keepAllProfilesScope: true }
  ])
})

test('a page that will not let its settings be read still opens the session', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openPaneMachines()

  host.state.profile = atom('default')
  globalThis.localStorage = {
    getItem: () => {
      throw new Error('denied')
    }
  }

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))
  delete globalThis.localStorage

  assert.equal(calls.opened.length, 1)
  assert.equal(calls.opened[0][1].keepAllProfilesScope, false)
})

test('an app that cannot start sessions is given the folder to open', { skip: SKIP }, async () => {
  const { box, calls, click, host, made } = await openPaneMachines()

  delete host.request

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  assert.deepEqual(made.copied, [`${MOUNT}/home/dev`])
  assert.equal(calls.opened.length, 0)
  assert.match(JSON.stringify(calls.notify), /Open this folder to work there/)
})

test('a machine that cannot be opened says why', { skip: SKIP }, async () => {
  const { backend, box, calls, click } = await openPaneMachines()

  backend.routes['/open'] = () => ({ ok: false, error: 'Connection refused' })

  await click(box.querySelector('[data-onsite-new-session="laptop"]'))

  assert.equal(box.querySelector('[role="alert"]').textContent, 'Connection refused')
  assert.deepEqual(calls.rpc, [])
})

test('a disk that could not be mounted is said beside its machine', { skip: SKIP }, async () => {
  const made = await setup()
  const rows = TWO_MACHINES.rows.map(row => ({ ...row }))

  rows[0].mount_error =
    'laptop cannot be mounted: this server has no `sshfs`. ' +
    'Install it, then try again: sudo apt install sshfs'
  made.backend.status = { ...TWO_MACHINES, rows }

  await made.settle(made.made.intervals[0])

  const box = await made.mount(made.made.contributions.pane.render())
  const row = box.querySelector('[data-onsite-machine-row="laptop"]')

  assert.match(row.querySelector('[role="alert"]').textContent, /sudo apt install sshfs/)
  assert.equal(
    box.querySelector('[data-onsite-machine-row="mac"] [role="alert"]'),
    null
  )
})

/* ------------------------------------------------------------------ */
/* 5. MACHINES PAGE                                                    */
/* ------------------------------------------------------------------ */

const SAVED = {
  ok: true,
  default: 'mac',
  problem: '',
  targets: {
    laptop: {
      kind: 'ssh',
      host: 'laptop.example',
      user: 'dev',
      prefixes: ['/home/dev/'],
      path_map: { '/Users/dev/Development': '/home/dev/Development' }
    },
    mac: { kind: 'local' }
  }
}

async function openMachines() {
  const made = await setup()

  made.backend.routes['/targets'] = () => SAVED
  made.backend.routes['/targets/save'] = () => SAVED
  made.backend.routes['/targets/remove'] = () => SAVED
  made.backend.routes['/targets/test'] = () => ({
    ok: true,
    connected: true,
    detail: '',
    latency_ms: 41
  })

  const page = made.made.pages[0]
  const machines = page.children[0]
  const box = await made.mount(machines.render())

  await made.settle()

  return { ...made, box, machines, page }
}

test('it adds one entry to Settings, named for the plugin', { skip: SKIP }, async () => {
  const { page } = await openMachines()

  assert.equal(page.id, 'onsite')
  assert.equal(page.title, 'Onsite')
})

test('Machines is a page under that entry', { skip: SKIP }, async () => {
  const { machines, page } = await openMachines()

  assert.equal(page.children.length, 1)
  assert.equal(machines.id, 'machines')
  assert.equal(machines.title, 'Machines')
})

test('the entry itself opens on a summary of the machines', { skip: SKIP }, async () => {
  const { mount, page, settle } = await openMachines()
  const home = await mount(page.render())

  await settle()

  const rows = [...home.querySelectorAll('[data-onsite-summary]')]

  assert.deepEqual(rows.map(row => row.getAttribute('data-onsite-summary')), ['laptop', 'mac'])
  assert.match(rows[1].textContent, /Hermes server.*\(default\)/)
  assert.equal(home.querySelector('[data-onsite-edit]'), null)
  assert.match(home.textContent, /on the Machines page/)
})

test('each settings page names the profile it really edits', { skip: SKIP }, async () => {
  const { host, machines, mount, page, settle } = await openMachines()

  host.state.profile = atom('buster')

  const home = await mount(page.render())
  const list = await mount(machines.render())

  await settle()

  for (const box of [home, list]) {
    const note = box.querySelector('[data-onsite-profile-note]')

    assert.match(note.textContent, /settings of the "buster" profile/)
    assert.match(note.textContent, /"Applies to" row in Settings does not change this page/)
  }
})

test('with no profile known, the page still says whose settings these are', { skip: SKIP }, async () => {
  const { box } = await openMachines()
  const note = box.querySelector('[data-onsite-profile-note]')

  assert.match(note.textContent, /profile the app is using now/)
})

test('the page lists the machines in the settings', { skip: SKIP }, async () => {
  const { box } = await openMachines()
  const rows = [...box.querySelectorAll('[data-onsite-machine]')]

  assert.deepEqual(rows.map(row => row.getAttribute('data-onsite-machine')), ['laptop', 'mac'])
  assert.match(rows[0].textContent, /dev@laptop\.example/)
  assert.match(rows[1].textContent, /Hermes server.*\(default\)/)
})

test('the default machine cannot be removed from the page', { skip: SKIP }, async () => {
  const { box } = await openMachines()

  assert.equal(box.querySelector('[data-onsite-remove="mac"]').disabled, true)
  assert.equal(box.querySelector('[data-onsite-remove="laptop"]').disabled, false)
})

test('saving keeps the settings the form does not show', { skip: SKIP }, async () => {
  const { box, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="laptop"]'))

  assert.equal(box.querySelector('[data-onsite-field="host"]').value, 'laptop.example')
  assert.equal(box.querySelector('[data-onsite-field="name"]').disabled, true)

  await click(box.querySelector('[data-onsite-save]'))

  const [, options] = made.requests.find(([route]) => route === '/targets/save')

  assert.equal(options.body.name, 'laptop')
  assert.equal(options.body.default, false)
  assert.deepEqual(options.body.target, {
    kind: 'ssh',
    host: 'laptop.example',
    user: 'dev',
    port: '',
    key: '',
    mount: '',
    screen: '',
    prefixes: ['/home/dev/'],
    path_map: { '/Users/dev/Development': '/home/dev/Development' }
  })
})

test('a machine can be given a folder for its files', { skip: SKIP }, async () => {
  const { box, choose, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="laptop"]'))

  const field = box.querySelector('[data-onsite-field="mount"]')

  assert.equal(field.value, '')

  await choose(field, '/mnt/onsite/laptop')
  await click(box.querySelector('[data-onsite-save]'))

  const [, options] = made.requests.find(([route]) => route === '/targets/save')

  assert.equal(options.body.target.mount, '/mnt/onsite/laptop')
})

test('the machine Hermes runs on has no folder for its files', { skip: SKIP }, async () => {
  const { box, click } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="mac"]'))

  assert.equal(box.querySelector('[data-onsite-field="mount"]'), null)
})

test('a machine can be set to use its own screen sharing', { skip: SKIP }, async () => {
  const { box, choose, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="laptop"]'))

  const field = box.querySelector('[data-onsite-field="screen"]')

  assert.equal(field.value, 'auto')

  await choose(field, 'sharing')
  await click(box.querySelector('[data-onsite-save]'))

  const [, options] = made.requests.find(([route]) => route === '/targets/save')

  assert.equal(options.body.target.screen, 'sharing')
})

test('the machine Hermes runs on has no screen setting', { skip: SKIP }, async () => {
  const { box, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="mac"]'))

  assert.equal(box.querySelector('[data-onsite-field="screen"]'), null)

  await click(box.querySelector('[data-onsite-save]'))

  const [, options] = made.requests.find(([route]) => route === '/targets/save')

  assert.equal('screen' in options.body.target, false)
})

test('a machine can be tested before it is saved', { skip: SKIP }, async () => {
  const { box, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="laptop"]'))
  await click(box.querySelector('[data-onsite-test]'))

  assert.equal(box.querySelector('[role="status"]').textContent, 'Connected in 41 ms.')
  assert.equal(made.requests.some(([route]) => route === '/targets/save'), false)
})

test('a refused save keeps the form open and says why', { skip: SKIP }, async () => {
  const { backend, box, click } = await openMachines()

  backend.routes['/targets/save'] = () => ({
    ok: false,
    error: '`laptop.host` is required for an ssh target'
  })

  await click(box.querySelector('[data-onsite-edit="laptop"]'))
  await click(box.querySelector('[data-onsite-save]'))

  assert.notEqual(box.querySelector('[data-onsite-form]'), null)
  assert.match(box.querySelector('[role="status"]').textContent, /is required/)
})

test('removing a machine asks the host to take it out', { skip: SKIP }, async () => {
  const { box, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-remove="laptop"]'))

  const [, options] = made.requests.find(([route]) => route === '/targets/remove')

  assert.deepEqual(options.body, { name: 'laptop' })
})

test('the Hermes server has no address fields', { skip: SKIP }, async () => {
  const { box, click } = await openMachines()

  await click(box.querySelector('[data-onsite-edit="mac"]'))

  assert.equal(box.querySelector('[data-onsite-field="host"]'), null)
  assert.equal(box.querySelector('[data-onsite-test]'), null)
})

test('an app with no settings pages still loads the plugin', { skip: SKIP }, async () => {
  const require = createRequire(path.join(REACT_DOM, 'package.json'))

  installDom(require)

  const { host } = createHost()
  const plugin = await loadPlugin(require, host)
  const { ctx, made } = createContext({ status: UP, proposals: [], fail: '', routes: {} })

  delete ctx.registerSettingsPage
  plugin.register(ctx)

  assert.notEqual(made.contributions.chip, undefined)
  assert.equal(made.pages.length, 0)
})

/* ------------------------------------------------------------------ */
/* A MACHINE THAT DIALS IN                                             */
/* ------------------------------------------------------------------ */

const LINK_COMMAND =
  'ssh -N -o ExitOnForwardFailure=yes -R 127.0.0.1:20500:127.0.0.1:22 ' +
  'you@server.example'

const DIALS_IN = {
  kind: 'ssh',
  host: '127.0.0.1',
  port: 20500,
  user: 'dev',
  dial_in: 'you@server.example',
  mount: '/mnt/onsite/tablet'
}

const WITH_DIAL_IN = {
  ...SAVED,
  targets: { ...SAVED.targets, tablet: DIALS_IN }
}

const STEPS = {
  ok: true,
  target: 'tablet',
  label: 'tablet (dev, dials in)',
  port: 20500,
  connected: false,
  detail: 'it has not connected to this server, or its link dropped',
  latency_ms: 0,
  host_keys: [],
  command: LINK_COMMAND,
  key_options: 'restrict,port-forwarding,permitlisten="127.0.0.1:20500"',
  keep: {
    linux: {
      path: '~/.config/systemd/user/onsite-dial-in.service',
      text: '[Unit]\nDescription=onsite\n',
      start: ['systemctl --user daemon-reload', 'systemctl --user enable'],
      note: 'It runs while you are logged in.'
    },
    macos: {
      path: '~/Library/LaunchAgents/onsite.plist',
      text: '<plist version="1.0"></plist>\n',
      start: ['launchctl bootstrap gui/$(id -u) onsite.plist'],
      note: 'It starts again by itself.'
    }
  }
}

async function openWithDialIn() {
  const made = await openMachines()

  made.backend.routes['/targets'] = () => WITH_DIAL_IN
  made.backend.routes['/targets/save'] = () => WITH_DIAL_IN
  made.backend.routes['/dial-in'] = () => STEPS

  const box = await made.mount(made.machines.render())

  await made.settle()

  return { ...made, box }
}

function savedTarget(made) {
  const [, options] = made.requests.find(([route]) => route === '/targets/save')

  return options.body
}

function field(box, name) {
  return box.querySelector(`[data-onsite-field="${name}"]`)
}

test('a new machine can be one that connects to the server by itself', { skip: SKIP }, async () => {
  const { box, choose, click } = await openMachines()

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')

  assert.match(field(box, 'kind').textContent, /connects to the server by itself/)
  assert.equal(field(box, 'host'), null)
  assert.equal(field(box, 'port'), null)
  assert.notEqual(field(box, 'user'), null)
  assert.equal(field(box, 'server').placeholder, 'you@server.example')
})

test('it cannot be tested before it is saved, because it has no port yet', { skip: SKIP }, async () => {
  const { box, choose, click } = await openMachines()

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')

  assert.equal(box.querySelector('[data-onsite-test]'), null)
})

test('saving it sends the account and the server, and no address or port', { skip: SKIP }, async () => {
  const { box, choose, click, made } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'name'), 'desk')
  await choose(field(box, 'kind'), 'dial-in')
  await choose(field(box, 'user'), 'dev')
  await choose(field(box, 'server'), ' you@server.example ')
  await click(box.querySelector('[data-onsite-save]'))

  const body = savedTarget(made)

  assert.equal(body.name, 'desk')
  assert.equal(body.target.kind, 'ssh')
  assert.equal(body.target.user, 'dev')
  assert.equal(body.target.dial_in, 'you@server.example')
  assert.equal('host' in body.target, false)
  assert.equal('port' in body.target, false)
})

test('once saved, the page shows the one command to run on it', { skip: SKIP }, async () => {
  const { backend, box, choose, click, made } = await openWithDialIn()

  backend.routes['/dial-in'] = body => ({ ...STEPS, target: body.target })

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'name'), 'tablet')
  await choose(field(box, 'kind'), 'dial-in')
  await choose(field(box, 'user'), 'dev')
  await choose(field(box, 'server'), 'you@server.example')
  await click(box.querySelector('[data-onsite-save]'))

  const steps = box.querySelector('[data-onsite-dial-in="tablet"]')
  const command = steps.querySelector('[data-onsite-copy-block="command"]')
  const [, options] = made.requests.find(([route]) => route === '/dial-in')

  assert.equal(options.body.target, 'tablet')
  assert.equal(command.querySelector('code').textContent, LINK_COMMAND)
  assert.match(command.textContent, /On tablet, run this in a terminal/)
})

test('Copy puts the exact link command on the clipboard', { skip: SKIP }, async () => {
  const { box, click, made } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const command = box.querySelector('[data-onsite-copy-block="command"]')

  await click(command.querySelector('[data-action="copy"]'))

  assert.deepEqual(made.copied, [LINK_COMMAND])
})

test('the steps say by themselves when the machine has arrived', { skip: SKIP }, async () => {
  const { backend, box, click, made, settle } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const state = () => box.querySelector('[data-onsite-link-state]').textContent

  assert.match(state(), /^Waiting: it has not connected to this server/)

  backend.routes['/dial-in'] = () => ({
    ...STEPS,
    connected: true,
    detail: '',
    latency_ms: 76,
    host_keys: ['ED25519 SHA256:abc']
  })

  // The poll the steps took out is the last one the plugin asked for.
  await settle(made.intervals[made.intervals.length - 1])

  assert.equal(state(), 'Connected (76 ms).')
  assert.match(
    box.querySelector('[data-onsite-host-keys]').textContent,
    /ED25519 SHA256:abc/
  )
})

test('only a machine that dials in has a Connect button', { skip: SKIP }, async () => {
  const { box } = await openWithDialIn()

  assert.notEqual(box.querySelector('[data-onsite-link="tablet"]'), null)
  assert.equal(box.querySelector('[data-onsite-link="laptop"]'), null)
  assert.equal(box.querySelector('[data-onsite-link="mac"]'), null)
  assert.match(
    box.querySelector('[data-onsite-machine="tablet"]').textContent,
    /dev, dials in on port 20500/
  )
})

test('the steps show how to keep the link running on either system', { skip: SKIP }, async () => {
  const { box, choose, click } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const file = () => box.querySelector('[data-onsite-copy-block="keep-file"]')
  const start = () => box.querySelector('[data-onsite-copy-block="keep-start"]')

  // jsdom says it is not a Mac, so Linux is offered first.
  assert.match(file().textContent, /onsite-dial-in\.service/)
  assert.match(start().querySelector('code').textContent, /daemon-reload\n/)

  await choose(field(box, 'system'), 'macos')

  assert.match(file().textContent, /LaunchAgents\/onsite\.plist/)
  assert.match(start().querySelector('code').textContent, /launchctl bootstrap/)
})

test('the steps offer the line that limits the machine\'s key', { skip: SKIP }, async () => {
  const { box, click } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const options = box.querySelector('[data-onsite-copy-block="key-options"]')

  assert.equal(options.querySelector('code').textContent, STEPS.key_options)
  assert.match(options.textContent, /authorized_keys file on the server/)
})

test('Close puts the steps away, and Connect brings them back', { skip: SKIP }, async () => {
  const { box, click } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))
  await click(box.querySelector('[data-onsite-link-close]'))

  assert.equal(box.querySelector('[data-onsite-dial-in]'), null)

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  assert.notEqual(box.querySelector('[data-onsite-dial-in="tablet"]'), null)
})

test('Close stops asking the host about the machine', { skip: SKIP }, async () => {
  const { box, click, made } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const poll = made.intervals[made.intervals.length - 1]

  assert.deepEqual(made.stopped, [])

  await click(box.querySelector('[data-onsite-link-close]'))

  assert.deepEqual(made.stopped, [poll])
})

test('a slow answer is not asked for a second time', { skip: SKIP }, async () => {
  const { backend, box, click, made, settle } = await openWithDialIn()
  let release = null

  backend.routes['/dial-in'] = () =>
    new Promise(resolve => {
      release = () => resolve(STEPS)
    })

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const poll = made.intervals[made.intervals.length - 1]
  const asked = () =>
    made.requests.filter(([route]) => route === '/dial-in').length

  assert.equal(asked(), 1)

  await settle(poll)
  await settle(poll)

  assert.equal(asked(), 1)

  // The slow answer lands. The next question is asked again.
  backend.routes['/dial-in'] = () => STEPS

  await settle(release)
  await settle(poll)

  assert.equal(asked(), 2)
})

test('steps that stop being readable leave no old state behind', { skip: SKIP }, async () => {
  const { backend, box, click, made, settle } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  assert.notEqual(box.querySelector('[data-onsite-copy-block]'), null)

  backend.routes['/dial-in'] = () => ({ ok: false, error: 'gone' })
  await settle(made.intervals[made.intervals.length - 1])

  const steps = box.querySelector('[data-onsite-dial-in="tablet"]')

  assert.match(steps.textContent, /gone/)
  assert.equal(steps.querySelector('[data-onsite-copy-block]'), null)
  assert.equal(
    steps.querySelector('[data-onsite-link-state]').textContent,
    'Checking…'
  )
})

test('a machine that dials in is not saved without a server', { skip: SKIP }, async () => {
  const { box, choose, click, made } = await openMachines()

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'name'), 'desk')
  await choose(field(box, 'kind'), 'dial-in')
  await choose(field(box, 'user'), 'dev')
  await click(box.querySelector('[data-onsite-save]'))

  assert.equal(made.requests.some(([route]) => route === '/targets/save'), false)
  assert.match(
    box.querySelector('[role="status"]').textContent,
    /Fill in the server/
  )
})

test('steps the host will not give say why', { skip: SKIP }, async () => {
  const { backend, box, click } = await openWithDialIn()

  backend.routes['/dial-in'] = () => ({
    ok: false,
    error: '`tablet` does not dial in. The server logs in to it.'
  })

  await click(box.querySelector('[data-onsite-link="tablet"]'))

  const steps = box.querySelector('[data-onsite-dial-in="tablet"]')

  assert.match(steps.textContent, /does not dial in/)
  assert.equal(steps.querySelector('[data-onsite-copy-block]'), null)
})

test('editing a machine that dials in keeps its port', { skip: SKIP }, async () => {
  const { box, click, made } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-edit="tablet"]'))

  assert.equal(field(box, 'kind').value, 'dial-in')
  assert.equal(field(box, 'server').value, 'you@server.example')
  assert.notEqual(box.querySelector('[data-onsite-test]'), null)

  await click(box.querySelector('[data-onsite-save]'))

  const body = savedTarget(made)

  assert.equal(body.target.port, 20500)
  assert.equal(body.target.dial_in, 'you@server.example')
  assert.equal(body.target.mount, '/mnt/onsite/tablet')
  assert.equal('host' in body.target, false)
})

test('a machine the server dialed loses its old port when it dials in', { skip: SKIP }, async () => {
  const opened = await openMachines()
  const { backend, choose, click, made, machines, mount, settle } = opened
  const laptop = { ...SAVED.targets.laptop, port: 2222 }

  backend.routes['/targets'] = () => ({
    ...SAVED,
    targets: { ...SAVED.targets, laptop }
  })

  const page = await mount(machines.render())

  await settle()
  await click(page.querySelector('[data-onsite-edit="laptop"]'))
  await choose(field(page, 'kind'), 'dial-in')
  await choose(field(page, 'server'), 'you@server.example')
  await click(page.querySelector('[data-onsite-save]'))

  const body = savedTarget(made)

  // 2222 was the laptop's own SSH port. It is not a port on the server.
  assert.equal('port' in body.target, false)
  assert.equal('host' in body.target, false)
  assert.deepEqual(body.target.prefixes, ['/home/dev/'])
})

test('a machine that stops dialing in loses the setting', { skip: SKIP }, async () => {
  const { box, choose, click, made } = await openWithDialIn()

  await click(box.querySelector('[data-onsite-edit="tablet"]'))
  await choose(field(box, 'kind'), 'ssh')

  // The server's loopback address and the port the host picked are
  // not that machine's own. The form does not offer them as such.
  assert.equal(field(box, 'host').value, '')
  assert.equal(field(box, 'port').value, '')

  await choose(field(box, 'host'), 'tablet.example')
  await click(box.querySelector('[data-onsite-save]'))

  const body = savedTarget(made)

  assert.equal('dial_in' in body.target, false)
  assert.equal(body.target.host, 'tablet.example')
  assert.equal(body.target.port, '')
})

function connectAs(host, connection) {
  host.activeConnectionId = () => connection.id
  host.connections = async () => [
    { id: 'other', kind: 'ssh', host: 'elsewhere.example', user: 'x' },
    connection
  ]
}

test('the server field is filled with how the app reaches the server', { skip: SKIP }, async () => {
  const { box, choose, click, host, settle } = await openMachines()

  connectAs(host, { id: 'main', kind: 'ssh', host: 'server.example', user: 'you', port: 22 })

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')
  await settle()

  assert.equal(field(box, 'server').value, 'you@server.example')
})

test('a port other than 22 is kept in the suggestion', { skip: SKIP }, async () => {
  const { box, choose, click, host, settle } = await openMachines()

  connectAs(host, { id: 'main', kind: 'ssh', host: 'server.example', user: 'you', port: 2222 })

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')
  await settle()

  assert.equal(field(box, 'server').value, 'you@server.example:2222')
})

test('a connection with no port named suggests none', { skip: SKIP }, async () => {
  const { box, choose, click, host, settle } = await openMachines()

  connectAs(host, { id: 'main', kind: 'ssh', host: 'server', user: '', port: null })

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')
  await settle()

  assert.equal(field(box, 'server').value, 'server')
})

test('an app that reaches the server another way suggests nothing', { skip: SKIP }, async () => {
  const { box, choose, click, host, settle } = await openMachines()

  connectAs(host, { id: 'main', kind: 'remote', url: 'https://server.example' })

  await click(box.querySelector('[data-onsite-add]'))
  await choose(field(box, 'kind'), 'dial-in')
  await settle()

  assert.equal(field(box, 'server').value, '')
})

test('the chip says a machine that dials in is not connected', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  backend.status = {
    ...DOWN,
    dial_in: true,
    label: 'laptop (dev, dials in)',
    detail: 'it has not connected to this server, or its link dropped'
  }
  await settle(made.intervals[0])

  const tip = box.querySelector('[data-onsite-chip]').getAttribute('title')

  assert.match(tip, /State: not connected \(it has not connected to this server/)
  assert.doesNotMatch(tip, /not reachable/)
})

test('the pane says where the command of a machine that is not there is', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()

  backend.status = {
    ...UP,
    rows: [
      {
        name: 'tablet',
        kind: 'ssh',
        label: 'tablet (dev, dials in)',
        dial_in: true,
        connected: false,
        detail: 'it has not connected to this server, or its link dropped',
        mount_error: ''
      },
      { name: 'laptop', kind: 'ssh', label: 'laptop', connected: false, detail: 'no answer' }
    ]
  }

  const box = await mount(made.contributions.pane.render())

  await settle()

  const row = name => box.querySelector(`[data-onsite-machine-row="${name}"]`)
  const said = row('tablet').querySelector('[role="alert"]').textContent

  assert.match(said, /Not connected: it has not connected to this server/)
  assert.match(said, /Settings, Plugins, Onsite, Machines/)
  assert.equal(row('laptop').querySelector('[role="alert"]'), null)
})

