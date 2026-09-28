/**
 * 把 frontend-vue 里的 TS 源码编译成可被 Node 直接 import 的 ESM。
 *
 * 为什么不用 vitest：本项目前端没有测试框架，为了跑几个断言装一整套测试栈
 * 不划算。这里用项目自带的 tsc 编译**真实源码**，断言跑在真源码上，而不是
 * 复制一份逻辑（复制品永远测不出真源码的行为）。
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const projectRoot = resolve(here, '..')

function findEmitted(dir, filename) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      const hit = findEmitted(full, filename)
      if (hit) return hit
    } else if (entry === filename) {
      return full
    }
  }
  return null
}

/**
 * 编译并加载一个 TS 模块。
 * @param {string} relPath 相对 frontend-vue 的路径，如 'src/utils/dialogueRole.ts'
 * @returns {Promise<{ mod: any, cleanup: () => void }>}
 */
export async function loadTsModule(relPath) {
  const outDir = mkdtempSync(join(tmpdir(), 'ts-load-'))
  const cleanup = () => rmSync(outDir, { recursive: true, force: true })
  try {
    execFileSync(
      join(projectRoot, 'node_modules/.bin/tsc'),
      [
        relPath,
        // 单文件编译必须忽略 tsconfig（TS 6 起直接报错拒绝）。
        // 目标文件只依赖 type-only import，产物里不含运行时依赖。
        '--ignoreConfig',
        '--outDir', outDir,
        '--module', 'esnext',
        '--target', 'es2020',
        '--moduleResolution', 'bundler',
        '--skipLibCheck',
        '--noEmitOnError', 'false',
      ],
      { cwd: projectRoot, stdio: 'pipe' }
    )
  } catch (e) {
    // tsc 对「type-only import 无法解析路径别名」会报错但仍产出可运行 JS；
    // 真正没产出才算失败（由下面的 findEmitted 判定）
    if (!findEmitted(outDir, relPath.split('/').pop().replace(/\.ts$/, '.js'))) {
      cleanup()
      throw new Error(`tsc 未产出 ${relPath}：\n${e.stdout?.toString() || e.message}`)
    }
  }
  const emitted = findEmitted(outDir, relPath.split('/').pop().replace(/\.ts$/, '.js'))
  if (!emitted) {
    cleanup()
    throw new Error(`未找到 ${relPath} 的编译产物`)
  }
  const mod = await import(pathToFileURL(emitted).href)
  return { mod, cleanup }
}
