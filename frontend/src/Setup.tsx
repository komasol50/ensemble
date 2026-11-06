import { useEffect, useState, type ReactNode } from 'react'
import type { SetupChoices } from './api'
import { WORDS } from './words'

// 家族の設定。全部選ぶ形で、自由な文字を入れない（名前を伏せる仕組みが効かないため）。
// 連絡する相手は、初めから3つとも選んだ形（触らずに進んでも、下書きが消えないように）。
const ALL_CONTACTS = [...WORDS.setup.contactOptions]
const EMPTY: SetupChoices = {
  parent: null,
  adult: null,
  child: null,
  school: null,
  work: null,
  errand: null,
  errand_time: null,
  bedside: null,
  supports: [],
  no_support: false,
  contacts: ALL_CONTACTS,
}

// 前に保存した内容に連絡する相手が無ければ（この欄を足す前の家庭）、3つとも選んだ形で開く。
function startFrom(initial: SetupChoices | null): SetupChoices {
  if (!initial) return EMPTY
  return { ...EMPTY, ...initial, contacts: initial.contacts ?? ALL_CONTACTS }
}

type Single = Exclude<keyof SetupChoices, 'supports' | 'no_support' | 'contacts'>

function CheckMark() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 20 20">
      <path
        d="M4 10.5l4 4 8-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function SetupScreen({
  initial,
  editing = false,
  busy,
  message,
  onSave,
  onLater,
  footer,
}: {
  initial: SetupChoices | null
  editing?: boolean
  busy: boolean
  message: string | null
  onSave: (choices: SetupChoices) => void
  // 最初の画面では、そこまで選んだ内容（連絡する相手を含む）も渡す。
  onLater: (choices: SetupChoices) => void
  // 1ページ目の一番下に置くもの（最初の画面の「今ある記録から始める」）。
  footer?: ReactNode
}) {
  const words = WORDS.setup
  const [page, setPage] = useState<1 | 2>(1)
  const [choices, setChoices] = useState<SetupChoices>(() => startFrom(initial))

  // ページを移ったら頭から見せる。ボタンは一番下にあるので、そのままだと次のページの下から始まる。
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [page])

  const single = <K extends Single>(
    key: K,
    title: string,
    options: readonly string[],
    even = false,
  ) => (
    <fieldset className="setup-field" key={key}>
      <legend>{title}</legend>
      <div className={even ? 'setup-options even' : 'setup-options'}>
        {options.map((option) => (
          <button
            type="button"
            key={option}
            className="choice"
            aria-pressed={choices[key] === option}
            data-setup={`${key}:${option}`}
            onClick={() =>
              setChoices((current) => ({
                ...current,
                [key]: current[key] === option ? null : option,
              }))
            }
          >
            {option}
          </button>
        ))}
      </div>
    </fieldset>
  )

  const toggleSupport = (name: string) =>
    setChoices((current) => ({
      ...current,
      no_support: false,
      supports: current.supports.includes(name)
        ? current.supports.filter((entry) => entry !== name)
        : [...current.supports, name],
    }))

  const contacts = choices.contacts ?? ALL_CONTACTS
  const toggleContact = (name: string) =>
    setChoices((current) => {
      const now = current.contacts ?? ALL_CONTACTS
      return {
        ...current,
        contacts: now.includes(name)
          ? now.filter((entry) => entry !== name)
          : ALL_CONTACTS.filter((entry) => entry === name || now.includes(entry)),
      }
    })

  return (
    <div className="with-dock setup-page">
      <main className="setup-screen" data-setup-screen data-setup-step={page}>
        <p className="detail">{editing ? words.stepEdit(page) : words.step(page)}</p>
        <div
          className="setup-progress"
          role="progressbar"
          aria-label={words.progress}
          aria-valuemin={0}
          aria-valuemax={2}
          aria-valuenow={page}
          data-setup-progress
        >
          <span style={{ width: page === 1 ? '50%' : '100%' }} />
        </div>
        <h1>{page === 1 ? words.title1 : words.title2}</h1>
        <div className="setup-lead">
          <p className="detail">{words.lead}</p>
          <p className="detail">{words.noRealNames}</p>
        </div>
        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}
        {page === 1 ? (
          <>
            {single('parent', words.parent, words.parentOptions, true)}
            {single('adult', words.adult, words.yesNo, true)}
            {single('child', words.child, words.yesNo, true)}
            {single('school', words.school, words.schoolOptions, true)}
            {single('work', words.work, words.workOptions, true)}
            <fieldset className="setup-field" data-contacts>
              <legend>{words.contacts}</legend>
              <div className="setup-options even">
                {words.contactOptions.map((name) => (
                  <button
                    type="button"
                    key={name}
                    role="checkbox"
                    className="choice contact"
                    aria-checked={contacts.includes(name)}
                    data-contact={name}
                    data-setup={`contacts:${name}`}
                    onClick={() => toggleContact(name)}
                  >
                    <span className="contact-box">
                      {contacts.includes(name) && <CheckMark />}
                    </span>
                    {name}
                  </button>
                ))}
              </div>
            </fieldset>
          </>
        ) : (
          <>
            {single('errand', words.errand, words.errandOptions)}
            {choices.errand && choices.errand !== words.nobody
              ? single('errand_time', words.errandTime, words.errandTimeOptions)
              : null}
            {single('bedside', words.bedside, words.yesNo)}
            <fieldset className="setup-field">
              <legend>{words.supports}</legend>
              <div className="setup-options">
                {words.supportOptions.map((option) => (
                  <button
                    type="button"
                    key={option}
                    className="choice"
                    aria-pressed={choices.supports.includes(option)}
                    data-setup={`supports:${option}`}
                    onClick={() => toggleSupport(option)}
                  >
                    {option}
                  </button>
                ))}
                <button
                  type="button"
                  className="choice"
                  aria-pressed={choices.no_support}
                  data-setup="supports:なし"
                  onClick={() =>
                    setChoices((current) => ({
                      ...current,
                      supports: [],
                      no_support: !current.no_support,
                    }))
                  }
                >
                  {words.none}
                </button>
              </div>
            </fieldset>
          </>
        )}
      </main>
      {/* 図のとおり、ボタンは選ぶ欄の続き。欄が画面より長い間は、画面の下に張り付けて見せる。 */}
      <div className="dock setup-actions" data-setup-actions>
        {page === 1 ? (
          <>
            <button type="button" className="primary" data-primary onClick={() => setPage(2)}>
              {words.next}
            </button>
            <p className="detail setup-next">{words.nextLead(words.title2)}</p>
            {/* 直すときは2ページ目の［戻る］が1ページ目へ戻るので、1ページ目でだけ閉じる。 */}
            <button type="button" disabled={busy} onClick={() => onLater(choices)}>
              {editing ? WORDS.add.back : words.later}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              data-primary
              disabled={busy}
              onClick={() => onSave(choices)}
            >
              {editing ? words.saveEdit : words.save}
            </button>
            <div className="dock-pair">
              <button type="button" onClick={() => setPage(1)}>
                {WORDS.add.back}
              </button>
              {!editing && (
                <button type="button" disabled={busy} onClick={() => onLater(choices)}>
                  {words.later}
                </button>
              )}
            </div>
          </>
        )}
      </div>
      {page === 1 && footer}
    </div>
  )
}
