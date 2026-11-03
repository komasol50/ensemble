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
}

export type CandidateDay = { day: string; certainty: string; cancelled: boolean }

export type Action = {
  part: string
  type: string
  draft: string | null
  basis_ids: string[]
  release: string[]
}

export type Branch = {
  id: string
  condition_item_id: string
  candidate_days: CandidateDay[]
  outcomes: { label: string; actions: Action[] }[]
}

export type WeekEntry = {
  text: string
  style: 'plain' | 'released' | 'likely' | 'confirmed' | 'cancelled'
}

export type Week = {
  columns: string[]
  rows: { label: string; today: boolean; cells: WeekEntry[][] }[]
  others: string[]
}

export type ImpactQuote = { speaker: string; text: string }

export type ImpactNode = {
  part: string
  who: string
  effects: string[]
  task: string
  before: string[]
  after: { text: string; state: string | null }[]
  basis: ImpactQuote[]
  kept: { text: string; state: string | null }[]
}

export type Impact = {
  headline: string
  basis: ImpactQuote[]
  nodes: ImpactNode[]
}

export type Board = {
  version: number
  profile: {
    name: string
    errand_people: { label: string; availability: string }[]
    older_child: string | null
    work: string | null
    supports: { name: string; detail: string }[]
  }
  items: Item[]
  branches: Branch[]
  week: Week | null
  impact: Impact | null
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
export type ProposalResponse = {
  item_id: string
  stage: string
  branch: Branch | null
  message: string | null
  week: Week | null
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
  intake: (code: string, requestId: string, text: string, said?: string, who?: string) =>
    call<IntakeResponse>(
      '/api/board/intake',
      { method: 'POST', body: JSON.stringify({ request_id: requestId, text, said, who }) },
      code,
    ),
  propose: (code: string, itemId: string) =>
    call<ProposalResponse>(
      '/api/board/proposal',
      { method: 'POST', body: JSON.stringify({ item_id: itemId }) },
      code,
    ),
  acceptBranch: (code: string, version: number, branch: Branch) =>
    call<Board>(
      '/api/board/branches',
      { method: 'POST', body: JSON.stringify({ version, branch }) },
      code,
    ),
  createBoard: (home: string) =>
    call<{ code: string; board: Board }>('/api/boards', {
      method: 'POST',
      body: JSON.stringify({ home }),
    }),
  getBoard: (code: string) => call<Board>('/api/board', {}, code),
  changeStatus: (code: string, id: string, version: number, status: string, due?: string) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: JSON.stringify({ version, status, due }) },
      code,
    ),
  reply: (code: string, id: string, answer: string, when?: string) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}/reply`,
      { method: 'POST', body: JSON.stringify({ answer, when }) },
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
  removeItem: (code: string, id: string, version: number) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}?version=${version}`,
      // 画面を閉じる間際に送っても届くように。
      { method: 'DELETE', keepalive: true },
      code,
    ),
}
