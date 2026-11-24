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
    { name: 'mac', label: 'mac (this machine)', connected: true, default: true }
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
    pages: []
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

      return () => {}
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
    backend, calls, choose, click, ctx, host, made, mount, plugin, settle
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
/* 4. FOLDER BROWSER                                                   */
/* ------------------------------------------------------------------ */

const TWO_MACHINES = {
  ...UP,
  rows: [
    { name: 'laptop', kind: 'ssh', label: 'laptop (dev@laptop.example)', connected: true },
    { name: 'mac', kind: 'local', label: 'mac (this machine)', connected: true }
  ]
}

const LISTINGS = {
  '~': { ok: true, path: '/home/dev', parent: '/home', folders: ['notes', 'work'] },
  '/home/dev/work': { ok: true, path: '/home/dev/work', parent: '/home/dev', folders: [] }
}

async function openBrowser() {
  const made = await setup()

  made.backend.status = TWO_MACHINES
  made.backend.routes['/browse'] = body => LISTINGS[body.path]
  made.backend.routes['/open'] = body => ({
    ok: true,
    target: body.target,
    cwd: `/host/mirror/laptop${body.path}`
  })

  await made.settle(made.made.intervals[0])

  const box = await made.mount(made.made.contributions.pane.render())

  return { ...made, box }
}

test('the pane offers folders only for machines reached over SSH', { skip: SKIP }, async () => {
  const { box } = await openBrowser()
  const options = [...box.querySelectorAll('[data-onsite-browser] option')]

  assert.deepEqual(options.map(option => option.value), ['laptop'])
})

test('browsing asks the machine, one folder at a time', { skip: SKIP }, async () => {
  const { box, click, made } = await openBrowser()

  await click(box.querySelector('[data-onsite-browse]'))

  const names = () =>
    [...box.querySelectorAll('[data-onsite-folder]')].map(row => row.textContent)

  assert.deepEqual(names(), ['..', 'notes', 'work'])
  assert.equal(box.querySelector('[data-onsite-path]').textContent, '/home/dev')

  await click(box.querySelector('[data-onsite-folder="work"]'))

  assert.equal(box.querySelector('[data-onsite-path]').textContent, '/home/dev/work')

  const asked = made.requests.filter(([route]) => route === '/browse')

  assert.deepEqual(asked.map(([, options]) => options.body), [
    { target: 'laptop', path: '~' },
    { target: 'laptop', path: '/home/dev/work' }
  ])
})

test('picking a folder starts a session in its place on the host', { skip: SKIP }, async () => {
  const { box, calls, click } = await openBrowser()

  await click(box.querySelector('[data-onsite-browse]'))
  await click(box.querySelector('[data-onsite-folder="work"]'))
  await click(box.querySelector('[data-onsite-open]'))

  assert.deepEqual(calls.rpc, [
    ['session.create', { cwd: '/host/mirror/laptop/home/dev/work', cwd_explicit: true }],
    ['session.title', { session_id: 'runtime-new', title: 'work on laptop' }]
  ])
  assert.deepEqual(calls.opened, [['stored-new', { expectHistory: false }]])
})

test('a session is started in the profile the app is on', { skip: SKIP }, async () => {
  const { box, calls, click, host } = await openBrowser()

  host.state.profile = atom('buster')

  await click(box.querySelector('[data-onsite-browse]'))
  await click(box.querySelector('[data-onsite-open]'))

  assert.equal(calls.rpc[0][1].profile, 'buster')
})

test('an app that cannot start sessions is given the folder to open', { skip: SKIP }, async () => {
  const { box, calls, click, host, made } = await openBrowser()

  delete host.request

  await click(box.querySelector('[data-onsite-browse]'))
  await click(box.querySelector('[data-onsite-open]'))

  assert.deepEqual(made.copied, ['/host/mirror/laptop/home/dev'])
  assert.equal(calls.opened.length, 0)
  assert.match(JSON.stringify(calls.notify), /Open this folder to work there/)
})

test('a machine that cannot be listed says why', { skip: SKIP }, async () => {
  const { backend, box, click } = await openBrowser()

  backend.routes['/browse'] = () => ({ ok: false, error: 'Connection refused' })

  await click(box.querySelector('[data-onsite-browse]'))

  assert.equal(box.querySelector('[role="alert"]').textContent, 'Connection refused')
  assert.equal(box.querySelector('[data-onsite-open]'), null)
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
  assert.match(rows[1].textContent, /this machine.*\(default\)/)
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
  assert.match(rows[1].textContent, /this machine.*\(default\)/)
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
    mirror: '',
    screen: '',
    prefixes: ['/home/dev/'],
    path_map: { '/Users/dev/Development': '/home/dev/Development' }
  })
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

test('this machine has no address fields', { skip: SKIP }, async () => {
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

