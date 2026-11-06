import type { Item, Week } from './api'
import { stateWord, type StateWord } from './parts'
import { TALK_ANSWER, WORDS } from './words'

// 今、誰の手にあるか。相手別の見出しと進捗別の札は、この関数で数える。台帳は変えない。
export type Turn = 'mine' | 'theirs' | 'done'
export type Group =
  | 'difficult'
  | 'held'
  | 'send'
  | 'older'
  | 'todo'
  | 'timed'
  | 'accepted'
  | 'waiting'
export type Place = { turn: Turn; group: Group | null }

const MINE = (group: Group): Place => ({ turn: 'mine', group })
const THEIRS = (group: Group): Place => ({ turn: 'theirs', group })
const DONE: Place = { turn: 'done', group: null }

// どの列にも入らないものは null。記録と、まだ返事のない医療の問い。
export function placeOf(item: Item, isSend: boolean): Place | null {
  if (item.released) return DONE
  // 案の文に引き継がれた頼みごとは、その文が送る一覧にあるので、ここでは済の扱い。
  if (item.carried_into) return DONE
  const word = stateWord(item.status_label)
  if (!word) return null
  if (word === '済') return DONE
  if (item.medical_reply && word !== '返事あり') return null
  // 医療の問いが「難しい」でも、別の手を決める話にはしない。病棟に聞く用事のまま。
  if (item.status === '難しい') return MINE(item.medical_reply ? 'todo' : 'difficult')
  if (item.superseded) return MINE('older')
  // 保留は返事の1つ。「返事待ち」に入れると、相手別の表と数が合わない。
  if (item.status === '保留') return MINE('held')
  if (isSend) return MINE('send')
  if (word === '未送信') return MINE('todo')
  if (item.status === '時刻つきで可') return THEIRS('timed')
  if (item.status === '引き受けた') return THEIRS('accepted')
  return THEIRS('waiting')
}

// 夫が3択で「時間を相談したい」と答えた用事。親が記録した「保留」とは経緯の語で分ける。
export function wantsToTalk(item: Item): boolean {
  if (item.part !== '家族' || item.status !== '保留') return false
  const last = [...item.events]
    .reverse()
    .find((event) => event.label.endsWith('保留') || event.label.endsWith(TALK_ANSWER))
  return !!last?.label.endsWith(TALK_ANSWER)
}

// 記録：経緯として残すもの。行に並べず畳む。医療の問いは畳まない。
export function isRecord(item: Item): boolean {
  return (item.kind === '情報' || item.kind === '予定') && !item.medical_reply && !item.released
}

export function isFinished(item: Item): boolean {
  // 案の文に引き継がれた頼みごとも、やりとりの一覧では済の畳みへ（送るのは引き継いだ文）。
  return item.released || !!item.carried_into || stateWord(item.status_label) === '済'
}

// ⑪の1列のカードの順。図のとおり 対応中 → 未送信 → 親の用事・返事のうち親が決めるもの → 返事待ち。
// 済は畳む（UX Design の決定 (a)）。
export const CARD_GROUPS: Group[] = [
  'accepted',
  'timed',
  'send',
  'older',
  'todo',
  'difficult',
  'held',
  'waiting',
]

// 相手別の表の列。カードと上の1行と同じ置き場から数える（数が食い違わないように）。
export function columnOf(place: Place | null): StateWord | null {
  if (!place) return null
  if (place.turn === 'done') return '済'
  // 未送信は送る文（下書き）だけ。親の用事（下書きの無いもの）は列に入れない（UX Design の P1-2）。
  if (place.group === 'send' || place.group === 'older') return '未送信'
  if (place.group === 'todo') return null
  if (place.group === 'waiting') return '返事待ち'
  return '返事あり'
}

export type Chip = {
  word: string
  tone: 'unsent' | 'doing' | 'waiting' | 'reply' | 'done' | 'plain'
}

// カードの札。図の語（未送信・対応中）。引き受けた・時刻つきで可は「対応中」、難しい・保留は返事の語のまま。
// 札は短く保つ（題の幅を細くしない）。夫の「時間を相談したい」は2段目に出す。
export function chipOf(item: Item, place: Place | null): Chip | null {
  if (!place) return null
  const chips = WORDS.progress.chips
  if (place.turn === 'done') {
    return item.released
      ? { word: WORDS.board.released, tone: 'plain' }
      : { word: chips.done, tone: 'done' }
  }
  switch (place.group) {
    case 'send':
    case 'older':
      return { word: chips.unsent, tone: 'unsent' }
    case 'todo':
      // 下書きの無い親の用事は札を付けない（［済にする］だけ）。
      return null
    case 'waiting':
      return { word: chips.waiting, tone: 'waiting' }
    case 'accepted':
    case 'timed':
      return { word: chips.doing, tone: 'doing' }
    case 'difficult':
      return { word: chips.difficult, tone: 'reply' }
    case 'held':
      return { word: chips.held, tone: 'reply' }
    default:
      return null
  }
}

// 黄のカード「予定の見直し」。取り消しになった候補日に、まだ済んでいない用事が置かれている日。
// 日と用事の置き場はサーバーのカレンダー（台帳の原文から作る）をそのまま使い、日を作らない。
export type Review = { date: string; day: string; topics: string[]; clauses: string[] }

// 「16日の迎え」の「迎え」。その日の文に書かれた語だけを取る。数字の入る語（時刻）は取らない。
const TOPIC =
  /(?:\d{1,2}日|\d{1,2}\/\d{1,2}|[月火水木金土日]曜日?)の([^\d、。をはがもにのでとへや\s！？!?]{1,6})(?=[をはがもにのでとへや、。！？!?\s]|$)/

export function reviewsOf(calendar: Week | null | undefined, items: Item[]): Review[] {
  const rows = calendar?.rows ?? []
  const byId = new Map(items.map((item) => [item.id, item]))
  const todayIndex = Math.max(
    rows.findIndex((row) => row.today),
    0,
  )
  return rows.slice(todayIndex).flatMap((row) => {
    if (!row.cells[0]?.some((entry) => entry.style === 'cancelled')) return []
    const entries = row.cells.slice(1).flat().filter((entry) => {
      const item = entry.item_id ? byId.get(entry.item_id) : undefined
      return (
        !!item &&
        entry.style !== 'released' &&
        item.kind === '行動' &&
        !item.superseded &&
        !isFinished(item)
      )
    })
    if (entries.length === 0) return []
    const topics = entries
      .map((entry) => TOPIC.exec(entry.text)?.[1])
      .filter((topic): topic is string => !!topic)
    return [
      {
        date: row.date,
        day: `${row.date}（${row.weekday}）`,
        topics: [...new Set(topics)],
        clauses: [...new Set(entries.map((entry) => entry.text))],
      },
    ]
  })
}

const RANK: Record<Group, number> = {
  difficult: 0,
  held: 1,
  send: 2,
  todo: 3,
  older: 4,
  timed: 5,
  accepted: 5,
  waiting: 6,
}

// 行の並び。難しい→保留→送る→これから→引き受けた→返事待ち。医療の問いは用事と同じ段に。
export function rankOf(item: Item, isSend: boolean): number {
  const place = placeOf(item, isSend)
  if (!place) return item.medical_reply ? RANK.todo : 7
  return place.group ? RANK[place.group] : 8
}
