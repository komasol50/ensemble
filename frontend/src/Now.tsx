import type { ReactNode } from 'react'
import type { Board, DoctorView, Item, ItemEvent } from './api'
import type { Proposal } from './device'
import { Chevron, shortTime, todayLabel, VERBS, whenOf, whoWord } from './parts'
import { doneStatus, OWN_PART, replyText, titleOf } from './Row'
import { isSentMessage, type Send } from './Send'
import { isFinished, type Place } from './turns'
import { WORDS } from './words'

// 「いま」。予定、次にできること、主なボタン1つの順に読める入口。台帳は変えない。

export type NowProps = {
  board: Board
  items: Item[]
  allItems: Item[]
  proposal: Proposal | null
  sends: Send[]
  unsent: Item[]
  pendingCount: number
  placeFor: (item: Item) => Place | null
  busy: boolean
  pendingCards: ReactNode
  pendingLimit: string | null
  replyBase: number
  medicalClosed: string[]
  onCloseMedical: (id: string) => void
  onOpenProposal: () => void
  onOpenSends: () => void
  onOpenMap: () => void
  onOpenItem: (item: Item) => void
  onDone: (item: Item, status: string) => void
  onAdd: () => void
  onAfter: () => void
  onSetup: () => void
  onVault: () => void
  onCalendar: () => void
  onProgress: () => void
  onUndoResult: (itemId: string) => void
  onStopProposal: () => void
  onRetryProposal: (itemId: string) => void
}

function timeOf(item: Item): number {
  const at = whenOf(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 返事の出来事。親が行を直したり戻したりしただけでは「届いた」と言わない。
const NOT_REPLY = new Set(['入れた', '文を作った', '直した', '元に戻した', '送った', '済', '病棟に聞いた'])

function replyEvent(item: Item): ItemEvent | undefined {
  return [...item.events].reverse().find((event) => !NOT_REPLY.has(event.label))
}

function replyTime(item: Item): number {
  const at = replyEvent(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

function newestOf(list: Item[]): Item | undefined {
  return list.reduce<Item | undefined>(
    (found, item) => (!found || timeOf(item) >= timeOf(found) ? item : found),
    undefined,
  )
}

// 入れたばかりの医療の問い。閉じるまで、次にできることの場所に固定文だけを出す。
export function askedMedicalOf(items: Item[], closed: string[]): Item | undefined {
  const newest = newestOf(items.filter((item) => item.part !== OWN_PART))
  return newest?.medical_reply && !isFinished(newest) && !closed.includes(newest.id)
    ? newest
    : undefined
}

// 返事のカードの題。案の連絡の1文目は状況の文で、夫の返事に読めることがあるので、頼みの文を選ぶ。
const ASKING = /ほしい|お願い|もらえ|頼/

function askingTitle(item: Item): string {
  if (item.ask) return item.ask
  if (!isSentMessage(item)) return titleOf(item)
  const sentences = (item.text_original ?? '').split(/(?<=[。！？!?\n])/)
  return sentences.find((text) => ASKING.test(text))?.trim() || titleOf(item)
}

// 夫のひとこと。サーバーが印を付けた項目だけ。親が入れた記録を一覧から隠さない。
export function isOneNote(item: Item): boolean {
  return item.id.startsWith('n-')
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
  const hasPaths = doctor.paths.length > 0
  // 分かれ道の行に出ていない取り消しの日は、消さずに残す。
  const onPaths = doctor.paths.map((row) => row.day)
  const others = doctor.candidates.filter((entry) => !onPaths.includes(entry.day))
  const allCancelled = hasPlan && doctor.candidates.every((entry) => entry.cancelled)
  return (
    <section className="now-card" data-schedule>
      <div className="now-card-head">
        <div>
          <h2 className="now-label">{words.heard}</h2>
          <p className="now-sublabel">{words.heardFrom(when)}</p>
        </div>
        <button type="button" className="quiet now-link" onClick={onMemo}>
          {words.memo}
          <Chevron />
        </button>
      </div>
      {hasPaths ? (
        <div className="clause-rows" data-paths>
          <p className="now-sublabel">{words.candidates}</p>
          {doctor.paths.map((row) => (
            <div className="clause-row" key={`${row.clause}-${row.day}`} data-path={row.kind}>
              <span className="clause-text">「{row.clause}」</span>
              <span className="path-day">
                <span className="clause-days">{row.day}</span>
                <span
                  className={
                    row.kind === '検査'
                      ? 'chip'
                      : row.cancelled
                        ? 'chip'
                        : row.certainty === '確定'
                          ? 'chip confirmed'
                          : 'chip likely'
                  }
                >
                  {row.kind === '検査'
                    ? words.test
                    : row.kind === 'その他'
                      ? words.other
                      : row.cancelled
                        ? words.cancelled
                        : row.certainty === '確定'
                          ? words.confirmed
                          : words.candidate}
                </span>
              </span>
            </div>
          ))}
          {others.map((entry) => (
            <div className="clause-row" key={entry.day} data-candidate-day={entry.day}>
              <span className={entry.cancelled ? 'clause-day cancelled' : 'clause-day'}>
                {entry.day}
              </span>
              <span className={entry.cancelled ? 'chip' : 'chip likely'}>
                {entry.cancelled ? words.cancelled : words.candidate}
              </span>
            </div>
          ))}
          {allCancelled && <p className="detail">{words.noCandidates}</p>}
        </div>
      ) : hasPlan ? (
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

// 次にできることの計算。文は上に、ボタンは下の帯に出すので、両方から呼ぶ。
function planOf({
  board,
  items,
  proposal,
  sends,
  unsent,
  pendingCount,
  placeFor,
  pendingLimit,
  replyBase,
  medicalClosed,
  onCloseMedical,
  onOpenProposal,
  onOpenSends,
  onOpenItem,
  onDone,
  onAdd,
  onAfter,
  onUndoResult,
  onStopProposal,
  onRetryProposal,
}: NowProps) {
  const words = WORDS.now
  const errand = board.profile.errand_people[0]?.label
  const doctor = board.doctor
  const groupOf = (name: Place['group']) => items.filter((item) => placeFor(item)?.group === name)
  const whoOf = (item: Item) => (item.part === '家族' && item.owner) || whoWord(item.part, errand)

  // 届いた返事。医療の問いの返事は固定文の行なので入れない。
  // 開いた後に届いたものだけ。初期データの返事を「届きました」と言わない。
  const replies = [...groupOf('difficult'), ...groupOf('timed'), ...groupOf('accepted')].filter(
    (item) => !item.medical_reply && replyTime(item) > replyBase,
  )
  const latestReply = newestOf(replies)
  const difficult = newestOf(groupOf('difficult').filter((item) => !item.medical_reply))
  const accepted = newestOf(
    [...groupOf('timed'), ...groupOf('accepted')].filter(
      (item) => replyTime(item) > replyBase,
    ),
  )
  const todo = groupOf('todo')
  const older = groupOf('older')
  const waiting = groupOf('waiting')
  const askedMedical = askedMedicalOf(items, medicalClosed)
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
      // 回数の上限は、押し直しても通らない。
      if (proposal.limit) {
        return {
          line: proposal.message,
          secondary: { label: words.closeFailed, onClick: onStopProposal },
        }
      }
      return {
        line: proposal.message,
        sub: proposal.after ? words.afterKept : undefined,
        primary: { label: words.again, onClick: () => onRetryProposal(proposal.item_id) },
        secondary: proposal.after
          ? { label: WORDS.row.undo, onClick: () => onUndoResult(proposal.item_id) }
          : { label: words.closeFailed, onClick: onStopProposal },
      }
    }
    if (proposal?.state === 'ready') {
      return {
        line: words.changed,
        primary: { label: words.seeChanges, onClick: onOpenProposal },
      }
    }
    if (pendingLimit) return { line: pendingLimit }
    if (askedMedical) {
      return {
        medical: askedMedical.medical_reply ?? undefined,
        secondary: { label: words.medicalOk, onClick: () => onCloseMedical(askedMedical.id) },
      }
    }
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
        line:
          accepted.status === '時刻つきで可'
            ? words.timedLine(whoOf(accepted), accepted.status_label ?? '')
            : words.acceptedLine(whoOf(accepted)),
        sub: askingTitle(accepted),
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

  return { next, replies, latestReply, difficult, promoteAfter, whoOf }
}

export function nowHasActions(props: NowProps): boolean {
  const { next } = planOf(props)
  return !!next.primary || !!next.secondary
}

// 下の帯のボタン。親指が届く場所に、いつも同じ形で出す。
export function NowActions(props: NowProps) {
  const { next } = planOf(props)
  if (!next.primary && !next.secondary) return null
  return (
    <div className="now-actions" data-now-actions>
      {next.primary && (
        <button
          type="button"
          className="primary"
          data-primary
          disabled={props.busy}
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
    </div>
  )
}

export function NowView(props: NowProps) {
  const {
    board,
    items,
    allItems,
    busy,
    pendingCards,
    onOpenMap,
    onOpenItem,
    onDone,
    onAdd,
    onAfter,
    onSetup,
    onVault,
    onCalendar,
    onProgress,
    proposal,
  } = props
  const words = WORDS.now
  const doctor = board.doctor
  const { next, replies, latestReply, difficult, promoteAfter, whoOf } = planOf(props)

  // 主なボタンが別のことを指すときは、受け取りのボタンを返事のカードの中に出す。
  const receiveInCard =
    !!latestReply &&
    latestReply !== difficult &&
    next.primary?.label !== words.received &&
    !latestReply.medical_reply
  const when = latestReply ? replyEvent(latestReply) : undefined
  const note = latestReply
    ? allItems.find((item) => item.refers_to === latestReply.id && isOneNote(item))
    : undefined

  return (
    <>
      <div className="now-head">
        <div className="now-title">
          <h1>{words.title}</h1>
          <p className="now-date">{todayLabel()}</p>
        </div>
        <button type="button" className="quiet now-calendar" data-calendar onClick={onCalendar}>
          <CalendarIcon />
          {words.calendar}
        </button>
      </div>

      {!board.profile.configured && (
        <section className="now-card" data-setup-note>
          <p>{words.setupNote}</p>
          <button type="button" onClick={onSetup}>
            {words.setupButton}
          </button>
        </section>
      )}

      <section className="now-next" data-next-step>
        <h2>{words.next}</h2>
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
          </>
        )}
      </section>

      {latestReply && (
        <section className="now-reply" data-now-reply>
          <article className="now-card">
            <p className="now-reply-head">{words.replyFrom(whoOf(latestReply))}</p>
            <h3>{askingTitle(latestReply)}</h3>
            <p className="now-reply-state">
              <span className="chip state reply">{replyText(latestReply)}</span>
              {when && (
                <time className="row-fact" dateTime={when.at}>
                  {shortTime(when.at)}
                </time>
              )}
            </p>
            {note && <p className="now-note">「{note.text_original}」</p>}
            {note?.medical_reply && <p className="medical">{note.medical_reply}</p>}
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
          {replies.length > 1 && (
            <button type="button" className="now-row" data-more-replies onClick={onProgress}>
              {words.moreReplies(replies.length - 1)}
              <Chevron />
            </button>
          )}
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

      {pendingCards}

      {/* 進めた案で変わったところを、いつでも見返せるようにする。 */}
      {board.impact && !proposal && (
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
