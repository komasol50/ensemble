import { useState } from 'react'
import type { SetupChoices } from './api'
import { WORDS } from './words'

// 家族の設定。全部選ぶ形で、自由な文字を入れない（名前を伏せる仕組みが効かないため）。
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
}

type Single = Exclude<keyof SetupChoices, 'supports' | 'no_support'>

export function SetupScreen({
  initial,
  editing = false,
  busy,
  message,
  onSave,
  onLater,
}: {
  initial: SetupChoices | null
  editing?: boolean
  busy: boolean
  message: string | null
  onSave: (choices: SetupChoices) => void
  onLater: () => void
}) {
  const words = WORDS.setup
  const [page, setPage] = useState<1 | 2>(1)
  const [choices, setChoices] = useState<SetupChoices>(initial ?? EMPTY)

  const single = <K extends Single>(key: K, title: string, options: readonly string[]) => (
    <fieldset className="setup-field" key={key}>
      <legend>{title}</legend>
      <div className="setup-options">
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

  return (
    <div className="with-dock">
      <main className="setup-screen" data-setup-screen>
        <p className="detail">{editing ? words.stepEdit(page) : words.step(page)}</p>
        <h1>{page === 1 ? words.title1 : words.title2}</h1>
        <p className="detail">{words.lead}</p>
        <p className="setup-warning">{words.noRealNames}</p>
        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}
        {page === 1 ? (
          <>
            {single('parent', words.parent, words.parentOptions)}
            {single('adult', words.adult, words.yesNo)}
            {single('child', words.child, words.yesNo)}
            {single('school', words.school, words.schoolOptions)}
            {single('work', words.work, words.workOptions)}
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
      <div className="dock">
        {page === 1 ? (
          <button type="button" className="primary" data-primary onClick={() => setPage(2)}>
            {words.next}
          </button>
        ) : (
          <button
            type="button"
            className="primary"
            data-primary
            disabled={busy}
            onClick={() => onSave(choices)}
          >
            {editing ? words.saveEdit : words.save}
          </button>
        )}
        <div className="dock-pair">
          {page === 2 && (
            <button type="button" onClick={() => setPage(1)}>
              {WORDS.add.back}
            </button>
          )}
          {/* 直すときは2ページ目の［戻る］が1ページ目へ戻るので、1ページ目でだけ閉じる。 */}
          {(!editing || page === 1) && (
            <button type="button" disabled={busy} onClick={onLater}>
              {editing ? WORDS.add.back : words.later}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
