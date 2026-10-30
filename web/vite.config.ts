import react from '@vitejs/plugin-react'
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'

// The files a build is made from; tests are left out, they do not change the build.
// Same list and same digest as src/sensai/web/frontend.py, which tells a stale web/dist
// in the CI, without Node (WEB-14).
function sources(root: string): string[] {
  const tree = (dir: string) =>
    readdirSync(join(root, dir), { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) => join(entry.parentPath, entry.name).slice(root.length + 1).replaceAll('\\', '/'))
  return ['index.html', 'package-lock.json', 'vite.config.ts', ...tree('src'), ...tree('public')]
    .filter((path) => !/\.test\.tsx?$/.test(path) && !path.startsWith('src/test/'))
    .sort()
}

// web/dist/build.json: the digest of the sources of this build, without a date, so that
// building twice the same sources gives the same files (no diff to commit)
function buildStamp(): Plugin {
  let root = ''
  let outDir = ''
  return {
    name: 'sensai-build-stamp',
    apply: 'build',
    configResolved(config) {
      root = config.root
      outDir = config.build.outDir
    },
    closeBundle() {
      const digest = createHash('sha256')
      for (const path of sources(root)) {
        digest.update(`${path}\0`)
        digest.update(readFileSync(join(root, path)))
        digest.update('\0')
      }
      const stamp = { sources: digest.digest('hex') }
      writeFileSync(resolve(root, outDir, 'build.json'), `${JSON.stringify(stamp, null, 2)}\n`)
    },
  }
}

export default defineConfig({
  plugins: [react(), buildStamp()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
