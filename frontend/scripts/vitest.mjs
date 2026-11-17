// Starts Vitest from a working directory whose Windows drive letter is upper case.
// A lower-case drive (for example a VS Code terminal at d:\) makes Vitest load two
// copies of itself, and every test fails with "failed to find the current suite".
// On other platforms this only forwards the arguments.
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const upperDrive = (p) => p.replace(/^[a-z]:/, (d) => d.toUpperCase())
const cwd = upperDrive(process.cwd())
const bin = upperDrive(fileURLToPath(new URL('../node_modules/vitest/vitest.mjs', import.meta.url)))
const { status } = spawnSync(process.execPath, [bin, ...process.argv.slice(2)], { cwd, stdio: 'inherit' })
process.exit(status ?? 1)
