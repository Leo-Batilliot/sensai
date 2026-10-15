import { describe, expect, it } from 'vitest'
import { gauge, modelChoices, modelLabel, serversUp } from './status'

describe('gauge', () => {
  it('turns orange at three quarters and red past the window', () => {
    expect(gauge(3412, 8192)).toEqual({ percent: 42, tone: 'ok' })
    expect(gauge(6200, 8192)).toEqual({ percent: 76, tone: 'warn' })
    expect(gauge(8192, 8192)).toEqual({ percent: 100, tone: 'warn' })
    // Monday's overflow: the model had forgotten its tools
    expect(gauge(8869, 8192)).toEqual({ percent: 108, tone: 'over' })
  })

  it('never divides by zero', () => {
    expect(gauge(10, 0)).toEqual({ percent: 0, tone: 'ok' })
  })
})

describe('models', () => {
  const qwen = { name: 'qwen3:8b', size_bytes: 5_200_000_000, supports_tools: true }
  const llama = { name: 'llama2', size_bytes: 0, supports_tools: false }

  it('labels a model with its size and warns when it cannot call tools', () => {
    expect(modelLabel(qwen)).toBe('qwen3:8b · 5,2 Go')
    expect(modelLabel(llama)).toBe('llama2 · sans outils')
  })

  it('keeps the current model in the list even if Ollama did not return it', () => {
    expect(modelChoices([qwen], 'qwen3:4b').map((model) => model.name)).toEqual(['qwen3:4b', 'qwen3:8b'])
    expect(modelChoices([qwen], 'qwen3:8b')).toEqual([qwen])
    expect(modelChoices([qwen, llama], null)).toEqual([qwen, llama])
  })
})

describe('serversUp', () => {
  it('counts the servers that answer', () => {
    const servers = [
      { name: 'sql_toolbox', status: 'up' as const },
      { name: 'time', status: 'down' as const, error: 'la commande uvx est introuvable' },
    ]
    expect(serversUp(servers)).toBe(1)
  })
})
