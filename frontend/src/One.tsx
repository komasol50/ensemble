import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Item } from './api'
import { Consent } from './Consent'
import { answerWord, rememberAnswer } from './device'
import { Highlighted, PartIcon, shortTime, stateWord, todayLabel } from './parts'
import { MEDICAL_FIXED_TEXT, WORDS } from './words'

// 家族の1件。LINEで届いたリンクから開く。アプリは要らない。
// URLは /m#家族コード/項目ID。# より後ろはサーバーの記録に残らない。
function parseHash(): { code: string; itemId: string } | null {
  const [code, itemId] = window.location.hash.slice(1).split('/')
  return code && itemId ? { code, itemId } : null
}

type Load = 'loading' | 'ready' | 'missing' | 'offline'

const FINISHED = new Set(['完了', '届いた'])
// 押した語と、それで入る状態。「◯時なら」は状態の語がそのまま押した語になる。
const ANSWER_STATUS: Record<string, string> = {
  持っていく: '引き受けた',
  手配する: '引き受けた',
  今日は難しい: '難しい',
}
const NOTES_SHOWN = 3

export function FamilyOne() {
  const [target, setTarget] = useState(parseHash)
  const [item, setItem] = useState<Item | null>(null)
  const [allItems, setAllItems] = useState<Item[]>([])
  const [load, setLoad] = useState<Load>(target ? 'loading' : 'missing')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [timeOpen, setTimeOpen] = useState(false)
  const [consentOpen, setConsentOpen] = useState(false)
  const [pressed, setPressed] = useState<string | null>(null)

  const receive = useCallback(
    (items: Item[]) => {
      setAllItems(items)
      const found = items.find((entry) => entry.id === target?.itemId)
      if (found && found.kind === '行動' && found.part === '家族') {
        setItem(found)
        setLoad('ready')
      } else {
        setLoad('missing')
      }
    },
    [target],
  )

  const read = useCallback(async () => {
    if (!target) return
    try {
      receive((await api.getBoard(target.code)).items)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setLoad('missing')
      else setLoad((current) => (current === 'ready' ? current : 'offline'))
    }
  }, [target, receive])

  useEffect(() => {
    void read()
  }, [read])

  // 一覧からほかの用事を開いたとき。
  useEffect(() => {
    const onHash = () => {
      setTarget(parseHash())
      setChoosing(false)
      setTimeOpen(false)
      setMessage(null)
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const answer = async (choice: string, when?: string) => {
    if (!target || busy) return
    setPressed(when ?? choice)
    setBusy(true)
    setMessage(null)
    try {
      receive((await api.reply(target.code, target.itemId, choice, when)).items)
      if (!when) rememberAnswer(target.itemId, choice)
      setChoosing(false)
      setTimeOpen(false)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setLoad('missing')
      // 取り消しや完了で答えられなくなった。今の状態を読み直す。
      else if (error instanceof ApiError && error.status === 422) void read()
      else setMessage(WORDS.board.failed)
    } finally {
      setBusy(false)
      setPressed(null)
    }
  }

  if (consentOpen) {
    return <Consent buttonLabel={WORDS.consent.close} onClose={() => setConsentOpen(false)} />
  }
  if (load === 'missing') {
    return (
      <main className="centered">
        <p>{WORDS.one.missing}</p>
      </main>
    )
  }
  if (load === 'loading') {
    return (
      <main className="centered">
        <p role="status">{WORDS.board.loading}</p>
      </main>
    )
  }
  if (load === 'offline' || !item) {
    return (
      <main className="centered">
        <p role="alert">{WORDS.board.offline}</p>
        <button type="button" className="primary" data-primary onClick={() => void read()}>
          {WORDS.board.retry}
        </button>
      </main>
    )
  }

  const finished = item.status === '完了' || item.status === '届いた'
  const answered =
    item.status === '引き受けた' || item.status === '難しい' || item.status === '時刻つきで可'
  const said = answerWord(item.id)
  const saidWord =
    said && ANSWER_STATUS[said] === item.status ? said : (item.status_label ?? '')
  const released = item.released
  const showChoices = !finished && !released && (!answered || choosing)
  // 取り消しの連絡が家族の用事なら、そちらへ進める。
  const newer = allItems.find(
    (other) => other.id === item.released_by && other.kind === '行動' && other.part === '家族',
  )
  const until = item.status !== '時刻つきで可' ? item.due : null
  // 押す前は4つとも同じ見た目。押した1つだけ塗る。
  const answerClass = (name: string) => (pressed === name ? 'answer pressed' : 'answer')
  // 夫が「何をしたら助かるか」を自分で見られるように。まだ送っていないものと、取り消したものは出さない。
  const others = item.owner
    ? allItems.filter(
        (other) =>
          other.id !== item.id &&
          other.kind === '行動' &&
          other.owner === item.owner &&
          other.status !== '頼みたい' &&
          !other.released &&
          !FINISHED.has(other.status ?? ''),
      )
    : []
  const notes = allItems
    .filter((other) => other.part === '親' && other.kind === '情報')
    .slice(-NOTES_SHOWN)

  return (
    <div className="one-screen">
      <header className="one-band tone-fam">
        <div className="one-band-top">
          <span className="part-head">
            <PartIcon part={WORDS.one.family} />
            {WORDS.one.family}
          </span>
          <span className="one-tools">
            <span className="detail">{todayLabel()}</span>
            <button
              type="button"
              className="quiet help"
              aria-label={WORDS.consent.openLabel}
              onClick={() => setConsentOpen(true)}
            >
              {WORDS.consent.open}
            </button>
          </span>
        </div>
        <h1>{released ? WORDS.one.releasedTitle : WORDS.one.title}</h1>
      </header>

      <main className="one">
        <p className={released ? 'one-text cancelled' : 'one-text'}>
          <Highlighted text={item.text_original ?? item.task_summary ?? ''} />
        </p>
        {item.outdated && !finished && !released && (
          <p className="one-changed" data-outdated>
            {WORDS.one.outdated}
          </p>
        )}

        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}

        {(item.location || until) && (
          <section className="one-facts">
            {item.location && (
              <>
                <h2>{WORDS.board.location}</h2>
                <p className="one-where">{item.location}</p>
              </>
            )}
            {until && (
              <>
                <h2>{WORDS.one.until}</h2>
                <p>{until}</p>
              </>
            )}
          </section>
        )}

        <div className="one-actions">
          {released && newer && (
            <button
              type="button"
              className="primary"
              data-primary
              onClick={() => {
                window.location.hash = `${target?.code}/${newer.id}`
              }}
            >
              {WORDS.one.newer}
            </button>
          )}
          {answered && !choosing && !released && (
            <>
              <p className="one-sent" role="status">
                {WORDS.one.sent(saidWord)}
              </p>
              <button type="button" onClick={() => setChoosing(true)}>
                {WORDS.one.change}
              </button>
            </>
          )}
          {finished && !released && (
            <p className="one-sent" role="status">
              {stateWord(item.status_label)}
            </p>
          )}
          {showChoices && <p className="detail">{WORDS.one.hint}</p>}
          {showChoices && (
            <div className="answers">
              {!timeOpen &&
                WORDS.one.answers.map((choice) => (
                  <button
                    key={choice}
                    type="button"
                    className={answerClass(choice)}
                    data-primary
                    aria-pressed={pressed === choice}
                    disabled={busy}
                    onClick={() => void answer(choice)}
                  >
                    {choice}
                  </button>
                ))}
              {!timeOpen && (
                <button
                  type="button"
                  className="answer"
                  data-primary
                  disabled={busy}
                  onClick={() => setTimeOpen(true)}
                >
                  {WORDS.one.withTime}
                </button>
              )}
              {timeOpen &&
                WORDS.one.times.map((when) => (
                  <button
                    key={when}
                    type="button"
                    className={answerClass(when)}
                    aria-pressed={pressed === when}
                    disabled={busy}
                    onClick={() => void answer(WORDS.one.withTime, when)}
                  >
                    {when}
                  </button>
                ))}
              {timeOpen && (
                <button
                  type="button"
                  className="answer quiet"
                  onClick={() => setTimeOpen(false)}
                >
                  {WORDS.add.back}
                </button>
              )}
            </div>
          )}
          <a className="board-link" href={`/b#${target?.code}`}>
            {WORDS.one.board}
          </a>
        </div>

        {others.length > 0 && (
          <section className="one-list" data-todo>
            <h2>{WORDS.one.todo(others.length)}</h2>
            {others.map((other) => (
              <a className="todo-row" key={other.id} href={`/m#${target?.code}/${other.id}`}>
                <span className="todo-title">
                  {other.short}
                  {other.outdated && <span className="todo-changed">{WORDS.one.outdated}</span>}
                </span>
                <span className="detail">
                  {other.status === '頼んだ'
                    ? WORDS.one.waiting
                    : WORDS.one.answered(other.status_label ?? '')}
                </span>
              </a>
            ))}
          </section>
        )}

        {notes.length > 0 && (
          <section className="one-list" data-parent-notes>
            <h2>{WORDS.one.notes}</h2>
            {notes.map((note) => (
              <p key={note.id}>
                <span className="detail">{shortTime(note.recorded_at)}　</span>
                {note.text_original}
              </p>
            ))}
          </section>
        )}

        <p className="fixed-note">{MEDICAL_FIXED_TEXT}</p>
      </main>
    </div>
  )
}
