import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Item } from './api'
import { Consent } from './Consent'
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
      setChoosing(false)
      setTimeOpen(false)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setLoad('missing')
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
  const showChoices = !finished && (!answered || choosing)
  const until = item.status !== '時刻つきで可' ? item.due : null
  // 押す前は4つとも同じ見た目。押した1つだけ塗る。
  const answerClass = (name: string) => (pressed === name ? 'answer pressed' : 'answer')
  // 夫が「何をしたら助かるか」を自分で見られるように。
  const others = item.owner
    ? allItems.filter(
        (other) =>
          other.id !== item.id &&
          other.kind === '行動' &&
          other.owner === item.owner &&
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
        <h1>{WORDS.one.title}</h1>
      </header>

      <main className="one">
        <p className="one-text">
          <Highlighted text={item.text_original ?? item.task_summary ?? ''} />
        </p>

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
          {answered && !choosing && (
            <>
              <p className="one-sent" role="status">
                {WORDS.one.sent(item.status_label ?? '')}
              </p>
              <button type="button" onClick={() => setChoosing(true)}>
                {WORDS.one.change}
              </button>
            </>
          )}
          {finished && (
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
                <span className="todo-title">{other.task_summary ?? other.text_original}</span>
                <span className="detail">{stateWord(other.status_label)}</span>
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
