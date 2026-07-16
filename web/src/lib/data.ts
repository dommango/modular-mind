import 'server-only'
import fs from 'node:fs'
import path from 'node:path'
import { cache } from 'react'
import type { ZodType } from 'zod'
import { tracksDocSchema, stagesDocSchema, modulesDocSchema } from './schemas'

const dataDir = () => path.join(process.cwd(), 'public', 'data')

function loadAndParse<T>(name: string, schema: ZodType<T>): T {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(dataDir(), name), 'utf-8'))
    return schema.parse(raw)
  } catch (err) {
    throw new Error(`Invalid ${name}: ${err instanceof Error ? err.message : String(err)}`)
  }
}

export const getTracks = cache(() => loadAndParse('tracks.json', tracksDocSchema).tracks)
export const getStages = cache(() => loadAndParse('stages.json', stagesDocSchema).stages)
export const getModules = cache(() => loadAndParse('modules.json', modulesDocSchema).modules)
export const getFeaturedTracks = cache(() => getTracks().filter((t) => t.featured))
