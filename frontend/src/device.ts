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

import type { Branch } from './api'

// failed は仕分けを断られたときの文。無ければ、つながり次第また送る。
// said と who は親が選んだこと（任意）。
export type Pending = {
  id: string
  text: string
  at: string
  failed?: string
  said?: string
  who?: string
}
export type Screen = 'board' | 'add' | 'decide' | 'vault' | 'map' | 'trash'

// 親が押すまで台帳に入れないので、案の控えは端末が持つ。
export type WorkingProposal = { state: 'working'; item_id: string }
export type ReadyProposal = {
  state: 'ready'
  item_id: string
  branch: Branch
}
export type FailedProposal = { state: 'failed'; item_id: string; message: string }
export type Proposal = WorkingProposal | ReadyProposal | FailedProposal

// 保管庫から探した結果。資料の文なので、サーバーには置かず端末が持つ。
export type VaultAnswer =
  | { found: true; photoId: string; category: string; quote: string }
  | { found: false }

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

  proposal: (code: string) => read<Proposal | null>(`ensemble.proposal.${code}`, null),
  setProposal: (code: string, proposal: Proposal | null) => {
    if (proposal) write(`ensemble.proposal.${code}`, proposal)
    else remove(`ensemble.proposal.${code}`)
  },

  seen: (code: string) => read<Record<string, string> | null>(`ensemble.seen.${code}`, null),
  setSeen: (code: string, seen: Record<string, string>) => write(`ensemble.seen.${code}`, seen),

  // 共有やコピーを押した項目。LINEから戻ると開き直されることがあるので、残す。
  shared: (code: string) => read<string[]>(`ensemble.shared.${code}`, []),
  setShared: (code: string, ids: string[]) => write(`ensemble.shared.${code}`, ids),

  // URLを送る案内を閉じたか。
  linkSaved: (code: string) => read<boolean>(`ensemble.linkSaved.${code}`, false),
  setLinkSaved: (code: string) => write(`ensemble.linkSaved.${code}`, true),

  // 開いた相手。別の画面から戻っても、開き直しても、そのままにする。
  openParts: (code: string) => read<Record<string, boolean>>(`ensemble.openParts.${code}`, {}),
  setOpenParts: (code: string, parts: Record<string, boolean>) =>
    write(`ensemble.openParts.${code}`, parts),

  answers: (code: string) => read<Record<string, VaultAnswer>>(`ensemble.answers.${code}`, {}),
  setAnswer: (code: string, itemId: string, answer: VaultAnswer) =>
    write(`ensemble.answers.${code}`, {
      ...read<Record<string, VaultAnswer>>(`ensemble.answers.${code}`, {}),
      [itemId]: answer,
    }),

  // このボードの控えをすべて消す。入れた文が端末に残らないように。
  forgetBoard: (code: string) => {
    for (const name of [
      'draft',
      'screen',
      'pending',
      'proposal',
      'seen',
      'shared',
      'answers',
      'openParts',
      'linkSaved',
    ]) {
      remove(`ensemble.${name}.${code}`)
    }
    remove('ensemble.lastCode')
  },
}
