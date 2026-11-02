import { useEffect, useState } from 'react'
import { WORDS } from './words'

const MAX_CHARS = 2000

// キーボードが開くと見える高さが変わる。入れるボタンが隠れないよう、見える高さに合わせる。
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

type Props = {
  draft: string
  onDraft: (text: string) => void
  onSubmit: (text: string) => void
  onBack: () => void
}

export function AddScreen({ draft, onDraft, onSubmit, onBack }: Props) {
  useVisibleHeight()
  const [text, setText] = useState(draft)
  const over = text.length - MAX_CHARS

  return (
    <main className="add">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.add.title}</h1>
      </header>
      {/* 字数で切り捨てない（貼った文が黙って消えないように）。超えたら入れるボタンを止める。 */}
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
        onClick={() => onSubmit(text)}
      >
        {WORDS.add.submit}
      </button>
    </main>
  )
}
