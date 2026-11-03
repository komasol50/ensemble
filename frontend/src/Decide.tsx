import { useEffect, useState } from 'react'
import { api, ApiError, type Action, type Board, type Branch, type Item } from './api'
import type { ReadyProposal } from './device'
import { Chevron, PartIcon, shortDay, shortTime, toneOf } from './parts'
import { MEDICAL_FIXED_TEXT, WORDS } from './words'

function textOf(item: Item | undefined): string {
  return item?.text_original ?? item?.task_summary ?? ''
}

// 文のある連絡か、外す連絡があるか。無ければその相手は「そのまま」。
function changes(action: Action): boolean {
  return (action.type !== '何もしない' && !!action.draft) || action.release.length > 0
}

const OWN_SPEAKERS = new Set(['親', '母', '父', '私', '自分'])
// これより長い案の文は、2行に畳んで押すと全文を出す（送る前にも全文を見られる）。
const LONG_DRAFT = 36

function evidenceTitle(item: Item): string {
  const who = item.speaker && !OWN_SPEAKERS.has(item.speaker) ? `${item.speaker}の言葉` : ''
  const day = shortDay(item.recorded_at)
  return WORDS.decide.evidence(item.part, who, day)
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
  const errand = board?.profile.errand_people[0]?.label
  const keyOf = (outcome: number, action: number) => `${outcome}-${action}`
  const remaining = branch.outcomes.reduce(
    (count, outcome, o) =>
      count + outcome.actions.filter((action, a) => changes(action) && !dropped.has(keyOf(o, a))).length,
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

  const whoOf = (part: string) => (part === '家族' && errand ? `${part}（${errand}）` : part)
  const enteredAt = shortTime(trigger?.recorded_at)

  return (
    <div className="with-dock">
      <main className="decide-screen">
        <header className="decide-head">
          <button type="button" className="quiet back" onClick={onBack}>
            <Chevron to="back" />
            {WORDS.decide.back}
          </button>
          <span className="count-pill">{WORDS.decide.title}</span>
        </header>
        <h1>{WORDS.decide.question}</h1>

        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}

        {trigger && (
          <section>
            <h2>{WORDS.decide.entered(enteredAt)}</h2>
            <p className="said">「{textOf(trigger)}」</p>
          </section>
        )}

        {earlier && (
          <section className={`card evidence glow ${toneOf(earlier.part)}`}>
            <h2 className="part-head">
              <PartIcon part={earlier.part} />
              {evidenceTitle(earlier)}
            </h2>
            <p className="evidence-text">{textOf(earlier)}</p>
          </section>
        )}

        {branch.candidate_days.length > 0 && (
          <section>
            <h2>{WORDS.decide.days}</h2>
            <div className="day-rows">
              {branch.candidate_days.map((day) => (
                <div className="day-row" key={day.day}>
                  <span
                    className={
                      day.cancelled
                        ? 'day-chip cancelled'
                        : day.certainty === '確定'
                          ? 'day-chip confirmed'
                          : 'day-chip likely'
                    }
                  >
                    {day.day}
                  </span>
                  <span className="detail">
                    {day.cancelled ? WORDS.decide.cancelled : day.certainty}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {branch.outcomes.map((outcome, o) => (
          <section key={outcome.label}>
            <h2>{outcome.label}</h2>
            {outcome.actions.map((action, a) => {
              const key = keyOf(o, a)
              const isDropped = dropped.has(key)
              const canChange = changes(action)
              const body = (
                <>
                  <span className="plan-head">
                    <PartIcon part={action.part} />
                    <span className="plan-name">{whoOf(action.part)}</span>
                    {canChange && !isDropped ? (
                      <span className="badge change">{WORDS.decide.change}</span>
                    ) : (
                      <span className="badge keep">{WORDS.decide.keep}</span>
                    )}
                  </span>
                  {action.draft &&
                    (!partial && action.draft.length > LONG_DRAFT ? (
                      <details className="plan-fold">
                        <summary className="plan-text">{action.draft}</summary>
                      </details>
                    ) : (
                      <span className="plan-text">{action.draft}</span>
                    ))}
                  {action.release.map((id) => (
                    <span className="plan-release" key={id}>
                      {WORDS.decide.release(textOf(byId.get(id)) || id)}
                    </span>
                  ))}
                </>
              )
              const className = `plan-row ${toneOf(action.part)}${isDropped ? ' dropped' : ''}`
              // 切り替えられるのは［一部だけ変える］の後だけ。普段はただの札。
              return partial && canChange ? (
                <button
                  key={key}
                  type="button"
                  className={className}
                  data-action={action.part}
                  aria-pressed={!isDropped}
                  onClick={() => toggle(key)}
                >
                  {body}
                </button>
              ) : (
                <article key={key} className={className} data-action={action.part}>
                  {body}
                </article>
              )
            })}
          </section>
        ))}

        {partial && <p className="detail">{WORDS.decide.partialHint}</p>}

        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
      </main>

      <div className="dock">
        <button
          type="button"
          className="primary"
          data-primary
          disabled={!board || busy || remaining === 0}
          onClick={() => void accept()}
        >
          {WORDS.decide.accept}
        </button>
        <div className="dock-pair">
          {!partial && (
            <button type="button" onClick={() => setPartial(true)}>
              {WORDS.decide.partial}
            </button>
          )}
          <button type="button" className="quiet" onClick={onNothing}>
            {WORDS.decide.nothing}
          </button>
        </div>
      </div>
    </div>
  )
}
