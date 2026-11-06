import { useEffect, useState } from 'react'
import { api, ApiError, type Action, type Board, type Branch, type Item, type NextRow } from './api'
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

// 同じ相手の用件は1通にまとめる（図の⑥「4件を、3人分の文にまとめます」）。AIは2つ目からの文を
// 挨拶なしで続けて書いている。種類は強いほう、根拠と外す項目は合わせる。頼みごとの引用は1つ目。
const TYPE_RANK = ['何もしない', '確認する', '空きを確認する', '伝える', '頼む']
function mergeByPart(actions: Action[]): Action[] {
  const parts: string[] = []
  const groups = new Map<string, Action[]>()
  for (const action of actions) {
    const group = groups.get(action.part)
    if (group) group.push(action)
    else {
      groups.set(action.part, [action])
      parts.push(action.part)
    }
  }
  return parts.map((part) => {
    const group = groups.get(part) as Action[]
    if (group.length === 1) return group[0]
    const written = group.filter((action) => action.type !== '何もしない' && !!action.draft)
    const first = written[0] ?? group[0]
    const type = group.reduce(
      (best, action) => (TYPE_RANK.indexOf(action.type) > TYPE_RANK.indexOf(best) ? action.type : best),
      first.type,
    )
    return {
      ...first,
      type,
      draft: written.map((action) => action.draft).join('\n') || null,
      basis_ids: [...new Set(group.flatMap((action) => action.basis_ids))],
      release: [...new Set(group.flatMap((action) => action.release))],
      ask: first.ask ?? null,
    }
  })
}

// 見直しの黄の枠の行。候補日と次の段から、コードで作る（AIの文ではない）。
// 「→ 未定」は候補日が1つも残らないときだけ（Plan main）。残るときは日で出す。
const TEST_WORD = /再検査|採血|検査/
function reviewLines(
  candidates: { day: string; certainty: string; cancelled: boolean }[],
  paths: NextRow[],
): { label: string; text: string }[] {
  const words = WORDS.decide
  const kept = candidates.filter((row) => !row.cancelled).map((row) => row.day)
  const after = kept.length > 0 ? kept.join('・') : words.undecided
  const lines = candidates
    .filter((row) => row.cancelled)
    .map((row) => ({ label: words.candidateLabel, text: `${row.day} → ${after}` }))
  if (lines.length === 0 && kept.length > 0) {
    lines.push({ label: words.candidateLabel, text: `${after}（${WORDS.board.likely}）` })
  }
  for (const row of paths) {
    if (row.kind !== '検査' || row.cancelled) continue
    lines.push({ label: words.nextLabel, text: `${row.day} ${row.clause.match(TEST_WORD)?.[0] ?? '検査'}` })
  }
  return lines
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
  // 最初は「変わるところだけ」。線の図と根拠は［今回選んだ内容を見る］の先。
  const [view, setView] = useState<'summary' | 'detail'>('summary')
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
  // 外した用件は数えない。件数は用件の数、人数は相手の数。
  const active = branch.outcomes.flatMap((outcome, o) =>
    outcome.actions
      .map((action, a) => ({ action, key: keyOf(o, a) }))
      .filter(({ action, key }) => changes(action) && !dropped.has(key)),
  )
  const changeCount = active.length
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
        actions: mergeByPart(
          outcome.actions
            .map((action, a) => {
              // 空にした文は使わない（もとの文に戻す）。
              const edited = edits[keyOf(o, a)]?.trim()
              return edited ? { ...action, draft: edited } : action
            })
            .filter((_, a) => !dropped.has(keyOf(o, a))),
        ),
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
  const peopleCount = new Set(active.map(({ action }) => action.part)).size
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

  const dockMessage = message && (
    <p className="dock-note" role="alert">
      {message}
    </p>
  )

  // 図の⑥「変わるところだけ」。見直しの黄の枠、用件ごとのチェック、件数、下書きへ。
  if (!partial && view === 'summary') {
    const review = reviewLines(candidateRows, pathRows)
    const detailLink = (
      <button
        type="button"
        className="quiet review-more"
        data-decide-detail
        onClick={() => setView('detail')}
      >
        {WORDS.decide.detail}
        <Chevron />
      </button>
    )
    const rows = branch.outcomes.flatMap((outcome, o) =>
      outcome.actions
        .map((action, a) => ({ action, key: keyOf(o, a) }))
        .filter(({ action }) => changes(action)),
    )
    return (
      <div className="with-dock">
        <main className="decide-screen" data-decide-summary>
          <header className="decide-head">
            <button type="button" className="quiet back" onClick={onBack}>
              <Chevron to="back" />
              {WORDS.decide.back}
            </button>
            <span className="count-pill">{WORDS.decide.title}</span>
          </header>
          <h1>{WORDS.decide.question}</h1>
          <p className="detail">{WORDS.decide.lead}</p>

          {review.length > 0 && (
            <section className="review-card change-review" data-review>
              <span className="chip">{WORDS.decide.reviewChip}</span>
              <dl className="review-lines">
                {review.map((line) => (
                  <div key={`${line.label}${line.text}`} className="review-line" data-review-line>
                    <dt>{line.label}</dt>
                    <dd>{line.text}</dd>
                  </div>
                ))}
              </dl>
              {detailLink}
            </section>
          )}
          {/* 見直しが無い案でも、根拠と線の図、［この案をやめる］へ行ける。 */}
          {review.length === 0 && detailLink}

          <h2>{WORDS.decide.checksTitle}</h2>
          <div className="plan-checks" data-plan-rows>
            {rows.map(({ action, key }) => {
              const on = !dropped.has(key)
              return (
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  key={key}
                  className={`plan-check ${toneOf(action.part)}`}
                  data-plan-row={action.part}
                  onClick={() => toggle(key)}
                >
                  <span className="plan-box" aria-hidden="true">
                    {on ? '✓' : ''}
                  </span>
                  <span className="plan-who">{whoWord(action.part, errand)}</span>
                  <span className="plan-what">{action.ask || taskOf(action)}</span>
                </button>
              )
            })}
          </div>
          {rows.length > 1 && <p className="detail">{WORDS.decide.dropHint}</p>}
          <p className="plan-summary" data-plan-count>
            {changeCount > 0 ? WORDS.decide.summary(changeCount, peopleCount) : WORDS.decide.noSends}
          </p>
          <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
        </main>

        <div className="dock">
          {dockMessage}
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
            <button type="button" data-decide-edit onClick={() => setPartial(true)}>
              {WORDS.decide.editAll}
            </button>
            <button type="button" className="quiet" data-decide-later onClick={onBack}>
              {WORDS.decide.later}
            </button>
          </div>
        </div>
      </div>
    )
  }

  // 詳しい画面と、文を直す画面からは「変わるところだけ」へ戻る。
  const backToSummary = () => {
    setPartial(false)
    setEditing(null)
    setView('summary')
  }

  return (
    <div className="with-dock">
      <main className="decide-screen">
        <header className="decide-head">
          <button type="button" className="quiet back" data-decide-back onClick={backToSummary}>
            <Chevron to="back" />
            {WORDS.decide.backToSummary}
          </button>
          <span className="count-pill">{WORDS.decide.title}</span>
        </header>
        <h1>{partial ? WORDS.decide.editAll : WORDS.decide.detail}</h1>

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
