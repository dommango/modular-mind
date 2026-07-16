import { describe, expect, it } from 'vitest'
import { trackSchema } from './schemas'

const validTrack = {
  slug: 'batch3-01-omri-seq', title: 'Omri Seq', archetype: 'omri-seq', source: 'batch',
  verdict: { makes_sound: true, character: 'rhythmic', flags: [] },
  fitness: 80, metrics: { rms: 0.1 }, duration: 10, parent: null, repairs: [],
  featured: true, audio: 'audio/batch3-01-omri-seq.mp3', peaks: 'data/peaks/batch3-01-omri-seq.json',
}

describe('trackSchema', () => {
  it('accepts a valid track', () => {
    expect(trackSchema.parse(validTrack).slug).toBe('batch3-01-omri-seq')
  })
  it('rejects unknown character', () => {
    const bad = { ...validTrack, verdict: { ...validTrack.verdict, character: 'melodic' } }
    expect(() => trackSchema.parse(bad)).toThrow()
  })
  it('rejects unknown source', () => {
    expect(() => trackSchema.parse({ ...validTrack, source: 'wild' })).toThrow()
  })
})
