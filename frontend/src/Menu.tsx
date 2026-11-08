import { useEffect, useState, type ReactNode } from 'react'
import { api, type Board, type SetupChoices } from './api'
import { SetupScreen, setupInitial } from './Setup'
import { HomeCards, SaveLink } from './Board'
import { Chevron } from './parts'
import { CONSENT_LINES, MEDICAL_FIXED_TEXT, WORDS } from './words'

export type MenuTarget = 'parts' | 'progress' | 'week' | 'own'

type Page = 'home' | 'link' | 'rules' | 'howto' | 'consent' | 'try' | 'entered'

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
  consent: <path d="M10 2.5l6 2.5v4.5c0 3.6-2.6 6.4-6 8-3.4-1.6-6-4.4-6-8V5zM7.2 10l2 2 3.6-3.8" />,
  homes: (
    <>
      <path d="M2.5 10l4.5-4 4.5 4M4 9v6.5h6V9" />
      <path d="M11 8.5l3-2.5 3.5 3M12.5 8v7.5h4V8.6" />
    </>
  ),
}

// 試す家庭の設定を直すときの、最初に選んである内容（家庭A・Bの説明と同じ）。プリセットのファイルは
// AI への入力なので変えず、画面だけが持つ（2026-11-07 PO：試す家庭も、そのボードだけ直せる）。
// サーバーの presets.py の TRIAL_CHOICES と同じ（test_setup_family が突き合わせる）。
const TRIAL_CHOICES: Record<string, SetupChoices> = {
  家庭A: {
    parent: '母',
    work: '会社員',
    errand: '夫・パートナー',
    bedside: 'いない',
    supports: ['延長保育'],
    no_support: false,
    contacts: null,
    child_age: 3,
    child_sex: null,
    child_school: '保育園・幼稚園',
    siblings: [],
    errand_times: ['平日の朝', '平日の夜'],
  },
  家庭B: {
    parent: '母',
    work: '会社員',
    errand: '祖父母',
    bedside: 'いない',
    supports: [],
    no_support: true,
    contacts: ['夫', '職場'],
    child_age: null,
    child_sex: null,
    child_school: null,
    siblings: [{ age: null, sex: null, school: '学校' }],
    errand_times: ['平日の日中', '平日の夜'],
  },
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

// 2026-11-07 PO：設定は下のナビの「設定」、ハンバーガーは「このアプリについて」だけ。
// mode='settings' は設定のタブ（下のナビの中に出す）、mode='about' はハンバーガー。
export function MenuScreen({
  mode,
  code,
  onClose,
  onView,
  onTrash,
  onData,
  fromLink,
}: {
  mode: 'settings' | 'about'
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
  const [editingTrial, setEditingTrial] = useState(false)

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
      setEditingTrial(false)
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

  // 家庭A・Bで試す（①の入口と同じ）。新しい記録を作って、その記録へ移る。
  const tryHome = async (home: string) => {
    setBusy(true)
    setMessage(null)
    try {
      const created = await api.createBoard(home)
      // 今の記録は閉じたメニューのまま残さない。新しい記録は # だけ変えて開く。
      onClose()
      window.location.assign(`/b#${created.code}`)
    } catch {
      setMessage(WORDS.start.failed)
      setBusy(false)
    }
  }

  // 自分の家庭は同じ画面で直せる。試す家庭は事情のカードの下の［直す］から（2026-11-07 PO「見ると同時に
  // 編集も」）。直すのはそのボードだけ。
  const trial = board ? TRIAL_CHOICES[board.profile.name] : undefined
  if (
    page === 'home' &&
    board &&
    (board.profile.name === WORDS.setup.ownName || (trial && editingTrial))
  ) {
    return (
      <SetupScreen
        initial={
          !board.profile.choices && trial
            ? { ...trial, nickname: board.nickname ?? '', admitted_on: board.admitted_on ?? '' }
            : setupInitial(board)
        }
        editing={board.profile.configured || !!trial}
        busy={busy}
        message={message}
        onSave={(choices) => void saveSetup(choices)}
        onLater={() => {
          setEditingTrial(false)
          setPage(null)
        }}
      />
    )
  }

  if (page) {
    const titles: Record<Page, string> = {
      home: words.family,
      link: words.familyLink,
      rules: words.rules,
      howto: words.howToUse,
      consent: words.consentRules,
      try: words.tryHomes,
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
            <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
          </section>
        )}
        {page === 'consent' && (
          <section data-consent-rules>
            <ol>
              {CONSENT_LINES.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
          </section>
        )}
        {page === 'try' && (
          <section data-try-homes>
            <p className="detail">{words.tryNote}</p>
            {message && (
              <p className="message" role="alert">
                {message}
              </p>
            )}
            {WORDS.start.homes.map((home) => (
              <div className="try-home" key={home.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void tryHome(home.id)}
                >
                  {home.label}
                </button>
                <p className="detail">{home.note}</p>
              </div>
            ))}
          </section>
        )}
        {page === 'home' && board && <HomeCards profile={board.profile} open />}
        {page === 'home' && trial && (
          <button type="button" data-edit-home onClick={() => setEditingTrial(true)}>
            {WORDS.row.edit}
          </button>
        )}
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

  if (mode === 'settings') {
    return (
      <main className="menu-screen" data-settings>
        <h1>{words.settings}</h1>
        <section data-menu-section="settings">
          {row(words.family, () => setPage('home'), 'family')}
          {row(words.familyLink, () => setPage('link'), 'link')}
          {row(words.entered, () => setPage('entered'), 'entered')}
          {/* 付き添う親自身の食事と寝床への入口（インタビューの決定15）。 */}
          {row(WORDS.board.parent, () => onView('own'), 'family')}
          {row(words.rules, () => setPage('rules'), 'rules')}
          {row(words.data, onData, 'data')}
        </section>
        <p className="detail menu-note" data-menu-note>
          {words.skipped}
        </p>
      </main>
    )
  }

  return (
    <main className="menu-screen" data-menu>
      <header className="menu-head">
        <h1>{words.howTo}</h1>
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
      <section data-menu-section="howto">
        {row(words.howToUse, () => setPage('howto'), 'howto')}
        {row(words.consentRules, () => setPage('consent'), 'consent')}
        {!fromLink && row(words.tryHomes, () => setPage('try'), 'homes')}
      </section>
    </main>
  )
}
