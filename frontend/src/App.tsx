import { useEffect, useRef, useState, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { AfterScreen } from './After'
import { api, ApiError, type SetupChoices } from './api'
import { AddScreen } from './AddScreen'
import { BoardScreen, type View } from './Board'
import { Consent } from './Consent'
import { DecideScreen } from './Decide'
import { MapScreen } from './Map'
import { MenuScreen } from './Menu'
import { FamilyOne } from './One'
import {
  canSearch,
  deleteBoardPhotos,
  linkPending,
  readPhoto,
  savePhotos,
  searchVault,
  type Photo,
} from './photos'
import { device, type Pending, type Proposal, type Screen } from './device'
import { SetupScreen } from './Setup'
import { Start } from './Start'
import { TrashScreen } from './Trash'
import { VaultScreen } from './Vault'
import { WORDS } from './words'

// 家族コードはURLの # より後ろに置く（サーバーへ届かないので、記録に残らない）。
function codeFromUrl(): string | null {
  const code = window.location.hash.slice(1)
  return code || null
}

function firstCode(): string | null {
  const fromUrl = codeFromUrl()
  if (fromUrl) return fromUrl
  // 撮影のやり直しでは、前のボードを開かず最初の画面から始める。
  if (new URLSearchParams(window.location.search).has('shoot')) return null
  const last = device.lastCode()
  if (last) window.history.replaceState(null, '', `/b#${last}`)
  return last
}


const NAV_VIEWS = ['now', 'parts', 'progress'] as const
type NavView = (typeof NAV_VIEWS)[number]
// 夫がリンクから入ったときは「いま」を出さない。「いま」は親の視点なので。
const LINK_VIEWS = ['parts', 'progress'] as const
const NAV_LABELS: Record<NavView, string> = {
  now: WORDS.nav.now,
  parts: WORDS.nav.parts,
  progress: WORDS.nav.progress,
}
const NAV_ICONS: Record<NavView, ReactNode> = {
  now: <path d="M3 10l7-6 7 6M5 9v8h10V9" />,
  parts: <path d="M3 5.5A2.5 2.5 0 0 1 5.5 3h9A2.5 2.5 0 0 1 17 5.5v5a2.5 2.5 0 0 1-2.5 2.5H9l-4 3.5V13a2.5 2.5 0 0 1-2-2.5z" />,
  progress: <path d="M7 5h10M7 10h10M7 15h10M3 5h.5M3 10h.5M3 15h.5" />,
}

function AppBar({ onMenu }: { onMenu: () => void }) {
  return (
    <header className="app-bar">
      <span className="brand">
        <svg aria-hidden="true" width="26" height="24" viewBox="0 0 26 24">
          <circle cx="13" cy="7" r="6" fill="currentColor" opacity="0.8" />
          <circle cx="7" cy="17" r="6" fill="currentColor" opacity="0.55" />
          <circle cx="19" cy="17" r="6" fill="currentColor" />
        </svg>
        {WORDS.appName}
      </span>
      <button type="button" className="icon-button" aria-label={WORDS.menu.title} onClick={onMenu}>
        <svg
          aria-hidden="true"
          width="24"
          height="24"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        >
          <path d="M3 5h14M3 10h14M3 15h14" />
        </svg>
      </button>
    </header>
  )
}

function BottomNav({
  views,
  current,
  onGo,
}: {
  views: readonly NavView[]
  current: NavView | null
  onGo: (view: NavView) => void
}) {
  return (
    <nav className="bottom-nav" aria-label={WORDS.nav.label}>
      {views.map((view) => (
        <button
          type="button"
          key={view}
          aria-current={current === view ? 'page' : undefined}
          onClick={() => onGo(view)}
        >
          <svg
            aria-hidden="true"
            width="24"
            height="24"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {NAV_ICONS[view]}
          </svg>
          {NAV_LABELS[view]}
        </button>
      ))}
    </nav>
  )
}

// いまの案内から設定する。
function SetupFromNow({ code, onDone }: { code: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  // 途中まで選んで保存した家庭では、前に選んだ内容を残す。
  const [initial, setInitial] = useState<SetupChoices | null | undefined>(undefined)
  useEffect(() => {
    api
      .getBoard(code)
      .then((board) => setInitial(board.profile.choices))
      .catch(() => setInitial(null))
  }, [code])
  if (initial === undefined) return null
  return (
    <SetupScreen
      initial={initial}
      busy={busy}
      message={message}
      onSave={(choices) => {
        setBusy(true)
        setMessage(null)
        api
          .getBoard(code)
          .then((board) => api.changeSetup(code, board.version, choices))
          .then(onDone)
          .catch(() => {
            setMessage(WORDS.board.failed)
            setBusy(false)
          })
      }}
      onLater={onDone}
    />
  )
}

export default function App() {
  // 家族の1件はボードを開かない。この端末の「前に開いたボード」も変えない。
  return window.location.pathname === '/m' ? <FamilyOne /> : <ParentApp />
}

function ParentApp() {
  const [code, setCode] = useState<string | null>(firstCode)

  useEffect(() => {
    const onHash = () => setCode(codeFromUrl())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (!code) {
    return (
      <Start
        onCreated={(created) => {
          device.setLastCode(created)
          window.history.pushState(null, '', `/b#${created}`)
          setCode(created)
        }}
      />
    )
  }
  return (
    <Family
      key={code}
      code={code}
      onLeave={() => {
        device.forgetBoard(code)
        void deleteBoardPhotos(code).catch(() => undefined)
        window.history.pushState(null, '', '/')
        setCode(null)
      }}
    />
  )
}

// 止まっている仕分けと案を送り直す間隔。切り替えの知らせが来ない端末でも止まらないように。
const RETRY_MS = 5000
const NOTICE_MS = 6000

function Family({ code, onLeave }: { code: string; onLeave: () => void }) {
  const [screen, setScreenState] = useState<Screen>(() => device.screen(code))
  const [consentOpen, setConsentOpen] = useState(false)
  const [pending, setPendingState] = useState<Pending[]>(() => device.pending(code))
  const [consented, setConsented] = useState(device.hasConsented)
  // 開いた直後は「いま」。下のナビとメニューで変える。
  const [fromLink] = useState(() => device.fromLink(code))
  const [view, setViewState] = useState<View>(() => {
    const stored = device.tabView(code) as View | null
    if (fromLink) return stored && stored !== 'now' ? stored : 'progress'
    return stored ?? 'now'
  })
  const setView = (next: View) => {
    device.setTabView(code, next)
    setViewState(next)
  }
  const [justAdded, setJustAdded] = useState<{ id: string; text: string } | null>(null)

  const setScreen = (next: Screen) => {
    device.setScreen(code, next)
    setScreenState(next)
  }
  const [proposal, setProposalState] = useState<Proposal | null>(() => device.proposal(code))
  // 仕分けと案は時間がかかる。画面を移っても続くよう、ここで持つ。
  const [boardRefresh, setBoardRefresh] = useState(0)
  const [retry, setRetry] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [photoRefresh, setPhotoRefresh] = useState(0)
  // 送り直しの後に古い通信が終わっても、新しい通信の印を消さないよう、回ごとの番号で見分ける。
  const sorting = useRef(new Map<string, number>())
  const proposing = useRef<{ itemId: string; run: number } | null>(null)
  const runs = useRef(0)
  // 通信の応答が届いた時点の状態で判断する（閉じた案を、遅れた応答で戻さないため）。
  const pendingNow = useRef(pending)
  const proposalNow = useRef(proposal)

  const updatePending = (change: (current: Pending[]) => Pending[]) => {
    const next = change(pendingNow.current)
    pendingNow.current = next
    device.setPending(code, next)
    setPendingState(next)
  }
  const setProposal = (next: Proposal | null) => {
    proposalNow.current = next
    device.setProposal(code, next)
    setProposalState(next)
  }

  useEffect(() => device.setLastCode(code), [code])

  // 画面を移ったら頭から見せる。読んでいた位置のまま開くと、案の画面の上が隠れる。
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [screen])

  // つながったときと、別のアプリから戻ったときに、止まっていた分を送り直す。
  // スマホは裏に回ると通信を切る。応答が返らないままのこともあるので、送信中の印も外す。
  // サーバーは同じ要求をAIに2回渡さないので、送り直しても費用は増えない。
  useEffect(() => {
    const resume = () => {
      if (document.visibilityState !== 'visible') return
      sorting.current.clear()
      proposing.current = null
      setRetry((n) => n + 1)
    }
    window.addEventListener('online', resume)
    document.addEventListener('visibilitychange', resume)
    return () => {
      window.removeEventListener('online', resume)
      document.removeEventListener('visibilitychange', resume)
    }
  }, [])

  // 知らせが来なくても、待っている分があれば定期的に送り直す。通信中のものは下で飛ばす。
  const isWaiting = pending.some((entry) => !entry.failed) || proposal?.state === 'working'
  useEffect(() => {
    if (!isWaiting) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setRetry((n) => n + 1)
    }, RETRY_MS)
    return () => window.clearInterval(timer)
  }, [isWaiting])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), NOTICE_MS)
    return () => window.clearTimeout(timer)
  }, [notice])

  // 入れた文を1つずつ仕分ける。断られたら文は消さず、その理由を出す。
  useEffect(() => {
    const next = pending.find((entry) => !entry.failed && !sorting.current.has(entry.id))
    if (!next || sorting.current.size > 0) return
    runs.current += 1
    const run = runs.current
    sorting.current.set(next.id, run)
    api
      .intake(code, next.id, next.text, next.said, next.who)
      .then((response) => {
        // 送り直しの応答が2回届いても、処理は1回だけ。
        if (!pendingNow.current.some((entry) => entry.id === next.id)) return
        updatePending((current) => current.filter((entry) => entry.id !== next.id))
        setBoardRefresh((n) => n + 1)
        // 一緒に入れた写真を、仕分けた項目と結ぶ。写真そのものはどこへも送らない。
        void linkPending(code, next.id, response.added)
          .then((linked) => {
            if (linked) setPhotoRefresh((n) => n + 1)
          })
          .catch(() => undefined)
        // 新しい質問は、読み取った資料があれば探しておく。答えは資料の文の引用だけ。
        for (const id of response.added) {
          const item = response.board.items.find((entry) => entry.id === id)
          if (item && canSearch(item)) {
            void searchVault(code, id)
              .then((answer) => {
                if (answer) setPhotoRefresh((n) => n + 1)
              })
              .catch(() => undefined)
          }
        }
        if (response.trigger) {
          setProposal({ state: 'working', item_id: response.trigger })
          return
        }
        // 案にならない文でも、どこへ入ったかを必ず知らせる。
        const names = response.added
          .map((id) => response.board.items.find((item) => item.id === id)?.part)
          .filter((part): part is string => !!part)
          .map((part) => (part === '親' ? WORDS.board.parent : part))
        setNotice(WORDS.board.sortedInto([...new Set(names)]))
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError) {
          const failed = error.message || WORDS.board.sortFailed
          const limit = error.status === 429
          updatePending((current) =>
            current.map((entry) => (entry.id === next.id ? { ...entry, failed, limit } : entry)),
          )
        }
        // 通信の失敗は何もしない。定期の送り直しで進む。
      })
      .finally(() => {
        if (sorting.current.get(next.id) === run) sorting.current.delete(next.id)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, retry, code])

  // 案を作る。押すまで台帳は変わらない。
  useEffect(() => {
    if (proposal?.state !== 'working' || proposing.current?.itemId === proposal.item_id) return
    const itemId = proposal.item_id
    const after = proposal.after
    const chosen = proposal.chosen
    runs.current += 1
    const run = runs.current
    proposing.current = { itemId, run }
    // 待っている案と違えば、遅れて届いた応答なので捨てる。閉じた案を戻さない。
    const stillWanted = () => {
      const current = proposalNow.current
      return current?.state === 'working' && current.item_id === itemId
    }
    api
      .propose(code, itemId, chosen)
      .then((response) => {
        if (!stillWanted()) return
        setProposal(
          response.branch
            ? {
                state: 'ready',
                item_id: itemId,
                branch: response.branch,
                impact: response.impact,
                week: response.week,
              }
            : {
                state: 'failed',
                item_id: itemId,
                after,
                chosen,
                message: response.message ?? WORDS.board.proposalFailed,
              },
        )
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && stillWanted()) {
          setProposal({
            state: 'failed',
            item_id: itemId,
            after,
            chosen,
            limit: error.status === 429,
            message: error.message || WORDS.board.proposalFailed,
          })
        }
        // 通信の失敗は何もしない。定期の送り直しで進む。
      })
      .finally(() => {
        if (proposing.current?.run === run) proposing.current = null
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal, retry, code])

  // 読ませると選んだ写真を読み取る。文の仕分けとは別に、裏で進める。
  const readInBackground = async (saved: Photo[]) => {
    setNotice(WORDS.photos.reading)
    let failed = false
    let refused = false
    for (const photo of saved) {
      try {
        const result = await readPhoto(code, photo)
        refused = refused || result.refused
        failed = failed || (!result.text && !result.refused)
      } catch {
        failed = true
      }
    }
    setPhotoRefresh((n) => n + 1)
    setNotice(
      failed
        ? WORDS.photos.readFailed
        : refused
          ? WORDS.photos.readRefused
          : WORDS.photos.readDone,
    )
  }

  // 画面を移ったら、新しい画面の頭から見せる。高さが変わった後のブラウザの補正より後に動かす。
  const go = (next: View) => {
    flushSync(() => {
      setScreen('board')
      setView(next)
    })
    window.scrollTo(0, 0)
  }
  const nav: NavView | null =
    screen === 'board'
      ? view === 'week'
        ? 'now'
        : view === 'own'
          ? null
          : view
      : screen === 'decide' || screen === 'map' || screen === 'after'
        ? 'now'
        : null
  const inShell = (content: ReactNode) => (
    <div className="app-shell">
      <AppBar onMenu={() => setScreen('menu')} />
      {content}
      <BottomNav views={fromLink ? LINK_VIEWS : NAV_VIEWS} current={nav} onGo={go} />
    </div>
  )

  if (consentOpen) {
    return (
      <Consent
        buttonLabel={WORDS.consent.close}
        onClose={() => setConsentOpen(false)}
        onRemoveBoard={async () => {
          await api.deleteBoard(code)
          onLeave()
        }}
      />
    )
  }
  if ((screen === 'add' || screen === 'after') && !consented) {
    return (
      <Consent
        buttonLabel={WORDS.consent.agree}
        onClose={() => {
          device.setConsented()
          setConsented(true)
        }}
      />
    )
  }
  if (screen === 'add') {
    return (
      <AddScreen
        code={code}
        draft={device.draft(code)}
        onDraft={(text) => device.setDraft(code, text)}
        onOpenList={() => go('parts')}
        onBack={() => setScreen('board')}
        onSubmit={async (text, said, who, photos, category, letRead) => {
          // 写真を先に保存する。失敗したら文も入れず、この画面に残す。
          const pendingId = `${Date.now()}-${Math.random()}`
          const hasText = !!text.trim()
          if (photos.length > 0) {
            const saved = await savePhotos(code, photos, category, hasText ? pendingId : undefined)
            setPhotoRefresh((n) => n + 1)
            if (letRead) void readInBackground(saved)
          }
          if (hasText) {
            // 先に端末へ保存する。電波が切れても、入れた文は消えない。
            updatePending((current) => [
              ...current,
              // crypto.randomUUID は https でしか使えないので、端末の中だけの番号はこれで足りる。
              {
                id: pendingId,
                text,
                at: new Date().toISOString(),
                said: said ?? undefined,
                who: who ?? undefined,
              },
            ])
          } else if (!letRead) {
            // 写真だけで読ませないなら、AIを呼ばない。
            setNotice(WORDS.photos.saved)
          }
          device.setDraft(code, '')
          setScreen('board')
        }}
      />
    )
  }
  if (screen === 'setup') {
    return (
      <SetupFromNow
        code={code}
        onDone={() => {
          setBoardRefresh((n) => n + 1)
          setScreen('board')
        }}
      />
    )
  }
  if (screen === 'menu') {
    return (
      <MenuScreen
        code={code}
        onClose={() => setScreen('board')}
        onView={go}
        onVault={() => setScreen('vault')}
        onTrash={() => setScreen('trash')}
        onData={() => setConsentOpen(true)}
        fromLink={fromLink}
      />
    )
  }
  if (screen === 'after') {
    return inShell(
      <AfterScreen
        code={code}
        onBack={() => setScreen('board')}
        onOther={() => setScreen('add')}
        onSaved={(itemId, text, chosen) => {
          setBoardRefresh((n) => n + 1)
          setProposal({ state: 'working', item_id: itemId, after: true, chosen })
          setJustAdded({ id: itemId, text })
          go('now')
        }}
      />,
    )
  }
  if (screen === 'map') {
    return inShell(<MapScreen code={code} onBack={() => setScreen('board')} />)
  }
  if (screen === 'trash') {
    return inShell(
      <TrashScreen
        code={code}
        onBack={() => setScreen('board')}
        onRestored={() => {
          setBoardRefresh((n) => n + 1)
          setPhotoRefresh((n) => n + 1)
        }}
      />,
    )
  }
  if (screen === 'vault') {
    return inShell(
      <VaultScreen
        code={code}
        onBack={() => setScreen('board')}
        onAdd={() => setScreen('add')}
        onChanged={() => setPhotoRefresh((n) => n + 1)}
      />,
    )
  }
  if (screen === 'decide' && proposal?.state === 'ready') {
    return inShell(
      <DecideScreen
        code={code}
        proposal={proposal}
        onAccepted={(hasDrafts) => {
          setProposal(null)
          setBoardRefresh((n) => n + 1)
          // 下書きがあるときは、相手ごとの下書きへ。無いときは「いま」へ。
          go(hasDrafts ? 'parts' : 'now')
        }}
        onNothing={() => {
          setProposal(null)
          setScreen('board')
        }}
        onBack={() => setScreen('board')}
      />,
    )
  }
  return inShell(
    <BoardScreen
      code={code}
      view={view}
      justAdded={justAdded}
      onView={go}
      onJustAddedShown={() => setJustAdded(null)}
      onOpenAfter={() => setScreen('after')}
      onOpenSetup={() => setScreen('setup')}
      onRetryProposal={(itemId) =>
        setProposal({
          state: 'working',
          item_id: itemId,
          after: proposal?.state === 'failed' ? proposal.after : undefined,
          chosen: proposal?.state === 'failed' ? proposal.chosen : undefined,
        })
      }
      pending={pending}
      proposal={proposal}
      refresh={boardRefresh}
      notice={notice}
      photoRefresh={photoRefresh}
      onOpenVault={() => setScreen('vault')}
      onOpenMap={() => setScreen('map')}
      onRemovePending={(id) =>
        updatePending((current) => current.filter((entry) => entry.id !== id))
      }
      onSortAgain={(id) =>
        updatePending((current) =>
          current.map((entry) =>
            entry.id === id ? { ...entry, failed: undefined, limit: undefined } : entry,
          ),
        )
      }
      onRetry={() => setRetry((n) => n + 1)}
      onOpenProposal={() => setScreen('decide')}
      onCloseProposal={() => setProposal(null)}
      onItemRemoved={(id) => {
        if (proposalNow.current?.item_id === id) setProposal(null)
      }}
      onAdd={() => setScreen('add')}
      onLeave={onLeave}
    />,
  )
}
