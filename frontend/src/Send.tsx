import { useState } from 'react'
import type { Action, Board, Item } from './api'
import { Highlighted, PartIcon, personWord, Timeline, toneOf } from './parts'
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
      if (item?.status === '頼みたい' && !item.medical_reply && SEND_PARTS.has(item.part))
        sends.push({ item, action })
    })
  })
  const listed = new Set(sends.map((send) => send.item.id))
  for (const item of board.items) {
    if (
      !listed.has(item.id) &&
      item.kind === '行動' &&
      item.part === '家族' &&
      item.status === '頼みたい' &&
      item.speaker === '親' &&
      !item.medical_reply &&
      !item.superseded &&
      !item.carried_into
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

// 親が家族に頼みたいと入れた用事（案から作った文ではない）。
function isOwnRequest(send: Send): boolean {
  return send.action === null && !isSentMessage(send.item) && send.item.part === '家族'
}

// 同じ相手のまだ送っていない親の頼みごとは、1通にまとめて送る（2026-11-07 PO の決定、Plan main の条件）。
// 文とリンクは1つ（リンクは最初の用事を開く）。夫の答えは用事ごと（残りは夫の画面の「ほかのお願い」から）。
export function sendGroupOf(sends: Send[], item: Item): Item[] {
  const first = sends.find((send) => send.item.id === item.id)
  if (!first || !isOwnRequest(first)) return [item]
  return sends
    .filter((send) => isOwnRequest(send) && send.item.part === item.part)
    .map((send) => send.item)
}

const squeeze = (text: string) => text.replace(/\s+/g, '')

// まとめた文。同じ原文から分かれた用事の文は1回だけ（Plan main の条件2）。ほかの文に含まれる文も外す。
// 親が最初の用事の文を直したときは、直した文がそのまま送る文になる。
export function groupTextOf(items: Item[]): string {
  if (items.length === 1 || items[0].events.some((event) => event.label === '直した'))
    return textOf(items[0])
  const texts: string[] = []
  for (const text of items.map(textOf).filter(Boolean)) {
    if (texts.some((kept) => squeeze(kept).includes(squeeze(text)))) continue
    for (let i = texts.length - 1; i >= 0; i -= 1) {
      if (squeeze(text).includes(squeeze(texts[i]))) texts.splice(i, 1)
    }
    texts.push(text)
  }
  return texts.join('\n')
}

export function groupMessageOf(items: Item[], code: string): string {
  const link = `${window.location.origin}/m#${code}/${items[0].id}`
  return `${groupTextOf(items)}\n\n${WORDS.send.linkLead}\n${link}`
}


type SendProps = {
  send: Send
  code: string
  items: Item[]
  shared: boolean
  busy: boolean
  onShared: () => void
  onAsked: () => void
}

// 送る文の中身と操作。次にやることの画面と、ボードの行で同じものを使う。
export function SendBody({ send, code, items, shared, busy, onShared, onAsked }: SendProps) {
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
    <>
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
      </div>
    </>
  )
}

export function SendCard(props: SendProps) {
  const { item } = props.send
  return (
    <article className={`card ${toneOf(item.part)}`} data-send={item.part} data-item={item.id}>
      <p className="part-head">
        <PartIcon part={item.part} />
        <span>
          {item.part}
          {item.owner && item.part === '家族' ? `（${personWord(item.owner)}）` : ''}
          {WORDS.send.toSuffix}
        </span>
      </p>
      <SendBody {...props} />
      <Timeline item={item} />
    </article>
  )
}
