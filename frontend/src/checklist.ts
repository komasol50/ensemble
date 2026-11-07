import { pushPlanner } from './planner'
import type { Board, Item } from './api'
import { WORDS } from './words'

// 持ち物の控え（v3.6）。この端末にだけ残し、台帳には書かない。AIは使わない。
// 残せない端末（LINEの中のブラウザなど）でも、初めの一覧で画面は出る。
// 記録を消すときは device.forgetBoard が同じ名前（ensemble.checklist.<code>）を消す。

export type Saved = {
  // 「ある」を押した物（id → true）。
  marks: Record<string, boolean>
  // 頼んだ物と、作ったやることの項目のID・頼んだ人。
  asked: Record<string, { item: string; person: string }>
  // 置き場所。台帳の項目のありかは後から直す口が無いので、端末にだけ持つ。
  places: Record<string, string>
  // 直した名前（数を書き足すなど）。
  names: Record<string, string>
  // この端末の一覧から消した物（初めの一覧の物）。
  removed: string[]
  // いらない物。組の下の畳みへ移し、数に入れない。
  unneeded: string[]
  // 足した物。一覧の最後に並べる。
  added: { id: string; name: string }[]
}

export type Thing = {
  id: string
  name: string
  // 初日に頼むことが多い物。画面に印は出さない。
  star: boolean
  // 初めの一覧の組の番号。足した物は null。
  group: number | null
}

export type ThingState = 'have' | 'asked' | 'todo' | 'unneeded'

const EMPTY: Saved = {
  marks: {},
  asked: {},
  places: {},
  names: {},
  removed: [],
  unneeded: [],
  added: [],
}

function keyOf(code: string): string {
  return `ensemble.checklist.${code}`
}

export function loadChecklist(code: string): Saved {
  try {
    const raw = localStorage.getItem(keyOf(code))
    if (raw === null) return EMPTY
    const value = JSON.parse(raw) as Partial<Saved> | null
    if (!value || typeof value !== 'object') return EMPTY
    return {
      marks: value.marks ?? {},
      asked: value.asked ?? {},
      places: value.places ?? {},
      names: value.names ?? {},
      removed: Array.isArray(value.removed) ? value.removed : [],
      unneeded: Array.isArray(value.unneeded) ? value.unneeded : [],
      added: Array.isArray(value.added) ? value.added : [],
    }
  } catch {
    return EMPTY
  }
}

export function saveChecklist(code: string, saved: Saved): void {
  try {
    localStorage.setItem(keyOf(code), JSON.stringify(saved))
  } catch {
    // 残せなくても、今の画面はそのまま使える。
  }
  // サーバーにも送る（2026-11-08 PO の決定。別の端末・シークレットモードでも戻るように）。
  pushPlanner(code)
}

// 初めの一覧の物のID。組と順番から決める。
function defaultId(group: number, index: number): string {
  return `g${group}-${index}`
}

// 今の一覧。初めの一覧から消した物を外し、直した名前にし、足した物を最後に足す。
export function thingsOf(saved: Saved): Thing[] {
  const removed = new Set(saved.removed)
  const things: Thing[] = []
  WORDS.checklist.groups.forEach((group, g) => {
    group.items.forEach((entry, i) => {
      const id = defaultId(g, i)
      if (removed.has(id)) return
      things.push({ id, name: saved.names[id] ?? entry.name, star: entry.star, group: g })
    })
  })
  for (const entry of saved.added) {
    things.push({ id: entry.id, name: saved.names[entry.id] ?? entry.name, star: false, group: null })
  }
  return things
}

// 頼んだ物の、台帳のやることの項目。ごみ箱へ移したなどで見つからなければ、頼む前に戻す。
export function linkedItem(saved: Saved, id: string, board: Board): Item | undefined {
  const link = saved.asked[id]
  return link ? board.items.find((item) => item.id === link.item) : undefined
}

// やることが届いた・完了になったら「ある」にする。
export function isDelivered(item: Item | undefined): boolean {
  return item?.status === '届いた' || item?.status === '完了'
}

export function stateOf(saved: Saved, id: string, board: Board): ThingState {
  if (saved.unneeded.includes(id)) return 'unneeded'
  const item = linkedItem(saved, id, board)
  if (saved.marks[id] || isDelivered(item)) return 'have'
  return item ? 'asked' : 'todo'
}

// 「いま」の画面でも使う数。いらない物は数えない。touched は「ある」を押したか、頼んだ物が1つでもあるか。
export function checklistCounts(
  code: string,
  board: Board,
): { have: number; asked: number; todo: number; touched: boolean } {
  const saved = loadChecklist(code)
  const counts = { have: 0, asked: 0, todo: 0 }
  for (const thing of thingsOf(saved)) {
    const state = stateOf(saved, thing.id, board)
    if (state !== 'unneeded') counts[state] += 1
  }
  const touched =
    Object.values(saved.marks).some(Boolean) || Object.keys(saved.asked).length > 0
  return { ...counts, touched }
}

// 用事を頼める人。いなければ空（［頼む］を出さない）。
export function askablePeople(board: Board): string[] {
  return board.profile.errand_people
    .map((person) => person.label)
    .filter((label) => !!label && label !== 'いない')
}
