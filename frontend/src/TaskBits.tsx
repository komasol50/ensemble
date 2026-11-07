import type { Item } from './api'
import { Chevron } from './parts'
import { doneStatus, titleOf } from './Row'
import { canResend, reminderWho } from './tasks'
import { WORDS } from './words'

// 知らせ（v3.6・UX Design §3）：親が決めた時刻を過ぎて、済でないもの。「いま」の一番上に出す。
// 知らせる相手は親だけ。家族へ自動で送らない。音・催促の文・回数は出さない。
export function RemindBand({
  items,
  whenOf,
  busy,
  onResend,
  onDone,
  onClose,
}: {
  items: Item[]
  // 決めた時刻の語（今日なら「8:00」、ほかの日は「11/12 8:00」）と、10分を超えて過ぎたか。
  whenOf: (item: Item) => { text: string; past: boolean }
  busy: boolean
  onResend: (item: Item) => void
  onDone: (item: Item, status: string) => void
  onClose: (item: Item) => void
}) {
  if (items.length === 0) return null
  const words = WORDS.tasks
  const list = items.map((item) => {
        const what = item.task_summary || titleOf(item)
        const who = reminderWho(item)
        const done = doneStatus(item)
        return (
          <div className="remind-item" key={item.id} data-remind-item={item.id}>
            <p className="remind-what">{who ? words.remindFor(who, what) : words.remindOwn(what)}</p>
            <p className="remind-at">
              {words.remindAt(whenOf(item).text)}
              {whenOf(item).past && words.remindPast}
            </p>
            <div className="remind-actions">
              {canResend(item) && (
                <button type="button" disabled={busy} onClick={() => onResend(item)}>
                  {words.resend}
                </button>
              )}
              {done && (
                <button type="button" disabled={busy} onClick={() => onDone(item, done)}>
                  {words.done}
                </button>
              )}
              <button type="button" className="quiet" onClick={() => onClose(item)}>
                {words.close}
              </button>
            </div>
          </div>
        )
      })
  // 3件以上は1行に畳む（土台固めの指摘 P2）。
  return (
    <section className="remind-band" data-remind-band aria-label={words.remindHead}>
      {items.length >= 3 ? (
        <details data-remind-fold>
          <summary>{words.remindFold(items.length)}</summary>
          {list}
        </details>
      ) : (
        <>
          <h2>{words.remindHead}</h2>
          {list}
        </>
      )}
    </section>
  )
}

// 「いま」に足す短い行（UX Design §6）。0件の項は出さない。押すとメッセージの中のその一覧へ。
export function NowLine({
  label,
  text,
  onOpen,
  marker,
}: {
  label: string
  text: string
  onOpen: () => void
  marker: string
}) {
  if (!text) return null
  return (
    <button type="button" className="now-row now-count-line" data-now-line={marker} onClick={onOpen}>
      <span className="now-count-label">{label}</span>
      <span className="now-count-text">{text}</span>
      <Chevron />
    </button>
  )
}
