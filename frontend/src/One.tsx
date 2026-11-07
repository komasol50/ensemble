import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Board, type Item } from './api'
import { Consent } from './Consent'
import { answerWord, device, rememberAnswer } from './device'
import { Highlighted, PartIcon, shortTime, stateWord, todayLabel, whenOf, withoutReshare } from './parts'
import { heldSentence, upcomingOf } from './schedule'
import { wantsToTalk } from './turns'
import { MEDICAL_FIXED_TEXT, TALK_ANSWER, WORDS } from './words'

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
  引き受ける: '引き受けた',
  むずかしい: '難しい',
  今日はむずかしい: '難しい',
  時間を相談したい: '保留',
}

// 1つ目の答えの語（2026-11-07 UX Design）：ありか（置き場所）のある物なら「持っていく」、
// ほか（迎え・手配・まとめたお願い）は「引き受ける」。
function answersFor(item: Item): string[] {
  const [, talk, hard] = WORDS.one.answers
  return [item.location ? WORDS.one.bring : WORDS.one.accept, talk, hard]
}
const NOTE_MAX = 40
const NOTES_SHOWN = 3

// 題に使う頼める人の語。設定の札（夫・パートナー／祖父母）を、呼びかけの短い語にする。
function errandWord(label: string): string {
  if (label === '夫・パートナー') return '夫'
  if (label === '祖父母') return '子の祖父母'
  return label
}

export function FamilyOne() {
  const [target, setTarget] = useState(parseHash)
  const [item, setItem] = useState<Item | null>(null)
  const [allItems, setAllItems] = useState<Item[]>([])
  const [load, setLoad] = useState<Load>(target ? 'loading' : 'missing')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [consentOpen, setConsentOpen] = useState(false)
  const [pressed, setPressed] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [today, setToday] = useState<string | null>(null)
  // 次の予定の帯と、取り消しになった日の一文のため（台帳から。夫の画面でAIは呼ばない）。
  const [schedule, setSchedule] = useState<Pick<Board, 'doctor' | 'branches'> | null>(null)
  // 題を「夫へ、病室からのお願い」にするため。頼める人の語（設定の札）。
  const [errand, setErrand] = useState<string | null>(null)
  // 帯「家族」の右の「〇〇の付き添い」（子のあだ名。無ければ出さない）。
  const [nickname, setNickname] = useState<string | null>(null)

  const receive = useCallback(
    (items: Item[], board?: Board) => {
      if (board) {
        setSchedule({ doctor: board.doctor, branches: board.branches })
        setErrand(board.profile.errand_people[0]?.label ?? null)
        setNickname(board.nickname ?? null)
      }
      // 親の「もう一度送る」の記録は夫の画面に出さない（B6・QA P1）。
      setAllItems(items.map(withoutReshare))
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
      const board = await api.getBoard(target.code)
      setToday(board.today)
      receive(board.items, board)
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
      // 何も入れなければ送らない。前のひとことを消さず、押し直しで「新」を付けない。
      const board = await api.reply(target.code, target.itemId, choice, when, note.trim() || undefined)
      receive(board.items, board)
      if (!when) rememberAnswer(target.itemId, choice)
      setChosen(null)
      setNote('')
      setChoosing(false)
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
    item.status === '引き受けた' ||
    item.status === '難しい' ||
    item.status === '時刻つきで可' ||
    item.status === '保留'
  const said = answerWord(item.id)
  const saidWord =
    said && ANSWER_STATUS[said] === item.status
      ? said
      : wantsToTalk(item)
        ? TALK_ANSWER
        : (item.status_label ?? '')
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
    .reverse()
  // 親が「もう一度送る」を押した記録は、夫の画面には出さない（夫の画面は変えない）。
  const last = [...item.events].reverse().find((event) => event.label !== 'もう一度共有した')
  const asked = !!item.ask && !released
  const held = heldSentence(item.text_original, schedule?.branches ?? [])
  // 次の予定は、結果を入れた後だけ（親の「いま」と同じ）。結果の前は「再検査なら17日」も条件つきの候補なので、
  // 「次は17日に再検査／退院日は未定」と確定の予定のように出さない（デモ動画作成の指摘、医療の線）。
  const upcoming =
    schedule?.doctor && !schedule.doctor.open ? upcomingOf(schedule.doctor) : null

  return (
    <div className="one-screen">
      <header className="one-band tone-fam">
        <div className="one-band-top">
          <span className="part-head">
            <PartIcon part={WORDS.one.family} />
            {WORDS.one.family}
          </span>
          <span className="one-tools">
            <span className="detail">{todayLabel(today)}</span>
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
        {/* あだ名は帯の上の行に入れると 320〜390px で折れるので、題の上の1行にする（QA Test の P1）。 */}
        {nickname && (
          <p className="care-for" data-care-for>
            {WORDS.child.careFor(nickname)}
          </p>
        )}
        <h1>
          {released
            ? WORDS.one.releasedTitle
            : errand
              ? WORDS.one.titleTo(errandWord(errand))
              : WORDS.one.title}
        </h1>
        {/* 2026-11-07 PO：夫も全体を見て自分から動けるように、題のすぐ下に置く。 */}
        <a
          className="board-link"
          data-one-board
          href={`/b#${target?.code}`}
          onClick={() => {
            if (target) device.setFromLink(target.code)
          }}
        >
          {WORDS.one.board}
        </a>
      </header>

      <main className="one">
        {asked ? (
          // 図の⑧：大きな題（頼みごとの引用）、場所、取り消しになった日の一文。文の全体は畳む。
          <>
            <h2 className="one-ask">{item.ask}</h2>
            {item.location && (
              <p className="one-where-line">
                {WORDS.board.location}：{item.location}
              </p>
            )}
            {held && held !== item.ask && (
              <p className="one-held" data-one-held>
                {held}
              </p>
            )}
            <details className="one-whole" data-one-whole>
              <summary>{WORDS.schedule.whole}</summary>
              <p className="one-text">
                <Highlighted text={item.text_original ?? item.task_summary ?? ''} />
              </p>
            </details>
          </>
        ) : (
          <p className={released ? 'one-text cancelled' : 'one-text'}>
            <Highlighted text={item.text_original ?? item.task_summary ?? ''} />
          </p>
        )}
        {last && (
          <p className="detail" data-last-event>
            <time dateTime={last.at}>{shortTime(last.at)}</time>{' '}
            {!WORDS.one.innerEvents.includes(last.label) && last.label}
          </p>
        )}
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

        {((item.location && !asked) || until) && (
          <section className="one-facts">
            {item.location && !asked && (
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
              <button
                type="button"
                onClick={() => {
                  // 前の答えを選んだ状態で開く（ひとことだけ直して送れる。UX Design の P2 ⑥）。
                  const before = answersFor(item).find((choice) => ANSWER_STATUS[choice] === item.status)
                  if (before) setChosen(before)
                  setChoosing(true)
                }}
              >
                {WORDS.one.change}
              </button>
            </>
          )}
          {finished && !released && (
            <p className="one-sent" role="status">
              {stateWord(item.status_label)}
            </p>
          )}
          {showChoices && <h2>{WORDS.one.choose}</h2>}
          {showChoices && (
            <div className="answers">
              {answersFor(item).map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className={answerClass(choice)}
                  aria-pressed={chosen === choice}
                  disabled={busy}
                  onClick={() => setChosen(choice)}
                >
                  <span className="after-mark" aria-hidden="true" />
                  {choice}
                </button>
              ))}
            </div>
          )}
          {showChoices && (
            <label className="one-note">
              {WORDS.one.note}
              <input
                type="text"
                maxLength={NOTE_MAX}
                value={note}
                placeholder={WORDS.one.notePlaceholder}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
          )}
          {showChoices && (
            // 下に留めたボタンの背に、画面の地の色の帯を敷く。ひとことの欄を透かさない。
            <div className="one-send">
              <button
                type="button"
                className="primary"
                data-primary
                disabled={busy || !chosen}
                onClick={() => chosen && void answer(chosen)}
              >
                {WORDS.one.send}
              </button>
            </div>
          )}
          {showChoices && <p className="detail">{WORDS.one.hint}</p>}
        </div>

        {upcoming && (
          <section className="one-upcoming" data-one-next>
            {upcoming.test && <p>{upcoming.test}</p>}
            {upcoming.candidates && <p>{upcoming.candidates}</p>}
            {upcoming.undecided && <p>{WORDS.schedule.undecided}</p>}
          </section>
        )}

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
                  {whenOf(other) && (
                    <>
                      <br />
                      {shortTime(whenOf(other)?.at)}
                    </>
                  )}
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
