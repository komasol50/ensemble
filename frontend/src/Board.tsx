import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { device, type Pending, type Proposal, type VaultAnswer } from './device'
import { canSearch, isReadDocument, listPhotos, searchVault, type Photo } from './photos'
import { RemoveButton, TrashButton } from './RemoveButton'
import { SendCard, sendsOf } from './Send'
import { PhotoImage, PhotoViewer } from './Vault'
import { WeekTable } from './Week'
import { COLUMNS, WORDS } from './words'

// 別の端末の変更を見に行く間隔。見えている間だけ。AIは呼ばない。
const POLL_MS = 3000
const UNDO_MS = 5000
const MESSAGE_MS = 8000
const REMOVE_TRIES = 3
const REPLIED = new Set(['引き受けた', '難しい', '保留', '時刻つきで可'])

type Props = {
  code: string
  pending: Pending[]
  proposal: Proposal | null
  refresh: number
  notice: string | null
  photoRefresh: number
  onOpenVault: () => void
  onOpenMap: () => void
  focusSends: boolean
  onSendsFocused: () => void
  onRemovePending: (id: string) => void
  onSortAgain: (id: string) => void
  onRetry: () => void
  onOpenProposal: () => void
  onCloseProposal: () => void
  onItemRemoved: (id: string) => void
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

// この項目を消すと、案も取り下げることになるか。
function usedByProposal(board: Board, id: string): boolean {
  return board.branches.some(
    (branch) =>
      branch.condition_item_id === id ||
      branch.outcomes.some((outcome) =>
        outcome.actions.some((action) => action.basis_ids.includes(id)),
      ),
  )
}

export function BoardScreen({
  code,
  pending,
  proposal,
  refresh,
  notice,
  photoRefresh,
  onOpenVault,
  onOpenMap,
  focusSends,
  onSendsFocused,
  onRemovePending,
  onSortAgain,
  onRetry,
  onOpenProposal,
  onCloseProposal,
  onItemRemoved,
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
  const [removing, setRemoving] = useState<{ id: string; withdraws: boolean } | null>(null)
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set())
  const [photos, setPhotos] = useState<Photo[]>([])
  const [photoReload, setPhotoReload] = useState(0)
  const [viewing, setViewing] = useState<Photo | null>(null)
  const [answers, setAnswers] = useState(() => device.answers(code))
  const [searching, setSearching] = useState<string | null>(null)
  const shownVersion = useRef(0)
  const removingId = useRef<string | null>(null)
  const undoTimer = useRef<number | undefined>(undefined)

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

  // 項目に結んだ写真を端末から読む。読めないブラウザでも、ボードはそのまま使える。
  useEffect(() => {
    let active = true
    listPhotos(code)
      .then((next) => {
        if (active) setPhotos(next)
      })
      .catch(() => undefined)
    setAnswers(device.answers(code))
    return () => {
      active = false
    }
  }, [code, photoRefresh, photoReload])

  const searchFor = async (id: string) => {
    setSearching(id)
    setMessage(null)
    try {
      await searchVault(code, id)
      setAnswers(device.answers(code))
    } catch (error) {
      // 回数の上限など、サーバーが理由を返したときはそれを出す。
      setMessage(error instanceof ApiError && error.message ? error.message : WORDS.photos.searchFailed)
    } finally {
      setSearching(null)
    }
  }

  // 地図の［まとめて送る］から来たら、その場所まで動かす。
  const sendsSection = useRef<HTMLElement>(null)
  const boardLoaded = !!board
  useEffect(() => {
    if (!focusSends || !boardLoaded) return
    sendsSection.current?.scrollIntoView()
    onSendsFocused()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSends, boardLoaded])

  // 知らせは見える場所（下の固定の帯）に出し、しばらくしたら消す。
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(null), MESSAGE_MS)
    return () => window.clearTimeout(timer)
  }, [message])

  const showAgain = useCallback((id: string) => {
    setHiddenIds((current) => new Set([...current].filter((entry) => entry !== id)))
  }, [])

  // 消す本体。別の端末が先に版を進めていても、消す意思は変わらないので読み直して通す。
  const commitRemove = useCallback(
    async (id: string) => {
      for (let attempt = 0; attempt < REMOVE_TRIES; attempt += 1) {
        try {
          receive(await api.removeItem(code, id, shownVersion.current), true)
          showAgain(id)
          onItemRemoved(id)
          return
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            showAgain(id)
            onItemRemoved(id)
            await load()
            return
          }
          if (!(error instanceof ApiError && error.status === 409)) break
          try {
            receive(await api.getBoard(code))
          } catch {
            break
          }
        }
      }
      showAgain(id)
      setMessage(WORDS.board.removeFailed)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [code, receive, load, showAgain],
  )

  const finishRemove = useCallback(() => {
    window.clearTimeout(undoTimer.current)
    const id = removingId.current
    if (!id) return
    removingId.current = null
    setRemoving(null)
    void commitRemove(id)
  }, [commitRemove])

  // 押したらすぐ画面から消し、5秒の間は元に戻せる。別の画面やアプリへ移るときは先に消す。
  useEffect(() => {
    const onHidden = () => {
      if (document.visibilityState === 'hidden') finishRemove()
    }
    document.addEventListener('visibilitychange', onHidden)
    window.addEventListener('pagehide', finishRemove)
    return () => {
      document.removeEventListener('visibilitychange', onHidden)
      window.removeEventListener('pagehide', finishRemove)
      finishRemove()
    }
  }, [finishRemove])

  const startRemove = (id: string) => {
    if (!board) return
    finishRemove()
    removingId.current = id
    setRemoving({ id, withdraws: usedByProposal(board, id) })
    setHiddenIds((current) => new Set([...current, id]))
    setMessage(null)
    undoTimer.current = window.setTimeout(finishRemove, UNDO_MS)
  }

  const undoRemove = () => {
    window.clearTimeout(undoTimer.current)
    const id = removingId.current
    removingId.current = null
    setRemoving(null)
    if (id) showAgain(id)
  }

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

  if (viewing) {
    return (
      <PhotoViewer
        code={code}
        photo={viewing}
        items={board?.items ?? []}
        onClose={() => setViewing(null)}
        onChanged={() => setPhotoReload((n) => n + 1)}
      />
    )
  }

  const items = (board?.items ?? []).filter((item) => !hiddenIds.has(item.id))
  const events = items.filter((item) => item.kind === '予定')
  const notEvents = items.filter((item) => item.kind !== '予定')
  const sends = board ? sendsOf({ ...board, items }) : []
  // まとめて送るに出ている連絡は、送るまで列に重ねて出さない。
  const sendIds = new Set(sends.map((send) => send.item.id))
  const inColumns = notEvents.filter((item) => item.part !== '親' && !sendIds.has(item.id))
  const own = notEvents.filter((item) => item.part === '親')
  const filled = COLUMNS.filter((name) => inColumns.some((item) => item.part === name))
  const empty = COLUMNS.filter((name) => !filled.includes(name))
  const hasDocuments = photos.some(isReadDocument)
  const card = (item: Item) => (
    <ItemCard
      answer={answers[item.id]}
      source={photos.find((photo) => {
        const answer = answers[item.id]
        return answer?.found && photo.id === answer.photoId
      })}
      canSearchVault={hasDocuments && canSearch(item)}
      searching={searching === item.id}
      onSearch={() => void searchFor(item.id)}
      key={item.id}
      item={item}
      isNew={newIds.has(item.id)}
      busy={busy}
      photos={photos.filter((photo) => photo.itemIds.includes(item.id))}
      onOpenPhoto={setViewing}
      onDone={(status) =>
        void save(item.id, (version) => api.changeStatus(code, item.id, version, status))
      }
      onReply={(status, due) =>
        void save(item.id, (version) => api.changeStatus(code, item.id, version, status, due))
      }
      onRemove={() => startRemove(item.id)}
    />
  )

  const isReady = proposal?.state === 'ready'
  const quiet = !removing && !message
  const showFailed = quiet && proposal?.state === 'failed'
  const showWorking = quiet && proposal?.state === 'working'
  const showNotice = quiet && !showFailed && !showWorking && !!notice
  const hasNote = !!removing || !!message || showFailed || showWorking || showNotice
  const dockLines = (hasNote ? 1 : 0) + (isReady ? 1 : 0)

  return (
    <>
      <main className={`board dock-lines-${dockLines}`}>
        <header className="board-head">
          <h1>{board?.profile.name ?? WORDS.appName}</h1>
          <div className="board-tools">
            <button type="button" aria-label={WORDS.photos.openVault} onClick={onOpenVault}>
              {WORDS.photos.vault}
            </button>
            <button
              type="button"
              className="quiet help"
              aria-label={WORDS.consent.openLabel}
              onClick={onConsent}
            >
              {WORDS.consent.open}
            </button>
          </div>
        </header>

        {/* 読み込めなくても画面を差し替えない。入れた文と追加のボタンは電波が無くても使える。 */}
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
                {/* 仕分け中に消しても、結果は台帳に入ってしまう。止められる失敗のときだけ出す。 */}
                {entry.failed && (
                  <div className="actions">
                    <button type="button" onClick={() => onSortAgain(entry.id)}>
                      {WORDS.board.sortAgain}
                    </button>
                    <RemoveButton icon onConfirm={() => onRemovePending(entry.id)} />
                  </div>
                )}
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
                <p>{WORDS.board.proposalReady}</p>
              </>
            )}
            {proposal?.state === 'failed' && <p>{proposal.message}</p>}
          </section>
        )}

        {loadState === 'loading' && <p role="status">{WORDS.board.loading}</p>}

        <WeekTable week={board?.week} />

        {board?.impact && (
          <button type="button" onClick={onOpenMap}>
            {WORDS.map.open}
          </button>
        )}

        {sends.length > 0 && (
          <section ref={sendsSection}>
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
                onRemove={() => startRemove(send.item.id)}
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

      {/* 主なボタンと知らせは、スクロールの位置によらず見えるように下へ固定する。 */}
      <div className="dock">
        {removing && (
          <div className="dock-note" role="status">
            <p>{removing.withdraws ? WORDS.board.removedWithProposal : WORDS.board.removed}</p>
            <button type="button" onClick={undoRemove}>
              {WORDS.board.undo}
            </button>
          </div>
        )}
        {!removing && message && (
          <p className="dock-note" role="alert">
            {message}
          </p>
        )}
        {showFailed && proposal?.state === 'failed' && (
          <div className="dock-note" role="alert">
            <p>{proposal.message}</p>
            <button type="button" onClick={onCloseProposal}>
              {WORDS.board.proposalClose}
            </button>
          </div>
        )}
        {showWorking && (
          <p className="dock-note" role="status">
            {WORDS.board.proposalWorking}
          </p>
        )}
        {showNotice && (
          <p className="dock-note" role="status">
            {notice}
          </p>
        )}
        {isReady && (
          <button type="button" className="primary" data-primary onClick={onOpenProposal}>
            {WORDS.board.proposalOpen}
          </button>
        )}
        <button
          type="button"
          className={isReady ? undefined : 'primary'}
          data-primary
          onClick={onAdd}
        >
          {WORDS.add.open}
        </button>
      </div>
    </>
  )
}

function ItemCard({
  answer,
  source,
  canSearchVault,
  searching,
  onSearch,
  item,
  isNew,
  busy,
  photos,
  onOpenPhoto,
  onDone,
  onReply,
  onRemove,
}: {
  answer: VaultAnswer | undefined
  source: Photo | undefined
  canSearchVault: boolean
  searching: boolean
  onSearch: () => void
  item: Item
  isNew: boolean
  busy: boolean
  photos: Photo[]
  onOpenPhoto: (photo: Photo) => void
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
      {photos.length > 0 && (
        <div className="photo-row">
          {photos.map((photo) => (
            <button
              key={photo.id}
              type="button"
              className="photo-thumb"
              aria-label={WORDS.photos.show(photo.category)}
              onClick={() => onOpenPhoto(photo)}
            >
              <PhotoImage blob={photo.thumb} alt="" />
            </button>
          ))}
        </div>
      )}
      {item.medical_reply && <p className="medical">{item.medical_reply}</p>}
      {answer?.found && (
        <div className="vault-answer">
          <p>
            <strong>{WORDS.photos.foundIn(answer.category)}</strong>
          </p>
          <p className="quote">「{answer.quote}」</p>
          {source && (
            <button type="button" onClick={() => onOpenPhoto(source)}>
              {WORDS.photos.openSource}
            </button>
          )}
        </div>
      )}
      {answer && !answer.found && <p className="detail">{WORDS.photos.notFound}</p>}
      {canSearchVault && !answer?.found && (
        <div className="reply">
          <button type="button" disabled={searching} onClick={onSearch}>
            {searching ? WORDS.photos.searching : WORDS.photos.search}
          </button>
        </div>
      )}
      {waiting && <ReplyPanel busy={busy} onReply={onReply} />}
      <div className="actions">
        {next && (
          <button type="button" disabled={busy} onClick={() => onDone(next)}>
            {WORDS.board.done}
          </button>
        )}
        <TrashButton disabled={busy} onClick={onRemove} />
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
