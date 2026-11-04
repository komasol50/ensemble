import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, type Board, type Item, type Week } from './api'
import { device, type Pending, type Proposal } from './device'
import {
  Chevron,
  PartIcon,
  STATE_WORDS_IN_ORDER,
  stateWord,
  todayLabel,
  toneOf,
  whenOf,
  type StateWord,
} from './parts'
import { canSearch, isReadDocument, listPhotos, searchVault, type Photo } from './photos'
import { RemoveButton, TrashButton } from './RemoveButton'
import { ItemRow, OWN_PART, replyText, titleOf } from './Row'
import { isSentMessage, sendsOf, type Send } from './Send'
import { isFinished, isRecord, placeOf, rankOf, type Turn } from './turns'
import { PhotoViewer } from './Vault'
import { WeekList } from './Week'
import { COLUMNS, MEDICAL_FIXED_TEXT, WORDS } from './words'

// 別の端末の変更を見に行く間隔。見えている間だけ。AIは呼ばない。
const POLL_MS = 3000
const MESSAGE_MS = 8000
const TITLE_MAX = 24
const NOTE_MAX = 200
const REMOVE_TRIES = 3

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

// 親自身の一言。AIを呼ばず、数値も催促も無い。残したいときだけ。
function OwnNote({ busy, onSave }: { busy: boolean; onSave: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState('')
  const trimmed = text.trim()
  return (
    <form
      className="own-note"
      onSubmit={(event) => {
        event.preventDefault()
        if (!trimmed || busy) return
        void onSave(trimmed).then((saved) => {
          if (saved) setText('')
        })
      }}
    >
      <label>
        {WORDS.board.own.label}
        <textarea
          rows={2}
          maxLength={NOTE_MAX}
          placeholder={WORDS.board.own.placeholder}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <p className="detail">{WORDS.board.own.note}</p>
      <button type="submit" disabled={busy || !trimmed}>
        {WORDS.board.own.save}
      </button>
    </form>
  )
}

// ボードを作った直後に、URLを自分のLINEへ送っておく案内。送るかどうかは親が決める。
function SaveLink({ code, onClose }: { code: string; onClose: () => void }) {
  const [note, setNote] = useState<string | null>(null)
  const url = `${window.location.origin}/b#${code}`
  const canShare = typeof navigator.share === 'function'
  const share = async () => {
    try {
      await navigator.share({ text: WORDS.board.saveLink.text, url })
      onClose()
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) void copy()
    }
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setNote(WORDS.board.saveLink.copied)
    } catch {
      // LINEの中のブラウザでは使えないことがある。手で写せるように出す。
      setNote(url)
    }
  }
  return (
    <section className="save-link" data-save-link>
      <h2>{WORDS.board.saveLink.title}</h2>
      <p className="detail">{WORDS.board.saveLink.why}</p>
      {note && (
        <p className="copy-note" role="status">
          {note}
        </p>
      )}
      <div className="save-link-actions">
        {canShare ? (
          <button type="button" className="primary" onClick={() => void share()}>
            {WORDS.board.saveLink.share}
          </button>
        ) : (
          <button type="button" onClick={() => void copy()}>
            {WORDS.board.saveLink.copy}
          </button>
        )}
        <button type="button" className="quiet" onClick={onClose}>
          {WORDS.board.saveLink.close}
        </button>
      </div>
    </section>
  )
}

// この家の事情。3枚のカード。案はこれに合わせて書き分ける。
function HomeCards({ profile }: { profile: Board['profile'] }) {
  const errand = profile.errand_people
    .map((person) => `${person.label}（${person.availability}）`)
    .join('、')
  const supports = profile.supports.map((entry) => `${entry.name}（${entry.detail}）`).join('、')
  const cards = [
    { title: WORDS.board.home.errand, text: errand || WORDS.board.home.errandNone },
    { title: WORDS.board.home.child, text: profile.older_child },
    {
      title: WORDS.board.home.work,
      text: [profile.work, supports].filter(Boolean).join('／'),
    },
  ]
  return (
    <details className="fold" data-home-cards>
      <summary>{WORDS.board.home.title}</summary>
      {cards
        .filter((card) => card.text)
        .map((card) => (
          <article className="card" key={card.title}>
            <h3>{card.title}</h3>
            <p>{card.text}</p>
          </article>
        ))}
    </details>
  )
}

// 上段。今どの段にいて、次に何をするかを1か所で言う。案があるときは題と原文と候補日も。
function NextStep({
  proposal,
  items,
  unsent,
  difficult,
  theirs,
  waiting,
  todo,
  leftover,
  mapButton,
}: {
  proposal: Proposal | null
  items: Item[]
  unsent: number
  difficult: number
  theirs: number
  waiting: number
  todo: number
  leftover: number
  mapButton: ReactNode
}) {
  const words = WORDS.board.next
  // 帯は連絡の段を示す。「これから」は連絡の段ではないので、それだけのときは強調しない。
  const step =
    proposal ? 1 : unsent > 0 ? 2 : difficult + theirs > 0 ? 3 : todo + leftover > 0 ? null : 0
  const trigger = proposal ? items.find((item) => item.id === proposal.item_id) : undefined
  const title = trigger ? titleOf(trigger) : ''
  const original = trigger?.text_original
  const day =
    proposal?.state === 'ready'
      ? proposal.branch.candidate_days.find((entry) => !entry.cancelled)
      : undefined
  return (
    <section className={proposal ? 'decide' : 'next-step'} data-next-step>
      <ol className="steps" aria-label={words.stepsLabel}>
        {words.steps.map((label, index) => {
          const count = index === 2 ? unsent : index === 3 ? waiting : 0
          return (
            <li key={label} aria-current={index === step ? 'step' : undefined}>
              {label}
              {count > 0 && <span className="step-count"> {count}</span>}
            </li>
          )
        })}
      </ol>
      {proposal ? (
        <>
          <div className="decide-title">
            <h2>{WORDS.board.decide}</h2>
            {proposal.state === 'ready' && (
              <span className="count-pill">{WORDS.board.decideCount}</span>
            )}
          </div>
          {proposal.state === 'working' && <p role="status">{WORDS.board.proposalWorking}</p>}
          {proposal.state === 'failed' && <p>{proposal.message}</p>}
          {proposal.state === 'ready' && (
            <>
              <h3>{title}</h3>
              {original && original !== title && (
                <p className="detail">{WORDS.board.originalQuote(original)}</p>
              )}
              {day && (
                <span className={day.certainty === '確定' ? 'chip confirmed' : 'chip likely'}>
                  {day.day}　{day.certainty}
                </span>
              )}
            </>
          )}
        </>
      ) : (
        <>
          <div className="next-line">
            <h2>{words.title}</h2>
            <p>
              {unsent > 0
                ? words.sends(unsent, difficult)
                : difficult > 0
                  ? words.difficult(difficult)
                  : todo > 0
                    ? words.todo(todo, waiting)
                    : leftover > 0
                      ? words.leftover(leftover)
                      : waiting > 0
                        ? words.waiting(waiting)
                        : words.none}
            </p>
          </div>
          {mapButton}
        </>
      )}
    </section>
  )
}

// 数える状態。医療の問いは病棟に聞くものなので、依頼に数えない。
function boardState(item: Item): StateWord | null {
  if (item.released) return null
  const word = stateWord(item.status_label)
  // 返事があれば、札を出して数える（返事と［元に戻す］が見えなくならないように）。
  return item.medical_reply && word !== '済' && word !== '返事あり' ? null : word
}

const DAY_ONLY = /^\d+\/\d+（.）$/

// 退院の候補日。台帳に確定した案の、取り消していない最も早い日。日が無ければ文のまま。
function firstCandidate(week: Week | null | undefined): { text: string; confirmed: boolean } | null {
  if (!week) return null
  for (const row of week.rows) {
    const cell = row.cells[0]?.find((entry) => entry.style === 'likely' || entry.style === 'confirmed')
    if (cell) return { text: `${row.date}（${row.weekday}）`, confirmed: cell.style === 'confirmed' }
  }
  const other = week.others.find((text) => !text.endsWith('（取り消し）'))
  if (!other) return null
  return { text: other.replace(/（(候補|確定)）$/, ''), confirmed: other.endsWith('（確定）') }
}

function timeOf(item: Item): number {
  const at = whenOf(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 帯の［元に戻す］が戻す項目と、押した後の状態。
type DockUndo = { id: string; after: string }

// まだ確定していない案が、この項目をもとにしているか。
function proposalUses(proposal: Proposal | null, id: string): boolean {
  if (!proposal) return false
  if (proposal.item_id === id) return true
  if (proposal.state !== 'ready') return false
  return (
    proposal.branch.condition_item_id === id ||
    proposal.branch.outcomes.some((outcome) =>
      outcome.actions.some((action) => action.basis_ids.includes(id)),
    )
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
  const [linkOpen, setLinkOpen] = useState(() => !device.linkSaved(code))
  const [toggled, setToggled] = useState(() => device.openParts(code))
  const [openRows, setOpenRows] = useState<Set<string>>(new Set())
  const [undo, setUndo] = useState<DockUndo | null>(null)
  const [searching, setSearching] = useState<string | null>(null)
  const shownVersion = useRef(0)
  // 一度開いた相手は、行が済んでも急に閉じない。親が閉じたときだけ閉じる。
  const seenOpen = useRef(new Set<string>())

  // 知らせと［元に戻す］は必ず一緒に変える。別の知らせに、前の操作の［元に戻す］を残さない。
  const say = useCallback((text: string | null, undoable: DockUndo | null = null) => {
    setMessage(text)
    setUndo(undoable)
  }, [])

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
    say(null)
    try {
      await searchVault(code, id)
      setAnswers(device.answers(code))
    } catch (error) {
      // 回数の上限など、サーバーが理由を返したときはそれを出す。
      say(error instanceof ApiError && error.message ? error.message : WORDS.photos.searchFailed)
    } finally {
      setSearching(null)
    }
  }

  // 知らせは見える場所（下の固定の帯）に出し、しばらくしたら消す。
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => say(null), MESSAGE_MS)
    return () => window.clearTimeout(timer)
  }, [message, say])

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
          say(WORDS.board.removed)
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
      say(WORDS.board.removeFailed)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [code, receive, load, showAgain],
  )

  // 消す前に必ず聞く。消したものはゴミ箱から戻せる。
  const askRemove = (id: string) => {
    if (!board) return
    const item = board.items.find((entry) => entry.id === id)
    const title = item ? titleOf(item) : ''
    setConfirming({
      id,
      title: title.length > TITLE_MAX ? `${title.slice(0, TITLE_MAX)}…` : title,
      withdraws: usedByProposal(board, id),
    })
    say(null)
  }

  const confirmRemove = () => {
    if (!confirming) return
    const { id } = confirming
    setConfirming(null)
    setHiddenIds((current) => new Set([...current, id]))
    void commitRemove(id)
  }

  const save = async (
    id: string,
    request: (version: number) => Promise<Board>,
  ): Promise<boolean> => {
    if (!board || busy) return false
    setBusy(true)
    say(null)
    try {
      const next = await request(board.version)
      setNewIds((current) => new Set([...current].filter((entry) => entry !== id)))
      receive(next, true)
      return true
    } catch (error) {
      if (error instanceof ApiError && error.message) {
        say(error.message)
        await load()
      } else {
        say(WORDS.board.failed)
      }
      return false
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
  // 前の案の分でまだ送っていない連絡も、送る行として出す（済だけを押す行き止まりを作らない）。
  const sendOf = (item: Item): Send | null => {
    if (item.released) return null
    const listed = sends.find((send) => send.item.id === item.id)
    if (listed) return listed
    return isSentMessage(item) && item.status === '頼みたい' ? { item, action: null } : null
  }
  // 前の案の文は開けば送れるが、「送る文」には数えない（地図の数と揃える）。
  const unsent = items.filter((item) => sendOf(item) && !item.superseded)
  const leftover = items.filter((item) => item.superseded && !item.released).length
  const sorted = (list: Item[]) =>
    [...list].sort(
      (a, b) => rankOf(a, !!sendOf(a)) - rankOf(b, !!sendOf(b)) || timeOf(b) - timeOf(a),
    )
  const listOf = (part: string) => sorted(items.filter((item) => item.part === part))
  const placeFor = (item: Item) => placeOf(item, !!sendOf(item))
  const inTurn = (list: Item[], turn: Turn) =>
    list.filter((item) => placeFor(item)?.turn === turn).length
  const inGroup = (group: string) => items.filter((item) => placeFor(item)?.group === group).length
  // 見出しの数。あなたの番と相手の番を先に。無ければ記録と済。
  const headCounts = (list: Item[], isOwn: boolean) => {
    const words = WORDS.board.turn
    const moving = [
      inTurn(list, 'mine') > 0 && `${words.mine}${inTurn(list, 'mine')}`,
      inTurn(list, 'theirs') > 0 && `${words.theirs}${inTurn(list, 'theirs')}`,
    ].filter(Boolean)
    if (moving.length > 0 || isOwn) return moving.join('・')
    const records = list.filter(isRecord).length
    const done = list.filter(isFinished).length
    return [records > 0 && `${words.record}${records}`, done > 0 && `${words.done}${done}`]
      .filter(Boolean)
      .join('・')
  }
  // 初めから開く。動いている行のある相手だけ。医療の問いは固定文を隠さない。あとは親が開け閉めした通りにする。
  const isOpen = (part: string) => {
    const moving = listOf(part).some((item) => {
      const turn = placeFor(item)?.turn
      return turn === 'mine' || turn === 'theirs' || (!!item.medical_reply && !isFinished(item))
    })
    if (moving) seenOpen.current.add(part)
    return toggled[part] ?? seenOpen.current.has(part)
  }
  const setOpen = (part: string, open: boolean) => {
    const next = { ...toggled, [part]: open }
    device.setOpenParts(code, next)
    setToggled(next)
  }
  const closeRow = (id: string) =>
    setOpenRows((current) => new Set([...current].filter((entry) => entry !== id)))
  const toggleRow = (id: string) => {
    if (openRows.has(id)) closeRow(id)
    else setOpenRows((current) => new Set([...current, id]))
  }
  // 相手別のその行を開いて、そこへ移動する。畳みの中にあれば畳みも開く。
  const openItem = (item: Item) => {
    setView('parts')
    setOpen(item.part, true)
    setOpenRows((current) => new Set([...current, item.id]))
    const quiet = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.requestAnimationFrame(() => {
      const row = document.querySelector(`[data-item="${item.id}"]`)
      const fold = row?.closest('details')
      if (fold) fold.open = true
      row?.scrollIntoView({ behavior: quiet ? 'auto' : 'smooth', block: 'center' })
    })
  }
  // まだ送っていない最初の連絡を開く。
  const openFirstSend = () => {
    if (unsent[0]) openItem(unsent[0])
  }
  const markDone = async (item: Item, status: string) => {
    const saved = await save(item.id, (version) =>
      api.changeStatus(code, item.id, version, status),
    )
    if (!saved) return
    closeRow(item.id)
    say(WORDS.row.doneMessage, { id: item.id, after: status })
  }
  // 押し間違えた返事も、丸と同じように下の帯から戻せるようにする。
  const recordReply = async (item: Item, status: string, due?: string) => {
    const saved = await save(item.id, (version) =>
      api.changeStatus(code, item.id, version, status, due),
    )
    if (!saved) return
    say(WORDS.row.replySaved, { id: item.id, after: status })
  }
  const restore = async (id: string) => {
    const saved = await save(id, (version) => api.undoStatus(code, id, version))
    if (saved) say(WORDS.row.undone)
    return saved
  }
  // 帯の［元に戻す］は直前の自分の操作を戻す。もう1台が先に変えていたら、さらに戻さない。
  const restoreFromDock = async ({ id, after }: DockUndo) => {
    if (board?.items.find((item) => item.id === id)?.status !== after) {
      say(WORDS.row.undoStale)
      return
    }
    const saved = await restore(id)
    if (!saved) setUndo({ id, after })
  }
  const hasDocuments = photos.some(isReadDocument)
  const row = (item: Item, showPart = false) => (
    <ItemRow
      key={item.id}
      item={item}
      send={sendOf(item)}
      code={code}
      items={items}
      shared={shared.includes(item.id)}
      showPart={showPart}
      isNew={newIds.has(item.id)}
      open={openRows.has(item.id)}
      busy={busy}
      photos={photos.filter((photo) => photo.itemIds.includes(item.id))}
      answer={answers[item.id]}
      source={photos.find((photo) => {
        const answer = answers[item.id]
        return answer?.found && photo.id === answer.photoId
      })}
      canSearchVault={hasDocuments && canSearch(item)}
      searching={searching === item.id}
      onToggle={() => toggleRow(item.id)}
      onSearch={() => void searchFor(item.id)}
      onOpenPhoto={setViewing}
      onDone={(status) => void markDone(item, status)}
      onUndone={() => void restore(item.id)}
      onReply={(status, due) => void recordReply(item, status, due)}
      onShared={() => {
        const next = [...new Set([...shared, item.id])]
        device.setShared(code, next)
        setShared(next)
      }}
      onAsked={() =>
        void save(item.id, (version) =>
          api.changeStatus(code, item.id, version, '頼んだ'),
        ).then((saved) => {
          if (!saved) return
          closeRow(item.id)
          say(WORDS.row.askedMessage, { id: item.id, after: '頼んだ' })
        })
      }
      onEdit={async (changes) => {
        const saved = await save(item.id, (version) =>
          api.editItem(code, item.id, version, changes),
        )
        if (saved) {
          // 案のもとの文を直したら、前の文で作った案は取り下げる。AIは呼び直さない。
          const textChanged = changes.task_summary !== undefined || changes.text_original !== undefined
          const dropped = textChanged && proposalUses(proposal, item.id)
          if (dropped) onCloseProposal()
          say(dropped ? WORDS.row.editDroppedProposal : WORDS.row.editSaved)
          if (changes.part) setOpen(changes.part, true)
        }
        return saved
      }}
      onRemove={() => askRemove(item.id)}
    />
  )

  const candidate = firstCandidate(board?.week)
  const isReady = proposal?.state === 'ready'
  const hasNextMove = isReady || unsent.length > 0
  const quiet = !confirming && !message
  const showFailed = quiet && proposal?.state === 'failed'
  const showWorking = quiet && proposal?.state === 'working'
  const showNotice = quiet && !showFailed && !showWorking && !!notice

  return (
    <div className="with-dock">
      <main className="board">
        <header className="board-head">
          <h1>{board?.profile.name ?? ''}</h1>
          <span className="board-date">{todayLabel()}</span>
          <div className="board-tools">
            <button type="button" aria-label={WORDS.photos.openVault} onClick={onOpenVault}>
              {WORDS.photos.vault}
            </button>
          </div>
        </header>

        {loadState === 'ready' && candidate && (
          <button
            type="button"
            className="candidate-line"
            data-candidate
            onClick={() => setView('week')}
          >
            <span>{WORDS.board.candidate}</span>
            <span
              className={`${candidate.confirmed ? 'chip confirmed' : 'chip likely'}${
                DAY_ONLY.test(candidate.text) ? ' whole' : ''
              }`}
            >
              {candidate.text}
              {candidate.confirmed ? WORDS.board.certain : WORDS.board.likely}
            </span>
          </button>
        )}

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

        {loadState === 'ready' && linkOpen && (
          <SaveLink
            code={code}
            onClose={() => {
              device.setLinkSaved(code)
              setLinkOpen(false)
            }}
          />
        )}

        {/* 読み込めていないのに「ありません」とは言わない。 */}
        {loadState === 'ready' && (
          <NextStep
            proposal={proposal}
            items={items}
            unsent={unsent.length}
            difficult={inGroup('difficult')}
            theirs={inTurn(items, 'theirs')}
            waiting={inGroup('waiting')}
            todo={inGroup('todo')}
            leftover={leftover}
            mapButton={
              board?.impact &&
              unsent.length === 0 && (
                <button type="button" onClick={onOpenMap}>
                  {WORDS.map.open}
                </button>
              )
            }
          />
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
              </button>
            ))}
          </div>
        )}

        {view === 'week' && loadState === 'ready' && (
          <WeekList
            week={board?.week}
            items={items}
            isSend={(item) => !!sendOf(item)}
            onOpen={openItem}
          />
        )}

        {view === 'progress' &&
          STATE_WORDS_IN_ORDER.map((word) => {
            const inState = sorted(items.filter((item) => boardState(item) === word))
            if (inState.length === 0) return null
            return (
              <details className="fold" key={word} data-state={word} open={word !== '済'}>
                <summary>
                  {word} {inState.length}
                </summary>
                <div className="rows">{inState.map((item) => row(item, true))}</div>
              </details>
            )
          })}

        {view === 'parts' &&
          loadState === 'ready' &&
          [...COLUMNS, OWN_PART].map((name) => {
            const label = name === OWN_PART ? WORDS.board.parent : name
            const isOwn = name === OWN_PART
            // 自分のことは書いた順の逆に。同じ秒に2つ残しても、新しいものを上にする。
            const here = isOwn
              ? sorted(items.filter((item) => item.part === name).reverse())
              : listOf(name)
            const open = isOwn ? (toggled[name] ?? here.length > 0) : isOpen(name)
            const empty = here.length === 0 && !isOwn
            // 記録と済は行に並べず、1行の畳みにする。自分のことは今のまま。
            const records = isOwn ? [] : here.filter(isRecord)
            const done = here.filter(isFinished)
            const active = here.filter((item) => !isFinished(item) && !records.includes(item))
            const reply = here.find((item) => boardState(item) === '返事あり')
            return (
              <section
                key={name}
                id={`part-${name}`}
                className={`part-section ${toneOf(name)}`}
                data-part={name}
              >
                <h2 className="part-title">
                  <button
                    type="button"
                    className="part-fold"
                    aria-expanded={open}
                    disabled={empty}
                    onClick={() => setOpen(name, !open)}
                  >
                    {!empty && <Chevron to={open ? 'down' : 'next'} />}
                    <span className="part-head">
                      <PartIcon part={name} />
                      {label}
                    </span>
                    {!open && here.some((item) => newIds.has(item.id)) && (
                      <span className="chip new">{WORDS.board.newMark}</span>
                    )}
                    <span className="part-counts">
                      {empty
                        ? WORDS.board.partNone
                        : isOwn && here.length === 0
                          ? WORDS.board.own.hint
                          : headCounts(here, isOwn)}
                    </span>
                    {/* 閉じていても、返事の中身は読めるようにする。 */}
                    {!open && reply && <span className="part-reply">{replyText(reply)}</span>}
                  </button>
                </h2>
                {open && isOwn && (
                  <OwnNote
                    busy={busy}
                    onSave={async (text) => {
                      const saved = await save('', (version) =>
                        api.addNote(code, version, text),
                      )
                      if (saved) say(WORDS.board.own.saved)
                      return saved
                    }}
                  />
                )}
                {open && active.length > 0 && (
                  <div className="rows">{active.map((item) => row(item))}</div>
                )}
                {open && records.length + done.length > 0 && (
                  <div className="folds">
                    {records.length > 0 && (
                      <details className="fold small" data-record-fold>
                        <summary>
                          {WORDS.row.recordFold(records.length)}
                          {records.some((item) => newIds.has(item.id)) && (
                            <span className="chip new">{WORDS.board.newMark}</span>
                          )}
                        </summary>
                        <div className="rows">{records.map((item) => row(item))}</div>
                      </details>
                    )}
                    {done.length > 0 && (
                      <details className="fold small" data-done-fold>
                        <summary>{WORDS.row.doneFold(done.length)}</summary>
                        <div className="rows">{done.map((item) => row(item))}</div>
                      </details>
                    )}
                  </div>
                )}
              </section>
            )
          })}

        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>

        <div className="board-foot">
          <TrashButton label={WORDS.board.openTrash} text={WORDS.board.trash} onClick={onOpenTrash} />
          <button
            type="button"
            className="quiet"
            aria-label={WORDS.consent.openLabel}
            onClick={onConsent}
          >
            {WORDS.consent.open}
          </button>
          {board && <HomeCards profile={board.profile} />}
        </div>
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
          <div className="dock-note" role="alert">
            <p>{message}</p>
            {undo && (
              <button type="button" disabled={busy} onClick={() => void restoreFromDock(undo)}>
                {WORDS.row.undo}
              </button>
            )}
          </div>
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
        {/* 次の一手があるときはそちらを塗り、追加は枠線にする。上段の言葉と目が向く先を揃える。 */}
        <div className={hasNextMove ? 'dock-buttons two' : 'dock-buttons'}>
          {isReady && (
            <button type="button" className="tall primary" data-primary onClick={onOpenProposal}>
              {WORDS.board.proposalOpen}
            </button>
          )}
          {!isReady && unsent.length > 0 && (
            <button
              type="button"
              className="tall primary"
              data-primary
              onClick={sends.length > 0 && board?.impact ? onOpenMap : openFirstSend}
            >
              {WORDS.board.next.openSends(unsent.length)}
            </button>
          )}
          <button
            type="button"
            className={hasNextMove ? undefined : 'primary'}
            data-primary
            onClick={onAdd}
          >
            {WORDS.add.open}
          </button>
        </div>
      </div>
    </div>
  )
}
