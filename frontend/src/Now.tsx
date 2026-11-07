import type { ReactNode } from 'react'
import type { Board, DoctorView, Item, ItemEvent } from './api'
import { device, type Proposal } from './device'
import { NowStrip } from './ChildStrip'
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
  // 夫がリンクから開いた「いま」。見るだけ（親が押すものは出さない。2026-11-07 PO・UX Design）。
  readOnly?: boolean
  linkSummary?: string
  // 「いま」に足す短い行（やること・持ち物、v3.6）。入れるボタンの上に出す。
  countLines?: ReactNode
}

function timeOf(item: Item): number {
  const at = whenOf(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 返事の出来事。親が行を直したり戻したりしただけでは「届いた」と言わない。
const NOT_REPLY = new Set(['入れた', '文を作った', '直した', '元に戻した', '送った', '済', '病棟に聞いた', 'もう一度共有した'])

function replyEvent(item: Item): ItemEvent | undefined {
  return [...item.events].reverse().find((event) => !NOT_REPLY.has(event.label))
}

function replyTime(item: Item): number {
  const at = replyEvent(item)?.at
  const time = at ? new Date(at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 夫のひとことの時刻（その用事を指すひとこと）。
function noteTime(item: Item, allItems: Item[]): number {
  const note = allItems.find((other) => other.refers_to === item.id && isOneNote(other))
  const time = note?.recorded_at ? new Date(note.recorded_at).getTime() : 0
  return Number.isNaN(time) ? 0 : time
}

// 返事が届いた時刻。答えの記録か、夫のひとことの新しい方（UX Design・Plan main の ①）。
function answeredAt(item: Item, allItems: Item[]): number {
  return Math.max(replyTime(item), noteTime(item, allItems))
}

// 夫が返事を変えたか（2回目以降の答えか、答えの後にひとことを変えた）。前の答えの語も返す。
function changedReply(item: Item, allItems: Item[]): { changed: boolean; before: string | null } {
  const answers = item.events.filter((event) => !NOT_REPLY.has(event.label))
  const word = (label: string) => label.replace(/^.+?：/, '')
  const latest = answers.length > 0 ? word(answers[answers.length - 1].label) : null
  // 前の答えは、答えが変わったときだけ（ひとことだけ変えたときは出さない。UX Design の P2）。
  const prior = answers.length >= 2 ? word(answers[answers.length - 2].label) : null
  const before = prior && prior !== latest ? prior : null
  const last = answers[answers.length - 1]
  const lastTime = last ? new Date(last.at).getTime() : 0
  return { changed: answers.length >= 2 || noteTime(item, allItems) > lastTime + 1000, before }
}

// 退院の候補日（日にちの数）。取り消していない・確定していない候補日と、結果の前は医師の言葉の
// 条件つきの退院の節の日（11/9 はまだ案が無く、候補日の欄が空のため）。
function candidateDayNumbers(doctor: DoctorView | null | undefined): Set<string> {
  const days = new Set<string>()
  if (!doctor) return days
  const add = (text: string) => {
    const m = text.match(/\d{1,2}\/(\d{1,2})/)
    if (m) days.add(String(Number(m[1])))
  }
  doctor.candidates
    .filter((row) => !row.cancelled && row.certainty !== '確定')
    .forEach((row) => add(row.day))
  if (doctor.open) {
    doctor.clauses
      .filter((clause) => CONDITIONAL.test(clause.text) && /退院/.test(clause.text))
      .forEach((clause) => clause.days.forEach(add))
  }
  return days
}

// 次にできることの行の中の候補日に「（退院の候補）」を添える（2026-11-07 本人の決定）。1行に1回だけ。
// 画面の表示だけで、送る文と台帳は変えない。
function markCandidateDay(line: string, days: Set<string>): string {
  if (days.size === 0) return line
  const m = line.match(/(?<!\d)(\d{1,2})日/)
  if (!m || m.index === undefined || !days.has(String(Number(m[1])))) return line
  const end = m.index + m[0].length
  return `${line.slice(0, end)}${WORDS.now.candidateMark}${line.slice(end)}`
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

const CONDITIONAL = /なら|たら|れば/
const TEST_WORD = /再検査|採血|検査/
// 親が家族に頼みたいと入れた用事の短い名前。名詞で終わる短い語のときだけ「◯◯をお願いする」にする。
const SHORT_TASK = /^[^。、]{1,10}$/
const VERB_END = /[たいるしてほ]$/

// 医師の言葉は原文のまま。日付は別の列に補助の色で出す。
function Schedule({
  doctor,
  promoted,
  readOnly = false,
  onMemo,
  onAfter,
}: {
  doctor: DoctorView
  promoted: boolean
  readOnly?: boolean
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
        return doctor.open && kind && !readOnly ? (
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
      {doctor.open && !promoted && !readOnly && (
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
  allItems,
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
  ].filter((item) => !item.medical_reply && answeredAt(item, allItems) > replyBase)
  // 一番新しく返事が届いた（変わった）用事。ひとことだけ変えたときも上に来る。
  const latestReply = replies.reduce<Item | undefined>(
    (found, item) =>
      !found || answeredAt(item, allItems) >= answeredAt(found, allItems) ? item : found,
    undefined,
  )
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
  // 候補日ごとの空きの確認は、結果も案もまだ無く、作り手ルール②が有効なときだけ。
  const checkKey = doctor ? `${doctor.item_id}@${doctor.recorded_at}` : ''
  // 医師の言葉の後に、預け先や支援の用事を親がもう入れていれば（家庭Bの16日の仮押さえ）、出さない。
  const arranged =
    !!doctor &&
    items.some(
      (item) =>
        (item.part === '保育園・預け先' || item.part === '支援') &&
        item.kind === '行動' &&
        (item.recorded_at ?? '') > (doctor.recorded_at ?? ''),
    )
  const checkCandidates =
    !!doctor?.open &&
    !arranged &&
    !device.candidateChecked().includes(checkKey) &&
    doctor.candidates.length === 0 &&
    doctor.paths.length === 0 &&
    !proposal &&
    board.rules.some((rule) => rule.active && rule.text.includes('候補日'))
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
      const own = !isSentMessage(first) && first.part === '家族'
      if (own && !latestReply) {
        // 同じ相手の親の頼みごとは1通にまとめて送るので、1行にまとめて出す（「夫に、迎えと着替えをお願いする」）。
        const group = unsent.filter(
          (entry) => !isSentMessage(entry) && entry.part === '家族' && whoOf(entry) === whoOf(first),
        )
        const tasks = group.map((entry) => entry.task_summary ?? '')
        const named = tasks.every((entry) => SHORT_TASK.test(entry) && !VERB_END.test(entry))
        const others = unsent.length - group.length
        const tail = others > 0 ? words.andMore(others) : ''
        const task = markCandidateDay(tasks.join('と'), candidateDayNumbers(doctor))
        return {
          line: `${named ? words.askFor(whoOf(first), task) : words.askOf(whoOf(first))}${tail}`,
          sub: named ? undefined : titleOf(first),
          primary: { label: words.seeRequest, onClick: onOpenSends },
        }
      }
      // 図の⑨：返事の後は「まだ伝えていない文 2件」と相手の名前、［残り2人分を見る］。
      if (latestReply) {
        return {
          line: words.unsentLeft(unsent.length),
          sub: [...new Set(unsent.map(whoOf))].join('・'),
          primary: { label: words.remaining(people), onClick: onOpenSends },
        }
      }
      return {
        line: `${whoOf(first)}${verb}${more}`,
        sub: titleOf(first),
        primary: { label: words.seeDrafts, onClick: onOpenSends },
      }
    }
    // 作り手ルール②（候補日ごとに仮押さえ）：結果の前に、親が押して確認の文を作る（PO の決定 A）。
    // 医師の言葉を入れた直後は自動で作らない（図の③）。
    if (checkCandidates && doctor) {
      return {
        line: words.checkCandidates,
        primary: {
          label: words.makeCheck,
          onClick: () => {
            device.markCandidateChecked(checkKey)
            onRetryProposal(doctor.item_id)
          },
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
  if (props.readOnly) return false
  const { next, latestReply } = planOf(props)
  return !!next.medical || !!noteOf(latestReply, props.allItems)?.medical_reply
}

export function nowHasActions(props: NowProps): boolean {
  if (props.readOnly) return false
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
    onProgress,
    proposal,
    countLines,
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
  const note = noteOf(latestReply, allItems)
  // 返事の時刻は、答えの記録か、ひとことの新しい方。
  const answered = latestReply ? answeredAt(latestReply, allItems) : 0
  const when = answered ? { at: new Date(answered).toISOString() } : undefined
  const change = latestReply ? changedReply(latestReply, allItems) : { changed: false, before: null }
  // 結果が入った後だけ、次の予定を出す（結果の前は医師の言葉の欄）。
  const upcoming = doctor && !doctor.open ? upcomingOf(doctor) : null

  // 病院で聞いた予定（図の③）。結果の後は次の予定の1枚に置き換わる。
  const scheduleBlock = doctor && !upcoming && (
    <Schedule
      doctor={doctor}
      promoted={promoteAfter && next.primary?.label === words.afterEntry}
      onMemo={() => {
        const item = items.find((entry) => entry.id === doctor.item_id)
        if (item) onOpenItem(item)
      }}
      onAfter={onAfter}
    />
  )
  const nextBlock = (
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
  )
  // 図の⑨：帯「夫から返事が届きました」、カードに返事の変わり方と、対応中の一言。
  const replyBlock = latestReply && (
    <section className="now-reply" data-now-reply>
      <p className="now-reply-head now-reply-band" data-reply-band>
        {change.changed ? words.replyChanged(whoOf(latestReply)) : words.replyFrom(whoOf(latestReply))}
      </p>
      <article className="now-card">
        <h3>{askingTitle(latestReply)}</h3>
        <p className="now-reply-state">
          {/* 答えが変わったときは前の答え、初めての返事は「返事待ち」。ひとことだけ変えたときは出さない。 */}
          {(change.before || !change.changed) && (
            <span className="detail">{change.before ?? words.fromWaiting} →</span>
          )}
          <span className="chip state reply">{replyText(latestReply)}</span>
          {when && (
            <time className="row-fact" dateTime={when.at}>
              {shortTime(when.at)}
            </time>
          )}
        </p>
        {note && <p className="now-note">「{note.text_original}」</p>}
        {note?.medical_reply && <p className="medical">{note.medical_reply}</p>}
        {/* 状態の行に「引き受けた」が出ているので、黄の「夫が引き受けました」は重ねない（UX Design の P2 ④）。 */}
        {/* 答えが「難しい」に変わったら、次の一手もカードの中に（UX Design の P2）。 */}
        {latestReply.status === '難しい' && next.primary?.label !== words.decideOther && (
          <>
            <p className="now-line">{words.difficult(whoOf(latestReply))}</p>
            <button type="button" disabled={busy} onClick={() => onOpenItem(latestReply)}>
              {words.decideOther}
            </button>
          </>
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
  )
  // 結果を入れて案を進めた後は、医師の言葉の欄でなく、次の予定の1枚だけ（図の⑨）。
  const upcomingBlock = upcoming && (
    <section className="now-card now-upcoming" data-upcoming>
      {upcoming.test && <p>{upcoming.test}</p>}
      {upcoming.candidates && <p>{upcoming.candidates}</p>}
      {upcoming.undecided && <p>{WORDS.schedule.undecided}</p>}
    </section>
  )

  // 夫の見るだけの「いま」：病院で聞いた予定（または次の予定）と、返事の状態だけ。
  if (props.readOnly) {
    return (
      <>
        <div className="now-head">
          <div className="now-title">
            <h1>{words.title}</h1>
            <p className="now-date">{todayLabel(board.today)}</p>
          </div>
        </div>
        <NowStrip board={board} />
        {/* 夫は親の「いま」と同じ時点のカードを見る（UX Design・Plan main の ②）。結果の後に節ごとの
            カードを出すと、再検査の日が退院の候補日の見出しの下に並んでしまうため。 */}
        {doctor && !upcoming && (
          <Schedule
            doctor={doctor}
            promoted={false}
            readOnly
            onMemo={() => {
              const item = items.find((entry) => entry.id === doctor.item_id)
              if (item) onOpenItem(item)
            }}
            onAfter={() => undefined}
          />
        )}
        {doctor && upcoming && (
          <section className="now-card now-upcoming" data-upcoming>
            <h2 className="now-label">{words.heard}</h2>
            {upcoming.test && <p>{upcoming.test}</p>}
            {upcoming.candidates && <p>{upcoming.candidates}</p>}
            {upcoming.undecided && <p>{WORDS.schedule.undecided}</p>}
            <button
              type="button"
              className="quiet now-link now-memo"
              data-memo
              onClick={() => {
                const item = items.find((entry) => entry.id === doctor.item_id)
                if (item) onOpenItem(item)
              }}
            >
              {words.memoAt(shortTime(doctor.recorded_at))}
              <Chevron />
            </button>
          </section>
        )}
        {props.linkSummary && (
          <p className="now-card now-link-summary" data-link-summary>
            {props.linkSummary}
          </p>
        )}
        {!doctor && !props.linkSummary && <p className="detail">{words.empty}</p>}
      </>
    )
  }

  return (
    <>
      <div className="now-head">
        <div className="now-title">
          <h1>{words.title}</h1>
          <p className="now-date">{todayLabel(board.today)}</p>
        </div>
        {/* 見出しの行の「カレンダー」は下のナビと二重なので出さない（UX Design の P2）。 */}
      </div>
      <NowStrip board={board} />

      {!board.profile.configured && (
        <section className="now-card" data-setup-note>
          <p>{words.setupNote}</p>
          <button type="button" onClick={onSetup}>
            {words.setupButton}
          </button>
        </section>
      )}

      {/* 返事が届いたら、帯と返事のカードを一番上に。次の予定、まだ伝えていない文の順（図の⑨）。
          返事の前は、病院で聞いた予定を次にできることより先に出す（図の③）。 */}
      {latestReply ? (
        <>
          {replyBlock}
          {upcomingBlock}
          {nextBlock}
          {/* 結果の前に返事が届いても、医師の言葉の欄（結果を入れる入口）は残す。 */}
          {scheduleBlock}
        </>
      ) : (
        <>
          {scheduleBlock}
          {nextBlock}
          {upcomingBlock}
        </>
      )}

      {pendingCards}

      {/* 進めた案で変わったところを、いつでも見返せるようにする。 */}
      {board.impact && !proposal && (
        <button type="button" className="now-row" data-map-open onClick={onOpenMap}>
          {words.seeMap}
          <Chevron />
        </button>
      )}
      {countLines}
      <button type="button" className="now-input" data-now-input onClick={onAdd}>
        {words.input}
      </button>
    </>
  )
}
