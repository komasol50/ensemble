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

// 夫のリンクから開いたタブの印。このタブの間だけ残す。
function linkMark(): string | null {
  try {
    return sessionStorage.getItem('ensemble.fromLink')
  } catch {
    return null
  }
}

function linkBack(): string | null {
  try {
    return sessionStorage.getItem('ensemble.fromLinkBack')
  } catch {
    return null
  }
}

function setLinkMark(code: string): void {
  try {
    sessionStorage.setItem('ensemble.fromLink', code)
    // 夫が自分の用事の画面へ戻るための先。
    sessionStorage.setItem(
      'ensemble.fromLinkBack',
      `${window.location.pathname}${window.location.hash}`,
    )
  } catch {
    // 残せなければ、いつもの語で開く。
  }
}

// 夫が押した語。台帳には状態だけを持つので、この端末でだけ押した語のまま見せる。
export function answerWord(itemId: string): string | null {
  return read<string | null>(`ensemble.answer.${itemId}`, null)
}

export function rememberAnswer(itemId: string, word: string): void {
  write(`ensemble.answer.${itemId}`, word)
}

import type { Branch, Impact, Week } from './api'

// failed は仕分けを断られたときの文。無ければ、つながり次第また送る。
// said と who は親が選んだこと（任意）。
export type Pending = {
  id: string
  text: string
  at: string
  failed?: string
  limit?: boolean
  said?: string
  who?: string
}
export type Screen = 'board' | 'add' | 'decide' | 'vault' | 'map' | 'trash' | 'menu' | 'after' | 'setup'

// 親が押すまで台帳に入れないので、案の控えは端末が持つ。
// after は、説明を聞いたあとの選択で入れた結果の案。失敗したら取り消せる。
export type WorkingProposal = {
  state: 'working'
  item_id: string
  after?: boolean
  // 親が説明を聞いたあとの画面で選んだ節。文を読み直させず、選択のまま渡す。
  chosen?: string
}
export type ReadyProposal = {
  state: 'ready'
  item_id: string
  branch: Branch
  // 押す前の線と帯。サーバーが控えのコピーから計算する。古い控えには無い。
  impact?: Impact | null
  week?: Week | null
}
export type FailedProposal = {
  state: 'failed'
  item_id: string
  message: string
  after?: boolean
  chosen?: string
  // 回数の上限。押し直しても通らないので［もう一度］を出さない。
  limit?: boolean
}
export type Proposal = WorkingProposal | ReadyProposal | FailedProposal

// 保管庫から探した結果。資料の文なので、サーバーには置かず端末が持つ。
export type VaultAnswer =
  | { found: true; photoId: string; category: string; quote: string }
  | { found: false }

// 開いている画面はこのタブの間だけ覚える。読み直しても同じ画面、開き直すと「いま」から。
function tabView(code: string): string | null {
  try {
    return sessionStorage.getItem(`ensemble.view.${code}`)
  } catch {
    return null
  }
}

function setTabView(code: string, view: string): void {
  try {
    sessionStorage.setItem(`ensemble.view.${code}`, view)
  } catch {
    // 覚えられなくても、今の画面はそのまま使える。
  }
}

export const device = {
  tabView,
  setTabView,
  fromLink: (code: string): boolean => linkMark() === code,
  linkBack,
  setFromLink: setLinkMark,
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

  // 開いた時点の台帳の最後の時刻。これより後の返事だけを「届いた」と言う。サーバーの時刻で比べる。
  replyBase: (code: string) => read<number | null>(`ensemble.replyBase.${code}`, null),
  setReplyBase: (code: string, time: number) => write(`ensemble.replyBase.${code}`, time),
  // ［わかった］で閉じた医療の問い。固定文は行の中に残る。
  medicalClosed: (code: string) => read<string[]>(`ensemble.medicalClosed.${code}`, []),
  setMedicalClosed: (code: string, ids: string[]) =>
    write(`ensemble.medicalClosed.${code}`, ids),
  seen: (code: string) => read<Record<string, string> | null>(`ensemble.seen.${code}`, null),
  setSeen: (code: string, seen: Record<string, string>) => write(`ensemble.seen.${code}`, seen),

  // 「候補日ごとに空きを確認する」を一度押した医師の言葉（項目のIDと記録の時刻）。押した後は出さない。
  // 案をやめても出し直さない（ずっと残る次にできることにしない）。
  candidateChecked: () => read<string[]>('ensemble.candidateChecked', []),
  markCandidateChecked: (key: string) =>
    write('ensemble.candidateChecked', [...read<string[]>('ensemble.candidateChecked', []), key]),

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
      'replyBase',
      'medicalClosed',
    ]) {
      remove(`ensemble.${name}.${code}`)
    }
    try {
      sessionStorage.removeItem(`ensemble.view.${code}`)
    } catch {
      // 使えないブラウザでは、もともと残っていない。
    }
    remove('ensemble.lastCode')
  },
}
