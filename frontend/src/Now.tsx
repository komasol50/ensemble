import type { ReactNode } from 'react'
import type { Board, DoctorView, Item, ItemEvent } from './api'
import type { Proposal } from './device'
import { Chevron, shortTime, todayLabel, VERBS, whenOf, whoWord } from './parts'
import { doneStatus, OWN_PART, replyText, titleOf } from './Row'
import { isSentMessage, type Send } from './Send'
import { PathRows } from './Paths'
import { upcomingOf } from './schedule'
import { isFinished, wantsToTalk, type Place } from './turns'
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

const CONDITIONAL = /なら|たら|れば/
const TEST_WORD = /再検査|採血|検査/
// 親が家族に頼みたいと入れた用事の短い名前。名詞で終わる短い語のときだけ「◯◯をお願いする」にする。
const SHORT_TASK = /^[^。、]{1,10}$/
const VERB_END = /[たいるしてほ]$/

// 医師の言葉は原文のまま。日付は別の列に補助の色で出す。
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
  const allCancelled = hasPlan && doctor.candidates.every((entry) => entry.cancelled)
  // 図の③：一番上に検査の日（条件でない節の検査の語）、その下に条件の節を原文のまま、
  // 右に日付と種類の札。種類は節の語から決める（退院＝退院の候補、検査の語＝その語）。
  const headline = doctor.clauses.find(
    (clause) =>
      clause.days.length > 0 && !CONDITIONAL.test(clause.text) && TEST_WORD.test(clause.text),
  )
  const rest = doctor.clauses.filter((clause) => clause !== headline)
  const kindOf = (text: string) =>
    !CONDITIONAL.test(text)
      ? null
      : /退院/.test(text)
        ? words.kindDischarge
        : (text.match(TEST_WORD)?.[0] ?? null)
  const clauseRows = (
    <div className="clause-rows">
      {headline && (
        <p className="schedule-headline" data-schedule-headline>
          {headline.days.join('・')} {headline.text.match(TEST_WORD)?.[0]}
        </p>
      )}
      {rest.map((clause) => {
        const kind = kindOf(clause.text)
        const body = (
          <>
            <span className="clause-text">「{clause.text}」</span>
            {clause.days.length > 0 && (
              <span className="clause-days">
                {clause.days.join('・')}
                {kind && (
                  <span className={kind === words.kindDischarge ? 'chip likely' : 'chip'}>
                    {kind}
                  </span>
                )}
              </span>
            )}
          </>
        )
        // 結果を入れる前は、条件の節を押すと「採血のあと」へ進む。
        return doctor.open && kind ? (
          <button
            type="button"
            className="clause-row clause-button"
            key={clause.text}
            data-clause
            onClick={onAfter}
          >
            {body}
          </button>
        ) : (
          <div className="clause-row" key={clause.text} data-clause>
            {body}
          </div>
        )
      })}
    </div>
  )
  return (
    <section className="now-card" data-schedule>
      <div className="now-card-head">
        <div>
          <h2 className="now-label">{words.heard}</h2>
        </div>
      </div>
      {hasPlan || hasPaths ? (
        <>
          {/* 次の段が無いときも、医師の言葉の節は消さない。 */}
          {!hasPaths && clauseRows}
          <div className="clause-rows" data-paths={hasPaths ? '' : undefined}>
            <p className="now-sublabel">{words.candidates}</p>
            <PathRows paths={doctor.paths} candidates={doctor.candidates} />
            {allCancelled && <p className="detail">{words.noCandidates}</p>}
          </div>
        </>
      ) : (
        clauseRows
      )}
      <button type="button" className="quiet now-link now-memo" data-memo onClick={onMemo}>
        {words.memoAt(when)}
        <Chevron />
      </button>
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
  const replies = [
    ...groupOf('difficult'),
    ...groupOf('held'),
    ...groupOf('timed'),
    ...groupOf('accepted'),
  ].filter((item) => !item.medical_reply && replyTime(item) > replyBase)
  const latestReply = newestOf(replies)
  const difficult = newestOf(groupOf('difficult').filter((item) => !item.medical_reply))
  const talk = newestOf(groupOf('held').filter(wantsToTalk))
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
    // 医療の固定文はほかの案内より先に出す。案ができていても後ろに回さない。
    if (askedMedical) {
      return {
        medical: askedMedical.medical_reply ?? undefined,
        secondary: { label: words.medicalOk, onClick: () => onCloseMedical(askedMedical.id) },
      }
    }
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
    if (unsent.length > 0) {
      const first = unsent[0]
      const action = sends.find((send) => send.item.id === first.id)?.action
      const verb = VERBS[action?.type ?? ''] ?? VERBS['頼む']
      const rest = unsent.length - 1
      const more = rest > 0 ? words.andMore(rest) : ''
      // 図の③：親が家族に頼みたいと入れた用事は「夫に、着替えをお願いする」［お願い文を見る］。
      const task = first.task_summary ?? ''
      const own = !isSentMessage(first) && first.part === '家族'
      if (own && !latestReply) {
        const named = SHORT_TASK.test(task) && !VERB_END.test(task)
        return {
          line: `${named ? words.askFor(whoOf(first), task) : words.askOf(whoOf(first))}${more}`,
          sub: named ? undefined : titleOf(first),
          primary: { label: words.seeRequest, onClick: onOpenSends },
        }
      }
      return {
        line: `${whoOf(first)}${verb}${more}`,
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
    if (talk) {
      return {
        line: words.talk(whoOf(talk)),
        primary: { label: words.seeTodo, onClick: () => onOpenItem(talk) },
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
      // 質問の用事は、何をするのかを先に書く。
      const asking = todo[0].kind === '質問'
      return {
        line: asking ? `${whoOf(todo[0])}${VERBS['確認する']}` : titleOf(todo[0]),
        sub: asking ? titleOf(todo[0]) : undefined,
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

// 夫のひとこと。
function noteOf(reply: Item | undefined, allItems: Item[]): Item | undefined {
  return reply
    ? allItems.find((item) => item.refers_to === reply.id && isOneNote(item))
    : undefined
}

// 医療の固定文を、次にできることか返事のカードに出しているか。出ていれば足元に重ねない。
export function nowShowsMedical(props: NowProps): boolean {
  const { next, latestReply } = planOf(props)
  return !!next.medical || !!noteOf(latestReply, props.allItems)?.medical_reply
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
    latestReply.status !== '保留' &&
    !latestReply.medical_reply
  const when = latestReply ? replyEvent(latestReply) : undefined
  const note = noteOf(latestReply, allItems)
  // 引き受けた返事は、受け取るまで「夫が対応中です」。
  const working =
    !!latestReply &&
    (latestReply.status === '引き受けた' || latestReply.status === '時刻つきで可')
  // 結果が入った後だけ、次の予定を出す（結果の前は医師の言葉の欄）。
  const upcoming = doctor && !doctor.open ? upcomingOf(doctor) : null

  return (
    <>
      <div className="now-head">
        <div className="now-title">
          <h1>{words.title}</h1>
          <p className="now-date">{todayLabel(board.today)}</p>
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

      {/* 図の③：病院で聞いた予定を、次にできることより先に出す。 */}
      {doctor && !upcoming && (
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
          {/* 図の⑨：帯「夫から返事が届きました」、カードに返事の変わり方と、対応中の一言。 */}
          <p className="now-reply-head now-reply-band" data-reply-band>
            {words.replyFrom(whoOf(latestReply))}
          </p>
          <article className="now-card">
            <h3>{askingTitle(latestReply)}</h3>
            <p className="now-reply-state">
              <span className="detail">{words.fromWaiting} →</span>
              <span className="chip state reply">{replyText(latestReply)}</span>
              {when && (
                <time className="row-fact" dateTime={when.at}>
                  {shortTime(when.at)}
                </time>
              )}
            </p>
            {note && <p className="now-note">「{note.text_original}」</p>}
            {note?.medical_reply && <p className="medical">{note.medical_reply}</p>}
            {working && (
              <p className="now-in-progress" data-in-progress>
                {words.inProgress(whoOf(latestReply))}
              </p>
            )}
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

      {/* 結果を入れて案を進めた後は、医師の言葉の欄でなく、次の予定の1枚だけ（図の⑨）。 */}
      {upcoming && (
        <section className="now-card now-upcoming" data-upcoming>
          {upcoming.test && <p>{upcoming.test}</p>}
          {upcoming.undecided && <p>{WORDS.schedule.undecided}</p>}
        </section>
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
