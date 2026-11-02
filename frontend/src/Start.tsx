import { useState } from 'react'
import { api } from './api'
import { WORDS } from './words'

export function Start({ onCreated }: { onCreated: (code: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

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
      <p>{WORDS.start.lead}</p>
      <h2>{WORDS.start.title}</h2>
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
