import { useEffect, useState } from 'react'
import { api, ApiError, type Board, type Branch, type CandidateDay, type Item } from './api'
import type { ReadyProposal } from './device'
import { WORDS } from './words'

function textOf(item: Item | undefined): string {
  return item?.text_original ?? item?.task_summary ?? ''
}

export function DayBand({ days }: { days: CandidateDay[] }) {
  if (days.length === 0) return null
  return (
    <section aria-label={WORDS.decide.candidates}>
      <h2>{WORDS.decide.candidates}</h2>
      <ul className="band">
        {days.map((day) => (
          <li key={day.day} className={day.cancelled ? 'day cancelled' : 'day'}>
            {day.day}（{day.cancelled ? WORDS.decide.cancelled : day.certainty}）
          </li>
        ))}
      </ul>
    </section>
  )
}

// 親が案を確かめる画面。押すまで台帳は何も変わらない（案の控えは端末にある）。
export function DecideScreen({
  code,
  proposal,
  onAccepted,
  onNothing,
  onBack,
}: {
  code: string
  proposal: ReadyProposal
  onAccepted: () => void
  onNothing: () => void
  onBack: () => void
}) {
  const [board, setBoard] = useState<Board | null>(null)
  const [partial, setPartial] = useState(false)
  const [dropped, setDropped] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

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

  const items = board?.items ?? []
  const byId = new Map(items.map((item) => [item.id, item]))
  const trigger = byId.get(proposal.item_id)
  const earlier = trigger?.refers_to ? byId.get(trigger.refers_to) : undefined
  const branch = proposal.branch
  const keyOf = (outcome: number, action: number) => `${outcome}-${action}`
  const remaining = branch.outcomes.reduce(
    (count, outcome, o) =>
      count + outcome.actions.filter((_, a) => !dropped.has(keyOf(o, a))).length,
    0,
  )

  const accept = async () => {
    if (!board || busy) return
    setBusy(true)
    setMessage(null)
    const chosen: Branch = {
      ...branch,
      outcomes: branch.outcomes.map((outcome, o) => ({
        ...outcome,
        actions: outcome.actions.filter((_, a) => !dropped.has(keyOf(o, a))),
      })),
    }
    try {
      await api.acceptBranch(code, board.version, chosen)
      onAccepted()
    } catch (error) {
      if (error instanceof ApiError && error.message) {
        setMessage(error.message)
        setBoard(await api.getBoard(code).catch(() => board))
      } else {
        setMessage(WORDS.board.failed)
      }
      setBusy(false)
    }
  }

  const toggle = (key: string) => {
    setDropped((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <main className="decide-screen">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.decide.title}</h1>
      </header>

      {message && (
        <p className="message" role="alert">
          {message}
        </p>
      )}

      <DayBand days={branch.candidate_days} />

      <section>
        <h2>{WORDS.decide.basis}</h2>
        {trigger && <p className="quote">{textOf(trigger)}</p>}
        {earlier && <p className="quote">{textOf(earlier)}</p>}
      </section>

      {branch.outcomes.map((outcome, o) => (
        <section key={outcome.label}>
          <h2>{outcome.label}</h2>
          {outcome.actions.map((action, a) => {
            const key = keyOf(o, a)
            const isDropped = dropped.has(key)
            return (
              <article key={key} className={isDropped ? 'card dropped' : 'card'} data-action={action.part}>
                <div className="chips">
                  <span className="chip">{action.part}</span>
                  <span className="chip">{action.type}</span>
                </div>
                {action.draft && <p className="original">{action.draft}</p>}
                {action.release.map((id) => (
                  <p className="detail" key={id}>
                    {WORDS.decide.release(textOf(byId.get(id)) || id)}
                  </p>
                ))}
                {partial && (
                  <div className="actions">
                    <button type="button" onClick={() => toggle(key)}>
                      {isDropped ? WORDS.decide.keep : WORDS.decide.drop}
                    </button>
                  </div>
                )}
              </article>
            )
          })}
        </section>
      ))}

      <div className="decide-actions">
        <p>{WORDS.decide.question}</p>
        <button
          type="button"
          className="primary"
          data-primary
          disabled={!board || busy || remaining === 0}
          onClick={() => void accept()}
        >
          {WORDS.decide.accept}
        </button>
        {!partial && (
          <button type="button" onClick={() => setPartial(true)}>
            {WORDS.decide.partial}
          </button>
        )}
        <button type="button" onClick={onNothing}>
          {WORDS.decide.nothing}
        </button>
      </div>
    </main>
  )
}
