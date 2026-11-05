import type { NextRow } from './api'
import { WORDS } from './words'

type Candidate = { day: string; certainty: string; cancelled?: boolean }

function kindWord(row: NextRow): string {
  const words = WORDS.now
  if (row.kind === '検査') return words.test
  if (row.kind === 'その他') return words.other
  if (row.cancelled) return words.cancelled
  return row.certainty === '確定' ? words.confirmed : words.candidate
}

function chipClass(certainty: string, cancelled?: boolean): string {
  if (cancelled) return 'chip'
  return certainty === '確定' ? 'chip confirmed' : 'chip likely'
}

// 分かれ道の行と候補日。③いま と ⑥案 で同じ形にする。札は検査／見込み／確定／取り消し。
export function PathRows({ paths, candidates }: { paths: NextRow[]; candidates: Candidate[] }) {
  const words = WORDS.now
  // 分かれ道の行に出ていない日は、消さずに残す。取り消しの日もここに出る。
  const onPaths = paths.map((row) => row.day)
  const others = candidates.filter((entry) => !onPaths.includes(entry.day))
  return (
    <>
      {paths.map((row) => (
        <div className="clause-row" key={`${row.clause}-${row.day}`} data-path={row.kind}>
          <span className="clause-text">「{row.clause}」</span>
          <span className="path-day">
            <span className="clause-days">{row.day}</span>
            <span className={row.kind === '検査' ? 'chip' : chipClass(row.certainty, row.cancelled)}>
              {kindWord(row)}
            </span>
          </span>
        </div>
      ))}
      {others.map((entry) => (
        <div className="clause-row" key={entry.day} data-candidate-day={entry.day}>
          <span className={entry.cancelled ? 'clause-day cancelled' : 'clause-day'}>
            {entry.day}
          </span>
          <span className={chipClass(entry.certainty, entry.cancelled)}>
            {entry.cancelled
              ? words.cancelled
              : entry.certainty === '確定'
                ? words.confirmed
                : words.candidate}
          </span>
        </div>
      ))}
    </>
  )
}
