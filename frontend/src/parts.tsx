import type { ReactNode } from 'react'
import type { Item, ItemEvent } from './api'
import { DECISION_WORDS } from './words'

// 相手ごとの色とアイコン、状態の語。文字を読まなくても、どの相手かが分かるようにする。

const TONES: Record<string, string> = {
  病院: 'tone-hosp',
  家族: 'tone-fam',
  '保育園・預け先': 'tone-care',
  職場: 'tone-work',
  支援: 'tone-help',
}

// 「自分のこと」には色を付けない。
export function toneOf(part: string): string {
  return TONES[part] ?? ''
}

// 相手に頼むことを、案の種類からコードで短く書く。AIの文ではない。
export const VERBS: Record<string, string> = {
  頼む: 'に頼む',
  伝える: 'に伝える',
  確認する: 'に確認する',
  空きを確認する: 'に空きを確認する',
}

// 画面に出す相手の呼び方。頼める人がいない家庭では「夫」を出さない。
// 頼める人の札を、画面の呼びかけの語にする（夫・パートナー→夫、妻・パートナー→妻）。値は変えない。
export function personWord(label: string | null | undefined): string {
  if (!label) return ''
  if (label === '夫・パートナー') return '夫'
  if (label === '妻・パートナー') return '妻'
  return label
}

export function whoWord(part: string, errand?: string): string {
  if (part === '家族' && errand) return personWord(errand)
  return part === '保育園・預け先' ? '保育園' : part
}

// 入れた文を引くときのかぎかっこ。『』で始まる文に「」を重ねない。
export function inQuotes(text: string): string {
  return text.startsWith('『') ? text : `「${text}」`
}

// 仮の線画。本番のアイコンは後で差し替える。
const ICONS: Record<string, ReactNode> = {
  病院: <path d="M4 17V6h12v11M2 17h16M8 17v-4h4v4M10 8v4M8 10h4" />,
  家族: <path d="M3 10l7-6 7 6M5 9v8h10V9M8.5 17v-4h3v4" />,
  '保育園・預け先': (
    <>
      <circle cx="10" cy="11" r="5.5" />
      <circle cx="5.5" cy="6" r="2" />
      <circle cx="14.5" cy="6" r="2" />
      <path d="M8 12.5c1 1 3 1 4 0" />
    </>
  ),
  職場: (
    <>
      <rect x="3" y="7" width="14" height="10" rx="2" />
      <path d="M7 7V5h6v2M3 11h14" />
    </>
  ),
  支援: <path d="M10 17s-6-4-6-8.5A3.5 3.5 0 0 1 10 6a3.5 3.5 0 0 1 6 2.5C16 13 10 17 10 17z" />,
}

export function PartIcon({ part }: { part: string }) {
  const icon = ICONS[part]
  if (!icon) return null
  return (
    <span className="part-icon">
      <svg
        aria-hidden="true"
        width="18"
        height="18"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {icon}
      </svg>
    </span>
  )
}

const CHEVRONS = {
  next: 'M7 4l6 6-6 6',
  back: 'M12 4l-6 6 6 6',
  up: 'M4 13l6-6 6 6',
  down: 'M4 7l6 6 6-6',
}

export function Chevron({ to = 'next' }: { to?: keyof typeof CHEVRONS }) {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 20 20">
      <path
        d={CHEVRONS[to]}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// 状態の語は4つだけ（相手別の表の列）。図の語に揃える。返事の中身（引き受けた など）は、語の隣に別に出す。
// 引き受けた・時刻つきで可は「返事あり」の列に数え、⑪のカードの札は「対応中」にする（turns.ts の chipOf）。
export const STATE_WORDS_IN_ORDER = ['未送信', '返事待ち', '返事あり', '済'] as const
export type StateWord = (typeof STATE_WORDS_IN_ORDER)[number]

const STATE_WORDS: Record<string, StateWord> = {
  未依頼: '未送信',
  未確認: '未送信',
  返答待ち: '返事待ち',
  完了: '済',
  届いた: '済',
  答えあり: '済',
}

// サーバーの表示名から、4つの語へまとめる。残りは返事の中身。
export function stateWord(label: string | null | undefined): StateWord | null {
  if (!label) return null
  return STATE_WORDS[label] ?? '返事あり'
}

// 1行の文の中に出すとき。返事のときは中身も添える。
export function stateText(label: string | null | undefined): string {
  const word = stateWord(label)
  if (!word) return ''
  return word === '返事あり' ? `${word}（${label}）` : word
}

const DATE_WORDS =
  '\\d{1,2}月\\d{1,2}日|\\d{1,2}/\\d{1,2}|\\d{1,2}:\\d{2}|\\d{1,2}時半?|[月火水木金土日]曜日?|今日|明日|明後日|今夜|今朝|今週|来週'
const DATE_PATTERN = new RegExp(`^(${DATE_WORDS})$`)
const DATES_ONLY = new RegExp(`(${DATE_WORDS})`)
// 「延長保育」は制度の名前なので印を付けない。「延長をお願い」など、決める言葉にだけ付ける。
const DECISIONS = DECISION_WORDS.map((word) => (word === '延長' ? '延長(?!保育)' : word)).join('|')
const DATES_AND_DECISIONS = new RegExp(`(${DATE_WORDS}|${DECISIONS})`)

// 下書きの文。日付は太字にする。marks を付けると、決める言葉に薄い印を付ける。
export function Highlighted({ text, marks = false }: { text: string; marks?: boolean }) {
  return (
    <>
      {text.split(marks ? DATES_AND_DECISIONS : DATES_ONLY).map((part, index) => {
        // split は区切りも残すので、奇数番目が当たった語。
        if (index % 2 === 0) return part
        return DATE_PATTERN.test(part) ? (
          <strong key={index}>{part}</strong>
        ) : (
          <mark key={index} className="decide-word">
            {part}
          </mark>
        )
      })}
    </>
  )
}

const WEEKDAYS = '日月火水木金土'

// サーバーの今日（YYYY-MM-DD）を、その土地の暦の日にする。読めなければ端末の今日。
function dayFrom(today: string | null | undefined): Date {
  const [year, month, day] = (today ?? '').split('-').map(Number)
  return year && month && day ? new Date(year, month - 1, day) : new Date()
}

export function todayLabel(today?: string | null): string {
  const now = dayFrom(today)
  return `${now.getMonth() + 1}月${now.getDate()}日（${WEEKDAYS[now.getDay()]}）`
}

// 記録した日時を短く出す。読めない値は出さない。
export function shortDay(iso: string | null | undefined): string {
  const date = iso ? new Date(iso) : null
  if (!date || Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1}/${date.getDate()}`
}

export function shortTime(iso: string | null | undefined): string {
  const day = shortDay(iso)
  if (!day) return ''
  const date = new Date(iso as string)
  return `${day} ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`
}

// 最後に送ってから何日か（暦日）。送った記録が無ければ0。
export function daysSinceSent(item: Item, today?: string | null): number {
  const now = dayFrom(today)
  const sent = [...item.events].reverse().find((event) => event.label === '送った')
  const at = sent ? new Date(sent.at) : null
  if (!at || Number.isNaN(at.getTime())) return 0
  const dayOf = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  return Math.round((dayOf(now) - dayOf(at)) / 86400000)
}

// 親が「もう一度送る」を押した記録。夫の画面には出さない（催促に見えるため。B6）。
export const RESHARED = 'もう一度共有した'
export function withoutReshare(item: Item): Item {
  return item.events.some((event) => event.label === RESHARED)
    ? { ...item, events: item.events.filter((event) => event.label !== RESHARED) }
    : item
}

// その項目に最後に起きたこと。一覧にはこれだけを出す。
export function whenOf(item: Item): ItemEvent | undefined {
  return item.events[item.events.length - 1]
}

// いくつかの項目の中で、一番新しい出来事。
export function latestWhen(items: Item[]): ItemEvent | undefined {
  const timeOf = (event: ItemEvent) => new Date(event.at).getTime() || 0
  return items
    .map(whenOf)
    .filter((event): event is ItemEvent => !!event)
    .reduce<ItemEvent | undefined>(
      (latest, event) => (!latest || timeOf(event) > timeOf(latest) ? event : latest),
      undefined,
    )
}

// 起きた順の履歴。入れた→送った→返事→済。
export function Timeline({ item }: { item: Item }) {
  if (item.events.length === 0) return null
  return (
    <ol className="timeline">
      {item.events.map((event, index) => (
        <li key={index}>
          <time dateTime={event.at}>{shortTime(event.at)}</time> {event.label}
        </li>
      ))}
    </ol>
  )
}
