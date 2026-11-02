import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { DayBand } from './Decide'
import { device, type Pending, type Proposal } from './device'
import { COLUMNS, WORDS } from './words'

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

  // 前に見たときから変わった項目に「新」を付ける。台帳に欄を足さず、端末の控えと比べる。
  const receive = useCallback(
    (next: Board, ownChange = false) => {
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
  const inColumns = notEvents.filter((item) => item.part !== '親')
  const own = notEvents.filter((item) => item.part === '親')
  const latest = board?.branches[board.branches.length - 1]
  const sends = (latest?.outcomes ?? []).flatMap((outcome) =>
    outcome.actions.filter((action) => action.type !== '何もしない'),
  )
  const textOfId = (id: string) => {
    const found = items.find((item) => item.id === id)
    return found?.task_summary ?? found?.text_original ?? id
  }
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
            {sends.map((action, index) => (
              <article key={index} className="card" data-send={action.part}>
                <div className="chips">
                  <span className="chip">{action.part}</span>
                  <span className="chip">{action.type}</span>
                </div>
                {action.draft && <p className="original">{action.draft}</p>}
                {action.release.map((id) => (
                  <p className="detail" key={id}>
                    {WORDS.decide.release(textOfId(id))}
                  </p>
                ))}
              </article>
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
  onRemove,
}: {
  item: Item
  isNew: boolean
  busy: boolean
  onDone: (status: string) => void
  onRemove: () => void
}) {
  const title = item.task_summary ?? item.text_original ?? ''
  const showOriginal = item.text_original && item.text_original !== title
  const showCertainty =
    (item.kind === '情報' || item.kind === '予定') && item.certainty !== '確定'
  const next = doneStatus(item)
  const finished = item.status === '完了' || item.status === '届いた'
  const details = [
    item.owner && `${WORDS.board.owner}：${item.owner}`,
    item.due && `${WORDS.board.due}：${item.due}`,
    item.location && `${WORDS.board.location}：${item.location}`,
  ].filter(Boolean)

  return (
    <article className={finished ? 'card finished' : 'card'} data-item={item.id}>
      <div className="chips">
        {isNew && <span className="chip new">{WORDS.board.newMark}</span>}
        {item.status_label && <span className="chip">{item.status_label}</span>}
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

// 元に戻す仕組みがまだ無いので、押し間違いで消えないよう2回押しにする。
function RemoveButton({ disabled, onConfirm }: { disabled?: boolean; onConfirm: () => void }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = window.setTimeout(() => setArmed(false), 4000)
    return () => window.clearTimeout(timer)
  }, [armed])
  return (
    <button
      type="button"
      disabled={disabled}
      className={armed ? 'armed' : undefined}
      onClick={() => (armed ? onConfirm() : setArmed(true))}
    >
      {armed ? WORDS.board.removeAgain : WORDS.board.remove}
    </button>
  )
}
