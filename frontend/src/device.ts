// 端末に残すもの。LINEの中のブラウザなどで使えないことがあるので、失敗しても画面は止めない。

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 残せなくても、今の画面はそのまま使える。
  }
}

function remove(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    // 同上。
  }
}

export type Pending = { id: string; text: string; at: string }
export type Screen = 'board' | 'add'

export const device = {
  lastCode: () => read<string | null>('ensemble.lastCode', null),
  setLastCode: (code: string) => write('ensemble.lastCode', code),
  forgetLastCode: () => remove('ensemble.lastCode'),

  hasConsented: () => read<boolean>('ensemble.consented', false),
  setConsented: () => write('ensemble.consented', true),

  draft: (code: string) => read<string>(`ensemble.draft.${code}`, ''),
  setDraft: (code: string, text: string) => write(`ensemble.draft.${code}`, text),

  screen: (code: string) => read<Screen>(`ensemble.screen.${code}`, 'board'),
  setScreen: (code: string, screen: Screen) => write(`ensemble.screen.${code}`, screen),

  pending: (code: string) => read<Pending[]>(`ensemble.pending.${code}`, []),
  setPending: (code: string, list: Pending[]) => write(`ensemble.pending.${code}`, list),

  seen: (code: string) => read<Record<string, string> | null>(`ensemble.seen.${code}`, null),
  setSeen: (code: string, seen: Record<string, string>) => write(`ensemble.seen.${code}`, seen),
}
