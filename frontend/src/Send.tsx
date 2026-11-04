import { useState } from 'react'
import type { Action, Board, Item } from './api'
import { Highlighted, PartIcon, toneOf } from './parts'
import { TrashButton } from './RemoveButton'
import { WORDS } from './words'

// 送る相手のいる列。病院は親が直接聞き、「自分のこと」は送らない。
const SEND_PARTS = new Set(['家族', '保育園・預け先', '職場', '支援'])

export type Send = { item: Item; action: Action | null }

// 案の連絡の項目のIDは、サーバーが案の位置から決めている（台帳に欄を足さないため）。
function sentItemId(branchId: string, outcome: number, action: number): string {
  return `s-${branchId}-${outcome}-${action}`
}

// まだ送っていない連絡。最新の案の分と、親が家族に頼みたいと入れた分。
export function sendsOf(board: Board): Send[] {
  const sends: Send[] = []
  const latest = board.branches[board.branches.length - 1]
  latest?.outcomes.forEach((outcome, o) => {
    outcome.actions.forEach((action, a) => {
      const item = board.items.find((entry) => entry.id === sentItemId(latest.id, o, a))
      if (item?.status === '頼みたい' && SEND_PARTS.has(item.part)) sends.push({ item, action })
    })
  })
  const listed = new Set(sends.map((send) => send.item.id))
  for (const item of board.items) {
    if (
      !listed.has(item.id) &&
      item.kind === '行動' &&
      item.part === '家族' &&
      item.status === '頼みたい' &&
      item.speaker === '親'
    ) {
      sends.push({ item, action: null })
    }
  }
  return sends
}

// 案から作った連絡か。全文は手紙なので、一覧では題を短くする。
export function isSentMessage(item: Item): boolean {
  return item.id.startsWith('s-')
}

function textOf(item: Item): string {
  return item.text_original ?? item.task_summary ?? ''
}

// 家族にだけ、返事を押せるリンクを付ける。職場や保育園にはボードの鍵を渡さない。
export function messageOf(item: Item, code: string): string {
  if (item.part !== '家族') return textOf(item)
  const link = `${window.location.origin}/m#${code}/${item.id}`
  return `${textOf(item)}\n\n${WORDS.send.linkLead}\n${link}`
}

export function SendCard({
  send,
  code,
  items,
  shared,
  busy,
  onShared,
  onAsked,
  onRemove,
}: {
  send: Send
  code: string
  items: Item[]
  shared: boolean
  busy: boolean
  onShared: () => void
  onAsked: () => void
  onRemove?: () => void
}) {
  const { item, action } = send
  const message = messageOf(item, code)
  const [note, setNote] = useState<string | null>(null)
  const [manual, setManual] = useState(false)
  const canShare = typeof navigator.share === 'function'
  const find = (id: string) => items.find((entry) => entry.id === id)
  const textOfId = (id: string) => find(id)?.short || id
  const basis = (action?.basis_ids ?? [])
    .map((id) => find(id))
    .filter((entry): entry is Item => !!entry && entry.id !== item.id)

  const share = async () => {
    setNote(null)
    try {
      await navigator.share({ text: message })
      onShared()
    } catch (error) {
      // 相手を選ばず閉じたときは何もしない。使えなかったときは手で写せるようにする。
      if (error instanceof DOMException && error.name === 'AbortError') return
      setManual(true)
      onShared()
    }
  }

  const copy = async () => {
    setNote(null)
    try {
      await navigator.clipboard.writeText(message)
      setNote(WORDS.send.copied)
      onShared()
    } catch {
      // LINEの中のブラウザでは使えないことがある。
      setManual(true)
      onShared()
    }
  }

  return (
    <article className={`card ${toneOf(item.part)}`} data-send={item.part} data-item={item.id}>
      <p className="part-head">
        <PartIcon part={item.part} />
        <span>
          {item.part}
          {item.owner && item.part === '家族' ? `（${item.owner}）` : ''}
          {WORDS.send.toSuffix}
        </span>
      </p>
      <p className="original">
        <Highlighted text={textOf(item)} marks />
      </p>
      {item.location && (
        <p className="detail">
          {WORDS.board.location}：{item.location}
        </p>
      )}
      {basis.length > 0 && (
        <details className="fold small">
          <summary>{WORDS.send.basisOpen(basis.length)}</summary>
          {basis.map((entry) => (
            <p className="detail" key={entry.id}>
              {WORDS.send.basis}：{entry.text_original ?? entry.task_summary}
            </p>
          ))}
        </details>
      )}
      {action?.release.map((id) => (
        <p className="detail" key={id}>
          {WORDS.decide.release(textOfId(id))}
        </p>
      ))}
      {manual && (
        <>
          <p className="message" role="status">
            {WORDS.send.manual}
          </p>
          <textarea className="copy-box" readOnly rows={5} value={message} aria-label={WORDS.send.manual} />
        </>
      )}
      {/* 次にすることと、押した結果は同じ行に出す。行を増やさない。 */}
      <p className="detail" role={note ? 'status' : undefined}>
        {note ?? WORDS.send.after}
      </p>
      <div className="send-actions">
        {canShare && (
          <button type="button" className="primary" onClick={() => void share()}>
            {WORDS.send.share}
          </button>
        )}
        <button type="button" onClick={() => void copy()}>
          {WORDS.send.copy}
        </button>
        {/* 送ったかどうかは親が決める。自動では変えない。次の一手が見えるよう常に出し、コピーか共有の前は薄くする。 */}
        <button type="button" className={shared ? undefined : 'faint'} disabled={busy} onClick={onAsked}>
          {WORDS.send.asked}
        </button>
        {onRemove && <TrashButton disabled={busy} onClick={onRemove} />}
      </div>
    </article>
  )
}
