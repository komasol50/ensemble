import { useEffect, useState } from 'react'
import { AddScreen } from './AddScreen'
import { BoardScreen } from './Board'
import { Consent } from './Consent'
import { device, type Pending, type Screen } from './device'
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
  const setPending = (next: Pending[]) => {
    device.setPending(code, next)
    setPendingState(next)
  }

  useEffect(() => device.setLastCode(code), [code])

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
          setPending([
            ...pending,
            // crypto.randomUUID は https でしか使えないので、端末の中だけの番号はこれで足りる。
            { id: `${Date.now()}-${Math.random()}`, text, at: new Date().toISOString() },
          ])
          device.setDraft(code, '')
          setScreen('board')
        }}
      />
    )
  }
  return (
    <BoardScreen
      code={code}
      pending={pending}
      onRemovePending={(id) => setPending(pending.filter((entry) => entry.id !== id))}
      onAdd={() => setScreen('add')}
      onConsent={() => setConsentOpen(true)}
      onLeave={onLeave}
    />
  )
}
