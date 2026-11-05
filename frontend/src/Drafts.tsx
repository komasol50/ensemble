import { useState } from 'react'
import type { Item } from './api'
import { Highlighted, toneOf, whoWord } from './parts'
import { titleOf } from './Row'
import { messageOf, type Send } from './Send'
import { WORDS } from './words'

const NOTE_MAX = 2000
// タブの順。頼む相手を先に。
const TAB_ORDER = ['家族', '職場', '保育園・預け先', '支援']

// 下書きを相手ごとに確かめて送る。送るのは親で、押した後に［伝えた］で記録する。
export function DraftTabs({
  sends,
  code,
  errand,
  shared,
  busy,
  onShared,
  onAsked,
  onEdit,
  onLater,
}: {
  sends: Send[]
  code: string
  errand?: string
  shared: string[]
  busy: boolean
  onShared: (item: Item) => void
  onAsked: (item: Item) => void
  onEdit: (item: Item, text: string) => Promise<boolean>
  onLater: () => void
}) {
  const words = WORDS.drafts
  const parts = TAB_ORDER.filter((name) => sends.some((send) => send.item.part === name))
  const [chosen, setChosen] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [manual, setManual] = useState(false)
  // 送った相手は一覧から消えるので、次の相手へ自動で移る。
  const part = chosen && parts.includes(chosen) ? chosen : parts[0]
  const item = sends.find((send) => send.item.part === part)?.item
  if (!item) return null
  const message = messageOf(item, code)
  const isEditing = editing === item.id
  const isShared = shared.includes(item.id)
  const canShare = typeof navigator.share === 'function'
  const edited = item.events.some((event) => event.label === '直した')

  const share = async () => {
    setNote(null)
    try {
      await navigator.share({ text: message })
      onShared(item)
    } catch (error) {
      // 相手を選ばず閉じたときは何もしない。使えなかったときは手で写せるようにする。
      if (error instanceof DOMException && error.name === 'AbortError') return
      setManual(true)
      onShared(item)
    }
  }
  const copy = async () => {
    setNote(null)
    try {
      await navigator.clipboard.writeText(message)
      setNote(WORDS.send.copied)
      onShared(item)
    } catch {
      // LINEの中のブラウザでは使えないことがある。
      setManual(true)
      onShared(item)
    }
  }

  return (
    <section className="draft-tabs" data-draft-tabs>
      <h2>{words.title}</h2>
      <p className="detail">{words.count(parts.length)}</p>
      <div className="draft-tablist" role="tablist" aria-label={words.title}>
        {parts.map((entry) => (
          <button
            type="button"
            role="tab"
            key={entry}
            className="draft-tab"
            aria-selected={entry === part}
            onClick={() => {
              setChosen(entry)
              setEditing(null)
              setNote(null)
              setManual(false)
            }}
          >
            {whoWord(entry, errand)}
          </button>
        ))}
      </div>
      <article className={`card ${toneOf(item.part)}`} data-draft={item.part} data-draft-item={item.id}>
        <div className="draft-head">
          <h3>{item.ask || titleOf(item)}</h3>
          <span className="chip">{words.unsent}</span>
        </div>
        {isEditing ? (
          <label className="plan-edit">
            {WORDS.decide.editLabel(whoWord(item.part, errand))}
            <textarea
              rows={6}
              maxLength={NOTE_MAX}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
        ) : (
          <p className="original">
            <Highlighted text={item.text_original ?? item.task_summary ?? ''} marks />
          </p>
        )}
        {edited && !isEditing && <p className="detail">{words.edited}</p>}
        {item.location && (
          <p className="detail">
            {WORDS.board.location}：{item.location}
          </p>
        )}
        {manual && (
          <>
            <p className="message" role="status">
              {WORDS.send.manual}
            </p>
            <textarea
              className="copy-box"
              readOnly
              rows={5}
              value={message}
              aria-label={WORDS.send.manual}
            />
          </>
        )}
        {note && (
          <p className="detail" role="status">
            {note}
          </p>
        )}
        {isEditing ? (
          <div className="send-actions">
            <button type="button" onClick={() => setEditing(null)}>
              {WORDS.add.back}
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !draft.trim()}
              onClick={() =>
                void onEdit(item, draft.trim()).then((saved) => {
                  if (saved) setEditing(null)
                })
              }
            >
              {WORDS.decide.editDone}
            </button>
          </div>
        ) : (
          <>
            <div className="send-actions">
              {canShare && (
                <button type="button" onClick={() => void share()}>
                  {words.share}
                </button>
              )}
              <button type="button" onClick={() => void copy()}>
                {WORDS.send.copy}
              </button>
            </div>
            {/* 送ったかどうかは親が決める。共有かコピーの後に押せるようにする。 */}
            {isShared && (
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => {
                  setNote(null)
                  setManual(false)
                  onAsked(item)
                }}
              >
                {WORDS.send.asked}
              </button>
            )}
            <div className="send-actions">
              <button
                type="button"
                className="quiet"
                onClick={() => {
                  setDraft(item.text_original ?? '')
                  setEditing(item.id)
                }}
              >
                {WORDS.decide.edit}
              </button>
              <button type="button" className="quiet" onClick={onLater}>
                {words.later}
              </button>
            </div>
          </>
        )}
      </article>
      {item.part === '家族' && <p className="detail">{words.linkNote}</p>}
    </section>
  )
}
