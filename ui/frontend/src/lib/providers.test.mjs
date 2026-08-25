// Run with: npm test  (node --test, no extra deps)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { modeGroups, ALL_MODES } from './providers.js'

test('catalog-listed modes rank first, every other litellm mode stays offered', () => {
  // Real upstream row: deepinfra is marked rerank:false although litellm ships a deepinfra rerank handler
  const g = modeGroups('deepinfra', ['chat', 'responses'])
  assert.deepEqual(g.supported, ['chat', 'responses'])
  assert.ok(g.other.includes('rerank'), 'rerank must remain selectable')
  assert.deepEqual([...g.supported, ...g.other].sort(), [...ALL_MODES].sort())
  assert.ok(!g.other.some(m => g.supported.includes(m)), 'no duplicates across groups')
})

test('custom/local providers get one flat list of all modes', () => {
  assert.deepEqual(modeGroups('hosted_vllm', ['chat', 'embedding']), { supported: ALL_MODES, other: [] })
})

test('no catalog row (fallback providers / offline) → flat list of all modes', () => {
  assert.deepEqual(modeGroups('openai', undefined), { supported: ALL_MODES, other: [] })
  assert.deepEqual(modeGroups('openai', []), { supported: ALL_MODES, other: [] })
})

test('unknown catalog modes are ignored rather than rendered', () => {
  const g = modeGroups('cohere', ['chat', 'bogus_mode', 'rerank'])
  assert.deepEqual(g.supported, ['chat', 'rerank'])
})
