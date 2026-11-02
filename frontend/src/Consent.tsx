import { CONSENT_LINES, WORDS } from './words'

type Props = { buttonLabel: string; onClose: () => void }

export function Consent({ buttonLabel, onClose }: Props) {
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
    </main>
  )
}
