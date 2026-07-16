import fs from 'node:fs'
import path from 'node:path'
import { cache } from 'react'
import { tracksDocSchema, stagesDocSchema, modulesDocSchema } from './schemas'

const dataDir = () => path.join(process.cwd(), 'public', 'data')

function loadJson(name: string): unknown {
  return JSON.parse(fs.readFileSync(path.join(dataDir(), name), 'utf-8'))
}

export const getTracks = cache(() => tracksDocSchema.parse(loadJson('tracks.json')).tracks)
export const getStages = cache(() => stagesDocSchema.parse(loadJson('stages.json')).stages)
export const getModules = cache(() => modulesDocSchema.parse(loadJson('modules.json')).modules)
export const getFeaturedTracks = cache(() => getTracks().filter((t) => t.featured))
