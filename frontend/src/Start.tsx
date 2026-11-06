import { useState } from 'react'
import { api, type SetupChoices } from './api'
import { Chevron } from './parts'
import { SetupScreen } from './Setup'
import { WORDS } from './words'

// 家族コードの形（サーバーは 32 バイトの乱数を URL 用の文字で作る）。
const CODE = /^[A-Za-z0-9_-]{32,}$/
// 夫のリンク（/m#コード/項目）。
const FAMILY_HASH = /^[A-Za-z0-9_-]{32,}\/[A-Za-z0-9_-]+$/

type Opened = { code: string } | { family: string }

// 貼ったURLから記録を開く先を読む。読むのは # より後ろだけ（どこにも送らない）。
function openedFrom(text: string): Opened | null {
  const raw = text.trim()
  const at = raw.indexOf('#')
  if (at < 0) return CODE.test(raw) ? { code: raw } : null
  const hash = raw.slice(at + 1)
  if (/\/m\/?$/.test(raw.slice(0, at)) && FAMILY_HASH.test(hash)) {
    return { family: `/m#${hash}` }
  }
  return CODE.test(hash) ? { code: hash } : null
}

function Brand() {
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
    </header>
  )
}

// 最初の画面は ① はじめに。試す家庭とURLは一番下の「今ある記録から始める」に置く。
export function Start({ onCreated }: { onCreated: (code: string) => void }) {
  const words = WORDS.start
  const [busy, setBusy] = useState(false)
  // どこで押して失敗したか。押した所の近くにだけ出す。
  const [failed, setFailed] = useState<'setup' | 'existing' | null>(null)
  const [existingOpen, setExistingOpen] = useState(false)
  const [urlOpen, setUrlOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [urlFailed, setUrlFailed] = useState(false)
  // 動画の撮影用。URLに ?shoot を付けたときだけ、押す手間なしで見える所に出す。
  const shooting = new URLSearchParams(window.location.search).has('shoot')

  const create = async (home: string, choices?: SetupChoices) => {
    setBusy(true)
    setFailed(null)
    try {
      const { code } = await api.createBoard(home, choices)
      onCreated(code)
    } catch {
      setFailed(home === 'own' ? 'setup' : 'existing')
      setBusy(false)
    }
  }

  const openUrl = () => {
    const opened = openedFrom(url)
    if (!opened) {
      setUrlFailed(true)
      return
    }
    if ('family' in opened) window.location.assign(opened.family)
    else onCreated(opened.code)
  }

  const footer = (
    <section className="start-existing" data-start-existing>
      <button
        type="button"
        className="quiet start-existing-toggle"
        aria-expanded={existingOpen}
        data-start-existing-toggle
        onClick={() => setExistingOpen(!existingOpen)}
      >
        {words.existing}
        <Chevron to={existingOpen ? 'up' : 'down'} />
      </button>
      {existingOpen && (
        <div className="start-existing-body">
          {words.homes.map((home) => (
            <div key={home.id} className="home-choice">
              <button
                type="button"
                disabled={busy}
                data-start-home={home.id}
                onClick={() => void create(home.id)}
              >
                {home.label}
              </button>
              <p className="detail">{home.note}</p>
            </div>
          ))}
          <button
            type="button"
            aria-expanded={urlOpen}
            data-start-url-toggle
            onClick={() => setUrlOpen(!urlOpen)}
          >
            {words.url}
          </button>
          {/* 入力欄は押すまで置かない（設定の画面に自由な文字の欄を出さない）。 */}
          {urlOpen && (
            <form
              className="start-url"
              noValidate
              onSubmit={(event) => {
                event.preventDefault()
                openUrl()
              }}
            >
              <label className="detail" htmlFor="start-url">
                {words.urlLabel}
              </label>
              <input
                id="start-url"
                type="url"
                inputMode="url"
                autoComplete="off"
                placeholder={words.urlPlaceholder}
                value={url}
                data-start-url
                onChange={(event) => {
                  setUrl(event.target.value)
                  setUrlFailed(false)
                }}
              />
              {urlFailed && (
                <p className="message" role="alert">
                  {words.urlFailed}
                </p>
              )}
              <button type="submit" disabled={!url.trim()}>
                {words.urlOpen}
              </button>
            </form>
          )}
        </div>
      )}
      {shooting && (
        <div className="home-choice">
          <button
            type="button"
            className="primary"
            disabled={busy}
            data-start-shoot
            onClick={() => void create(words.shoot.id)}
          >
            {words.shoot.label}
          </button>
          <p className="detail">{words.shoot.note}</p>
        </div>
      )}
      {failed === 'existing' && (
        <p className="message" role="alert">
          {words.failed}
        </p>
      )}
    </section>
  )

  return (
    <div className="start-shell" data-start>
      <Brand />
      <SetupScreen
        initial={null}
        busy={busy}
        message={failed === 'setup' ? words.failed : null}
        onSave={(choices) => void create('own', choices)}
        onLater={(choices) => void create('own', choices)}
        footer={footer}
      />
    </div>
  )
}
