import { useEffect, useState } from 'react'
import { api, ApiError, type Board } from './api'
import { Chevron } from './parts'
import { MEDICAL_FIXED_TEXT, WORDS } from './words'

const SAVE_TRIES = 3

// 説明を聞いたあと。医師の言葉の節を選ぶだけで結果を入れる。AIの呼び出しは案を作る1回だけ。
export function AfterScreen({
  code,
  onBack,
  onOther,
  onSaved,
}: {
  code: string
  onBack: () => void
  onOther: () => void
  onSaved: (itemId: string, text: string) => void
}) {
  const words = WORDS.after
  const [board, setBoard] = useState<Board | null>(null)
  const [chosen, setChosen] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((next) => {
        if (active) setBoard(next)
      })
      .catch(() => {
        if (active) setMessage(WORDS.board.offline)
      })
    return () => {
      active = false
    }
  }, [code])

  const doctor = board?.doctor
  const options = doctor?.options ?? []

  const save = async () => {
    if (!board || !doctor || chosen === null || busy) return
    const text = options[chosen].result
    setBusy(true)
    setMessage(null)
    let version = board.version
    for (let attempt = 0; attempt < SAVE_TRIES; attempt += 1) {
      try {
        const next = await api.addResult(code, version, text, doctor.item_id)
        const added = next.items[next.items.length - 1]
        onSaved(added.id, text)
        return
      } catch (error) {
        // もう1台が先に版を進めても、入れる意思は変わらないので読み直して通す。
        if (!(error instanceof ApiError && error.status === 409)) break
        try {
          version = (await api.getBoard(code)).version
        } catch {
          break
        }
      }
    }
    setMessage(WORDS.board.failed)
    setBusy(false)
  }

  return (
    <div className="with-dock">
      <main className="after-screen">
        <h1>{words.title}</h1>
        <p className="after-question">{words.question}</p>
        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}
        {options.length > 0 ? (
          <div className="after-options" role="radiogroup" aria-label={words.question}>
            {options.map((option, index) => (
              <button
                type="button"
                key={option.label}
                role="radio"
                aria-checked={chosen === index}
                className="after-option"
                data-option={index}
                onClick={() => setChosen(index)}
              >
                <span className="after-mark" aria-hidden="true" />
                <span className="after-option-body">
                  <span>「{option.label}」</span>
                  <span className="clause-days">{option.days.join('・')}</span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          doctor && <p className="after-whole">「{doctor.text}」</p>
        )}
        <button type="button" className="after-other" onClick={onOther}>
          {words.other}
          <Chevron />
        </button>
        <button type="button" className="quiet" onClick={onBack}>
          {words.notYet}
        </button>
        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
      </main>
      {options.length > 0 && (
        <div className="dock">
          <button
            type="button"
            className="primary"
            data-primary
            disabled={chosen === null || busy}
            onClick={() => void save()}
          >
            {words.submit}
          </button>
        </div>
      )}
    </div>
  )
}
