import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { device, type Pending, type Proposal, type VaultAnswer } from './device'
import {
  Chevron,
  PartIcon,
  STATE_WORDS_IN_ORDER,
  stateWord,
  todayLabel,
  toneOf,
} from './parts'
import { canSearch, isReadDocument, listPhotos, searchVault, type Photo } from './photos'
import { RemoveButton, TrashButton } from './RemoveButton'
import { SendCard, sendsOf } from './Send'
import { PhotoImage, PhotoViewer } from './Vault'
import { WeekTable } from './Week'
import { COLUMNS, MEDICAL_FIXED_TEXT, WORDS } from './words'

// 別の端末の変更を見に行く間隔。見えている間だけ。AIは呼ばない。
const POLL_MS = 3000
const MESSAGE_MS = 8000
const TITLE_MAX = 24
const REMOVE_TRIES = 3
const TILE_LINES = 2
const STATE_ORDER = ['返事あり', '未依頼', '返答待ち', '済'] as const
const OWN_PART = '親'

type View = 'parts' | 'progress' | 'week'
const VIEWS: { id: View; label: string }[] = [
  { id: 'parts', label: WORDS.board.viewParts },
  { id: 'progress', label: WORDS.board.viewProgress },
  { id: 'week', label: WORDS.board.viewWeek },
]

type Props = {
  code: string
  pending: Pending[]
  proposal: Proposal | null
  refresh: number
  notice: string | null
  photoRefresh: number
  onOpenVault: () => void
  onOpenMap: () => void
  onOpenTrash: () => void
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

function titleOf(item: Item): string {
  return item.task_summary ?? item.text_original ?? ''
}

// 最上段。案があるときは題・原文・候補日の札と「開く」、無いときは1行だけ。
function DecideCard({
  proposal,
  items,
  onOpen,
}: {
  proposal: Proposal | null
  items: Item[]
  onOpen: () => void
}) {
  if (!proposal) {
    return (
      <section className="decide empty">
        <p>{WORDS.board.decideNone}</p>
      </section>
    )
  }
  const trigger = items.find((item) => item.id === proposal.item_id)
  const title = trigger ? titleOf(trigger) : ''
  const original = trigger?.text_original
  const day =
    proposal.state === 'ready'
      ? proposal.branch.candidate_days.find((entry) => !entry.cancelled)
      : undefined
  return (
    <section className="decide">
      <div className="decide-title">
        <h2>{WORDS.board.decide}</h2>
        {proposal.state === 'ready' && <span className="count-pill">{WORDS.board.decideCount}</span>}
      </div>
      {proposal.state === 'working' && <p role="status">{WORDS.board.proposalWorking}</p>}
      {proposal.state === 'failed' && <p>{proposal.message}</p>}
      {proposal.state === 'ready' && (
        <>
          <h3>{title}</h3>
          {original && original !== title && (
            <p className="detail">{WORDS.board.originalQuote(original)}</p>
          )}
          <div className="decide-foot">
            {day && (
              <span className={day.certainty === '確定' ? 'chip confirmed' : 'chip likely'}>
                {day.day}　{day.certainty}
              </span>
            )}
            <button type="button" className="quiet open" onClick={onOpen}>
              {WORDS.board.open}
              <Chevron />
            </button>
          </div>
        </>
      )}
    </section>
  )
}

// 返事の中身。家族の答えは、誰の答えかも添える。
function replyText(item: Item): string {
  return item.part === '家族' && item.owner
    ? `${item.status_label}（${item.owner}）`
    : (item.status_label ?? '')
}

// 相手のタイル。状態の数を出す。押すと、その相手のカードを開いて移動する。
function Tile({
  part,
  label = part,
  items,
  hasNew,
  onOpen,
}: {
  part: string
  label?: string
  items: Item[]
  hasNew: boolean
  onOpen: () => void
}) {
  const counts = STATE_ORDER.map((word) => ({
    word,
    count: items.filter((item) => stateWord(item.status_label) === word).length,
  })).filter((entry) => entry.count > 0)
  const replied = counts.some((entry) => entry.word === '返事あり')
  const lines = counts.slice(0, TILE_LINES)
  const reply = items.find((item) => stateWord(item.status_label) === '返事あり')
  const featured = items.find((item) => stateWord(item.status_label) !== '済') ?? items[0]
  const wide = part === '支援' || part === OWN_PART
  return (
    <button
      type="button"
      className={`tile ${toneOf(part)}${replied ? ' replied' : ''}${wide ? ' wide' : ''}`}
      data-tile={part}
      disabled={items.length === 0}
      onClick={onOpen}
    >
      <span className="tile-head">
        <PartIcon part={part} />
        <span className="tile-name">
          {label.includes('・') ? (
            <>
              {label.split('・')[0]}・<wbr />
              {label.split('・')[1]}
            </>
          ) : (
            label
          )}
        </span>
        {hasNew && <span className="chip new">{WORDS.board.newMark}</span>}
      </span>
      <span className="tile-lines">
        {lines.slice(0, reply ? 1 : TILE_LINES).map(({ word, count }) => (
          <span key={word} className={word === '返事あり' ? 'tile-line reply' : 'tile-line'}>
            {word} {count}
          </span>
        ))}
        {/* 返事の中身は、押さなくても読めるようにする。 */}
        {reply && <span className="tile-line short">{replyText(reply)}</span>}
        {!reply && lines.length < TILE_LINES && featured && (
          <span className="tile-line short">{titleOf(featured)}</span>
        )}
        {items.length === 0 && <span className="tile-line">{WORDS.board.tileNone}</span>}
      </span>
    </button>
  )
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
  onOpenTrash,
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
  const [confirming, setConfirming] = useState<{
    id: string
    title: string
    withdraws: boolean
  } | null>(null)
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set())
  const [photos, setPhotos] = useState<Photo[]>([])
  const [photoReload, setPhotoReload] = useState(0)
  const [viewing, setViewing] = useState<Photo | null>(null)
  const [answers, setAnswers] = useState(() => device.answers(code))
  const [view, setView] = useState<View>('parts')
  const [toggled, setToggled] = useState(() => device.openParts(code))
  const [searching, setSearching] = useState<string | null>(null)
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
          setMessage(WORDS.board.removed)
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

  // 消す前に必ず聞く。消したものはゴミ箱から戻せる。
  const askRemove = (id: string) => {
    if (!board) return
    const item = board.items.find((entry) => entry.id === id)
    const title = item?.task_summary ?? item?.text_original ?? ''
    setConfirming({
      id,
      title: title.length > TITLE_MAX ? `${title.slice(0, TITLE_MAX)}…` : title,
      withdraws: usedByProposal(board, id),
    })
    setMessage(null)
  }

  const confirmRemove = () => {
    if (!confirming) return
    const { id } = confirming
    setConfirming(null)
    setHiddenIds((current) => new Set([...current, id]))
    void commitRemove(id)
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
  const sends = board ? sendsOf({ ...board, items }) : []
  // まとめて送るに出ている連絡は、送るまで列に重ねて出さない。
  const sendIds = new Set(sends.map((send) => send.item.id))
  const listed = items.filter((item) => !sendIds.has(item.id))
  const listOf = (part: string) => listed.filter((item) => item.part === part)
  const filled = [...COLUMNS, OWN_PART].filter((name) => listOf(name).length > 0)
  const newCount = items.filter((item) => newIds.has(item.id)).length
  // 新しいものか返事があれば初めから開く。あとは親が開け閉めした通りにする。
  const isOpen = (part: string) =>
    toggled[part] ??
    listOf(part).some(
      (item) => newIds.has(item.id) || stateWord(item.status_label) === '返事あり',
    )
  const setOpen = (part: string, open: boolean) => {
    const next = { ...toggled, [part]: open }
    device.setOpenParts(code, next)
    setToggled(next)
  }
  const scrollTo = (id: string) => {
    const quiet = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.requestAnimationFrame(() => {
      document
        .getElementById(id)
        ?.scrollIntoView({ behavior: quiet ? 'auto' : 'smooth', block: 'start' })
    })
  }
  // タイルは、その相手のカードへ連れて行く。閉じるのはカードの見出しから。
  const openTile = (part: string) => {
    if (filled.includes(part)) {
      if (!isOpen(part)) setOpen(part, true)
      scrollTo(`part-${part}`)
      return
    }
    const box = document.getElementById('sends')
    if (box instanceof HTMLDetailsElement) box.open = true
    scrollTo('sends')
  }
  const hasDocuments = photos.some(isReadDocument)
  const card = (item: Item, showPart = false) => (
    <ItemCard
      showPart={showPart}
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
      onRemove={() => askRemove(item.id)}
    />
  )

  const isReady = proposal?.state === 'ready'
  const quiet = !confirming && !message
  const showFailed = quiet && proposal?.state === 'failed'
  const showWorking = quiet && proposal?.state === 'working'
  const showNotice = quiet && !showFailed && !showWorking && !!notice

  return (
    <div className="with-dock">
      <main className="board">
        <header className="board-head">
          <div className="board-top">
            <span>{WORDS.appName}</span>
            <span>{todayLabel()}</span>
          </div>
          <div className="board-title">
            <h1>{board?.profile.name ?? WORDS.appName}</h1>
            <div className="board-tools">
              <button type="button" aria-label={WORDS.photos.openVault} onClick={onOpenVault}>
                {WORDS.photos.vault}
              </button>
              <TrashButton label={WORDS.board.openTrash} onClick={onOpenTrash} />
              <button
                type="button"
                className="quiet help"
                aria-label={WORDS.consent.openLabel}
                onClick={onConsent}
              >
                {WORDS.consent.open}
              </button>
            </div>
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

        {/* 読み込めていないのに「ありません」とは言わない。 */}
        {loadState === 'ready' && (
          <DecideCard proposal={proposal} items={items} onOpen={onOpenProposal} />
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

        {loadState === 'loading' && <p role="status">{WORDS.board.loading}</p>}

        {board?.impact && (
          <button type="button" onClick={onOpenMap}>
            {sends.length > 0 ? WORDS.map.openWith(sends.length) : WORDS.map.open}
          </button>
        )}

        {loadState === 'ready' && (
          <div className="tabs" role="tablist" aria-label={WORDS.board.views}>
            {VIEWS.map(({ id, label }) => (
              <button
                key={id}
                type="button"
                role="tab"
                className="tab"
                aria-selected={view === id}
                onClick={() => setView(id)}
              >
                {label}
                {/* 別の表示を見ていても、返事が届いたことが分かるように。 */}
                {view !== id && id !== 'week' && newCount > 0 && (
                  <span className="tab-new">{WORDS.board.newCount(newCount)}</span>
                )}
              </button>
            ))}
          </div>
        )}

        {view === 'week' && <WeekTable week={board?.week} />}

        {view === 'progress' &&
          STATE_WORDS_IN_ORDER.map((word) => {
            const inState = items.filter((item) => stateWord(item.status_label) === word)
            if (inState.length === 0) return null
            return (
              <details className="fold" key={word} data-state={word} open={word !== '済'}>
                <summary>
                  {word} {inState.length}
                </summary>
                {inState.map((item) => card(item, true))}
              </details>
            )
          })}

        {view === 'parts' && loadState === 'ready' && (
          <section className="tiles" aria-label={WORDS.board.tiles}>
            {COLUMNS.map((name) => {
              const here = items.filter((item) => item.part === name)
              return (
                <Tile
                  key={name}
                  part={name}
                  items={here}
                  hasNew={here.some((item) => newIds.has(item.id))}
                  onOpen={() => openTile(name)}
                />
              )
            })}
            {listOf(OWN_PART).length > 0 && (
              <Tile
                part={OWN_PART}
                label={WORDS.board.parent}
                items={listOf(OWN_PART)}
                hasNew={listOf(OWN_PART).some((item) => newIds.has(item.id))}
                onOpen={() => openTile(OWN_PART)}
              />
            )}
          </section>
        )}

        {view === 'parts' && sends.length > 0 && (
          // 案を進めた後は「次にやること」から送れるので、畳んでおく。
          <details className="fold" id="sends" open={!board?.impact}>
            <summary>{WORDS.board.sendsCount(sends.length)}</summary>
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
                onRemove={() => askRemove(send.item.id)}
              />
            ))}
          </details>
        )}

        {view === 'parts' &&
          filled
            .filter((name) => isOpen(name))
            .map((name) => {
              const label = name === OWN_PART ? WORDS.board.parent : name
              return (
                <section key={name} id={`part-${name}`} className={`part-section ${toneOf(name)}`}>
                  <h2 className="part-title">
                    <button
                      type="button"
                      className="part-fold"
                      aria-expanded="true"
                      aria-label={WORDS.board.fold(label)}
                      onClick={() => setOpen(name, false)}
                    >
                      <span className="part-head">
                        <PartIcon part={name} />
                        {label}
                      </span>
                      <Chevron to="up" />
                    </button>
                  </h2>
                  {listOf(name).map((item) => card(item))}
                </section>
              )
            })}

        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
      </main>

      {/* 主なボタンと知らせは、スクロールの位置によらず見えるように下へ固定する。 */}
      <div className="dock">
        {confirming && (
          <div className="dock-note confirm" role="alertdialog" aria-label={WORDS.board.remove}>
            <p>{WORDS.board.confirmRemove(confirming.title)}</p>
            {confirming.withdraws && <p>{WORDS.board.confirmWithdraw}</p>}
            <div className="confirm-actions">
              <button type="button" onClick={() => setConfirming(null)}>
                {WORDS.board.confirmNo}
              </button>
              <button type="button" className="armed" onClick={confirmRemove}>
                {WORDS.board.confirmYes}
              </button>
            </div>
          </div>
        )}
        {!confirming && message && (
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
        <div className="dock-buttons">
          {isReady && (
            <button type="button" className="tall" data-primary onClick={onOpenProposal}>
              {WORDS.board.proposalOpen}
            </button>
          )}
          <button type="button" className="primary" data-primary onClick={onAdd}>
            {WORDS.add.open}
          </button>
        </div>
      </div>
    </div>
  )
}

function ItemCard({
  showPart,
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
  showPart: boolean
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
    item.kind === '予定' || (item.kind === '情報' && item.certainty !== '確定')
  const next = doneStatus(item)
  const finished = item.status === '完了' || item.status === '届いた'
  const word = stateWord(item.status_label)
  const isReply = word === '返事あり'
  // 返事の中身は、状態の語の隣に出す。家族の答えは誰の答えかも添える。
  const ownerInReply = isReply && item.part === '家族' && !!item.owner
  const replyContent = replyText(item)
  const details = [
    !ownerInReply && item.owner && `${WORDS.board.owner}：${item.owner}`,
    item.status !== '時刻つきで可' && item.due && `${WORDS.board.due}：${item.due}`,
    item.location && `${WORDS.board.location}：${item.location}`,
  ].filter(Boolean)
  const waiting = item.kind === '行動' && (item.status === '頼んだ' || item.status === '保留')
  const [more, setMore] = useState(false)
  const hasMore = !!showOriginal || details.length > 0
  // 医療の問いは畳まない。自分の問いと固定文を並べて読めるようにする。
  const moreOpen = more || !!item.medical_reply

  return (
    <article
      className={`card ${toneOf(item.part)}${finished ? ' finished' : ''}`}
      data-item={item.id}
    >
      {showPart && (
        <span className="part-head">
          <PartIcon part={item.part} />
          {item.part}
        </span>
      )}
      <div className="chips">
        {isNew && <span className="chip new">{WORDS.board.newMark}</span>}
        {/* 進捗別では見出しに同じ語があるので、カードには重ねない。 */}
        {word && !showPart && (
          <span className={isReply ? 'chip state reply' : 'chip state'}>{word}</span>
        )}
        {isReply && <span className="reply-content">{replyContent}</span>}
        {showCertainty && (
          <span className={item.certainty === '確定' ? 'chip confirmed' : 'chip likely'}>
            {item.certainty}
          </span>
        )}
      </div>
      <h3>{title}</h3>
      {/* 原文を残していない理由は、畳まずに出す。 */}
      {item.notice && <p className="detail">{item.notice}</p>}
      {hasMore && (
        <div className="more-body" hidden={!moreOpen}>
          {showOriginal && (
            <p className="original">
              {item.speaker ? WORDS.board.speaker(item.speaker) : WORDS.board.original}：
              {item.text_original}
            </p>
          )}
          {details.map((detail) => (
            <p className="detail" key={String(detail)}>
              {detail}
            </p>
          ))}
        </div>
      )}
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
        {hasMore && !item.medical_reply && (
          <button
            type="button"
            className="quiet more-toggle"
            aria-expanded={more}
            aria-label={WORDS.board.more}
            onClick={() => setMore(!more)}
          >
            {!finished && WORDS.board.more}
            <Chevron to={more ? 'up' : 'down'} />
          </button>
        )}
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
