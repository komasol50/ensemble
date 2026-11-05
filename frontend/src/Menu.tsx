import { useEffect, useState } from 'react'
import { api, type Board } from './api'
import { HomeCards, SaveLink } from './Board'
import { Chevron } from './parts'
import { WORDS } from './words'

export type MenuTarget = 'parts' | 'progress' | 'week' | 'own'

// メニュー。下のナビにない入口と、設定をここへ集める。
export function MenuScreen({
  code,
  onClose,
  onView,
  onVault,
  onTrash,
  onData,
}: {
  code: string
  onClose: () => void
  onView: (view: MenuTarget) => void
  onVault: () => void
  onTrash: () => void
  onData: () => void
}) {
  const words = WORDS.menu
  const [page, setPage] = useState<'home' | 'link' | null>(null)
  const [board, setBoard] = useState<Board | null>(null)

  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((next) => {
        if (active) setBoard(next)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [code])

  if (page) {
    return (
      <main className="menu-screen">
        <header className="decide-head">
          <button type="button" className="quiet back" onClick={() => setPage(null)}>
            <Chevron to="back" />
            {WORDS.add.back}
          </button>
        </header>
        <h1>{page === 'home' ? words.family : words.link}</h1>
        {page === 'home' && board && <HomeCards profile={board.profile} />}
        {page === 'link' && <SaveLink code={code} onClose={() => setPage(null)} />}
      </main>
    )
  }

  const row = (label: string, onClick: () => void) => (
    <button type="button" className="menu-row" onClick={onClick}>
      {label}
      <Chevron />
    </button>
  )

  return (
    <main className="menu-screen" data-menu>
      <header className="menu-head">
        <h1>{words.title}</h1>
        <button type="button" className="icon-button" aria-label={words.close} onClick={onClose}>
          <svg
            aria-hidden="true"
            width="22"
            height="22"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M5 5l10 10M15 5L5 15" />
          </svg>
        </button>
      </header>
      <section>
        <h2 className="menu-label">{words.settings}</h2>
        {row(words.family, () => setPage('home'))}
        {row(words.link, () => setPage('link'))}
      </section>
      <section>
        <h2 className="menu-label">{words.records}</h2>
        {row(words.entered, () => onView('parts'))}
        {row(words.vault, onVault)}
        {row(words.calendar, () => onView('week'))}
        {row(words.progress, () => onView('progress'))}
        {row(WORDS.board.parent, () => onView('own'))}
        {row(WORDS.board.trash, onTrash)}
      </section>
      <section>
        <h2 className="menu-label">{words.howTo}</h2>
        {row(words.data, onData)}
      </section>
    </main>
  )
}
