// サーバーとのやりとり。家族コードはURLに入れず、ヘッダで渡す。

export type Item = {
  id: string
  kind: '情報' | '質問' | '行動' | '予定'
  part: string
  text_original: string | null
  task_summary: string | null
  speaker: string | null
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

export type Board = {
  version: number
  profile: { name: string }
  items: Item[]
  branches: Branch[]
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, init: RequestInit = {}, code?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (code) headers.set('X-Family-Code', code)
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
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
}

export const api = {
  intake: (code: string, requestId: string, text: string) =>
    call<IntakeResponse>(
      '/api/board/intake',
      { method: 'POST', body: JSON.stringify({ request_id: requestId, text }) },
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
  deleteBoard: (code: string) =>
    call<{ deleted: boolean }>('/api/board', { method: 'DELETE' }, code),
  removeItem: (code: string, id: string, version: number) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}?version=${version}`,
      { method: 'DELETE' },
      code,
    ),
}
