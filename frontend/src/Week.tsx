import { useState } from 'react'
import type { CalendarMark, CalendarRow, Discharge, Item, Week, WeekEntry } from './api'
import { Chevron, PartIcon, shortTime, toneOf, whenOf, whoWord } from './parts'
import { replyText } from './Row'
import { isSentMessage } from './Send'
import { isRecord, placeOf, rankOf } from './turns'
import { WORDS } from './words'

type Row = Week['rows'][number]

function dayText(row: Row): string {
  return `${row.today ? `${WORDS.week.today} ` : ''}${row.date}（${row.weekday}）`
}

function plansOf(row: Row): WeekEntry[] {
  return row.cells.slice(1).flat()
}

// 行の2段目の状態。reply は返事の札（「夫：引き受けた」）で、時刻だけを添える。
function statusOf(item: Item, isSend: boolean): { text: string; reply: boolean } | null {
  const plain = (text: string) => ({ text, reply: false })
  if (item.released) return plain(WORDS.board.released)
  if (item.superseded) return plain(WORDS.row.supersededMark)
  if (isSend) return plain(WORDS.row.sendText)
  const group = placeOf(item, isSend)?.group
  if (group === 'waiting') return plain(WORDS.row.waitingMark)
  if (group === 'accepted' || group === 'timed' || group === 'difficult' || group === 'held') {
    return { text: replyText(item), reply: true }
  }
  if (!isRecord(item)) return null
  return plain(item.certainty === '確定' ? WORDS.week.record : WORDS.week.recordLikely)
}

// 2段目の文。予定の見出しの下なので、時刻だけだと予定の時刻に読める。返事の札の行の
// ほかは、時刻の前に出来事の語（入れた・文を作った・送った など）を付ける（やりとりの行と同じ決まり）。
function lineOf(item: Item, isSend: boolean): string {
  const status = statusOf(item, isSend)
  const when = whenOf(item)
  const at = shortTime(when?.at)
  if (status?.reply) return [status.text, at].filter(Boolean).join(' ')
  const stamp = at && when ? `${when.label} ${at}` : ''
  return [status?.text, stamp].filter(Boolean).join('・')
}

// 退院の列の内容。確定していない候補には必ず「見込み」を付ける。
function CandidateChip({ entry }: { entry: WeekEntry }) {
  if (entry.style === 'cancelled') return <span className="chip">{WORDS.board.released}</span>
  return entry.style === 'confirmed' ? (
    <span className="chip confirmed">{WORDS.board.certain}</span>
  ) : (
    <span className="chip likely">{WORDS.board.likely}</span>
  )
}

function PlanRow({
  text,
  item,
  isSend,
  onOpen,
}: {
  text: string
  item: Item
  isSend: boolean
  onOpen: (item: Item) => void
}) {
  const line = lineOf(item, isSend)
  return (
    <button
      type="button"
      className={`week-row ${toneOf(item.part)}`}
      data-week-item={item.id}
      onClick={() => onOpen(item)}
    >
      <PartIcon part={item.part} />
      <span className="week-row-body">
        <span className="week-row-text">{text}</span>
        {line && <span className="week-row-status">{line}</span>}
      </span>
    </button>
  )
}

const WINDOW_DAYS = 14
const WEEK_DAYS = 7
const WEEKDAY_HEAD = ['月', '火', '水', '木', '金', '土', '日']

// 表示している2週間の年月（図の⑩「2026年11月」）。サーバーが年を渡さないときは出さない。
function monthOf(shown: CalendarRow[]): string | null {
  const first = shown[0]?.iso
  const last = shown[shown.length - 1]?.iso
  if (!first || !last) return null
  const [year, month] = first.split('-').map(Number)
  const [endYear, endMonth] = last.split('-').map(Number)
  return WORDS.week.month(year, month, endYear, endMonth)
}

// サーバーの印が無い（古い応答の）ときだけ、今までの決め方で出す。
function oldMark(row: Row, testDays: string[]): CalendarMark | null {
  const entry = row.cells[0][0]
  if (entry) {
    const tone =
      entry.style === 'cancelled' || entry.style === 'confirmed' ? entry.style : 'likely'
    return { text: WORDS.map.mark(entry.text), tone }
  }
  return testDays.some((day) => day.startsWith(`${row.date}（`))
    ? { text: WORDS.now.test, tone: 'test' }
    : null
}

// 退院の候補日が1つも残っていないときだけ（Plan main）。日付の無い予定の段の先頭に置く。
function UndecidedDischarge({ discharge }: { discharge: Discharge }) {
  const words = WORDS.week
  return (
    <div className="week-discharge" data-discharge-undecided>
      <svg
        aria-hidden="true"
        className="week-discharge-icon"
        width="24"
        height="24"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M2.5 5v11M2.5 12.5h15V16M17.5 12.5V10a2 2 0 0 0-2-2H8.5v4.5" />
        <circle cx="5.5" cy="9.8" r="1.6" />
      </svg>
      <span className="week-discharge-body">
        <span className="week-discharge-text">
          <strong>{words.discharge}</strong>
          <span>{words.undecided}</span>
        </span>
        {discharge.previous.length > 0 && (
          <span className="week-discharge-sub">{words.previous(discharge.previous.join('、'))}</span>
        )}
      </span>
    </div>
  )
}

// カレンダー。描く内容はサーバーが台帳から決める（データの無い予定は描かない）。
// マスは日付と印だけ。文は切らず、下の「その日の予定」に原文で出す。
export function WeekList({
  week,
  items,
  testDays,
  errand,
  isSend,
  onOpen,
  onAdd,
}: {
  week: Week | null | undefined
  items: Item[]
  testDays: string[]
  errand: string | undefined
  isSend: (item: Item) => boolean
  onOpen: (item: Item) => void
  onAdd: () => void
}) {
  const byId = new Map(items.map((item) => [item.id, item]))
  const rows: CalendarRow[] = week?.rows ?? []
  const discharge = week?.discharge?.undecided ? week.discharge : null
  // 下書きの行は、文の途中で切らない。送る前は「保育園への下書き」、送った後は「保育園への連絡」。
  const sentWord = (item: Item) =>
    (item.status === '頼みたい' ? WORDS.week.draftOf : WORDS.week.sentOf)(
      whoWord(item.part, errand),
    )
  // 日付のある行は、その日の文から置いた行。1つの連絡が2つの日にまたがるので、用事全体の
  // 頼みごとの文を題にすると、別の日に貼りつく。日付の無い行だけ頼みごとの文を使う。
  const datedText = (item: Item, text: string) => (isSentMessage(item) ? sentWord(item) : text)
  const undatedText = (item: Item, text: string) =>
    isSentMessage(item) ? item.ask || sentWord(item) : text
  // ゴミ箱に入れた直後の行は、読み直す前でも出さない。
  const shown = (row: Row) =>
    plansOf(row).filter((entry) => entry.item_id && byId.has(entry.item_id))
  const hasAnything = (row: Row) => row.cells[0].length > 0 || shown(row).length > 0
  const todayIndex = Math.max(
    rows.findIndex((row) => row.today),
    0,
  )
  // 初めは今日。今日に予定が無ければ、次に予定のある日。
  const firstWithPlan = rows.findIndex((row, index) => index >= todayIndex && hasAnything(row))
  const [picked, setPicked] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const selected =
    rows.find((row) => row.date === picked) ??
    rows[firstWithPlan >= 0 ? firstWithPlan : todayIndex]
  const windowed = rows.slice(offset, offset + WINDOW_DAYS)
  const month = monthOf(windowed)

  // これからの予定：今日から先の、印か用事のある日（図の⑩の下。PO 2026-11-07）。
  const upcomingRows = rows
    .slice(todayIndex)
    .map((row) => {
      const mark = row.mark === undefined ? oldMark(row, testDays) : row.mark
      const errands = shown(row).filter((entry) => entry.part !== '病院').length
      return { row, mark, errands }
    })
    .filter(({ mark, errands }) => mark || errands > 0)

  const onCalendar = new Set(rows.flatMap((row) => shown(row).map((entry) => entry.item_id)))
  const undated = items
    .filter((item) => {
      const turn = placeOf(item, isSend(item))?.turn
      const moving = turn === 'mine' || turn === 'theirs'
      // 医療の問いは固定文なしで出さない。
      return moving && !item.medical_reply && !onCalendar.has(item.id)
    })
    .sort((a, b) => rankOf(a, isSend(a)) - rankOf(b, isSend(b)))
  const words = WORDS.week

  return (
    <section aria-label={words.title} className="week-view">
      {rows.length > 0 && (
        <>
          {month && (
            <h2 className="cal-month" data-calendar-month>
              {month}
            </h2>
          )}
          <div className="cal-nav">
            <button
              type="button"
              className="cal-step"
              disabled={offset === 0}
              onClick={() => setOffset((current) => current - WEEK_DAYS)}
            >
              <Chevron to="back" />
              {words.prev}
            </button>
            <span className="cal-range">{words.span}</span>
            <button
              type="button"
              className="cal-step"
              disabled={offset + WINDOW_DAYS >= rows.length}
              onClick={() => setOffset((current) => current + WEEK_DAYS)}
            >
              {words.next}
              <Chevron />
            </button>
          </div>
          <div className="cal-head" aria-hidden="true">
            {WEEKDAY_HEAD.map((day) => (
              <span key={day}>{day}</span>
            ))}
          </div>
          <ol className="cal-grid">
            {windowed.map((row) => {
              // 印の語はサーバーが種類から決める（採血・再検査・候補・取消・見直し）。
              const mark = row.mark === undefined ? oldMark(row, testDays) : row.mark
              const plans = shown(row)
              // 用件（着替え・迎え など）は語にせず点で出す。印のある日にも出す（図の12日・16日）。
              const hasErrand = plans.some((entry) => entry.part !== '病院')
              const dot = hasErrand || (plans.length > 0 && !mark)
              return (
                <li key={row.date}>
                  <button
                    type="button"
                    className={['cal-day', mark?.tone ?? '', row.today ? 'today' : ''].join(' ')}
                    aria-pressed={selected?.date === row.date}
                    aria-label={dayText(row)}
                    data-day={row.weekday}
                    data-cal-day={row.date}
                    onClick={() => setPicked(row.date)}
                  >
                    <span className="cal-date">{row.date.split('/')[1]}</span>
                    {mark && (
                      <span className="cal-mark" data-cal-mark={mark.tone}>
                        {WORDS.map.mark(mark.text)}
                      </span>
                    )}
                    {dot && <span className="cal-dot" data-cal-dot />}
                  </button>
                </li>
              )
            })}
          </ol>
        </>
      )}

      {/* 2026-11-07 PO：選んだ日だけでなく、これからの予定を常に出す。印と用事はサーバーが台帳から
          置いたものだけ（新しい計算は足さない）。押すとその日を選ぶ。 */}
      {rows.length > 0 && (
        <div className="week-day-block" data-upcoming-list>
          <h3>{words.upcomingTitle}</h3>
          {upcomingRows.length === 0 && !discharge && (
            <p className="week-empty">{words.upcomingNone}</p>
          )}
          {upcomingRows.length > 0 && (
            <ul className="upcoming-list">
              {upcomingRows.map(({ row, mark, errands }) => (
                <li key={row.date}>
                  <button
                    type="button"
                    className={`upcoming-row ${mark?.tone ?? ''}`}
                    data-upcoming-day={row.date}
                    aria-pressed={selected?.date === row.date}
                    onClick={() => setPicked(row.date)}
                  >
                    <span className="upcoming-date">{dayText(row)}</span>
                    <span className="upcoming-text">
                      {[
                        mark ? words.upcomingMark(WORDS.map.mark(mark.text), mark.tone) : null,
                        errands > 0 ? words.upcomingErrands(errands) : null,
                      ]
                        .filter(Boolean)
                        .join('・')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {discharge && (
            <p className="upcoming-undecided" data-upcoming-undecided>
              {words.discharge} {words.undecided}
            </p>
          )}
        </div>
      )}

      {selected && (
        <div className="week-day-block" data-week-selected>
          <div className="week-day-head">
            <h3>{words.dayPlans(dayText(selected))}</h3>
            {selected.cells[0].length > 0 && (
              <span className="week-candidate">
                {words.candidate}
                {selected.cells[0].map((entry) => (
                  <CandidateChip key={entry.text} entry={entry} />
                ))}
              </span>
            )}
          </div>
          {!hasAnything(selected) && <p className="week-empty">{words.empty}</p>}
          {shown(selected).map((entry) => {
            const item = byId.get(entry.item_id as string) as Item
            return (
              <PlanRow
                key={`${entry.item_id}`}
                text={datedText(item, entry.text)}
                item={item}
                isSend={isSend(item)}
                onOpen={onOpen}
              />
            )
          })}
        </div>
      )}

      {(week?.others.length || undated.length > 0 || discharge) && (
        <div className="week-day-block" data-undated>
          <h3>{words.undatedTitle}</h3>
          {discharge && <UndecidedDischarge discharge={discharge} />}
          {week?.others.map((text) => (
            <p className="detail" key={text}>
              {words.other(text)}
            </p>
          ))}
          {undated.map((item) => (
            <PlanRow
              key={item.id}
              text={undatedText(item, item.short || item.task_summary || item.text_original || '')}
              item={item}
              isSend={isSend(item)}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}

      <button type="button" className="quiet cal-add" data-cal-add onClick={onAdd}>
        {words.add}
      </button>
    </section>
  )
}
