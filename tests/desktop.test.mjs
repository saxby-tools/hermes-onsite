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
const PLUGIN_PATH = path.join(HERE, '..', 'desktop', 'plugin.js')

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
  const calls = { submit: [], notify: [], reveal: [] }

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
    revealPane: id => calls.reveal.push(id)
  }

  return { host, calls }
}

// Writes the plugin beside its stand-ins and imports it from there,
// which is what the real loader does with blob URLs.
async function loadPlugin(require, host) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'hands-desktop-'))
  const reactPath = require.resolve('react')
  const jsxPath = require.resolve('react/jsx-runtime')

  globalThis.__HANDS_TEST_HOST__ = host

  const shims = {
    '@hermes/plugin-sdk': [
      'export const host = globalThis.__HANDS_TEST_HOST__',
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
  const made = { contributions: {}, intervals: [], events: {}, copied: [], requests: [] }

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

      return { items: backend.proposals }
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
  const backend = { status: UP, proposals: [], fail: '' }
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

  await settle(() => plugin.register(ctx))

  return { backend, calls, click, ctx, made, mount, plugin, settle }
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

  assert.equal(plugin.id, 'hermes-hands')
  assert.equal(made.contributions.chip.area, 'statusBar.right')
  assert.equal(made.contributions.pane.area, 'panes')
  assert.equal(made.contributions['run-card'].area, 'transcript.directives')
  assert.equal(made.contributions['run-card'].data.name, 'hands-run')
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
  const chip = () => box.querySelector('[data-hands-chip]')

  assert.equal(chip().getAttribute('data-hands-chip'), 'up')
  assert.match(chip().textContent, /hands: laptop/)
  assert.match(chip().textContent, /Development\/app/)
  assert.match(chip().getAttribute('title'), /connected \(41 ms\)/)

  backend.status = DOWN
  await settle(made.intervals[0])

  assert.equal(chip().getAttribute('data-hands-chip'), 'down')
  assert.match(chip().getAttribute('title'), /not reachable \(connection refused\)/)

  backend.status = UP
  await settle(made.intervals[0])

  assert.equal(chip().getAttribute('data-hands-chip'), 'up')
})

test('the chip says so when the host has no plugin', { skip: SKIP }, async () => {
  const { backend, made, mount, settle } = await setup()
  const box = await mount(made.contributions.chip.render())

  backend.fail = 'HTTP 404'
  await settle(made.intervals[0])

  const chip = box.querySelector('[data-hands-chip]')

  assert.equal(chip.getAttribute('data-hands-chip'), 'down')
  assert.match(chip.textContent, /not set up/)
  assert.match(chip.getAttribute('title'), /no hermes-hands plugin enabled/)
})

test('clicking the chip opens the pane', { skip: SKIP }, async () => {
  const { calls, click, made, mount } = await setup()
  const box = await mount(made.contributions.chip.render())

  await click(box.querySelector('[data-hands-chip]'))

  assert.deepEqual(calls.reveal, ['hermes-hands:pane'])
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

  globalThis.__HANDS_TEST_HOST__.composer.submit = () => false

  const run = box.querySelector('[data-action="run"]')

  await click(run)

  assert.equal(run.textContent, 'Run')
  assert.match(calls.notify[0].message, /Type \/run 4/)
})
