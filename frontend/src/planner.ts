// 持ち物と知らせをサーバーに持つ（2026-11-08 PO「サーバーに入れて！でないと覚えられないじゃん」）。
// 画面はこれまでどおり端末の控え（ensemble.tasks.<code>・ensemble.checklist.<code>）を読み書きし、
// 変えるたびにサーバーへ送る。読み込んだ台帳にサーバーの控えがあれば、端末の控えをそれで置き換える
// （別の端末・シークレットモードでも戻る）。まだ送れていない変更がある間は、サーバーの控えで上書きしない。
// サーバーでは台帳の外の表に置き、AIへは渡さない。
import { api } from './api'

type Planner = { tasks?: Record<string, unknown>; checklist?: Record<string, unknown> }

const KEYS = { tasks: 'ensemble.tasks', checklist: 'ensemble.checklist' } as const

function readJson(key: string): Record<string, unknown> | undefined {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return undefined
    const value = JSON.parse(raw)
    return value && typeof value === 'object' && !Array.isArray(value) ? value : undefined
  } catch {
    return undefined
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 残せなくても、サーバーにはある。
  }
}

function dirtyKey(code: string): string {
  return `ensemble.plannerDirty.${code}`
}

function isDirty(code: string): boolean {
  try {
    return localStorage.getItem(dirtyKey(code)) !== null
  } catch {
    return false
  }
}

function setDirty(code: string, dirty: boolean): void {
  try {
    if (dirty) localStorage.setItem(dirtyKey(code), String(Date.now()))
    else localStorage.removeItem(dirtyKey(code))
  } catch {
    // 同上。
  }
}

function localPlanner(code: string): Planner {
  const planner: Planner = {}
  const tasks = readJson(`${KEYS.tasks}.${code}`)
  const checklist = readJson(`${KEYS.checklist}.${code}`)
  if (tasks) planner.tasks = tasks
  if (checklist) planner.checklist = checklist
  return planner
}

const timers = new Map<string, number>()
// 送る前にもう一度触られたら、まとめて1回にする。
export function pushPlanner(code: string, delay = 300): void {
  setDirty(code, true)
  const previous = timers.get(code)
  if (previous) window.clearTimeout(previous)
  timers.set(
    code,
    window.setTimeout(() => {
      timers.delete(code)
      void api
        .putPlanner(code, localPlanner(code))
        .then(() => {
          // 送っている間にまた触られたら、次の送りに任せる。
          if (!timers.has(code)) setDirty(code, false)
        })
        .catch(() => {
          // 電波が無いなど。印は残し、次に読み込んだときにもう一度送る。
        })
    }, delay),
  )
}

// 読み込んだ台帳のサーバーの控えを、端末へ。戻り値：端末の控えを書き換えたか。
export function syncPlanner(code: string, server: Planner | undefined): boolean {
  if (!server || typeof server !== 'object') return false
  if (isDirty(code)) {
    // まだ送れていない変更がある。サーバーで上書きせず、送り直す。
    if (!timers.has(code)) pushPlanner(code, 0)
    return false
  }
  const hasServer = !!server.tasks || !!server.checklist
  if (!hasServer) {
    // サーバーにまだ無い（この版の前に端末だけで使っていた）。端末の控えを上げる。
    const local = localPlanner(code)
    if (local.tasks || local.checklist) pushPlanner(code, 0)
    return false
  }
  let changed = false
  for (const name of ['tasks', 'checklist'] as const) {
    const value = server[name]
    if (!value) continue
    const key = `${KEYS[name]}.${code}`
    const before = JSON.stringify(readJson(key) ?? null)
    if (before !== JSON.stringify(value)) {
      writeJson(key, value)
      changed = true
    }
  }
  return changed
}
