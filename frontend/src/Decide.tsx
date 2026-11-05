import { useEffect, useState } from 'react'
import { api, ApiError, type Action, type Board, type Branch, type Item } from './api'
import type { ReadyProposal } from './device'
import { ImpactTree, PreviewCard, WeekStrip } from './Map'
import {
  Chevron,
  Highlighted,
  inQuotes,
  PartIcon,
  shortDay,
  shortTime,
  toneOf,
  VERBS,
  whoWord,
} from './parts'
import { PathRows } from './Paths'
import { MEDICAL_FIXED_TEXT, WORDS } from './words'

function textOf(item: Item | undefined): string {
  return item?.text_original ?? item?.task_summary ?? ''
}

// 文のある連絡か、外す連絡があるか。無ければその相手は「そのまま」。
function changes(action: Action): boolean {
  return (action.type !== '何もしない' && !!action.draft) || action.release.length > 0
}

const OWN_SPEAKERS = new Set(['親', '母', '父', '私', '自分'])
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
  onAccepted: (hasDrafts: boolean) => void
  onNothing: () => void
  onBack: () => void
}) {
  const [board, setBoard] = useState<Board | null>(null)
  const [partial, setPartial] = useState(false)
  const [dropped, setDropped] = useState<Set<string>>(new Set())
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [openPart, setOpenPart] = useState<string | null>(null)
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
  const changeCount = branch.outcomes.reduce(
    (count, outcome) => count + outcome.actions.filter(changes).length,
    0,
  )
  // 相手に頼むことを、案の種類からコードで短く書く。AIの文ではない。
  const taskOf = (action: Action) => {
    const who = whoWord(action.part, errand)
    if (VERBS[action.type] && action.draft) return `${who}${VERBS[action.type]}`
    return action.release.length > 0 ? WORDS.decide.releaseOnly : WORDS.decide.noChange
  }

  const accept = async () => {
    if (!board || busy) return
    setBusy(true)
    setMessage(null)
    const chosen: Branch = {
      ...branch,
      outcomes: branch.outcomes.map((outcome, o) => ({
        ...outcome,
        actions: outcome.actions
          .map((action, a) => {
            // 空にした文は使わない（もとの文に戻す）。
            const edited = edits[keyOf(o, a)]?.trim()
            return edited ? { ...action, draft: edited } : action
          })
          .filter((_, a) => !dropped.has(keyOf(o, a))),
      })),
    }
    try {
      await api.acceptBranch(code, board.version, chosen)
      onAccepted(makesSends)
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
  const peopleCount = new Set(
    branch.outcomes.flatMap((outcome) =>
      outcome.actions.filter(changes).map((action) => action.part),
    ),
  ).size
  // 控えに影響が無い古い案と、文を直すときは、相手ごとの一覧を出す。
  const showTree = !partial && !!proposal.impact
  // 押すと送る文ができるか。文が1つも無ければ、記録するだけと言う。
  const makesSends = branch.outcomes.some((outcome, o) =>
    outcome.actions.some(
      (action, a) => action.type !== '何もしない' && !!action.draft && !dropped.has(keyOf(o, a)),
    ),
  )
  const enteredAt = shortTime(trigger?.recorded_at)
  // ③と同じ形。次の段が無いときも、同じ見出しで候補日を出す。
  const pathRows = proposal.impact?.next ?? []
  const candidateRows = proposal.impact?.candidates ?? branch.candidate_days

  const opened = proposal.impact?.nodes.find((node) => node.part === openPart)
  if (opened) return <PreviewCard node={opened} onBack={() => setOpenPart(null)} />

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

        {trigger && (
          <section>
            <h2>{WORDS.decide.entered(enteredAt)}</h2>
            <p className="said">{inQuotes(textOf(trigger))}</p>
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

        {(pathRows.length > 0 || candidateRows.length > 0) && (
          <section className="review-card" data-review>
            <h2>{WORDS.now.candidates}</h2>
            <div className="clause-rows">
              <PathRows paths={pathRows} candidates={candidateRows} />
            </div>
          </section>
        )}

        <h2 className="plan-summary" data-plan-count>
          {changeCount > 0 ? WORDS.decide.summary(changeCount, peopleCount) : WORDS.decide.noSends}
        </h2>

        {/* 影響は線で見せる。文を直すときは、相手ごとの一覧に切り替える。 */}
        {showTree && proposal.impact && (
          <>
            {proposal.week && <WeekStrip week={proposal.week} delay={proposal.impact.nodes.length} />}
            <ImpactTree
              nodes={proposal.impact.nodes}
              unsentOf={(part) =>
                branch.outcomes.some((outcome) =>
                  outcome.actions.some((action) => action.part === part && changes(action)),
                )
                  ? 1
                  : 0
              }
              onOpen={setOpenPart}
            />
          </>
        )}

        {!showTree && branch.outcomes.map((outcome, o) => (
          <section key={outcome.label}>
            {branch.outcomes.length > 1 && <h2>{outcome.label}</h2>}
            {outcome.actions.map((action, a) => {
              const key = keyOf(o, a)
              const isDropped = dropped.has(key)
              const canChange = changes(action)
              const draft = edits[key] ?? action.draft
              const canToggle = partial && canChange
              const head = (
                <>
                  <PartIcon part={action.part} />
                  <span className="plan-name">{whoOf(action.part)}</span>
                </>
              )
              return (
                <article
                  key={key}
                  className={`plan-row ${toneOf(action.part)}${isDropped ? ' dropped' : ''}`}
                  data-action={action.part}
                >
                  <div className="plan-head">{head}</div>
                  <p className="plan-task">{taskOf(action)}</p>
                  {/* 下書きの全文は、直すときと送る段で読む。 */}
                  {partial && draft && editing !== key && (
                    <p className="plan-text">
                      <Highlighted text={draft} marks />
                    </p>
                  )}
                  {editing === key && (
                    <label className="plan-edit">
                      {WORDS.decide.editLabel(whoOf(action.part))}
                      <textarea
                        rows={6}
                        value={draft ?? ''}
                        onChange={(event) => {
                          const edited = event.target.value
                          setMessage(null)
                          setEdits((current) => ({ ...current, [key]: edited }))
                        }}
                      />
                    </label>
                  )}
                  {/* 読み込みが終わるまで、記号のまま見せない。 */}
                  {action.release.map((id) => {
                    const short = byId.get(id)?.short
                    return (
                      short && (
                        <p className="plan-release" key={id}>
                          {WORDS.decide.release(short)}
                        </p>
                      )
                    )
                  })}
                  {editing === key && !isDropped && (
                    <p className="detail">{WORDS.decide.editNote}</p>
                  )}
                  {/* 切り替えと直すのは［文を確かめて直す］の後だけ。1つの相手の操作は1行にまとめる。 */}
                  {canToggle && (
                    <div className="plan-actions">
                      <button
                        type="button"
                        className="plan-toggle"
                        aria-pressed={!isDropped}
                        onClick={() => toggle(key)}
                      >
                        <span className="plan-choice">
                          <span className={isDropped ? 'badge' : 'badge change'}>
                            {isDropped ? WORDS.decide.unchecked : WORDS.decide.checked}
                            {WORDS.decide.change}
                          </span>
                          <span className={isDropped ? 'badge change' : 'badge'}>
                            {isDropped ? WORDS.decide.checked : WORDS.decide.unchecked}
                            {WORDS.decide.keep}
                          </span>
                        </span>
                      </button>
                      {!isDropped && (
                        <button
                          type="button"
                          onClick={() => setEditing(editing === key ? null : key)}
                        >
                          {editing === key ? WORDS.decide.editDone : WORDS.decide.edit}
                        </button>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </section>
        ))}

        {partial && <p className="detail">{WORDS.decide.partialHint}</p>}

        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
      </main>

      <div className="dock">
        {/* 押した場所から見えるように、下の帯に出す。 */}
        {message && (
          <p className="dock-note" role="alert">
            {message}
          </p>
        )}
        {makesSends && <p className="dock-hint">{WORDS.decide.acceptNote}</p>}
        <button
          type="button"
          className="primary"
          data-primary
          disabled={!board || busy}
          onClick={() => void accept()}
        >
          {makesSends ? WORDS.decide.accept(peopleCount) : WORDS.decide.acceptNothing}
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
