import { useState } from 'react'
import { RemoveButton } from './RemoveButton'
import { CONSENT_LINES, WORDS } from './words'

type Props = {
  buttonLabel: string
  onClose: () => void
  // 親がボードから開いたときだけ渡す。最初の同意と家族の1件では出さない。
  onRemoveBoard?: () => Promise<void>
}

export function Consent({ buttonLabel, onClose, onRemoveBoard }: Props) {
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)

  const remove = async () => {
    if (!onRemoveBoard) return
    setBusy(true)
    setFailed(false)
    try {
      await onRemoveBoard()
    } catch {
      setFailed(true)
      setBusy(false)
    }
  }

  return (
    <main className="consent">
      <h1>{WORDS.consent.title}</h1>
      <ol>
        {CONSENT_LINES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ol>
      <button type="button" className="primary" data-primary onClick={onClose}>
        {buttonLabel}
      </button>
      {onRemoveBoard && (
        <div className="remove-board">
          {failed && (
            <p className="message" role="alert">
              {WORDS.consent.removeBoardFailed}
            </p>
          )}
          <RemoveButton
            disabled={busy}
            label={WORDS.consent.removeBoard}
            armedLabel={WORDS.consent.removeBoardAgain}
            onConfirm={() => void remove()}
          />
        </div>
      )}
    </main>
  )
}
