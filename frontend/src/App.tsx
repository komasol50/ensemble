import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { AddScreen } from './AddScreen'
import { BoardScreen } from './Board'
import { Consent } from './Consent'
import { DecideScreen } from './Decide'
import { device, type Pending, type Proposal, type Screen } from './device'
import { Start } from './Start'
import { WORDS } from './words'

// 家族コードはURLの # より後ろに置く（サーバーへ届かないので、記録に残らない）。
function codeFromUrl(): string | null {
  const code = window.location.hash.slice(1)
  return code || null
}

function firstCode(): string | null {
  const fromUrl = codeFromUrl()
  if (fromUrl) return fromUrl
  const last = device.lastCode()
  if (last) window.history.replaceState(null, '', `/b#${last}`)
  return last
}

export default function App() {
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
        device.forgetLastCode()
        window.history.pushState(null, '', '/')
        setCode(null)
      }}
    />
  )
}

function Family({ code, onLeave }: { code: string; onLeave: () => void }) {
  const [screen, setScreenState] = useState<Screen>(() => device.screen(code))
  const [consentOpen, setConsentOpen] = useState(false)
  const [pending, setPendingState] = useState<Pending[]>(() => device.pending(code))
  const [consented, setConsented] = useState(device.hasConsented)

  const setScreen = (next: Screen) => {
    device.setScreen(code, next)
    setScreenState(next)
  }
  const [proposal, setProposalState] = useState<Proposal | null>(() => device.proposal(code))
  // 仕分けと案は時間がかかる。画面を移っても続くよう、ここで持つ。
  const [boardRefresh, setBoardRefresh] = useState(0)
  const [retry, setRetry] = useState(0)
  const sorting = useRef(new Set<string>())
  const proposing = useRef<string | null>(null)

  const updatePending = (change: (current: Pending[]) => Pending[]) => {
    setPendingState((current) => {
      const next = change(current)
      device.setPending(code, next)
      return next
    })
  }
  const setProposal = (next: Proposal | null) => {
    device.setProposal(code, next)
    setProposalState(next)
  }

  useEffect(() => device.setLastCode(code), [code])

  // つながったら、止まっていた分を送り直す。
  useEffect(() => {
    const onOnline = () => setRetry((n) => n + 1)
    window.addEventListener('online', onOnline)
    return () => window.removeEventListener('online', onOnline)
  }, [])

  // 入れた文を1つずつ仕分ける。断られたら文は消さず、その理由を出す。
  useEffect(() => {
    const next = pending.find((entry) => !entry.failed && !sorting.current.has(entry.id))
    if (!next || sorting.current.size > 0) return
    sorting.current.add(next.id)
    api
      .intake(code, next.id, next.text)
      .then((response) => {
        updatePending((current) => current.filter((entry) => entry.id !== next.id))
        setBoardRefresh((n) => n + 1)
        if (response.trigger) setProposal({ state: 'working', item_id: response.trigger })
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError) {
          const failed = error.message || WORDS.board.sortFailed
          updatePending((current) =>
            current.map((entry) => (entry.id === next.id ? { ...entry, failed } : entry)),
          )
        }
        // 通信の失敗は何もしない。つながったら送り直す。
      })
      .finally(() => sorting.current.delete(next.id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, retry, code])

  // 案を作る。押すまで台帳は変わらない。
  useEffect(() => {
    if (proposal?.state !== 'working' || proposing.current === proposal.item_id) return
    const itemId = proposal.item_id
    proposing.current = itemId
    api
      .propose(code, itemId)
      .then((response) => {
        setProposal(
          response.branch
            ? { state: 'ready', item_id: itemId, branch: response.branch }
            : {
                state: 'failed',
                item_id: itemId,
                message: response.message ?? WORDS.board.proposalFailed,
              },
        )
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError) {
          setProposal({
            state: 'failed',
            item_id: itemId,
            message: error.message || WORDS.board.proposalFailed,
          })
        }
      })
      .finally(() => {
        proposing.current = null
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal, retry, code])

  if (consentOpen) {
    return <Consent buttonLabel={WORDS.consent.close} onClose={() => setConsentOpen(false)} />
  }
  if (screen === 'add' && !consented) {
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
        draft={device.draft(code)}
        onDraft={(text) => device.setDraft(code, text)}
        onBack={() => setScreen('board')}
        onSubmit={(text) => {
          // 先に端末へ保存する。電波が切れても、入れた文は消えない。
          updatePending((current) => [
            ...current,
            // crypto.randomUUID は https でしか使えないので、端末の中だけの番号はこれで足りる。
            { id: `${Date.now()}-${Math.random()}`, text, at: new Date().toISOString() },
          ])
          device.setDraft(code, '')
          setScreen('board')
        }}
      />
    )
  }
  if (screen === 'decide' && proposal?.state === 'ready') {
    return (
      <DecideScreen
        code={code}
        proposal={proposal}
        onAccepted={() => {
          setProposal(null)
          setBoardRefresh((n) => n + 1)
          setScreen('board')
        }}
        onNothing={() => {
          setProposal(null)
          setScreen('board')
        }}
        onBack={() => setScreen('board')}
      />
    )
  }
  return (
    <BoardScreen
      code={code}
      pending={pending}
      proposal={proposal}
      refresh={boardRefresh}
      onRemovePending={(id) =>
        updatePending((current) => current.filter((entry) => entry.id !== id))
      }
      onSortAgain={(id) =>
        updatePending((current) =>
          current.map((entry) => (entry.id === id ? { ...entry, failed: undefined } : entry)),
        )
      }
      onRetry={() => setRetry((n) => n + 1)}
      onOpenProposal={() => setScreen('decide')}
      onCloseProposal={() => setProposal(null)}
      onAdd={() => setScreen('add')}
      onConsent={() => setConsentOpen(true)}
      onLeave={onLeave}
    />
  )
}
