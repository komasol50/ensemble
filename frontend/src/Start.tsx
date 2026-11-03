import { useState } from 'react'
import { api } from './api'
import { WORDS } from './words'

export function Start({ onCreated }: { onCreated: (code: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // 動画の撮影用。URLに ?shoot を付けたときだけ出す。
  const shooting = new URLSearchParams(window.location.search).has('shoot')

  const create = async (home: string) => {
    setBusy(true)
    setFailed(false)
    try {
      const { code } = await api.createBoard(home)
      onCreated(code)
    } catch {
      setFailed(true)
      setBusy(false)
    }
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
