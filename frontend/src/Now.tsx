import type { ReactNode } from 'react'
import type { Board, DoctorView, Item } from './api'
import type { Proposal } from './device'
import { Chevron, shortTime, todayLabel, VERBS, whenOf, whoWord } from './parts'
import { doneStatus, OWN_PART, replyText, titleOf } from './Row'
import type { Send } from './Send'
import { isFinished, type Place } from './turns'
import { WORDS } from './words'

// 「いま」。予定、次にできること、主なボタン1つの順に読める入口。台帳は変えない。

type Props = {
  board: Board
  items: Item[]
  proposal: Proposal | null
  sends: Send[]
  unsent: Item[]
  pendingCount: number
  placeFor: (item: Item) => Place | null
  busy: boolean
  fromLink: boolean
  pendingCards: ReactNode
  onOpenProposal: () => void
  onOpenSends: () => void
  onOpenMap: () => void
  onOpenItem: (item: Item) => void
  onDone: (item: Item, status: string) => void
  onAdd: () => void
  onAfter: () => void
  onVault: () => void
  onCalendar: () => void
  onStopProposal: () => void
  onRetryProposal: (itemId: string) => void
}

function timeOf(item: Item): number {
  const at = whenOf(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

function newestOf(list: Item[]): Item | undefined {
  return list.reduce<Item | undefined>(
    (found, item) => (!found || timeOf(item) >= timeOf(found) ? item : found),
    undefined,
  )
}

function CalendarIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="4" width="14" height="13" rx="2" />
      <path d="M3 8h14M7 2v4M13 2v4" />
    </svg>
  )
}

// 医師の言葉は原文のまま。日付は別の列に補助の色で出す。案の前は札を付けない。
function Schedule({
  doctor,
  promoted,
  onMemo,
  onAfter,
}: {
  doctor: DoctorView
  promoted: boolean
  onMemo: () => void
  onAfter: () => void
}) {
  const words = WORDS.now
  const when = shortTime(doctor.recorded_at)
  const hasPlan = doctor.candidates.length > 0
  const allCancelled = hasPlan && doctor.candidates.every((entry) => entry.cancelled)
  return (
    <section className="now-card" data-schedule>
      <div className="now-card-head">
        <h2 className="now-label">{words.heard(when)}</h2>
        <button type="button" className="quiet now-link" onClick={onMemo}>
          {words.memo}
          <Chevron />
        </button>
      </div>
      {hasPlan ? (
        <div className="clause-rows">
          <p className="now-sublabel">{words.candidates}</p>
          {doctor.candidates.map((entry) => (
            <div className="clause-row" key={entry.day} data-candidate-day={entry.day}>
              <span className={entry.cancelled ? 'clause-day cancelled' : 'clause-day'}>
                {entry.day}
              </span>
              <span
                className={
                  entry.cancelled
                    ? 'chip'
                    : entry.certainty === '確定'
                      ? 'chip confirmed'
                      : 'chip likely'
                }
              >
                {entry.cancelled
                  ? words.cancelled
                  : entry.certainty === '確定'
                    ? words.confirmed
                    : words.candidate}
              </span>
            </div>
          ))}
          {allCancelled && <p className="detail">{words.noCandidates}</p>}
        </div>
      ) : (
        <div className="clause-rows">
          {doctor.clauses.map((clause) => (
            <div className="clause-row" key={clause.text} data-clause>
              <span className="clause-text">「{clause.text}」</span>
              {clause.days.length > 0 && (
                <span className="clause-days">{clause.days.join('・')}</span>
              )}
            </div>
          ))}
        </div>
      )}
      {doctor.open && !promoted && (
        <button type="button" className="now-after" data-after-entry onClick={onAfter}>
          {words.afterEntry}
        </button>
      )}
    </section>
  )
}

type Next = {
  line?: string
  sub?: string
  medical?: string
  status?: boolean
  primary?: { label: string; onClick: () => void }
  secondary?: { label: string; onClick: () => void }
}

export function NowView({
  board,
  items,
  proposal,
  sends,
  unsent,
  pendingCount,
  placeFor,
  busy,
  fromLink,
  pendingCards,
  onOpenProposal,
  onOpenSends,
  onOpenMap,
  onOpenItem,
  onDone,
  onAdd,
  onAfter,
  onVault,
  onCalendar,
  onStopProposal,
  onRetryProposal,
}: Props) {
  const words = WORDS.now
  const errand = board.profile.errand_people[0]?.label
  const doctor = board.doctor
  const groupOf = (name: Place['group']) => items.filter((item) => placeFor(item)?.group === name)
  const whoOf = (item: Item) => (item.part === '家族' && item.owner) || whoWord(item.part, errand)

  // 届いた返事。医療の問いの返事は固定文の行なので入れない。
  const replies = [...groupOf('difficult'), ...groupOf('timed'), ...groupOf('accepted')].filter(
    (item) => !item.medical_reply,
  )
  const latestReply = newestOf(replies)
  const difficult = newestOf(groupOf('difficult').filter((item) => !item.medical_reply))
  const accepted = newestOf([...groupOf('timed'), ...groupOf('accepted')])
  const todo = groupOf('todo')
  const older = groupOf('older')
  const waiting = groupOf('waiting')
  const newest = newestOf(items.filter((item) => item.part !== OWN_PART))
  const askedMedical = newest?.medical_reply && !isFinished(newest) ? newest : undefined
  const promoteAfter = !!doctor?.open && doctor.due
  const people = new Set(unsent.map((item) => item.part)).size

  const next = ((): Next => {
    if (proposal?.state === 'working') {
      return {
        line: words.working,
        status: true,
        secondary: { label: words.stop, onClick: onStopProposal },
      }
    }
    if (proposal?.state === 'failed') {
      return {
        line: proposal.message,
        primary: { label: words.again, onClick: () => onRetryProposal(proposal.item_id) },
        secondary: { label: words.closeFailed, onClick: onStopProposal },
      }
    }
    if (proposal?.state === 'ready') {
      return {
        line: words.changed,
        primary: { label: words.seeChanges, onClick: onOpenProposal },
      }
    }
    if (askedMedical) return { medical: askedMedical.medical_reply ?? undefined }
    if (unsent.length > 0) {
      const first = unsent[0]
      const action = sends.find((send) => send.item.id === first.id)?.action
      const verb = VERBS[action?.type ?? ''] ?? VERBS['頼む']
      const rest = unsent.length - 1
      return {
        line: `${whoOf(first)}${verb}${rest > 0 ? words.andMore(rest) : ''}`,
        sub: titleOf(first),
        primary: {
          label: latestReply ? words.remaining(people) : words.seeDrafts,
          onClick: onOpenSends,
        },
      }
    }
    if (difficult) {
      return {
        line: words.difficult(whoOf(difficult)),
        primary: { label: words.decideOther, onClick: () => onOpenItem(difficult) },
      }
    }
    if (accepted) {
      return {
        line: words.acceptedLine(whoOf(accepted)),
        primary: {
          label: words.received,
          onClick: () => onDone(accepted, doneStatus(accepted) ?? '完了'),
        },
      }
    }
    if (promoteAfter) {
      return {
        line: words.afterLine,
        primary: { label: words.afterEntry, onClick: onAfter },
      }
    }
    if (todo.length > 0) {
      return {
        line: titleOf(todo[0]),
        primary: { label: words.seeTodo, onClick: () => onOpenItem(todo[0]) },
      }
    }
    if (older.length > 0) {
      return {
        line: words.older(older.length),
        primary: { label: words.seeTodo, onClick: () => onOpenItem(older[0]) },
      }
    }
    if (waiting.length > 0) return { line: words.waiting(waiting.length) }
    if (items.length === 0 && pendingCount === 0) {
      return { line: words.empty, primary: { label: words.emptyButton, onClick: onAdd } }
    }
    return { line: words.none }
  })()

  // 主なボタンが別のことを指すときは、受け取りのボタンを返事のカードの中に出す。
  const receiveInCard =
    !!latestReply &&
    latestReply !== difficult &&
    next.primary?.label !== words.received &&
    !latestReply.medical_reply
  const when = latestReply ? whenOf(latestReply) : undefined

  return (
    <>
      <div className="now-head">
        <div>
          <h1>{words.title}</h1>
          <p className="now-date">{todayLabel()}</p>
        </div>
        <button type="button" className="quiet now-calendar" data-calendar onClick={onCalendar}>
          <CalendarIcon />
          {words.calendar}
        </button>
      </div>

      {latestReply && (
        <section className="now-reply" data-now-reply>
          <article className="now-card">
            <p className="now-reply-head">{words.replyFrom(whoOf(latestReply))}</p>
            <h3>{titleOf(latestReply)}</h3>
            <p className="now-reply-state">
              <span className="chip state reply">{replyText(latestReply)}</span>
              {when && (
                <time className="row-fact" dateTime={when.at}>
                  {shortTime(when.at)}
                </time>
              )}
            </p>
            {receiveInCard && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onDone(latestReply, doneStatus(latestReply) ?? '完了')}
              >
                {words.received}
              </button>
            )}
          </article>
        </section>
      )}

      {doctor && (
        <Schedule
          doctor={doctor}
          promoted={promoteAfter && next.primary?.label === words.afterEntry}
          onMemo={() => {
            const item = items.find((entry) => entry.id === doctor.item_id)
            if (item) onOpenItem(item)
          }}
          onAfter={onAfter}
        />
      )}

      <section className="now-next" data-next-step>
        <h2>{fromLink ? words.nextFromLink : words.next}</h2>
        {next.medical ? (
          <p className="medical" data-now-medical>
            {next.medical}
          </p>
        ) : (
          <>
            {next.line && (
              <p className="now-line" role={next.status ? 'status' : undefined}>
                {next.line}
              </p>
            )}
            {next.sub && <p className="detail now-sub">{next.sub}</p>}
            {next.primary && (
              <button
                type="button"
                className="primary"
                data-primary
                disabled={busy}
                onClick={next.primary.onClick}
              >
                {next.primary.label}
              </button>
            )}
            {next.secondary && (
              <button type="button" onClick={next.secondary.onClick}>
                {next.secondary.label}
              </button>
            )}
          </>
        )}
      </section>

      {pendingCards}

      {/* 全部送った後も、変わったところを見返せるようにする。 */}
      {board.impact && unsent.length === 0 && !proposal && (
        <button type="button" className="now-row" data-map-open onClick={onOpenMap}>
          {words.seeMap}
          <Chevron />
        </button>
      )}
      <button type="button" className="now-input" data-now-input onClick={onAdd}>
        {words.input}
      </button>
      <button type="button" className="now-row" onClick={onVault}>
        {words.vault}
        <Chevron />
      </button>
    </>
  )
}
