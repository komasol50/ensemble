import { useState } from 'react'
import { api, type SetupChoices } from './api'
import { SetupScreen } from './Setup'
import { WORDS } from './words'

export function Start({ onCreated }: { onCreated: (code: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [settingUp, setSettingUp] = useState(false)
  // 動画の撮影用。URLに ?shoot を付けたときだけ出す。
  const shooting = new URLSearchParams(window.location.search).has('shoot')

  const create = async (home: string, choices?: SetupChoices) => {
    setBusy(true)
    setFailed(false)
    try {
      const { code } = await api.createBoard(home, choices)
      onCreated(code)
    } catch {
      setFailed(true)
      setBusy(false)
    }
  }

  if (settingUp) {
    return (
      <SetupScreen
        initial={null}
        busy={busy}
        message={failed ? WORDS.start.failed : null}
        onSave={(choices) => void create('own', choices)}
        onLater={() => void create('own')}
      />
    )
  }

  return (
    <main className="start">
      <h1>{WORDS.appName}</h1>
      <p className="start-line">{WORDS.start.oneLine}</p>
      <ul className="start-can">
        {WORDS.start.can.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="detail">{WORDS.start.cannot}</p>
      <p className="detail">{WORDS.start.lead}</p>
      <section className="home-choice">
        <p>{WORDS.start.ownNote}</p>
        <button type="button" disabled={busy} onClick={() => setSettingUp(true)}>
          {WORDS.start.own}
        </button>
      </section>
      <h2>{WORDS.start.title}</h2>
      {shooting && (
        <section className="home-choice">
          <p>{WORDS.start.shoot.note}</p>
          <button
            type="button"
            className="primary"
            data-primary
            disabled={busy}
            onClick={() => void create(WORDS.start.shoot.id)}
          >
            {WORDS.start.shoot.label}
          </button>
        </section>
      )}
      {WORDS.start.homes.map((home) => (
        <section key={home.id} className="home-choice">
          <p>{home.note}</p>
          <button
            type="button"
            className="primary"
            data-primary
            disabled={busy}
            onClick={() => void create(home.id)}
          >
            {home.label}
          </button>
        </section>
      ))}
      {failed && <p role="alert">{WORDS.start.failed}</p>}
    </main>
  )
}
