import { useEffect, useState } from 'react'
import { api } from './api'
import { WORDS } from './words'

const MAX_CHARS = 2000

// キーボードが開くと見える高さが変わる。追加のボタンが隠れないよう、見える高さに合わせる。
function useVisibleHeight(): void {
  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return
    const apply = () => {
      document.documentElement.style.setProperty('--visible-height', `${viewport.height}px`)
    }
    apply()
    viewport.addEventListener('resize', apply)
    return () => {
      viewport.removeEventListener('resize', apply)
      document.documentElement.style.removeProperty('--visible-height')
    }
  }, [])
}

type Who = { part: string; label: string }

const BASE_WHO: Who[] = [
  { part: '病院', label: '医師・看護師' },
  { part: '家族', label: '家族' },
  { part: '保育園・預け先', label: '保育園' },
  { part: '職場', label: '職場' },
]

// 相手の並びは、その家の設定に合わせる（用事を頼める人の呼び方、保育園以外の支援）。
function useWhoChoices(code: string): Who[] {
  const [choices, setChoices] = useState(BASE_WHO)
  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((board) => {
        if (!active) return
        const errand = board.profile.errand_people[0]?.label
        const hasSupport = board.profile.supports.some((entry) => !entry.name.includes('保育'))
        setChoices([
          ...BASE_WHO.map((who) =>
            who.part === '家族' && errand ? { ...who, label: errand } : who,
          ),
          ...(hasSupport ? [{ part: '支援', label: '支援' }] : []),
        ])
      })
      .catch(() => {
        // 読めなくても、決まった並びで使える。
      })
    return () => {
      active = false
    }
  }, [code])
  return choices
}

type Props = {
  code: string
  draft: string
  onDraft: (text: string) => void
  onSubmit: (text: string, said: string | null, who: string | null) => void
  onBack: () => void
}

export function AddScreen({ code, draft, onDraft, onSubmit, onBack }: Props) {
  useVisibleHeight()
  const [text, setText] = useState(draft)
  const [said, setSaid] = useState<string | null>(null)
  const [who, setWho] = useState<string | null>(null)
  const whoChoices = useWhoChoices(code)
  const over = text.length - MAX_CHARS
  // 言われたなら「誰から」、聞きたい・頼みたいなら「誰に」。気づいたことに相手はいない。
  const whoLabel =
    said === '言われた'
      ? WORDS.add.choices.from
      : said === '聞きたい' || said === '頼みたい'
        ? WORDS.add.choices.to
        : null

  return (
    <main className="add">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.add.title}</h1>
      </header>

      {/* 選ぶのは任意。選ばなければAIが仕分ける（LINEを貼るだけで使える）。 */}
      <div className="choices" role="group" aria-label={WORDS.add.choices.saidLabel}>
        <p className="choice-label">{WORDS.add.choices.saidLabel}</p>
        <div className="choice-row">
          {WORDS.add.choices.said.map((choice) => (
            <button
              key={choice}
              type="button"
              className="choice"
              aria-pressed={said === choice}
              onClick={() => {
                setSaid(said === choice ? null : choice)
                setWho(null)
              }}
            >
              {choice}
            </button>
          ))}
        </div>
      </div>
      {whoLabel && (
        <div className="choices" role="group" aria-label={whoLabel}>
          <p className="choice-label">{whoLabel}</p>
          <div className="choice-wrap">
            {whoChoices.map((choice) => (
              <button
                key={choice.part}
                type="button"
                className="choice"
                aria-pressed={who === choice.part}
                onClick={() => setWho(who === choice.part ? null : choice.part)}
              >
                {choice.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 字数で切り捨てない（貼った文が黙って消えないように）。超えたら追加のボタンを止める。 */}
      <textarea
        aria-label={WORDS.add.title}
        placeholder={WORDS.add.placeholder}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          onDraft(event.target.value)
        }}
      />
      <p className={over > 0 ? 'count over' : 'count'} role="status">
        {over > 0 ? WORDS.add.over(over) : WORDS.add.count(text.length)}
      </p>
      <button
        type="button"
        className="primary"
        data-primary
        disabled={!text.trim() || over > 0}
        onClick={() => onSubmit(text, said, who)}
      >
        {WORDS.add.submit}
      </button>
    </main>
  )
}
