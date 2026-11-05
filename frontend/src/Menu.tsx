import { useEffect, useState } from 'react'
import { api, type Board, type SetupChoices } from './api'
import { SetupScreen } from './Setup'
import { HomeCards, SaveLink } from './Board'
import { Chevron } from './parts'
import { CONSENT_LINES, MEDICAL_FIXED_TEXT, WORDS } from './words'

export type MenuTarget = 'parts' | 'progress' | 'week' | 'own'

// メニュー。下のナビにない入口と、設定をここへ集める。
export function MenuScreen({
  code,
  onClose,
  onView,
  onVault,
  onTrash,
  onData,
  fromLink,
}: {
  code: string
  onClose: () => void
  onView: (view: MenuTarget) => void
  onVault: () => void
  onTrash: () => void
  onData: () => void
  fromLink: boolean
}) {
  const words = WORDS.menu
  const [page, setPage] = useState<'home' | 'link' | 'rules' | 'howto' | null>(null)
  const [board, setBoard] = useState<Board | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

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

  const saveSetup = async (choices: SetupChoices) => {
    if (!board) return
    setBusy(true)
    setMessage(null)
    try {
      setBoard(await api.changeSetup(code, board.version, choices))
      setPage(null)
    } catch {
      setMessage(WORDS.board.failed)
    } finally {
      setBusy(false)
    }
  }

  // 自分の家庭は同じ画面で直せる。試す家庭は設定を読むだけ。
  if (page === 'home' && board?.profile.name === WORDS.setup.ownName) {
    return (
      <SetupScreen
        initial={board.profile.choices}
        busy={busy}
        message={message}
        onSave={(choices) => void saveSetup(choices)}
        onLater={() => setPage(null)}
      />
    )
  }

  if (page) {
    return (
      <main className="menu-screen">
        <header className="decide-head">
          <button type="button" className="quiet back" onClick={() => setPage(null)}>
            <Chevron to="back" />
            {WORDS.add.back}
          </button>
        </header>
        <h1>
          {page === 'home'
            ? words.family
            : page === 'link'
              ? words.link
              : page === 'rules'
                ? words.rules
                : words.howToUse}
        </h1>
        {page === 'rules' && (
          <section data-rules>
            {board && <HomeCards profile={board.profile} open />}
            {(board?.rules ?? [])
              .filter((rule) => rule.active)
              .map((rule) => (
                <p className="detail" key={rule.id}>
                  {rule.text}
                </p>
              ))}
          </section>
        )}
        {page === 'howto' && (
          <section data-howto>
            <ol className="howto-steps">
              {words.howToSteps.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            <h2>{WORDS.consent.title}</h2>
            <ol>
              {CONSENT_LINES.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
          </section>
        )}
        {page === 'home' && board && <HomeCards profile={board.profile} open />}
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
        <button
          type="button"
          className="icon-button menu-close"
          aria-label={words.close}
          onClick={onClose}
        >
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
      {!fromLink && (
        <section>
          <h2 className="menu-label">{words.settings}</h2>
          {row(words.family, () => setPage('home'))}
          {row(words.rules, () => setPage('rules'))}
          {row(words.link, () => setPage('link'))}
        </section>
      )}
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
        {row(words.howToUse, () => setPage('howto'))}
        {!fromLink && row(words.data, onData)}
      </section>
    </main>
  )
}
