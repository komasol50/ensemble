import { pushPlanner } from './planner'
import type { Board, Item } from './api'
import { WORDS } from './words'

// 持ち物の控え（v3.6）。端末に残し、サーバーの台帳の外の表にも送る（planner.ts）。AIへは渡さない。
// 案（v3.6.1）は PackingSuggestions.tsx が作り、親が押した物だけここに入る。しおりの引用はここに入れない。
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
  // 足した物。親が足した物は一覧の最後に、AIの案から足した物はその組の一番下に並べる。
  added: Added[]
  // 持ち物の案（v3.6.1）。前の版の控えには無い（読み込むときに空で補う）。
  // 断った案と、消した物の名前（比べる形）。同じ名前を、別の端末でも二度と案に出さない。
  rejected: string[]
  // 外す案に［残す］を押した型の物（ID）。
  kept: string[]
  // まだ押していない案。しおりの引用は入れない（端末の中の ensemble.packingQuotes.<code> にだけ置く）。
  proposals: Proposal[]
  // 初めて開いたときの1回の案を作ったか。作った案の回数（2回目からは「新しい案」）。
  packingRounds: number
  // 案を作ったときの設定の指紋（サーバーの packing_setting）。変わったら「設定が変わりました」。
  packingSetting: string | null
}

// 出どころ。template は型（出どころの1行を出さない）、user は親が足した物。
export type Source = 'template' | 'user' | 'setting' | 'shiori'

export type Added = {
  id: string
  name: string
  // 組の番号（AIの案から足した物）。無ければ親が足した物で、見出しの無い最後の段。
  group?: number | null
  source?: 'user' | 'setting' | 'shiori'
  // 設定から：かっこの中の語（サーバーのコードが設定の値から作った語）。
  basis?: string
}

// まだ押していない案1件。引用は持たない（端末の中の別の名前に置く）。
export type Proposal = {
  id: string
  action: 'add' | 'remove'
  name: string
  group: number
  target_id?: string | null
  source: 'setting' | 'shiori'
  basis?: string
}

export type Thing = {
  id: string
  name: string
  // 初日に頼むことが多い物。画面に印は出さない。
  star: boolean
  // 組の番号。親が足した物は null。
  group: number | null
  source: Source
  basis?: string
  // 親が名前を直したか。
  edited: boolean
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
  rejected: [],
  kept: [],
  proposals: [],
  packingRounds: 0,
  packingSetting: null,
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
      rejected: Array.isArray(value.rejected) ? value.rejected : [],
      kept: Array.isArray(value.kept) ? value.kept : [],
      proposals: Array.isArray(value.proposals) ? value.proposals : [],
      packingRounds: typeof value.packingRounds === 'number' ? value.packingRounds : 0,
      packingSetting: typeof value.packingSetting === 'string' ? value.packingSetting : null,
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
      things.push({
        id,
        name: saved.names[id] ?? entry.name,
        star: entry.star,
        group: g,
        source: 'template',
        edited: id in saved.names,
      })
    })
  })
  const groups = WORDS.checklist.groups.length
  for (const entry of saved.added) {
    const group =
      typeof entry.group === 'number' && entry.group >= 0 && entry.group < groups ? entry.group : null
    things.push({
      id: entry.id,
      name: saved.names[entry.id] ?? entry.name,
      star: false,
      group,
      source: entry.source ?? 'user',
      basis: entry.basis,
      edited: entry.id in saved.names,
    })
  }
  return things
}

// 名前の比べる形（NFKC と空白を取る）。サーバーの packing.normalize_name と同じ。
export function normName(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '')
}

// 足した物か（型の物でないか）。消すときに、型の物は removed へ、足した物は added から外す。
export function isAdded(saved: Saved, id: string): boolean {
  return saved.added.some((entry) => entry.id === id)
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
