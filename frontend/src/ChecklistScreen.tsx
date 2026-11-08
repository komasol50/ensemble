import { useMemo, useState } from 'react'
import { api, ApiError, type Board } from './api'
import {
  askablePeople,
  isAdded,
  isDelivered,
  linkedItem,
  loadChecklist,
  normName,
  saveChecklist,
  stateOf,
  thingsOf,
  type Saved,
  type Thing,
} from './checklist'
import { Chevron, personWord } from './parts'
import { whenWord } from './tasks'
import { PackingBottom, PackingTop, sourceLine, usePacking } from './PackingSuggestions'
import { sendsOf } from './Send'
import { chipOf, placeOf } from './turns'
import { WORDS } from './words'
import './checklist.css'

// 持ち物（v3.6）。印は端末にだけ残す。［頼む］は家族へのやることを1件足すだけで、AIは呼ばない。
// 量は決まった文のまま。入院の日数や退院の候補日から数えない。

const NAME_MAX = 40
const PLACE_MAX = 40

type Props = {
  code: string
  board: Board
  onBoard: (board: Board) => void
  onOpenSend: () => void
  // 見るだけ（夫の画面など）。印・［頼む］・直す・足す・案のカードを出さない。
  readOnly?: boolean
}

export function Checklist({ code, board, onBoard, onOpenSend, readOnly = false }: Props) {
  const words = WORDS.checklist
  const [saved, setSaved] = useState<Saved>(() => loadChecklist(code))
  const [open, setOpen] = useState<string | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [nameText, setNameText] = useState('')
  const [placeText, setPlaceText] = useState('')
  const [choosing, setChoosing] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [placeSaved, setPlaceSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addText, setAddText] = useState('')
  // 開いている「いらない」の畳み（組の名前）。
  const [unfolded, setUnfolded] = useState<Set<string>>(() => new Set())

  // 別の記録に切り替わったら、その記録の控えを読み直す。
  const [savedCode, setSavedCode] = useState(code)
  if (savedCode !== code) {
    setSavedCode(code)
    setSaved(loadChecklist(code))
  }

  const update = (change: (current: Saved) => Saved) => {
    setSaved((current) => {
      const next = change(current)
      saveChecklist(code, next)
      return next
    })
  }

  // 持ち物の案（v3.6.1）。親が押した物だけ一覧に入る。
  const packing = usePacking({ code, board, saved, update, readOnly })
  const people = readOnly ? [] : askablePeople(board)
  const things = thingsOf(saved)
  const sendIds = useMemo(() => new Set(sendsOf(board).map((send) => send.item.id)), [board])
  const states = things.map((thing) => stateOf(saved, thing.id, board))
  const count = (state: string) => states.filter((value) => value === state).length
  const summary = words.summary(count('have'), count('asked'), count('todo'))
  // 頼んだのに、まだ送っていない物（UX Design §16）。上に1行で知らせる。
  const unsentAsked = things.filter((thing) => {
    const item = linkedItem(saved, thing.id, board)
    return !!saved.asked[thing.id] && !!item && sendIds.has(item.id)
  }).length

  const toggleOpen = (thing: Thing) => {
    if (readOnly) return
    setChoosing(null)
    setRenaming(false)
    setPlaceSaved(null)
    if (open === thing.id) {
      setOpen(null)
      return
    }
    setOpen(thing.id)
    setPlaceText(saved.places[thing.id] ?? '')
  }

  const toggleMark = (thing: Thing) => {
    if (readOnly) return
    update((current) => {
      const marks = { ...current.marks }
      if (marks[thing.id]) delete marks[thing.id]
      else marks[thing.id] = true
      return { ...current, marks }
    })
  }

  // やることを1件足す。版が古ければ読み直して1回だけ送り直す。
  const ask = async (thing: Thing, person: string) => {
    setBusy(true)
    setFailed(null)
    const place = (saved.places[thing.id] ?? '').trim()
    const task = {
      text: words.askText(thing.name, place),
      summary: thing.name,
      part: '家族' as const,
      owner: person,
      location: place || null,
    }
    const before = new Set(board.items.map((item) => item.id))
    try {
      let next: Board
      try {
        next = await api.addTask(code, board.version, task)
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 409)) throw error
        const fresh = await api.getBoard(code)
        onBoard(fresh)
        fresh.items.forEach((item) => before.add(item.id))
        next = await api.addTask(code, fresh.version, task)
      }
      // 足した項目は、前に無かったID。見つからなければ同じ題の最後の項目。
      const created =
        next.items.find((item) => !before.has(item.id)) ??
        [...next.items].reverse().find((item) => item.task_summary === thing.name)
      if (created) {
        update((current) => ({
          ...current,
          asked: { ...current.asked, [thing.id]: { item: created.id, person } },
        }))
      }
      onBoard(next)
      setChoosing(null)
    } catch {
      setFailed(thing.id)
    } finally {
      setBusy(false)
    }
  }

  const pressAsk = (thing: Thing) => {
    setOpen(null)
    if (people.length === 1) {
      void ask(thing, people[0])
      return
    }
    setChoosing((current) => (current === thing.id ? null : thing.id))
  }

  const savePlace = (thing: Thing) => {
    const place = placeText.trim()
    update((current) => {
      const places = { ...current.places }
      if (place) places[thing.id] = place
      else delete places[thing.id]
      return { ...current, places }
    })
    setPlaceSaved(thing.id)
  }

  const saveName = (thing: Thing) => {
    const name = nameText.trim()
    if (!name) return
    update((current) => ({ ...current, names: { ...current.names, [thing.id]: name } }))
    setRenaming(false)
  }

  const remove = (thing: Thing) => {
    update((current) => {
      const drop = <T,>(record: Record<string, T>) => {
        const next = { ...record }
        delete next[thing.id]
        return next
      }
      // 型の物は removed へ、足した物は added から外す。消した名前は、案にもう一度出さない（v3.6.1）。
      const added = isAdded(current, thing.id)
      const original = current.added.find((entry) => entry.id === thing.id)?.name
      const names = [thing.name, original]
        .filter((name): name is string => !!name)
        .map(normName)
        .filter((name) => !current.rejected.includes(name))
      return {
        ...current,
        marks: drop(current.marks),
        asked: drop(current.asked),
        places: drop(current.places),
        names: drop(current.names),
        removed: added ? current.removed : [...current.removed, thing.id],
        unneeded: current.unneeded.filter((id) => id !== thing.id),
        added: current.added.filter((entry) => entry.id !== thing.id),
        rejected: [...current.rejected, ...new Set(names)],
      }
    })
    setOpen(null)
  }

  // いらない物は組の下の畳みへ。［戻す］で元の行に戻る。
  const setUnneeded = (thing: Thing, unneeded: boolean) => {
    update((current) => ({
      ...current,
      unneeded: unneeded
        ? [...current.unneeded.filter((id) => id !== thing.id), thing.id]
        : current.unneeded.filter((id) => id !== thing.id),
    }))
    setOpen(null)
  }

  const add = () => {
    const name = addText.trim()
    if (!name) return
    const id = `u-${Date.now().toString(36)}`
    update((current) => ({ ...current, added: [...current.added, { id, name }] }))
    setAddText('')
    setAdding(false)
  }

  const row = (thing: Thing, index: number) => {
    const state = states[index]
    const link = saved.asked[thing.id]
    const item = linkedItem(saved, thing.id, board)
    const delivered = isDelivered(item)
    const chip = item ? chipOf(item, placeOf(item, sendIds.has(item.id))) : null
    // 札は「夫：これから」→「夫：返事待ち」→「夫：引き受けた」（UX Design §16）。済は札でなく1行で。
    const who = personWord(item?.owner || link?.person)
    const chipText = chip
      ? `${who ? `${who}：` : ''}${chip.word === '未送信' ? words.notYet : chip.word}`
      : ''
    const accepted = !!item && (item.status === '引き受けた' || item.status === '時刻つきで可')
    const until = accepted && item?.due ? whenWord(item.due, board.today) : null
    const isOpen = open === thing.id
    const source = sourceLine(thing, packing.quotes)
    return (
      <li className="cl-row" key={thing.id} data-thing={thing.id} data-state={state}>
        <div className="cl-line">
          <button
            type="button"
            role="checkbox"
            className="cl-mark"
            aria-checked={state === 'have'}
            aria-label={words.markLabel(thing.name)}
            // 届いた・完了のやることから「ある」になった物は、押しても戻さない。見るだけの画面でも押せない。
            disabled={delivered || readOnly}
            onClick={() => toggleMark(thing)}
          >
            {state === 'have' ? words.checked : words.unchecked}
          </button>
          <button
            type="button"
            className="quiet cl-name"
            aria-expanded={readOnly ? undefined : isOpen}
            onClick={() => toggleOpen(thing)}
          >
            {thing.name}
          </button>
          {chip && !delivered ? (
            <span className={`chip card-chip cl-chip ${chip.tone}`} data-cl-chip>
              {chipText}
            </span>
          ) : delivered ? null : (
            people.length > 0 &&
            state === 'todo' && (
              <button
                type="button"
                className="cl-ask"
                aria-label={words.askLabel(thing.name)}
                aria-expanded={people.length > 1 ? choosing === thing.id : undefined}
                disabled={busy}
                onClick={() => pressAsk(thing)}
              >
                {words.ask}
              </button>
            )
          )}
        </div>
        {delivered && who && (
          <p className="cl-source" data-brought>
            {words.brought(who)}
          </p>
        )}
        {until && (
          <p className="cl-source" data-cl-until>
            {words.until(until)}
          </p>
        )}
        {source && (
          <p className={isOpen ? 'cl-source cl-source-open' : 'cl-source'} data-source>
            {source}
          </p>
        )}
        {choosing === thing.id && (
          <div className="cl-who" role="group" aria-label={words.askWho}>
            <span className="cl-who-label">{words.askWho}</span>
            {people.map((person) => (
              <button
                type="button"
                key={person}
                disabled={busy}
                onClick={() => void ask(thing, person)}
              >
                {personWord(person)}
              </button>
            ))}
          </div>
        )}
        {failed === thing.id && (
          <p className="cl-note" role="status">
            {words.askFailed}
          </p>
        )}
        {/* 送る前だけ。送った後は札が「返事待ち」になる。 */}
        {link && item && sendIds.has(item.id) && (
          <p className="cl-note" data-asked-note>
            <span>{words.askedNote(personWord(link.person))}</span>
            <button type="button" className="link-button" onClick={onOpenSend}>
              {words.seeSend}
              <Chevron />
            </button>
          </p>
        )}
        {isOpen && item && (
          <div className="cl-panel" data-place>
            <label className="cl-field">
              <span>{words.place}</span>
              <input
                maxLength={PLACE_MAX}
                value={placeText}
                onChange={(event) => {
                  setPlaceText(event.target.value)
                  setPlaceSaved(null)
                }}
              />
            </label>
            <p className="cl-hint">{words.placeHint}</p>
            <div className="cl-tools">
              <button type="button" onClick={() => savePlace(thing)}>
                {words.placeSave}
              </button>
            </div>
            {placeSaved === thing.id && (
              <p className="cl-hint" role="status">
                {words.placeSaved}
              </p>
            )}
          </div>
        )}
        {isOpen && !item && !renaming && (
          <div className="cl-tools" data-thing-tools>
            <button
              type="button"
              onClick={() => {
                setNameText(thing.name)
                setRenaming(true)
              }}
            >
              {words.rename}
            </button>
            <button type="button" className="quiet" onClick={() => setUnneeded(thing, true)}>
              {words.unneed}
            </button>
            <button type="button" onClick={() => remove(thing)}>
              {words.remove}
            </button>
          </div>
        )}
        {isOpen && !item && renaming && (
          <div className="cl-panel" data-rename>
            <label className="cl-field">
              <span>{words.renameLabel}</span>
              <input
                maxLength={NAME_MAX}
                value={nameText}
                autoFocus
                onChange={(event) => setNameText(event.target.value)}
              />
            </label>
            <div className="cl-tools">
              <button type="button" disabled={!nameText.trim()} onClick={() => saveName(thing)}>
                {words.renameSave}
              </button>
              <button type="button" className="quiet" onClick={() => setRenaming(false)}>
                {words.cancel}
              </button>
            </div>
          </div>
        )}
      </li>
    )
  }

  // 組の行と、その組のいらない物の畳み。
  const indexed = things.map((thing, index) => ({ thing, index }))
  const block = (key: string, title: string | null, rows: typeof indexed) => {
    if (rows.length === 0) return null
    const active = rows.filter(({ index }) => states[index] !== 'unneeded')
    const off = rows.filter(({ index }) => states[index] === 'unneeded')
    const folded = !unfolded.has(key)
    return (
      <div className="cl-group" key={key} data-checklist-group={title ?? ''}>
        {title && <h3>{title}</h3>}
        {active.length > 0 && (
          <ul className="cl-rows">{active.map(({ thing, index }) => row(thing, index))}</ul>
        )}
        {off.length > 0 && (
          <>
            <button
              type="button"
              className="quiet cl-fold"
              aria-expanded={!folded}
              data-unneeded-fold
              onClick={() =>
                setUnfolded((current) => {
                  const next = new Set(current)
                  if (next.has(key)) next.delete(key)
                  else next.add(key)
                  return next
                })
              }
            >
              {words.unneededFold(off.length)}
              <Chevron to={folded ? 'down' : 'up'} />
            </button>
            {!folded && (
              <ul className="cl-rows">
                {off.map(({ thing }) => (
                  <li className="cl-row cl-off" key={thing.id} data-thing={thing.id}>
                    <span className="cl-off-name">{thing.name}</span>
                    {!readOnly && (
                    <button
                      type="button"
                      className="quiet"
                      aria-label={words.restoreLabel(thing.name)}
                      onClick={() => setUnneeded(thing, false)}
                    >
                      {words.restore}
                    </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    )
  }
  // 一番上は「まず確かめたい物」（star の物）。元の組には重ねない。足した物は最後に、見出しを付けずに。
  const firstRows = indexed.filter(({ thing }) => thing.group !== null && thing.star)
  return (
    <section className="checklist" data-checklist>
      <h2>{words.title}</h2>
      {summary && (
        <p className="cl-summary" data-checklist-summary>
          {summary}
        </p>
      )}
      {!readOnly && unsentAsked > 0 && (
        <p className="cl-note cl-unsent" data-unsent-asked>
          <span>{words.unsentAsked(unsentAsked)}</span>
          <button type="button" className="link-button" onClick={onOpenSend}>
            {words.seeSend}
            <Chevron />
          </button>
        </p>
      )}
      <PackingTop packing={packing} saved={saved} />
      {block('first', words.firstTitle, firstRows)}
      {words.groups.map((group, g) =>
        block(
          `g${g}`,
          group.title,
          indexed.filter(({ thing }) => thing.group === g && !thing.star),
        ),
      )}
      {block(
        'added',
        null,
        indexed.filter(({ thing }) => thing.group === null),
      )}
      {readOnly ? null : adding ? (
        <div className="cl-add-line" data-checklist-add>
          <input
            aria-label={words.addLabel}
            maxLength={NAME_MAX}
            value={addText}
            autoFocus
            onChange={(event) => setAddText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') add()
            }}
          />
          <button type="button" disabled={!addText.trim()} onClick={add}>
            {words.addSave}
          </button>
        </div>
      ) : (
        <button type="button" className="quiet cl-add-open" onClick={() => setAdding(true)}>
          {words.add}
        </button>
      )}
      <PackingBottom packing={packing} />
    </section>
  )
}
