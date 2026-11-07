import { useState, type ReactNode } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { device } from './device'
import type { Send } from './Send'
import { WORDS } from './words'

// 入院初日の職場への文（仕様 v3.5.11）。親が入れた職場あての頼みごとに［送る文を見る］を出し、
// 押したときだけAIが文を作る。文は端末が持ち、台帳には書かない（スキーマは契約）。
// 送る文の画面（Drafts）で確かめて直して送り、［送った］で今までどおり状態を変える。
// 切り替え（サーバーの work_first）が off なら何も出さない（今の「やることの1行」のまま）。

// 親の言葉の話し手（backend/ensemble/guards.py の PARENT_SPEAKERS と同じ）。
const PARENT_SPEAKERS = ['親', '母', '父', '私', '自分']
const WORK = '職場'

// サーバーの is_work_first_target と同じ判定。上司の文（原文なし）は親の頼みごとではない。
export function isWorkFirstTarget(board: Board, item: Item): boolean {
  const contacts = board.profile.contacts
  return (
    !!board.work_first &&
    item.part === WORK &&
    item.kind === '行動' &&
    item.status === '頼みたい' &&
    PARENT_SPEAKERS.includes(item.speaker ?? '') &&
    item.text_original !== null &&
    !item.medical_reply &&
    !item.released &&
    !item.id.startsWith('s-') &&
    !item.id.startsWith('n-') &&
    (contacts == null || contacts.includes(WORK))
  )
}

// 作ったときの親の文。親がもとの文を直したら、前の文から作った下書きは使わない。
function sourceOf(item: Item): string {
  return `${item.text_original ?? ''}\n${item.task_summary ?? ''}`
}

// 職場への文を作るボタン。作っている間と、作れなかったときの1行も出す。
function WorkFirstButton({
  code,
  item,
  onMade,
}: {
  code: string
  item: Item
  onMade: (draft: string) => void
}) {
  const words = WORDS.workFirst
  const [working, setWorking] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const make = async () => {
    setWorking(true)
    setFailed(null)
    try {
      const made = await api.workFirst(code, item.id)
      if (made.draft) onMade(made.draft)
      else setFailed(made.message || words.failed)
    } catch (error) {
      // 回数の上限など、サーバーが理由を返したときはそれを出す。
      setFailed(error instanceof ApiError && error.message ? error.message : words.failed)
    } finally {
      setWorking(false)
    }
  }
  // 知らせの1行とボタンは縦に並べる（ボタンの列 .row-action にはボタンだけを置く）。
  return (
    <div className="work-first" data-work-first={item.id}>
      {failed && (
        <p className="message" role="alert">
          {failed}
        </p>
      )}
      {working ? (
        <p className="detail" role="status">
          {words.working}
        </p>
      ) : (
        <div className="row-action">
          <button type="button" onClick={() => void make()}>
            {failed ? words.again : words.make}
          </button>
        </div>
      )}
    </div>
  )
}

// 作った後、送る文の画面の「職場」のタブを開いて、その上へ移る。
function focusWorkTab(): void {
  window.requestAnimationFrame(() =>
    window.requestAnimationFrame(() => {
      const tabs = document.querySelector('[data-draft-tabs]')
      const tab = [...(tabs?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])].find(
        (entry) => entry.textContent === WORK,
      )
      tab?.click()
      tabs?.scrollIntoView({ block: 'start' })
    }),
  )
}

export type WorkFirst = {
  // 送る文の一覧に足す分。台帳の原文はそのままで、端末の中でだけ送る文を差し替える。
  sendsOf: (items: Item[]) => Send[]
  // 送る文の画面の項目が、この端末の職場への文か（直すときにサーバーの台帳へ書かないため）。
  owns: (item: Item) => boolean
  edit: (item: Item, text: string) => Promise<boolean>
  button: (item: Item, onMade: () => void) => ReactNode
}

export function useWorkFirst(
  code: string,
  board: Board | null,
  say: (text: string | null) => void,
): WorkFirst {
  const [drafts, setDrafts] = useState(() => device.workDrafts(code))
  const real = (id: string) => board?.items.find((entry) => entry.id === id)
  const draftOf = (item: Item | undefined) => {
    if (!board || !item || !isWorkFirstTarget(board, item)) return null
    const entry = drafts[item.id]
    return entry && entry.source === sourceOf(item) ? entry : null
  }
  const keep = (item: Item, draft: string, edited: boolean) => {
    const next = {
      ...device.workDrafts(code),
      [item.id]: {
        draft,
        source: sourceOf(item),
        edited_at: edited ? new Date().toISOString() : undefined,
      },
    }
    device.setWorkDrafts(code, next)
    setDrafts(next)
  }
  return {
    sendsOf: (items) =>
      items.flatMap((item) => {
        const entry = draftOf(item)
        if (!entry) return []
        // 「あなたが編集済み」は、この下書きを直したときだけ（もとの文を直した記録とは分ける）。
        const events = item.events.filter((event) => event.label !== '直した')
        if (entry.edited_at) events.push({ at: entry.edited_at, label: '直した' })
        return [
          {
            item: { ...item, text_original: entry.draft, ask: null, events },
            action: { part: WORK, type: '伝える', draft: entry.draft, basis_ids: [], release: [] },
          },
        ]
      }),
    owns: (item) => !!draftOf(real(item.id)),
    edit: async (item, text) => {
      const target = real(item.id)
      if (!target) return false
      say(null)
      try {
        // 親が直した文には、医療の語だけ当てる（サーバーで確かめる。保存はしない）。
        const checked = await api.workFirstEdited(code, item.id, text)
        if (checked.draft) keep(target, checked.draft, true)
        say(WORDS.row.editSaved)
        return true
      } catch (error) {
        say(error instanceof ApiError && error.message ? error.message : WORDS.board.failed)
        return false
      }
    },
    button: (item, onMade) =>
      board && isWorkFirstTarget(board, item) && !draftOf(item) ? (
        <WorkFirstButton
          code={code}
          item={item}
          onMade={(draft) => {
            keep(item, draft, false)
            onMade()
            focusWorkTab()
          }}
        />
      ) : null,
  }
}
