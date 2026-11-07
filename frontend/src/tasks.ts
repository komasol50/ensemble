// やること（タスク管理 v3.6、2026-11-08 本人の決定「タスク管理もやる」）。
// 台帳の欄は足さない（CLAUDE.md の契約）。分類はコードの決まりで付け、親が直した分と知らせる時刻と
// 閉じた知らせは、この端末にだけ持つ（Plan main 了承。端末を替えると引き継がれない）。
// 期限（due）はアプリが作らない。親が入れた文のまま出す。日付と時刻の形で入れたときだけ、期限を過ぎたかを見る。
import type { Item } from './api'
import { stateWord } from './parts'
import { isSentMessage } from './Send'

export type Category = 1 | 2 | 3
export const CATEGORIES: Category[] = [1, 2, 3]

type Stored = {
  remind: Record<string, string>
  category: Record<string, Category>
  closed: Record<string, string>
}

const EMPTY: Stored = { remind: {}, category: {}, closed: {} }

function key(code: string): string {
  return `ensemble.tasks.${code}`
}

export function loadTasks(code: string): Stored {
  try {
    const raw = localStorage.getItem(key(code))
    if (!raw) return { remind: {}, category: {}, closed: {} }
    const parsed = JSON.parse(raw) as Partial<Stored>
    return {
      remind: parsed.remind ?? {},
      category: parsed.category ?? {},
      closed: parsed.closed ?? {},
    }
  } catch {
    return { ...EMPTY, remind: {}, category: {}, closed: {} }
  }
}

export function saveTasks(code: string, stored: Stored): void {
  try {
    localStorage.setItem(key(code), JSON.stringify(stored))
  } catch {
    // 残せなくても、今の画面はそのまま使える。
  }
}

// やることに当たるのは、行動と、親が入れた予定（子の世話の時刻など）だけ。夫のひとことと医師の言葉は入れない。
export function isTask(item: Item): boolean {
  if (item.id.startsWith('n-')) return false
  if (item.kind === '行動') return true
  return item.kind === '予定' && item.part === '親'
}

// 分類の決まり（土台固め sobani_36、Plan main v3.6）。語で判定しない。親が直した分を先に見る。
export function categoryOf(item: Item, stored: Stored): Category {
  const chosen = stored.category[item.id]
  if (chosen) return chosen
  if (item.part === '親' || item.part === '病院') return 1
  if (item.part === '家族') return 2
  return 3
}

export function isDone(item: Item): boolean {
  return stateWord(item.status_label) === '済'
}

// 自分の用事：親自身が持ち主のもの。
export function isOwn(item: Item): boolean {
  return item.part === '親' || item.owner === '親' || item.owner === '自分'
}

// 担当が決まった：引き受けた・〇時なら（時刻つきで可）・自分。
export function isDecided(item: Item): boolean {
  if (isOwn(item)) return true
  return item.status === '引き受けた' || item.status === '時刻つきで可'
}

// 「時刻つきで可」の due は家族が出した時刻で、期限ではない（今の行の表示と同じ）。
function deadlineText(item: Item): string | null {
  if (item.status === '時刻つきで可') return null
  return item.due?.trim() || null
}

// 知らせる時刻（端末）は「2026-11-12T19:00」。期限（台帳の自由な文字）は、日付と時刻で選ぶと「11/12 19:00」。
const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/
const MONTH_DAY = /^(\d{1,2})\/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$/
const WEEKDAYS = '日月火水木金土'

type Parts = { y: number; mo: number; d: number; h: number | null; mi: number | null }

function partsOf(text: string | null | undefined, today?: string): Parts | null {
  const value = text?.trim() ?? ''
  const iso = ISO.exec(value)
  if (iso) {
    const [, y, mo, d, h, mi] = iso
    return { y: +y, mo: +mo, d: +d, h: h ? +h : null, mi: mi ? +mi : null }
  }
  const md = MONTH_DAY.exec(value)
  if (md) {
    const [, mo, d, h, mi] = md
    const year = today ? Number(today.slice(0, 4)) : new Date().getFullYear()
    return { y: year, mo: +mo, d: +d, h: h ? +h : null, mi: mi ? +mi : null }
  }
  return null
}

export function parseWhen(text: string | null | undefined, today?: string): Date | null {
  const p = partsOf(text, today)
  if (!p) return null
  return new Date(p.y, p.mo - 1, p.d, p.h ?? 23, p.mi ?? 59)
}

// 日付と時刻の欄（2026-11-12T19:00）から、期限の文字（11/12 19:00）にする。
export function deadlineFromPicker(value: string): string {
  const p = partsOf(value)
  if (!p) return value
  const clock = p.h !== null ? ` ${p.h}:${String(p.mi ?? 0).padStart(2, '0')}` : ''
  return `${p.mo}/${p.d}${clock}`
}

// 「11/12（木）19:00」。今日なら「今日 19:00」。時刻が無ければ日付だけ。読めない文字はそのまま。
export function whenWord(text: string, today: string): string {
  const p = partsOf(text, today)
  if (!p) return text
  const date = new Date(p.y, p.mo - 1, p.d)
  const clock = p.h !== null ? `${p.h}:${String(p.mi ?? 0).padStart(2, '0')}` : ''
  const iso = `${p.y}-${String(p.mo).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`
  if (iso === today) return clock ? `今日 ${clock}` : '今日'
  const day = `${p.mo}/${p.d}（${WEEKDAYS[date.getDay()]}）`
  return clock ? `${day}${clock}` : day
}

// 画面の「いま」。日付はサーバーの今日（撮影の日付と台帳に揃える）、時刻は端末の時計。
export function nowOf(today: string | undefined): Date {
  const clock = new Date()
  if (!today) return clock
  const [y, mo, d] = today.split('-').map(Number)
  return new Date(y, mo - 1, d, clock.getHours(), clock.getMinutes())
}

export function isOverdue(item: Item, now: Date): boolean {
  if (isDone(item)) return false
  const at = parseWhen(deadlineText(item), isoDay(now))
  return !!at && at.getTime() < now.getTime()
}

export type TaskLine = { deadline: string | null; overdue: boolean; remind: string | null }

export function taskLineOf(item: Item, stored: Stored, today: string, now: Date): TaskLine {
  const deadline = deadlineText(item)
  const remind = stored.remind[item.id]
  return {
    deadline: deadline ? whenWord(deadline, today) : null,
    overdue: isOverdue(item, now),
    remind: remind ? whenWord(remind, today) : null,
  }
}

function isoDay(now: Date): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

function rank(item: Item, now: Date): number {
  if (isDone(item)) return 3
  if (isOverdue(item, now)) return 0
  return isDecided(item) ? 2 : 1
}

// 段の中の順：期限を過ぎた → 担当が決まっていない → 決まった → 済。同じ所は期限の早い順、期限なしは後ろ。
export function sortTasks(items: Item[], now: Date): Item[] {
  return [...items].sort((a, b) => {
    const byRank = rank(a, now) - rank(b, now)
    if (byRank !== 0) return byRank
    const at = parseWhen(deadlineText(a), isoDay(now))?.getTime() ?? Infinity
    const bt = parseWhen(deadlineText(b), isoDay(now))?.getTime() ?? Infinity
    return at - bt
  })
}

export type TaskSummary = { undecided: number; accepted: number; overdue: number }

export function summaryOf(items: Item[], now: Date): TaskSummary {
  const open = items.filter((item) => isTask(item) && !isDone(item) && !item.released)
  return {
    undecided: open.filter((item) => !isDecided(item) && !isOverdue(item, now)).length,
    accepted: open.filter(
      (item) => !isOwn(item) && isDecided(item) && !isOverdue(item, now),
    ).length,
    overdue: open.filter((item) => isOverdue(item, now)).length,
  }
}

// 知らせ：親が決めた時刻を過ぎ、済でなく、この端末で閉じていないもの。アプリは時刻を作らない。
export function dueReminders(items: Item[], stored: Stored, now: Date): Item[] {
  return items.filter((item) => {
    const at = parseWhen(stored.remind[item.id])
    if (!at || at.getTime() > now.getTime()) return false
    if (isDone(item) || item.released) return false
    return !stored.closed[item.id]
  })
}

// 知らせの1行の相手。家族は頼める人の語、ほかは相手の名前。自分の用事は相手を出さない。
export function reminderWho(item: Item): string | null {
  if (isOwn(item)) return null
  if (item.part === '家族') return item.owner || '家族'
  return item.part
}

export function canResend(item: Item): boolean {
  return !isOwn(item) && (isSentMessage(item) || item.part === '家族')
}

// 1行の置き場（要約の1行と同じ数え方）。検査と並べ方で使う。
export function taskState(
  item: Item,
  now: Date,
): 'done' | 'overdue' | 'undecided' | 'own' | 'accepted' {
  if (isDone(item)) return 'done'
  if (isOverdue(item, now)) return 'overdue'
  if (!isDecided(item)) return 'undecided'
  return isOwn(item) ? 'own' : 'accepted'
}

// 知らせの2段目の時刻：今日なら「8:00」、ほかの日は「11/12 8:00」。過ぎて10分を超えたら past。
export function remindWhen(
  value: string,
  today: string,
  now: Date,
): { text: string; past: boolean } {
  const p = partsOf(value, today)
  if (!p) return { text: value, past: false }
  const clock = p.h !== null ? `${p.h}:${String(p.mi ?? 0).padStart(2, '0')}` : ''
  const iso = `${p.y}-${String(p.mo).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`
  const text = iso === today ? clock || '今日' : `${p.mo}/${p.d}${clock ? ` ${clock}` : ''}`
  const at = new Date(p.y, p.mo - 1, p.d, p.h ?? 0, p.mi ?? 0).getTime()
  return { text, past: now.getTime() - at > 10 * 60 * 1000 }
}
