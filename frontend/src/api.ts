// サーバーとのやりとり。家族コードはURLに入れず、ヘッダで渡す。

export type Item = {
  id: string
  kind: '情報' | '質問' | '行動' | '予定'
  part: string
  text_original: string | null
  task_summary: string | null
  speaker: string | null
  certainty: '確定' | '見込み' | '推測'
  status: string | null
  owner: string | null
  due: string | null
  location: string | null
  status_label: string | null
  medical_reply: string | null
  notice: string | null
}

export type Board = {
  version: number
  profile: { name: string }
  items: Item[]
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

export const api = {
  createBoard: (home: string) =>
    call<{ code: string; board: Board }>('/api/boards', {
      method: 'POST',
      body: JSON.stringify({ home }),
    }),
  getBoard: (code: string) => call<Board>('/api/board', {}, code),
  changeStatus: (code: string, id: string, version: number, status: string) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: JSON.stringify({ version, status }) },
      code,
    ),
  removeItem: (code: string, id: string, version: number) =>
    call<Board>(
      `/api/board/items/${encodeURIComponent(id)}?version=${version}`,
      { method: 'DELETE' },
      code,
    ),
}
