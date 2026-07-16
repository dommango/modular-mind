import { z } from 'zod'

export const verdictSchema = z.object({
  makes_sound: z.boolean(),
  character: z.enum(['silent', 'noise', 'rhythmic', 'drone']),
  flags: z.array(z.enum(['clipping', 'near_silent', 'dc_offset'])),
})

export const trackSchema = z.object({
  slug: z.string(),
  title: z.string(),
  archetype: z.string(),
  source: z.enum(['handcrafted', 'batch', 'repair', 'llm']),
  verdict: verdictSchema,
  fitness: z.number().nullable(),
  metrics: z.record(z.string(), z.number()),
  duration: z.number(),
  parent: z.string().nullable(),
  repairs: z.array(z.string()),
  featured: z.boolean(),
  audio: z.string(),
  peaks: z.string(),
})

export const stageSchema = z.object({
  slug: z.string(),
  title: z.string(),
  blurb: z.string(),
  inputs: z.string(),
  outputs: z.string(),
  stat: z.object({ key: z.string(), value: z.number() }).nullable(),
})

export const moduleSchema = z.object({
  key: z.string(),
  plugin: z.string(),
  model: z.string(),
  role: z.string(),
  tags: z.array(z.string()),
  description: z.string(),
  instances: z.number(),
  manual_url: z.string().nullable(),
  n_params: z.number(),
  n_inputs: z.number(),
  n_outputs: z.number(),
})

export const tracksDocSchema = z.object({ schema_version: z.literal(1), tracks: z.array(trackSchema) })
export const stagesDocSchema = z.object({ schema_version: z.literal(1), stages: z.array(stageSchema) })
export const modulesDocSchema = z.object({ schema_version: z.literal(1), modules: z.array(moduleSchema) })

export type Track = z.infer<typeof trackSchema>
export type Stage = z.infer<typeof stageSchema>
export type ModuleProfile = z.infer<typeof moduleSchema>
