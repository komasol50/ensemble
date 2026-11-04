import type { Item } from './api'
import { stateWord } from './parts'

// 今、誰の手にあるか。相手別の見出しと進捗別の札は、この関数で数える。台帳は変えない。
export type Turn = 'mine' | 'theirs' | 'done'
export type Group = 'difficult' | 'send' | 'older' | 'todo' | 'timed' | 'accepted' | 'waiting'
export type Place = { turn: Turn; group: Group | null }

const MINE = (group: Group): Place => ({ turn: 'mine', group })
const THEIRS = (group: Group): Place => ({ turn: 'theirs', group })
const DONE: Place = { turn: 'done', group: null }

// どの列にも入らないものは null。記録と、まだ返事のない医療の問い。
export function placeOf(item: Item, isSend: boolean): Place | null {
  if (item.released) return DONE
  const word = stateWord(item.status_label)
  if (!word) return null
  if (word === '済') return DONE
  if (item.medical_reply && word !== '返事あり') return null
  if (item.status === '難しい') return MINE('difficult')
  if (item.superseded) return MINE('older')
  if (isSend) return MINE('send')
  if (word === 'これから') return MINE('todo')
  if (item.status === '時刻つきで可') return THEIRS('timed')
  if (item.status === '引き受けた') return THEIRS('accepted')
  return THEIRS('waiting')
}

// 記録：経緯として残すもの。行に並べず畳む。医療の問いは畳まない。
export function isRecord(item: Item): boolean {
  return (item.kind === '情報' || item.kind === '予定') && !item.medical_reply && !item.released
}

export function isFinished(item: Item): boolean {
  return item.released || stateWord(item.status_label) === '済'
}

const RANK: Record<Group, number> = {
  difficult: 0,
  send: 1,
  todo: 2,
  older: 3,
  timed: 4,
  accepted: 4,
  waiting: 5,
}

// 行の並び。難しい→送る→これから→引き受けた→返事待ち。医療の問いは用事と同じ段に。
export function rankOf(item: Item, isSend: boolean): number {
  const place = placeOf(item, isSend)
  if (!place) return item.medical_reply ? RANK.todo : 6
  return place.group ? RANK[place.group] : 7
}
