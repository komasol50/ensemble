import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { device, type Pending, type Proposal } from './device'
import {
  Chevron,
  inQuotes,
  latestWhen,
  PartIcon,
  shortTime,
  stateWord,
  toneOf,
  whenOf,
  whoWord,
  type StateWord,
} from './parts'
import { DraftTabs } from './Drafts'
import {
  nowShowsMedical,
  isOneNote,
  NowActions,
  nowHasActions,
  NowView,
  type NowProps,
} from './Now'
import { canSearch, isReadDocument, listPhotos, searchVault, type Photo } from './photos'
import { RemoveButton } from './RemoveButton'
import { ItemRow, OWN_PART, replyText, titleOf } from './Row'
import { isSentMessage, sendsOf, type Send } from './Send'
import {
  CARD_GROUPS,
  columnOf,
  isFinished,
  isRecord,
  placeOf,
  rankOf,
  reviewsOf,
  type Group,
  type Turn,
} from './turns'
import { PhotoViewer } from './Vault'
import { WeekList } from './Week'
import { COLUMNS, MEDICAL_FIXED_TEXT, WORDS } from './words'

// 別の端末の変更を見に行く間隔。見えている間だけ。AIは呼ばない。
const POLL_MS = 3000
const MESSAGE_MS = 8000
const TITLE_MAX = 24
const NOTE_MAX = 200
const REMOVE_TRIES = 3

export type View = 'now' | 'parts' | 'progress' | 'week' | 'own'
const VIEW_TITLES: Record<Exclude<View, 'now'>, string> = {
  parts: WORDS.nav.messages,
  progress: WORDS.nav.messages,
  week: WORDS.nav.week,
  own: WORDS.board.parent,
}
// メッセージの中の切り替え（2026-11-07 PO）。相手ごと＝今までのやりとり、1件ずつ・進捗＝今までの進み具合。
const MESSAGE_VIEWS = ['parts', 'items', 'table'] as const
const MESSAGE_LABELS: Record<(typeof MESSAGE_VIEWS)[number], string> = {
  parts: WORDS.nav.byPart,
  items: WORDS.nav.items,
  table: WORDS.nav.table,
}

type Props = {
  code: string
  view: View
  justAdded: { id: string; text: string } | null
  pending: Pending[]
  proposal: Proposal | null
  refresh: number
  notice: string | null
  photoRefresh: number
  onView: (view: View) => void
  onJustAddedShown: () => void
  onOpenAfter: () => void
  onOpenSetup: () => void
  onRetryProposal: (itemId: string) => void
  onOpenVault: () => void
  onOpenMap: () => void
  onRemovePending: (id: string) => void
  onSortAgain: (id: string) => void
  onRetry: () => void
  onOpenProposal: () => void
  onCloseProposal: () => void
  onItemRemoved: (id: string) => void
  onAdd: () => void
  onLeave: () => void
}

function fingerprint(item: Item): string {
  return `${item.status ?? ''}|${item.due ?? ''}`
}

function lastEventTime(board: Board): number {
  return Math.max(
    0,
    ...board.items.flatMap((item) => item.events.map((event) => new Date(event.at).getTime())),
  )
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
export function SaveLink({
  code,
  inMenu = false,
  onClose,
}: {
  code: string
  inMenu?: boolean
  onClose: () => void
}) {
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
        {!inMenu && (
          <button type="button" className="quiet" onClick={onClose}>
            {WORDS.board.saveLink.close}
          </button>
        )}
      </div>
    </section>
  )
}

// この家の事情。3枚のカード。案はこれに合わせて書き分ける。
export function HomeCards({
  profile,
  open,
}: {
  profile: Board['profile']
  open?: boolean
}) {
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
    <details className="fold" data-home-cards open={open}>
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

// 数える状態。医療の問いは病棟に聞くものなので、依頼に数えない。
function boardState(item: Item): StateWord | null {
  if (item.released) return null
  const word = stateWord(item.status_label)
  // 返事があれば、札を出して数える（返事と［元に戻す］が見えなくならないように）。
  return item.medical_reply && word !== '済' && word !== '返事あり' ? null : word
}

function timeOf(item: Item): number {
  const at = whenOf(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 帯の［元に戻す］が戻す項目と、押した後の状態。
// after が null のときは、選んで入れた結果を取り消す。
type DockUndo = { id: string; after: string | null }

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
  view,
  justAdded,
  pending,
  proposal,
  refresh,
  notice,
  photoRefresh,
  onView,
  onJustAddedShown,
  onOpenAfter,
  onOpenSetup,
  onRetryProposal,
  onOpenVault,
  onOpenMap,
  onRemovePending,
  onSortAgain,
  onRetry,
  onOpenProposal,
  onCloseProposal,
  onItemRemoved,
  onAdd,
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
  const [fromLink] = useState(() => device.fromLink(code))
  const turnWords = fromLink
    ? { ...WORDS.board.turn, ...WORDS.board.turnFromLink }
    : WORDS.board.turn
  const [medicalClosed, setMedicalClosed] = useState(() => device.medicalClosed(code))
  const [linkOpen, setLinkOpen] = useState(() => !fromLink && !device.linkSaved(code))
  // 2026-11-07 PO：URLの保存は上に小さい1行。押したときだけ今のカードを開く。
  const [linkExpanded, setLinkExpanded] = useState(false)
  const counts = fromLink ? WORDS.board.countsFromLink : WORDS.board.counts
  const [toggled, setToggled] = useState(() => device.openParts(code))
  const [openRows, setOpenRows] = useState<Set<string>>(new Set())
  const [undo, setUndo] = useState<DockUndo | null>(null)
  const [draftsLater, setDraftsLater] = useState(false)
  const [progressView, setProgressView] = useState<'items' | 'table'>('items')
  // 相手別の表の行を押したときの相手。用件で見るを、その相手の用件だけにする。
  const [progressPart, setProgressPart] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
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
      if (device.replyBase(code) === null) device.setReplyBase(code, lastEventTime(next))
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

  // 選んで入れた直後は、下の帯から取り消せるようにする。
  useEffect(() => {
    if (!justAdded) return
    say(WORDS.after.saved(justAdded.text), { id: justAdded.id, after: null })
    onJustAddedShown()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [justAdded])

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

  const allItems = board?.items ?? []
  // 夫のひとことは返事のカードの中だけに出す。一覧やスレッドにしない。
  const items = allItems.filter((item) => !hiddenIds.has(item.id) && !isOneNote(item))
  const sends = board ? sendsOf({ ...board, items }) : []
  // 前の案の分でまだ送っていない連絡も、送る行として出す（済だけを押す行き止まりを作らない）。
  const sendOf = (item: Item): Send | null => {
    if (item.released) return null
    const listed = sends.find((send) => send.item.id === item.id)
    if (listed) return listed
    return isSentMessage(item) && item.status === '頼みたい' ? { item, action: null } : null
  }
  // 前の案の文は開けば送れるが、「送る文」には数えない（地図の数と揃える）。
  // 医療の問いになった文は固定文を返す行で、送る文には数えない。
  const unsent = items.filter(
    (item) => sendOf(item) && !item.superseded && !item.carried_into && !item.medical_reply,
  )
  const sorted = (list: Item[]) =>
    [...list].sort(
      (a, b) => rankOf(a, !!sendOf(a)) - rankOf(b, !!sendOf(b)) || timeOf(b) - timeOf(a),
    )
  const listOf = (part: string) => sorted(items.filter((item) => item.part === part))
  const placeFor = (item: Item) => placeOf(item, !!sendOf(item))
  const inTurn = (list: Item[], turn: Turn) =>
    list.filter((item) => placeFor(item)?.turn === turn).length
  // 見出しの数。動いているものを先に。無ければ記録と済。
  const headCounts = (list: Item[], isOwn: boolean) => {
    const words = counts
    const groupCount = (groups: Group[]) =>
      list.filter((item) => groups.includes(placeFor(item)?.group as Group)).length
    const accepted = groupCount(['accepted', 'timed'])
    const waiting = groupCount(['waiting'])
    const held = groupCount(['held'])
    const mine = inTurn(list, 'mine') - held
    const moving = [
      mine > 0 && `${words.mine}${mine}`,
      held > 0 && `${words.held}${held}`,
      waiting > 0 && `${words.waiting}${waiting}`,
      accepted > 0 && `${words.accepted}${accepted}`,
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
    onView(item.part === OWN_PART ? 'own' : 'parts')
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
    if (after === null) {
      setUndo(null)
      await commitRemove(id)
      return
    }
    if (board?.items.find((item) => item.id === id)?.status !== after) {
      say(WORDS.row.undoStale)
      return
    }
    const saved = await restore(id)
    if (!saved) setUndo({ id, after })
  }
  const markAsked = async (item: Item) => {
    const saved = await save(item.id, (version) =>
      api.changeStatus(code, item.id, version, '頼んだ'),
    )
    if (!saved) return
    closeRow(item.id)
    say(turnWords.asked, { id: item.id, after: '頼んだ' })
  }
  const markShared = (item: Item) => {
    const next = [...new Set([...shared, item.id])]
    device.setShared(code, next)
    setShared(next)
  }
  const hasDocuments = photos.some(isReadDocument)
  const errand = board?.profile.errand_people[0]?.label
  const openDrafts = () => {
    setDraftsLater(false)
    onView('parts')
  }
  const row = (item: Item, showPart = false, layout: 'row' | 'card' = 'row') => (
    <ItemRow
      key={item.id}
      layout={layout}
      errand={errand}
      onOpenDrafts={openDrafts}
      item={item}
      send={sendOf(item)}
      fromLink={fromLink}
      code={code}
      items={items}
      shared={shared.includes(item.id)}
      showPart={showPart}
      hideSentDays={fromLink}
      today={board?.today}
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
      onAsked={() => void markAsked(item)}
      onReshared={() =>
        void save(item.id, (version) => api.reshare(code, item.id, version)).then((saved) => {
          if (saved) say(WORDS.row.reshared)
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

  // ⑪ 進み具合。カード・上の1行・相手別の表は、同じ置き場（placeFor）から数える。
  const progressItems = progressPart ? items.filter((item) => item.part === progressPart) : items
  const inCardGroups = (list: Item[], groups: Group[]) =>
    list.filter((item) => groups.includes(placeFor(item)?.group as Group))
  // 図の「未送信 2・対応中 1」。返事待ち・難しい・保留は、あるときだけ足す。
  const progressSum = [
    { word: counts.mine, count: inCardGroups(progressItems, ['send', 'older']).length },
    { word: counts.accepted, count: inCardGroups(progressItems, ['accepted', 'timed']).length },
    { word: counts.waiting, count: inCardGroups(progressItems, ['waiting']).length, more: true },
    {
      word: WORDS.progress.chips.difficult,
      count: inCardGroups(progressItems, ['difficult']).length,
      more: true,
    },
    { word: counts.held, count: inCardGroups(progressItems, ['held']).length, more: true },
  ].filter((entry) => !entry.more || entry.count > 0)
  const progressDone = sorted(progressItems.filter((item) => placeFor(item)?.turn === 'done'))
  const reviews = reviewsOf(board?.calendar, items)
  // 表に出す相手：4つの列のどれかに入るものか、親の用事（列には数えない）がある相手。
  const inTable = (item: Item) =>
    !!columnOf(placeFor(item)) || placeFor(item)?.group === 'todo'
  const tableParts = COLUMNS.filter((part) =>
    items.some((item) => item.part === part && inTable(item)),
  )
  // 表の行を押したら、その相手の用件の一覧（用件で見るを、その相手だけにする）。
  const openParty = (part: string) => {
    setProgressPart(part)
    setProgressView('items')
    window.scrollTo({ top: 0 })
  }
  // 黄のカードの「予定を見る」。カレンダーを開いて、その日を選ぶ。
  const openDay = (date: string) => {
    onView('week')
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>(`[data-cal-day="${date}"]`)?.click()
      document.querySelector('[data-week-selected]')?.scrollIntoView({ block: 'start' })
    })
  }

  const linkBack = fromLink ? device.linkBack() : null
  const pendingLimit = pending.find((entry) => entry.limit)?.failed ?? null
  const quiet = !confirming && !message
  // 「いま」では次にできることの場所に出るので、帯には出さない。
  const showFailed = quiet && view !== 'now' && proposal?.state === 'failed'
  const showWorking = quiet && view !== 'now' && proposal?.state === 'working'
  const showNotice = quiet && !showFailed && !showWorking && !!notice
  const pendingCards = pending.length > 0 && (
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
              {!entry.limit && (
                <button type="button" onClick={() => onSortAgain(entry.id)}>
                  {WORDS.board.sortAgain}
                </button>
              )}
              <RemoveButton icon onConfirm={() => onRemovePending(entry.id)} />
            </div>
          )}
        </article>
      ))}
    </section>
  )

  // 「いま」の文と下の帯のボタンは、同じ引数から作る。読み込めていないときは出さない。
  // 夫の見るだけの「いま」に出す返事の状態（countsFromLink の語。親の視点のまま）。
  const linkSummary = fromLink
    ? [
        { word: counts.mine, count: inCardGroups(items, ['send', 'older']).length },
        { word: counts.accepted, count: inCardGroups(items, ['accepted', 'timed']).length },
        { word: counts.waiting, count: inCardGroups(items, ['waiting']).length },
        { word: WORDS.progress.chips.difficult, count: inCardGroups(items, ['difficult']).length },
        { word: counts.held, count: inCardGroups(items, ['held']).length },
      ]
        .filter((entry) => entry.count > 0)
        .map((entry) => `${entry.word} ${entry.count}`)
        .join('・')
    : ''
  const nowProps: NowProps | null =
    view === 'now' && loadState === 'ready' && board
      ? {
        readOnly: fromLink,
        linkSummary: linkSummary,
        board: board,
        items: items,
        allItems: allItems,
        proposal: proposal,
        sends: sends,
        unsent: unsent,
        pendingCount: pending.length,
        placeFor: placeFor,
        busy: busy,
        pendingCards: pendingCards,
        pendingLimit: pendingLimit,
        replyBase: device.replyBase(code) ?? 0,
        medicalClosed: medicalClosed,
        onCloseMedical: (id) => {
              const next = [...medicalClosed, id]
              device.setMedicalClosed(code, next)
              setMedicalClosed(next)
            },
        onOpenProposal: onOpenProposal,
        onOpenSends: () => {
          setDraftsLater(false)
          onView('parts')
        },
        onOpenMap: onOpenMap,
        onOpenItem: openItem,
        onDone: (item, status) => void markDone(item, status),
        onAdd: onAdd,
        onAfter: onOpenAfter,
        onSetup: onOpenSetup,
        onVault: onOpenVault,
        onCalendar: () => onView('week'),
        onProgress: () => onView('progress'),
        onUndoResult: (id) => void commitRemove(id),
        onStopProposal: onCloseProposal,
        onRetryProposal: onRetryProposal,
        }
      : null
  const hasNowActions = !!nowProps && nowHasActions(nowProps)
  // 固定文を次にできることの場所か返事のカードに出している間は、足元に重ねて出さない。
  const medicalOnTop = !!nowProps && nowShowsMedical(nowProps)
  const showDock =
    !!confirming || !!message || showFailed || showWorking || showNotice || hasNowActions
  return (
    <div className="with-dock">
      <main className={view === 'now' ? 'board now' : 'board'}>
        {fromLink && linkBack && (
          <button
            type="button"
            className="quiet back-to-one"
            onClick={() => window.location.assign(linkBack)}
          >
            <Chevron to="back" />
            {WORDS.nav.backToOne}
          </button>
        )}
        {view !== 'now' && <h1>{VIEW_TITLES[view]}</h1>}
        {(view === 'parts' || view === 'progress') && (
          <div
            className="draft-tablist messages-switch"
            role="tablist"
            aria-label={WORDS.nav.switchLabel}
            data-messages-switch
          >
            {MESSAGE_VIEWS.map((key) => (
              <button
                type="button"
                role="tab"
                className="draft-tab"
                key={key}
                aria-selected={
                  key === 'parts' ? view === 'parts' : view === 'progress' && progressView === key
                }
                onClick={() => {
                  if (key === 'parts') {
                    onView('parts')
                    return
                  }
                  setProgressView(key)
                  setProgressPart(null)
                  onView('progress')
                }}
              >
                {MESSAGE_LABELS[key]}
              </button>
            ))}
          </div>
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

        {loadState === 'ready' &&
          linkOpen &&
          (linkExpanded ? (
            <SaveLink
              code={code}
              onClose={() => {
                device.setLinkSaved(code)
                setLinkOpen(false)
              }}
            />
          ) : (
            <button
              type="button"
              className="quiet save-link-line"
              data-save-link-line
              onClick={() => setLinkExpanded(true)}
            >
              {WORDS.now.saveLinkLine}
              <Chevron />
            </button>
          ))}

        {loadState === 'loading' && <p role="status">{WORDS.board.loading}</p>}

        {/* 通信が無くても、入れた文と入力の行は見える。読めていないのに「ありません」とは言わない。 */}
        {view === 'now' && loadState !== 'ready' && (
          <>
            {pendingCards}
            <button type="button" className="now-input" data-now-input onClick={onAdd}>
              {WORDS.now.input}
            </button>
          </>
        )}
        {nowProps && <NowView {...nowProps} />}

        {view === 'week' && loadState === 'ready' && (
          <WeekList
            week={board?.calendar}
            items={items}
            errand={board?.profile.errand_people[0]?.label}
            testDays={(board?.doctor?.paths ?? [])
              .filter((row) => row.kind === '検査')
              .map((row) => row.day)}
            isSend={(item) => !!sendOf(item)}
            onOpen={openItem}
            onAdd={onAdd}
          />
        )}

        {/* ⑪ 進み具合。段に分けず1列のカード。カード・上の1行・相手別の表は同じ置き場から数える。 */}
        {view === 'progress' && loadState === 'ready' && (
          <section className="lanes progress" data-lanes data-progress-view={progressView}>
            {progressView === 'items' && (
              <>
                {progressPart && (
                  <div className="progress-filter" data-party-filter={progressPart}>
                    <span className={`part-head ${toneOf(progressPart)}`}>
                      <PartIcon part={progressPart} />
                      {WORDS.progress.partyItems(whoWord(progressPart, errand))}
                    </span>
                    <button type="button" className="quiet" onClick={() => setProgressPart(null)}>
                      {WORDS.progress.showAll}
                    </button>
                  </div>
                )}
                <p className="lane-sum" data-progress-sum>
                  {progressSum.map((entry, index) => (
                    <span key={entry.word}>
                      {index > 0 && '・'}
                      <span data-column={entry.word}>
                        {entry.word} {entry.count}
                      </span>
                    </span>
                  ))}
                </p>
                {CARD_GROUPS.map((group) => {
                  const here = sorted(inCardGroups(progressItems, [group]))
                  if (here.length === 0) return null
                  return (
                    <div className="progress-list" key={group} data-lane={group}>
                      {here.map((item) => row(item, true, 'card'))}
                    </div>
                  )
                })}
                {inCardGroups(progressItems, CARD_GROUPS).length === 0 && (
                  <p className="lane-empty">{WORDS.now.none}</p>
                )}
                {progressDone.length > 0 && (
                  <details className="fold small" data-done-fold>
                    <summary>{WORDS.row.doneFold(progressDone.length)}</summary>
                    <div className="progress-list">
                      {progressDone.map((item) => row(item, true, 'card'))}
                    </div>
                  </details>
                )}
                {/* 取り消しになった候補日に、まだ済んでいない用事があるときだけ。作れなければ出さない。 */}
                {!fromLink && !progressPart && reviews.length > 0 && (
                  <article className="review-card progress-review" data-review-card>
                    <h3>{WORDS.progress.review}</h3>
                    {reviews.map((review) => (
                      <div className="review-line" key={review.date} data-review-day={review.date}>
                        <div className="review-text">
                          <p>
                            {review.topics.length > 0
                              ? WORDS.progress.reviewDay(review.day, review.topics)
                              : review.day}
                          </p>
                          {review.topics.length === 0 &&
                            review.clauses.map((clause) => (
                              <p className="review-clause" key={clause}>
                                {inQuotes(clause)}
                              </p>
                            ))}
                        </div>
                        <button
                          type="button"
                          className="link-button"
                          data-review-open={review.date}
                          onClick={() => openDay(review.date)}
                        >
                          {WORDS.progress.seeReview}
                          <Chevron />
                        </button>
                      </div>
                    ))}
                  </article>
                )}
              </>
            )}
            {progressView === 'table' &&
              (tableParts.length === 0 ? (
                <p className="lane-empty">{WORDS.now.none}</p>
              ) : (
                <table className="party-table" data-party-table data-part-table>
                  <colgroup>
                    <col className="party-col" />
                    {WORDS.progress.states.map((name) => (
                      <col key={name} />
                    ))}
                    <col className="last-col" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">{WORDS.progress.partyHead}</th>
                      {WORDS.progress.states.map((name) => (
                        <th scope="col" key={name}>
                          {/* 4文字の語は2文字ずつで折る（列の幅を360pxに収める）。 */}
                          {name.length > 3 ? (
                            <>
                              {name.slice(0, 2)}
                              <wbr />
                              {name.slice(2)}
                            </>
                          ) : (
                            name
                          )}
                        </th>
                      ))}
                      <th scope="col">{WORDS.progress.last}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tableParts.map((part) => {
                      const here = items.filter((item) => item.part === part && inTable(item))
                      const last = latestWhen(here)
                      const [lastDay, lastClock] = shortTime(last?.at).split(' ')
                      return (
                        <tr
                          key={part}
                          className={toneOf(part)}
                          data-table-part={part}
                          onClick={() => openParty(part)}
                        >
                          <th scope="row">
                            <button
                              type="button"
                              className="party-name"
                              onClick={(event) => {
                                event.stopPropagation()
                                openParty(part)
                              }}
                            >
                              <PartIcon part={part} />
                              {whoWord(part, errand)}
                            </button>
                          </th>
                          {WORDS.progress.states.map((name) => {
                            const count = here.filter(
                              (item) => columnOf(placeFor(item)) === name,
                            ).length
                            return (
                              <td
                                key={name}
                                data-count={name}
                                className={count === 0 ? 'zero' : undefined}
                              >
                                {count}
                              </td>
                            )
                          })}
                          <td className="party-last">
                            {last && lastDay && (
                              <time dateTime={last.at}>
                                <span>{lastDay}</span>
                                <span>{lastClock}</span>
                              </time>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ))}
            {/* 「家族に渡すリンク」と同じ働き。夫の入口には出さない。 */}
            {!fromLink && (
              <button
                type="button"
                className="link-button progress-share"
                data-family-share
                aria-expanded={shareOpen}
                onClick={() => setShareOpen((current) => !current)}
              >
                {WORDS.progress.share}
              </button>
            )}
            {!fromLink && shareOpen && <SaveLink code={code} onClose={() => setShareOpen(false)} />}
          </section>
        )}

        {/* 下書きを決めて送るのは親。夫の入口には出さない。 */}
        {view === 'parts' && !fromLink && loadState === 'ready' && !draftsLater && unsent.length > 0 && (
          <DraftTabs
            sends={unsent
              .map((item) => sends.find((send) => send.item.id === item.id))
              .filter((send): send is Send => !!send)}
            code={code}
            errand={board?.profile.errand_people[0]?.label}
            shared={shared}
            busy={busy}
            onShared={markShared}
            onAsked={(item) => void markAsked(item)}
            onEdit={async (item, text) => {
              const saved = await save(item.id, (version) =>
                api.editItem(code, item.id, version, { text_original: text }),
              )
              if (saved) say(WORDS.row.editSaved)
              return saved
            }}
            onLater={() => setDraftsLater(true)}
          />
        )}

        {(view === 'parts' || view === 'own') &&
          loadState === 'ready' &&
          (view === 'own' ? [OWN_PART] : [...COLUMNS]).map((name) => {
            const label = name === OWN_PART ? WORDS.board.parent : name
            const isOwn = name === OWN_PART
            // 自分のことは書いた順の逆に。同じ秒に2つ残しても、新しいものを上にする。
            const here = isOwn
              ? sorted(items.filter((item) => item.part === name).reverse())
              : listOf(name)
            const open = isOwn ? true : isOpen(name)
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
                {!isOwn && (
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
                        {empty ? WORDS.board.partNone : headCounts(here, isOwn)}
                      </span>
                      {/* 閉じていても、返事の中身は読めるようにする。 */}
                      {!open && reply && <span className="part-reply">{replyText(reply)}</span>}
                    </button>
                  </h2>
                )}
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

        {!medicalOnTop && <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>}
      </main>

      {/* 知らせと［元に戻す］は、スクロールの位置によらず見えるように下へ固定する。 */}
      {showDock && (
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
          {nowProps && <NowActions {...nowProps} />}
        </div>
      )}
    </div>
  )
}
