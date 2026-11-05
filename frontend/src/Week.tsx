import { useState } from 'react'
import type { Item, Week, WeekEntry } from './api'
import { PartIcon, toneOf, whoWord } from './parts'
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

// 行の2段目。状態があるときだけ出す。
function statusOf(item: Item, isSend: boolean): string | null {
  if (item.released) return WORDS.board.released
  if (item.superseded) return WORDS.row.supersededMark
  if (isSend) return WORDS.row.sendText
  const group = placeOf(item, isSend)?.group
  if (group === 'waiting') return WORDS.row.waitingMark
  if (group === 'accepted' || group === 'timed' || group === 'difficult' || group === 'held') {
    return replyText(item)
  }
  if (!isRecord(item)) return null
  return item.certainty === '確定' ? WORDS.week.record : WORDS.week.recordLikely
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
  const status = statusOf(item, isSend)
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
        {status && <span className="week-row-status">{status}</span>}
      </span>
    </button>
  )
}

const WINDOW_DAYS = 14
const WEEK_DAYS = 7
const WEEKDAY_HEAD = ['月', '火', '水', '木', '金', '土', '日']

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
  const rows = week?.rows ?? []
  // 下書きの行は、文の途中で切らない。頼みごとの文か「保育園への下書き」で出す。
  const rowText = (item: Item, text: string) =>
    isSentMessage(item) ? item.ask || WORDS.week.draftOf(whoWord(item.part, errand)) : text
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
          <div className="cal-nav">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset((current) => current - WEEK_DAYS)}
            >
              {words.prev}
            </button>
            <span className="cal-range">{words.span}</span>
            <button
              type="button"
              disabled={offset + WINDOW_DAYS >= rows.length}
              onClick={() => setOffset((current) => current + WEEK_DAYS)}
            >
              {words.next}
            </button>
          </div>
          <div className="cal-head" aria-hidden="true">
            {WEEKDAY_HEAD.map((day) => (
              <span key={day}>{day}</span>
            ))}
          </div>
          <ol className="cal-grid">
            {windowed.map((row) => {
              const entry = row.cells[0][0]
              const isTest = testDays.some((day) => day.startsWith(`${row.date}（`))
              const mark = entry
                ? WORDS.map.mark(entry.text)
                : isTest
                  ? WORDS.now.test
                  : null
              const hasPlan = shown(row).length > 0
              return (
                <li key={row.date}>
                  <button
                    type="button"
                    className={['cal-day', entry?.style ?? '', row.today ? 'today' : ''].join(' ')}
                    aria-pressed={selected?.date === row.date}
                    aria-label={dayText(row)}
                    data-day={row.weekday}
                    data-cal-day={row.date}
                    onClick={() => setPicked(row.date)}
                  >
                    <span className="cal-date">{row.date.split('/')[1]}</span>
                    {mark && <span className="cal-mark">{mark}</span>}
                    {hasPlan && !mark && <span className="cal-dot" />}
                  </button>
                </li>
              )
            })}
          </ol>
        </>
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
                text={rowText(item, entry.text)}
                item={item}
                isSend={isSend(item)}
                onOpen={onOpen}
              />
            )
          })}
        </div>
      )}

      {(week?.others.length || undated.length > 0) && (
        <div className="week-day-block" data-undated>
          <h3>{words.undatedTitle}</h3>
          {week?.others.map((text) => (
            <p className="detail" key={text}>
              {words.other(text)}
            </p>
          ))}
          {undated.map((item) => (
            <PlanRow
              key={item.id}
              text={rowText(item, item.short || item.task_summary || item.text_original || '')}
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
