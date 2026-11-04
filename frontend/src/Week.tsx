import type { Item, Week, WeekEntry } from './api'
import { PartIcon, toneOf } from './parts'
import { replyText } from './Row'
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
  if (group === 'waiting') return item.status === '保留' ? replyText(item) : WORDS.row.waitingMark
  if (group === 'accepted' || group === 'timed' || group === 'difficult') return replyText(item)
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

function scrollToDay(index: number): void {
  const quiet = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  document
    .querySelector(`[data-week-days~="${index}"]`)
    ?.scrollIntoView({ behavior: quiet ? 'auto' : 'smooth', block: 'start' })
}

// 週の予定。描く内容はサーバーが台帳から決める（データの無い予定は描かない）。
export function WeekList({
  week,
  items,
  isSend,
  onOpen,
}: {
  week: Week | null | undefined
  items: Item[]
  isSend: (item: Item) => boolean
  onOpen: (item: Item) => void
}) {
  const byId = new Map(items.map((item) => [item.id, item]))
  const rows = week?.rows ?? []
  // ゴミ箱に入れた直後の行は、読み直す前でも出さない。
  const shown = (row: Row) => plansOf(row).filter((entry) => entry.item_id && byId.has(entry.item_id))
  const isEmpty = (row: Row) => row.cells[0].length === 0 && shown(row).length === 0

  // 予定の無い日は続けて1行にまとめる。
  const runs: { rows: Row[]; first: number }[] = []
  rows.forEach((row, index) => {
    const last = runs[runs.length - 1]
    if (isEmpty(row) && last && isEmpty(last.rows[0])) last.rows.push(row)
    else runs.push({ rows: [row], first: index })
  })

  const onWeek = new Set(rows.flatMap((row) => shown(row).map((entry) => entry.item_id)))
  const undated = items
    .filter((item) => {
      const turn = placeOf(item, isSend(item))?.turn
      const moving = turn === 'mine' || turn === 'theirs'
      // 医療の問いは固定文なしで出さない。
      return moving && !item.medical_reply && !onWeek.has(item.id)
    })
    .sort((a, b) => rankOf(a, isSend(a)) - rankOf(b, isSend(b)))

  return (
    <section aria-label={WORDS.week.title} className="week-view">
      {rows.length > 0 && (
        <ol className="week-strip week-nav">
          {rows.map((row, index) => {
            const entry = row.cells[0][0]
            const classes = [
              'strip-day',
              entry?.style ?? '',
              row.today ? 'today' : '',
              shown(row).length > 0 ? 'has-plan' : '',
            ]
            return (
              <li key={index} className={classes.filter(Boolean).join(' ')}>
                <button
                  type="button"
                  data-day={row.weekday}
                  aria-label={dayText(row)}
                  onClick={() => scrollToDay(index)}
                >
                  <span className="strip-label">{row.weekday}</span>
                  <span className="strip-mark">{row.date.split('/')[1]}</span>
                  <span className="strip-dot" />
                </button>
              </li>
            )
          })}
        </ol>
      )}
      {week?.others.map((text) => (
        <p className="detail" key={text}>
          {WORDS.week.other(text)}
        </p>
      ))}

      {runs.map(({ rows: days, first }) => {
        const anchors = days.map((_, offset) => first + offset).join(' ')
        const head = days[0]
        if (isEmpty(head)) {
          const range =
            days.length === 1
              ? dayText(head)
              : `${dayText(head)}〜${dayText(days[days.length - 1])}`
          return (
            <p className="week-empty" key={first} data-week-days={anchors}>
              {range}
              {'　'}
              {WORDS.week.empty}
            </p>
          )
        }
        return (
          <div className="week-day-block" key={first} data-week-days={anchors}>
            <div className="week-day-head">
              <h3>{dayText(head)}</h3>
              {head.cells[0].length > 0 && (
                <span className="week-candidate">
                  {WORDS.week.candidate}
                  {head.cells[0].map((entry) => (
                    <CandidateChip key={entry.text} entry={entry} />
                  ))}
                </span>
              )}
            </div>
            {shown(head).map((entry) => {
              const item = byId.get(entry.item_id as string) as Item
              return (
                <PlanRow
                  key={`${entry.item_id}`}
                  text={entry.text}
                  item={item}
                  isSend={isSend(item)}
                  onOpen={onOpen}
                />
              )
            })}
          </div>
        )
      })}

      {undated.length > 0 && (
        <details className="fold small" data-undated>
          <summary>{WORDS.week.undated(undated.length)}</summary>
          {undated.map((item) => (
            <PlanRow
              key={item.id}
              text={item.short || item.task_summary || item.text_original || ''}
              item={item}
              isSend={isSend(item)}
              onOpen={onOpen}
            />
          ))}
        </details>
      )}
    </section>
  )
}
