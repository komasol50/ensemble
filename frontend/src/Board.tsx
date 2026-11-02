import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { DayBand } from './Decide'
import { device, type Pending, type Proposal } from './device'
import { RemoveButton } from './RemoveButton'
import { SendCard, sendsOf } from './Send'
import { COLUMNS, WORDS } from './words'

// 別の端末の変更を見に行く間隔。見えている間だけ。AIは呼ばない。
const POLL_MS = 3000
const REPLIED = new Set(['引き受けた', '難しい', '保留', '時刻つきで可'])

type Props = {
  code: string
  pending: Pending[]
  proposal: Proposal | null
  refresh: number
  onRemovePending: (id: string) => void
  onSortAgain: (id: string) => void
  onRetry: () => void
  onOpenProposal: () => void
  onCloseProposal: () => void
  onAdd: () => void
  onConsent: () => void
  onLeave: () => void
}

function fingerprint(item: Item): string {
  return `${item.status ?? ''}|${item.due ?? ''}`
}

function snapshot(board: Board): Record<string, string> {
  return Object.fromEntries(board.items.map((item) => [item.id, fingerprint(item)]))
}

// 済を押したときの行き先。情報と予定には状態が無いので、ボタンを出さない。
function doneStatus(item: Item): string | null {
  if (item.kind === '行動') {
    return item.status === '完了' || item.status === '届いた' ? null : '完了'
  }
  if (item.kind === '質問') return item.status === '答えあり' ? null : '答えあり'
  return null
}

function textOfOriginal(items: Item[], id: string): string {
  const found = items.find((item) => item.id === id)
  return found?.text_original ?? found?.task_summary ?? ''
}

export function BoardScreen({
  code,
  pending,
  proposal,
  refresh,
  onRemovePending,
  onSortAgain,
  onRetry,
  onOpenProposal,
  onCloseProposal,
  onAdd,
  onConsent,
  onLeave,
}: Props) {
  const [board, setBoard] = useState<Board | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'missing' | 'offline'>('loading')
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [shared, setShared] = useState<string[]>(() => device.shared(code))
  const shownVersion = useRef(0)

  // 前に見たときから変わった項目に「新」を付ける。台帳に欄を足さず、端末の控えと比べる。
  const receive = useCallback(
    (next: Board, ownChange = false) => {
      shownVersion.current = next.version
      const seen = device.seen(code)
      if (!ownChange && seen) {
        const changed = next.items
          .filter((item) => seen[item.id] !== fingerprint(item))
          .map((item) => item.id)
        setNewIds((current) => new Set([...current, ...changed]))
      }
      device.setSeen(code, snapshot(next))
      setBoard(next)
      setLoadState('ready')
    },
    [code],
  )

  const load = useCallback(async () => {
    try {
      receive(await api.getBoard(code))
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setLoadState('missing')
      else setLoadState((current) => (current === 'ready' ? current : 'offline'))
    }
  }, [code, receive])

  // 仕分けが終わったら読み直す。
  useEffect(() => {
    if (refresh > 0) void load()
  }, [refresh, load])

  useEffect(() => {
    void load()
    // 別の端末の変更は、この画面に戻ったときに読み直す。
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [load])

  // 夫が押した答えが、開いたままの画面に戻るように見に行く。
  // 自分の保存より古い応答で画面を戻さないよう、版が進んだときだけ受け取る。
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      api
        .getBoard(code)
        .then((next) => {
          if (next.version > shownVersion.current) receive(next)
        })
        .catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 404) setLoadState('missing')
          // 通信の失敗は黙って次を待つ。
        })
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [code, receive])

  const save = async (id: string, request: (version: number) => Promise<Board>) => {
    if (!board || busy) return
    setBusy(true)
    setMessage(null)
    try {
      const next = await request(board.version)
      setNewIds((current) => new Set([...current].filter((entry) => entry !== id)))
      receive(next, true)
    } catch (error) {
      if (error instanceof ApiError && error.message) {
        setMessage(error.message)
        await load()
      } else {
        setMessage(WORDS.board.failed)
      }
    } finally {
      setBusy(false)
    }
  }

  if (loadState === 'missing') {
    return (
      <main className="centered">
        <p>{WORDS.board.notFound}</p>
        <button type="button" className="primary" data-primary onClick={onLeave}>
          {WORDS.board.startOver}
        </button>
      </main>
    )
  }

  const items = board?.items ?? []
  const events = items.filter((item) => item.kind === '予定')
  const notEvents = items.filter((item) => item.kind !== '予定')
  const latest = board?.branches[board.branches.length - 1]
  const sends = board ? sendsOf(board) : []
  // まとめて送るに出ている連絡は、送るまで列に重ねて出さない。
  const sendIds = new Set(sends.map((send) => send.item.id))
  const inColumns = notEvents.filter((item) => item.part !== '親' && !sendIds.has(item.id))
  const own = notEvents.filter((item) => item.part === '親')
  const filled = COLUMNS.filter((name) => inColumns.some((item) => item.part === name))
  const empty = COLUMNS.filter((name) => !filled.includes(name))
  const card = (item: Item) => (
    <ItemCard
      key={item.id}
      item={item}
      isNew={newIds.has(item.id)}
      busy={busy}
      onDone={(status) =>
        void save(item.id, (version) => api.changeStatus(code, item.id, version, status))
      }
      onReply={(status, due) =>
        void save(item.id, (version) => api.changeStatus(code, item.id, version, status, due))
      }
      onRemove={() => void save(item.id, (version) => api.removeItem(code, item.id, version))}
    />
  )

  return (
    <>
      <main className="board">
        <header className="board-head">
          <h1>{board?.profile.name ?? WORDS.appName}</h1>
          <button
            type="button"
            className="quiet help"
            aria-label={WORDS.consent.openLabel}
            onClick={onConsent}
          >
            {WORDS.consent.open}
          </button>
        </header>

        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}

        {/* 読み込めなくても画面を差し替えない。入れた文と［＋入れる］は電波が無くても使える。 */}
        {loadState === 'offline' && (
          <div className="message-block">
            <p className="message" role="alert">
              {WORDS.board.offline}
            </p>
            <button
              type="button"
              onClick={() => {
                onRetry()
                void load()
              }}
            >
              {WORDS.board.retry}
            </button>
          </div>
        )}

        {pending.length > 0 && (
          <section aria-label={WORDS.board.sorting}>
            {pending.map((entry) => (
              <article key={entry.id} className="card sorting">
                {!entry.failed && <span className="chip">{WORDS.board.sorting}</span>}
                <p className="original">{entry.text}</p>
                {entry.failed && (
                  <p className="message" role="alert">
                    {entry.failed}
                  </p>
                )}
                <div className="actions">
                  {entry.failed && (
                    <button type="button" onClick={() => onSortAgain(entry.id)}>
                      {WORDS.board.sortAgain}
                    </button>
                  )}
                  <RemoveButton onConfirm={() => onRemovePending(entry.id)} />
                </div>
              </article>
            ))}
          </section>
        )}

        {/* 読み込めていないのに「ありません」とは言わない。 */}
        {loadState === 'ready' && (
          <section className="decide">
            <h2>{WORDS.board.decide}</h2>
            {!proposal && <p>{WORDS.board.decideNone}</p>}
            {proposal?.state === 'working' && <p role="status">{WORDS.board.proposalWorking}</p>}
            {proposal?.state === 'ready' && (
              <>
                <p className="original">{textOfOriginal(items, proposal.item_id)}</p>
                <button type="button" onClick={onOpenProposal}>
                  {WORDS.board.proposalOpen}
                </button>
              </>
            )}
            {proposal?.state === 'failed' && (
              <>
                <p>{proposal.message}</p>
                <button type="button" onClick={onCloseProposal}>
                  {WORDS.board.proposalClose}
                </button>
              </>
            )}
          </section>
        )}

        {loadState === 'loading' && <p role="status">{WORDS.board.loading}</p>}

        {latest && <DayBand days={latest.candidate_days} />}

        {sends.length > 0 && (
          <section>
            <h2>{WORDS.board.sends}</h2>
            {sends.map((send) => (
              <SendCard
                key={send.item.id}
                send={send}
                code={code}
                items={items}
                shared={shared.includes(send.item.id)}
                busy={busy}
                onShared={() => {
                  const next = [...new Set([...shared, send.item.id])]
                  device.setShared(code, next)
                  setShared(next)
                }}
                onAsked={() =>
                  void save(send.item.id, (version) =>
                    api.changeStatus(code, send.item.id, version, '頼んだ'),
                  )
                }
                onRemove={() =>
                  void save(send.item.id, (version) =>
                    api.removeItem(code, send.item.id, version),
                  )
                }
              />
            ))}
          </section>
        )}

        {events.length > 0 && (
          <section>
            <h2>{WORDS.board.events}</h2>
            {events.map(card)}
          </section>
        )}

        {filled.map((name) => (
          <section key={name}>
            <h2>{name}</h2>
            {inColumns.filter((item) => item.part === name).map(card)}
          </section>
        ))}

        {own.length > 0 && (
          <section>
            <h2>{WORDS.board.parent}</h2>
            {own.map(card)}
          </section>
        )}

        {loadState === 'ready' && empty.length > 0 && (
          <p className="empty-parts">{WORDS.board.emptyParts([...empty])}</p>
        )}
      </main>
      <div className="dock">
        <button type="button" className="primary" data-primary onClick={onAdd}>
          {WORDS.add.open}
        </button>
      </div>
    </>
  )
}

function ItemCard({
  item,
  isNew,
  busy,
  onDone,
  onReply,
  onRemove,
}: {
  item: Item
  isNew: boolean
  busy: boolean
  onDone: (status: string) => void
  onReply: (status: string, due?: string) => void
  onRemove: () => void
}) {
  const title = item.task_summary ?? item.text_original ?? ''
  const showOriginal = item.text_original && item.text_original !== title
  const showCertainty =
    (item.kind === '情報' || item.kind === '予定') && item.certainty !== '確定'
  const next = doneStatus(item)
  const finished = item.status === '完了' || item.status === '届いた'
  // 家族の答えは、誰の答えかが一目でわかるように状態の横に出す。
  const ownerInChip = item.part === '家族' && !!item.owner && REPLIED.has(item.status ?? '')
  const label = ownerInChip ? `${item.status_label}（${item.owner}）` : item.status_label
  const details = [
    !ownerInChip && item.owner && `${WORDS.board.owner}：${item.owner}`,
    item.status !== '時刻つきで可' && item.due && `${WORDS.board.due}：${item.due}`,
    item.location && `${WORDS.board.location}：${item.location}`,
  ].filter(Boolean)
  const waiting = item.kind === '行動' && (item.status === '頼んだ' || item.status === '保留')

  return (
    <article className={finished ? 'card finished' : 'card'} data-item={item.id}>
      <div className="chips">
        {isNew && <span className="chip new">{WORDS.board.newMark}</span>}
        {label && <span className="chip">{label}</span>}
        {showCertainty && <span className="chip">{item.certainty}</span>}
      </div>
      <h3>{title}</h3>
      {showOriginal && (
        <p className="original">
          {item.speaker ? WORDS.board.speaker(item.speaker) : WORDS.board.original}：
          {item.text_original}
        </p>
      )}
      {item.notice && <p className="detail">{item.notice}</p>}
      {details.map((detail) => (
        <p className="detail" key={String(detail)}>
          {detail}
        </p>
      ))}
      {item.medical_reply && <p className="medical">{item.medical_reply}</p>}
      {waiting && <ReplyPanel busy={busy} onReply={onReply} />}
      <div className="actions">
        {next && (
          <button type="button" disabled={busy} onClick={() => onDone(next)}>
            {WORDS.board.done}
          </button>
        )}
        <RemoveButton disabled={busy} onConfirm={onRemove} />
      </div>
    </article>
  )
}

// 職場・保育園・支援はアプリを使わないので、親が返事を記録する。AIは使わない。
function ReplyPanel({
  busy,
  onReply,
}: {
  busy: boolean
  onReply: (status: string, due?: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [timeOpen, setTimeOpen] = useState(false)
  const [time, setTime] = useState('')

  if (!open) {
    return (
      <div className="reply">
        <button type="button" disabled={busy} onClick={() => setOpen(true)}>
          {WORDS.board.replied}
        </button>
      </div>
    )
  }
  if (timeOpen) {
    return (
      <div className="reply">
        <label>
          {WORDS.board.replyTimeLabel}
          <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
        </label>
        <button
          type="button"
          disabled={busy || !time}
          onClick={() => onReply('時刻つきで可', time)}
        >
          {WORDS.board.replySave}
        </button>
        <button type="button" onClick={() => setTimeOpen(false)}>
          {WORDS.add.back}
        </button>
      </div>
    )
  }
  return (
    <div className="reply">
      {WORDS.board.replyChoices.map((choice) => (
        <button key={choice} type="button" disabled={busy} onClick={() => onReply(choice)}>
          {choice}
        </button>
      ))}
      <button type="button" disabled={busy} onClick={() => setTimeOpen(true)}>
        {WORDS.board.replyTime}
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        {WORDS.add.back}
      </button>
    </div>
  )
}
