// サーバーとのやりとり。家族コードはURLに入れず、ヘッダで渡す。

export type Item = {
  id: string
  kind: '情報' | '質問' | '行動' | '予定'
  part: string
  text_original: string | null
  task_summary: string | null
  speaker: string | null
  recorded_at: string | null
  refers_to: string | null
  certainty: '確定' | '見込み' | '推測'
  status: string | null
  owner: string | null
  due: string | null
  location: string | null
  status_label: string | null
  medical_reply: string | null
  notice: string | null
  released: boolean
  released_by: string | null
  short: string
  outdated: boolean
  superseded: boolean
  original_fixed: string | null
  ask: string | null
  events: ItemEvent[]
}

// 項目に起きたことと時刻。入れた時刻の後に、送った・返事・済・直したが続く。
export type ItemEvent = { at: string; label: string }

// 親が直す欄。送らなかった欄は変わらない。
export type ItemChanges = {
  text_original?: string
  task_summary?: string
  part?: string
  owner?: string
  due?: string
}

export type CandidateDay = { day: string; certainty: string; cancelled: boolean }

export type Action = {
  part: string
  type: string
  draft: string | null
  basis_ids: string[]
  release: string[]
  ask?: string | null
}

// 分かれ道の先の日。医師の言葉の引用と決まった種類だけで、文はAIに書かせない。
export type NextStep = { day: string; kind: '退院候補' | '検査' | 'その他'; clause: string }

export type Branch = {
  id: string
  condition_item_id: string
  candidate_days: CandidateDay[]
  outcomes: {
    label: string
    actions: Action[]
    clause?: string | null
    clause_item_id?: string | null
    next?: NextStep[]
  }[]
}

export type WeekEntry = {
  text: string
  style: 'plain' | 'released' | 'likely' | 'confirmed' | 'cancelled'
  item_id?: string
  part?: string
}

export type Week = {
  columns: string[]
  rows: { label: string; today: boolean; date: string; weekday: string; cells: WeekEntry[][] }[]
  others: string[]
  discharge?: Discharge | null
}

// 図の⑩。行の年月（iso）と、マスの印（語は種類から。用件の語は印にしない）。
export type CalendarMark = {
  text: string
  tone: 'likely' | 'confirmed' | 'cancelled' | 'review' | 'test'
}
export type CalendarRow = Week['rows'][number] & { iso?: string; mark?: CalendarMark | null }
// 退院の候補日が1つも残っていないときだけ来る。
export type Discharge = { undecided: boolean; previous: string[] }

export type ImpactQuote = { speaker: string; text: string }

export type ImpactNode = {
  part: string
  who: string
  effects: string[]
  task: string
  change: string | null
  before: string[]
  after: { text: string; state: string | null }[]
  basis: ImpactQuote[]
  kept: { text: string; state: string | null }[]
}

export type Impact = {
  cause: string | null
  headline: string
  basis: ImpactQuote[]
  nodes: ImpactNode[]
  next?: NextRow[]
  candidates?: { day: string; certainty: string; cancelled: boolean }[]
}

// 分かれ道の先の1行。医師の言葉の引用、書かれた日、決まった種類。
export type NextRow = {
  clause: string
  day: string
  kind: '退院候補' | '検査' | 'その他'
  certainty: string
  cancelled: boolean
}

// 「いま」の予定の欄。医師の言葉の節と、書かれた日。サーバーが台帳から作り、台帳には書かない。
export type DoctorView = {
  item_id: string
  recorded_at: string | null
  text: string
  clauses: { text: string; days: string[] }[]
  // title があるときは図の⑤の形（「退院の予定になった」＋日＋未定）。無いときは節をそのまま出す。
  options: {
    label: string
    days: string[]
    result: string
    certainty: string
    title?: string
    note?: string
  }[]
  after_title: string | null
  open: boolean
  due: boolean
  candidates: { day: string; certainty: string; cancelled: boolean }[]
  paths: NextRow[]
}

// 自分の家庭の設定。全部選ぶ形。
export type SetupChoices = {
  parent: string | null
  adult: string | null
  child: string | null
  school: string | null
  work: string | null
  errand: string | null
  errand_time: string | null
  bedside: string | null
  supports: string[]
  no_support: boolean
  // 連絡する相手（夫・保育園・職場）。無いときは誰も外さない。
  contacts?: string[] | null
}

export type Board = {
  version: number
  today: string
  profile: {
    name: string
    errand_people: { label: string; availability: string }[]
    older_child: string | null
    work: string | null
    supports: { name: string; detail: string }[]
    configured: boolean
    choices: SetupChoices | null
    // 連絡する相手の台帳の相手（家族・保育園・預け先・職場）。null は誰も外さない。
    contacts?: string[] | null
  }
  rules: { id: string; text: string; active: boolean }[]
  items: Item[]
  branches: Branch[]
  week: Week | null
  calendar: Week | null
  impact: Impact | null
  doctor: DoctorView | null
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// 応答が返らないまま止まる通信を、あきらめて送り直せるようにする上限。
// サーバーのAIの待ち（1回180秒、呼び直しで2回）より長くする。
const REQUEST_LIMIT_MS = 400_000

async function call<T>(path: string, init: RequestInit = {}, code?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (code) headers.set('X-Family-Code', code)
  if (init.body) headers.set('Content-Type', 'application/json')
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_LIMIT_MS)
  const response = await fetch(path, { ...init, headers, signal: controller.signal }).finally(() =>
    window.clearTimeout(timer),
  )
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null
    throw new ApiError(
      response.status,
      typeof body?.detail === 'string' ? body.detail : '',
    )
  }
  return (await response.json()) as T
}

export type IntakeResponse = { board: Board; added: string[]; trigger: string | null }
export type FreeSearchResponse = {
  medical: string | null
  found: boolean
  doc_id?: string | null
  quote?: string | null
}

export type ProposalResponse = {
  item_id: string
  stage: string
  branch: Branch | null
  message: string | null
  week: Week | null
  impact: Impact | null
}

export type TrashEntry = {
  id: string
  item_id: string
  part: string
  text: string
  status_label: string | null
  withdrew: boolean
}

export type ReadResponse = { text: string | null; refused: boolean; message: string | null }
export type SearchResponse = { found: boolean; doc_id: string | null; quote: string | null }

export const api = {
  readPhoto: (code: string, image: string, mediaType: string, category: string) =>
    call<ReadResponse>(
      '/api/photo/read',
      { method: 'POST', body: JSON.stringify({ image, media_type: mediaType, category }) },
      code,
    ),
  searchItem: (
    code: string,
    id: string,
    docs: { id: string; category: string; text: string }[],
  ) =>
    call<SearchResponse>(
      `/api/board/items/${encodeURIComponent(id)}/search`,
      { method: 'POST', body: JSON.stringify({ docs }) },
      code,
    ),
  searchFree: (
    code: string,
    question: string,
    docs: { id: string; category: string; text: string }[],
  ) =>
    call<FreeSearchResponse>(
      '/api/board/search',
      { method: 'POST', body: JSON.stringify({ question, docs }) },
      code,
    ),
  addQuestion: (code: string, version: number, text: string) =>
    call<Board>(
      '/api/board/items',
      {
        method: 'POST',
        body: JSON.stringify({
          version,
          kind: '質問',
          part: '病院',
          certainty: '見込み',
          text_original: text,
          speaker: '親',
          status: '未確認',
        }),
      },
      code,
    ),
  intake: (code: string, requestId: string, text: string, said?: string, who?: string) =>
    call<IntakeResponse>(
      '/api/board/intake',
      { method: 'POST', body: JSON.stringify({ request_id: requestId, text, said, who }) },
      code,
    ),
  propose: (code: string, itemId: string, chosen?: string) =>
    call<ProposalResponse>(
      '/api/board/proposal',
      { method: 'POST', body: JSON.stringify({ item_id: itemId, chosen }) },
      code,
    ),
  acceptBranch: (code: string, version: number, branch: Branch) =>
    call<Board>(
      '/api/board/branches',
      { method: 'POST', body: JSON.stringify({ version, branch }) },
      code,
    ),
  createBoard: (home: string, choices?: SetupChoices) =>
    call<{ code: string; board: Board }>('/api/boards', {
      method: 'POST',
      body: JSON.stringify({ home, choices }),
    }),
  changeSetup: (code: string, version: number, choices: SetupChoices) =>
    call<Board>(
      '/api/board/setup',
      { method: 'PUT', body: JSON.stringify({ version, choices }) },
      code,
    ),
  getBoard: (code: string) => call<Board>('/api/board', {}, code),
  changeStatus: (code: string, id: string, version: number, status: string, due?: string) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: JSON.stringify({ version, status, due }) },
      code,
    ),
  undoStatus: (code: string, id: string, version: number) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}/undo`,
      { method: 'POST', body: JSON.stringify({ version }) },
      code,
    ),
  editItem: (code: string, id: string, version: number, changes: ItemChanges) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}/content`,
      { method: 'PATCH', body: JSON.stringify({ version, ...changes }) },
      code,
    ),
  reply: (code: string, id: string, answer: string, when?: string, note?: string) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}/reply`,
      { method: 'POST', body: JSON.stringify({ answer, when, note }) },
      code,
    ),
  getTrash: (code: string) => call<{ entries: TrashEntry[] }>('/api/board/trash', {}, code),
  restoreTrash: (code: string, id: string) =>
    call<Board>(`/api/board/trash/${encodeURIComponent(id)}/restore`, { method: 'POST' }, code),
  purgeTrash: (code: string, id: string) =>
    call<{ purged: boolean }>(
      `/api/board/trash/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
      code,
    ),
  deleteBoard: (code: string) =>
    call<{ deleted: boolean }>('/api/board', { method: 'DELETE' }, code),
  // 親の一言。AIを呼ばずに台帳へ入れる。
  // 説明を聞いたあとの結果を、選んだ節から入れる。親が入れた文として扱う。
  addResult: (
    code: string,
    version: number,
    text: string,
    certainty: string,
    refersTo: string,
  ) =>
    call<Board>(
      '/api/board/items',
      {
        method: 'POST',
        body: JSON.stringify({
          version,
          kind: '情報',
          part: '病院',
          certainty,
          text_original: text,
          speaker: '親',
          medical: 'info',
          refers_to: refersTo,
        }),
      },
      code,
    ),
  addNote: (code: string, version: number, text: string) =>
    call<Board>(
      '/api/board/items',
      {
        method: 'POST',
        body: JSON.stringify({
          version,
          kind: '情報',
          part: '親',
          certainty: '確定',
          text_original: text,
          speaker: '親',
        }),
      },
      code,
    ),
  removeItem: (code: string, id: string, version: number) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}?version=${version}`,
      // 画面を閉じる間際に送っても届くように。
      { method: 'DELETE', keepalive: true },
      code,
    ),
}
