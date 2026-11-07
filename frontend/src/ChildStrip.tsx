import type { Board } from './api'
import { WORDS } from './words'
import './child.css'

const DAY_MS = 24 * 60 * 60 * 1000

// 入院した日を1日目として、画面の今日（board.today＝サーバーの今日。撮影用サーバーは差し替える）まで数える。
// 端末の時計は使わない。入院した日が今日より後なら数えない。
export function admittedDay(admittedOn: string | null | undefined, today: string): number | null {
  if (!admittedOn) return null
  const from = Date.parse(`${admittedOn}T00:00:00Z`)
  const to = Date.parse(`${today}T00:00:00Z`)
  if (Number.isNaN(from) || Number.isNaN(to)) return null
  const n = Math.round((to - from) / DAY_MS) + 1
  return n >= 1 ? n : null
}

// 「いま」の見出し行の下の1行。入院した日が無ければ出さない。退院までの日数は出さない。
export function NowStrip({ board }: { board: Board }) {
  const n = admittedDay(board.admitted_on, board.today)
  if (n === null) return null
  return (
    <p className="now-strip" data-now-strip>
      {board.nickname ? WORDS.child.withName(board.nickname, n) : WORDS.child.day(n)}
    </p>
  )
}

// 夫の画面（リンクから開いたボード）で、帯「家族」の右に置く小さな語。あだ名が無ければ出さない。
export function CareFor({ board }: { board: Board | null | undefined }) {
  if (!board?.nickname) return null
  return (
    <span className="care-for" data-care-for>
      {WORDS.child.careFor(board.nickname)}
    </span>
  )
}
