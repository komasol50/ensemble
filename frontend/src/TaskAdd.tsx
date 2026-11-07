import { useState } from 'react'
import { api, type Board } from './api'
import { CATEGORIES, deadlineFromPicker, loadTasks, saveTasks, type Category } from './tasks'
import { WORDS } from './words'

// やることを足す（v3.6・UX Design §2.3）。AIは呼ばない。親の文をそのまま1件にする。
// 期限と知らせる時刻は任意で、初期値を入れない（アプリが時刻を作らない・勧めない）。
type OwnerChoice = { key: string; label: string; part: '親' | '家族' | '保育園・預け先' | '職場'; owner: string | null }

function ownerChoices(board: Board): OwnerChoice[] {
  const list: OwnerChoice[] = [{ key: 'own', label: WORDS.tasks.own, part: '親', owner: '親' }]
  for (const person of board.profile.errand_people) {
    if (!person.label || person.label === 'いない') continue
    list.push({ key: `family:${person.label}`, label: person.label, part: '家族', owner: person.label })
  }
  const contacts = board.profile.contacts
  if (!contacts || contacts.includes('保育園・預け先')) {
    list.push({ key: 'care', label: '保育園', part: '保育園・預け先', owner: null })
  }
  if (!contacts || contacts.includes('職場')) {
    list.push({ key: 'work', label: '職場', part: '職場', owner: null })
  }
  return list
}

export function TaskAdd({
  code,
  board,
  onBoard,
  onSaid,
}: {
  code: string
  board: Board
  onBoard: (board: Board) => void
  onSaid: (text: string) => void
}) {
  const words = WORDS.tasks
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const choices = ownerChoices(board)
  const [ownerKey, setOwnerKey] = useState(choices[0].key)
  const [deadline, setDeadline] = useState('')
  const [remind, setRemind] = useState('')
  const [category, setCategory] = useState<Category | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!open) {
    return (
      <button type="button" className="quiet task-add-open" data-task-add onClick={() => setOpen(true)}>
        {words.add}
      </button>
    )
  }

  const submit = async () => {
    const body = text.trim()
    if (!body || busy) return
    const choice = choices.find((entry) => entry.key === ownerKey) ?? choices[0]
    setBusy(true)
    setFailed(false)
    try {
      const fresh = await api.getBoard(code)
      const before = new Set(fresh.items.map((item) => item.id))
      const saved = await api.addTask(code, fresh.version, {
        text: body,
        part: choice.part,
        owner: choice.owner,
        due: deadline ? deadlineFromPicker(deadline) : null,
      })
      const added = saved.items.find((item) => !before.has(item.id))
      if (added && (remind || category)) {
        const stored = loadTasks(code)
        if (remind) stored.remind[added.id] = remind
        if (category) stored.category[added.id] = category
        saveTasks(code, stored)
      }
      onBoard(saved)
      onSaid(words.added)
      setText('')
      setDeadline('')
      setRemind('')
      setCategory(null)
      setOpen(false)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="task-add" data-task-add-form aria-label={words.addTitle}>
      <h3>{words.addTitle}</h3>
      <label className="task-field">
        <span>{words.text}</span>
        <input
          type="text"
          className="setup-input"
          maxLength={200}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <fieldset className="task-field">
        <legend>{words.owner}</legend>
        <div className="choice-wrap">
          {choices.map((choice) => (
            <button
              type="button"
              key={choice.key}
              className="choice"
              aria-pressed={ownerKey === choice.key}
              data-task-owner={choice.key}
              onClick={() => setOwnerKey(choice.key)}
            >
              {choice.label}
            </button>
          ))}
        </div>
      </fieldset>
      <label className="task-field">
        <span>{words.deadline}</span>
        <input
          type="datetime-local"
          className="setup-input"
          value={deadline}
          data-task-deadline-input
          onChange={(event) => setDeadline(event.target.value)}
        />
      </label>
      <label className="task-field">
        <span>{words.remind}</span>
        <span className="detail">{words.remindDetail}</span>
        <input
          type="datetime-local"
          className="setup-input"
          value={remind}
          data-task-remind-input
          onChange={(event) => setRemind(event.target.value)}
        />
      </label>
      <fieldset className="task-field">
        <legend>{words.category}</legend>
        <div className="choice-wrap">
          <button
            type="button"
            className="choice"
            aria-pressed={category === null}
            onClick={() => setCategory(null)}
          >
            {words.categoryAuto}
          </button>
          {CATEGORIES.map((value) => (
            <button
              type="button"
              key={value}
              className="choice"
              aria-pressed={category === value}
              data-task-category={value}
              onClick={() => setCategory(value)}
            >
              {words.groups[value]}
            </button>
          ))}
        </div>
      </fieldset>
      {failed && (
        <p className="message" role="alert">
          {WORDS.board.failed}
        </p>
      )}
      <div className="task-add-actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !text.trim()}
          data-task-submit
          onClick={() => void submit()}
        >
          {words.submit}
        </button>
        <button type="button" className="quiet" onClick={() => setOpen(false)}>
          {words.cancel}
        </button>
      </div>
    </section>
  )
}
