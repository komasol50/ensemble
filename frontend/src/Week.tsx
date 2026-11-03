import type { Week } from './api'
import { WORDS } from './words'

// 週の表。読み取りだけ。描く内容はサーバーが台帳から決める（データの無い予定は描かない）。
export function WeekTable({ week }: { week: Week | null | undefined }) {
  if (!week) return null
  return (
    <section aria-label={WORDS.week.title}>
      <h2>{WORDS.week.title}</h2>
      <table className="week">
        <thead>
          <tr>
            <th scope="col" className="week-day">
              {WORDS.week.day}
            </th>
            {week.columns.map((column) => (
              <th scope="col" key={column}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {week.rows.map((row, index) => (
            <tr key={index} className={row.today ? 'today' : undefined}>
              <th scope="row" className="week-day">
                {row.label}
              </th>
              {row.cells.map((cell, column) => (
                <td key={column}>
                  {cell.map((entry) => (
                    <span key={entry.text} className={`week-entry ${entry.style}`}>
                      {entry.text}
                    </span>
                  ))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {week.others.map((text) => (
        <p className="detail" key={text}>
          {WORDS.week.other(text)}
        </p>
      ))}
    </section>
  )
}
