import type { Branch, DoctorView } from './api'
import { WORDS } from './words'

// 医師の言葉の検査の語。次の段の引用から取る（「再検査なら17日」→ 再検査）。
const TEST_WORD = /再検査|採血|検査/

export type Upcoming = { test: string | null; candidates: string | null; undecided: boolean }

// 案を進めた後の「次の予定」。サーバーが台帳から作った次の段と候補日だけで描く。
// 残っている候補日は見込みのまま出す。「退院日は未定」は候補日が1つも残っていないときだけ（Plan main）。
export function upcomingOf(doctor: DoctorView | null | undefined): Upcoming | null {
  if (!doctor) return null
  const step = doctor.paths.find((row) => row.kind === '検査' && !row.cancelled)
  const test = step
    ? WORDS.schedule.upcoming(step.day, step.clause.match(TEST_WORD)?.[0] ?? '検査')
    : null
  const kept = doctor.candidates.filter((row) => !row.cancelled).map((row) => row.day)
  const candidates = kept.length > 0 ? WORDS.schedule.candidates(kept.join('・')) : null
  const undecided = doctor.candidates.length > 0 && kept.length === 0
  return test || candidates || undecided ? { test, candidates, undecided } : null
}

// 送った文の中で、取り消しになった候補日を書いた一文。夫の画面の帯に、そのまま引く（言い換えない）。
export function heldSentence(text: string | null | undefined, branches: Branch[]): string | null {
  const latest = branches[branches.length - 1]
  if (!text || !latest) return null
  const days = latest.candidate_days.filter((day) => day.cancelled).map((day) => day.day)
  if (days.length === 0) return null
  const sentences = text.split(/(?<=[。！？!?])|\n/).map((part) => part.trim())
  return sentences.find((sentence) => days.some((day) => sentence.includes(day))) ?? null
}
