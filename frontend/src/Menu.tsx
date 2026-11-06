import { useEffect, useState, type ReactNode } from 'react'
import { api, type Board, type SetupChoices } from './api'
import { SetupScreen } from './Setup'
import { HomeCards, SaveLink } from './Board'
import { Chevron } from './parts'
import { CONSENT_LINES, MEDICAL_FIXED_TEXT, WORDS } from './words'

export type MenuTarget = 'parts' | 'progress' | 'week' | 'own'

type Page = 'home' | 'link' | 'rules' | 'howto' | 'entered'

// 行の頭の印（図の⑫）。意味は語で伝えるので、読み上げない。
const ICONS: Record<string, ReactNode> = {
  family: (
    <>
      <circle cx="10" cy="6.5" r="3.2" />
      <path d="M3.5 17c0-3.4 2.9-5.3 6.5-5.3s6.5 1.9 6.5 5.3" />
    </>
  ),
  rules: (
    <>
      <circle cx="10" cy="10" r="2.6" />
      <path d="M10 2.5v2.4M10 15.1v2.4M2.5 10h2.4M15.1 10h2.4M4.7 4.7l1.7 1.7M13.6 13.6l1.7 1.7M4.7 15.3l1.7-1.7M13.6 6.4l1.7-1.7" />
    </>
  ),
  link: (
    <path d="M8.3 11.7l3.4-3.4M7.2 9.4l-1.9 1.9a2.9 2.9 0 0 0 4.1 4.1l1.9-1.9M12.8 10.6l1.9-1.9a2.9 2.9 0 0 0-4.1-4.1L8.7 6.5" />
  ),
  entered: <path d="M5 2.5h6.5L15 6v11.5H5zM11.5 2.5V6H15M7.5 10h5M7.5 13h5" />,
  vault: (
    <>
      <path d="M3.5 3h7l2.5 2.5V8M3.5 3v14h5.5" />
      <circle cx="12.5" cy="12.5" r="3" />
      <path d="M14.7 14.7l2.8 2.8" />
    </>
  ),
  calendar: <path d="M3.5 5h13v12h-13zM3.5 8.5h13M7 3v3.5M13 3v3.5" />,
  progress: <path d="M7 5h10M7 10h10M7 15h10M3 5h.5M3 10h.5M3 15h.5" />,
  howto: (
    <path d="M10 5.5C8.4 4.3 5.9 4 3 4.5v11c2.9-.5 5.4-.2 7 1 1.6-1.2 4.1-1.5 7-1v-11c-2.9-.5-5.4-.2-7 1zM10 5.5v11" />
  ),
  data: (
    <>
      <path d="M3 6h3M9.6 6H17M3 14h7.4M14 14h3" />
      <circle cx="7.8" cy="6" r="1.8" />
      <circle cx="12.2" cy="14" r="1.8" />
    </>
  ),
  trash: <path d="M4 5.5h12M8 5.5v-2h4v2M5.5 5.5l.8 11h7.4l.8-11M8.5 8.5v5.5M11.5 8.5v5.5" />,
}

function MenuIcon({ name }: { name: string }) {
  return (
    <svg
      aria-hidden="true"
      className="menu-row-icon"
      width="22"
      height="22"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {ICONS[name]}
    </svg>
  )
}

// メニュー。下のナビにない入口と、設定をここへ集める。段は図の⑫のとおり。
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
  const [page, setPage] = useState<Page | null>(null)
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

  const row = (label: string, onClick: () => void, icon?: string) => (
    <button type="button" className="menu-row" onClick={onClick}>
      <span className="menu-row-label">
        {icon && <MenuIcon name={icon} />}
        {label}
      </span>
      <Chevron />
    </button>
  )

  // 自分の家庭は同じ画面で直せる。試す家庭は設定を読むだけ。
  if (page === 'home' && board?.profile.name === WORDS.setup.ownName) {
    return (
      <SetupScreen
        initial={board.profile.choices}
        editing={board.profile.configured}
        busy={busy}
        message={message}
        onSave={(choices) => void saveSetup(choices)}
        onLater={() => setPage(null)}
      />
    )
  }

  if (page) {
    const titles: Record<Page, string> = {
      home: words.family,
      link: words.familyLink,
      rules: words.rules,
      howto: words.howToUse,
      entered: words.entered,
    }
    return (
      <main className="menu-screen">
        <header className="decide-head">
          <button type="button" className="quiet back" onClick={() => setPage(null)}>
            <Chevron to="back" />
            {WORDS.add.back}
          </button>
        </header>
        <h1>{titles[page]}</h1>
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
        {page === 'link' && (
          <>
            <p className="detail" data-family-link-note>
              {words.familyLinkNote}
            </p>
            <SaveLink code={code} inMenu onClose={() => setPage(null)} />
          </>
        )}
        {/* 入れた情報・原文の中の段。自分のことと、ゴミ箱もここに置く（図の4行に収めるため）。 */}
        {page === 'entered' && (
          <section data-menu-section="entered">
            {row(words.byPart, () => onView('parts'), 'entered')}
            {row(WORDS.board.parent, () => onView('own'), 'family')}
            {row(WORDS.board.trash, onTrash, 'trash')}
          </section>
        )}
      </main>
    )
  }

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
        <section data-menu-section="settings">
          <h2 className="menu-label">{words.settings}</h2>
          {row(words.family, () => setPage('home'), 'family')}
          {row(words.rules, () => setPage('rules'), 'rules')}
          {row(words.familyLink, () => setPage('link'), 'link')}
        </section>
      )}
      <section data-menu-section="records">
        <h2 className="menu-label">{words.records}</h2>
        {row(words.entered, () => setPage('entered'), 'entered')}
        {/* 付き添う親自身の食事と寝床への入口（インタビューの決定15）。名前で見つかるように段に置く。 */}
        {row(WORDS.board.parent, () => onView('own'), 'family')}
        {row(words.vault, onVault, 'vault')}
        {row(words.calendar, () => onView('week'), 'calendar')}
        {row(words.progress, () => onView('progress'), 'progress')}
      </section>
      <section data-menu-section="howto">
        <h2 className="menu-label">{words.howTo}</h2>
        {row(words.howToUse, () => setPage('howto'), 'howto')}
        {!fromLink && row(words.data, onData, 'data')}
      </section>
      {!fromLink && (
        <p className="detail menu-note" data-menu-note>
          {words.skipped}
        </p>
      )}
    </main>
  )
}
