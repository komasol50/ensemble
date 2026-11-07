import { useState, type ReactNode } from 'react'
import {
  CATEGORIES,
  categoryOf,
  deadlineFromPicker,
  isOwn as isOwnTask,
  isTask,
  loadTasks,
  saveTasks,
  type Category,
  type TaskLine,
} from './tasks'
import { type Item, type ItemChanges } from './api'
import { type VaultAnswer } from './device'
import {
  Chevron,
  daysSinceSent,
  PartIcon,
  shortTime,
  stateWord,
  Timeline,
  toneOf,
  whenOf,
  personWord, whoWord,
} from './parts'
import { type Photo } from './photos'
import { TrashButton } from './RemoveButton'
import { isSentMessage, messageOf, SendBody, type Send } from './Send'
import { PhotoImage } from './Vault'
import { chipOf, placeOf, wantsToTalk } from './turns'
import { COLUMNS, TALK_ANSWER, WORDS } from './words'

export const OWN_PART = '親'
const HAS_SPEAKER = /^[^：:]{1,8}[：:]/
const NOTE_MAX = 2000
const OWNER_MAX = 20
const DUE_MAX = 50

// 済を押したときの行き先。情報と予定には状態が無いので、丸を出さない。
export function doneStatus(item: Item): string | null {
  if (item.released) return null
  if (item.kind === '行動') {
    return item.status === '完了' || item.status === '届いた' ? null : '完了'
  }
  if (item.kind === '質問') return item.status === '答えあり' ? null : '答えあり'
  return null
}

// 返事の中身。家族の答えは、誰の答えかも添える。
export function replyText(item: Item): string {
  if (wantsToTalk(item))
    return item.owner ? `${personWord(item.owner)}：${TALK_ANSWER}` : TALK_ANSWER
  return item.part === '家族' && item.owner
    ? `${personWord(item.owner)}：${item.status_label}`
    : (item.status_label ?? '')
}

// 相手へ送る文か。案から作った連絡と、親が家族に頼みたいと入れた文。
function isOutgoing(item: Item): boolean {
  return isSentMessage(item) || (item.kind === '行動' && item.part === '家族' && item.speaker === '親')
}

// 原文の前置き。原文に発言者が書いてあるときは、重ねない。
function originalLead(item: Item): string {
  if (isSentMessage(item)) return `${WORDS.board.fullText}：`
  if (HAS_SPEAKER.test(item.text_original ?? '')) return ''
  return `${item.speaker ? WORDS.board.speaker(item.speaker) : WORDS.board.original}：`
}

// 送った連絡は手紙の全文を持つので、題は最初の用件の文にする。全文は開いて読む。
export function titleOf(item: Item): string {
  return isSentMessage(item) ? item.short : (item.task_summary ?? item.text_original ?? '')
}

function BellIcon() {
  return (
    <svg
      aria-hidden="true"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg
      aria-hidden="true"
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 3 10 14M21 3l-7 18-4-7-7-4z" />
    </svg>
  )
}

function PencilIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M17 3l4 4L8 20l-5 1 1-5L17 3z" />
      <path d="M14.5 5.5l4 4" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 20 20">
      <path
        d="M4 11l4 4 8-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// この行で親が次にすること。「どうすればいいの」に、開いた先で1行で答える。
function hintOf(item: Item, isSend: boolean): string | null {
  if (isSend || item.released) return null
  const word = stateWord(item.status_label)
  if (item.medical_reply) return word === '済' ? null : WORDS.row.hintWard
  if (word === '未送信') return item.kind === '質問' ? WORDS.row.hintAsk : WORDS.row.hintTodo
  if (word === '返事待ち') return WORDS.row.hintWaiting
  if (word === '返事あり') {
    return item.status === '難しい' ? WORDS.row.hintDifficult : WORDS.row.hintReplied
  }
  return null
}

// 夫のリンクから開いたタブか（知らせと分類は親だけのもの）。
function fromLinkEdit(): boolean {
  try {
    return sessionStorage.getItem('ensemble.fromLink') !== null
  } catch {
    return false
  }
}

// 直す欄。文・相手・担当・期日のうち、この項目で直せるものだけを出す。
function EditPanel({
  item,
  code,
  busy,
  onSave,
  onClose,
  onTaskSaved,
}: {
  item: Item
  code: string
  busy: boolean
  onSave: (changes: ItemChanges) => Promise<boolean>
  onClose: () => void
  onTaskSaved?: () => void
}) {
  const fromProposal = isSentMessage(item)
  const textFixed = isOutgoing(item) && item.status !== '頼みたい' && item.status !== '未確認'
  const ownNote = item.part === OWN_PART && item.kind === '情報'
  const originalEditable = item.text_original !== null && !textFixed && !item.original_fixed
  const isTimeReply = item.status === '時刻つきで可'
  const hasWork = item.kind !== '情報' && !ownNote
  const [summary, setSummary] = useState(item.task_summary ?? '')
  const [original, setOriginal] = useState(item.text_original ?? '')
  const [part, setPart] = useState(item.part)
  const [owner, setOwner] = useState(item.owner ?? '')
  const [due, setDue] = useState(isTimeReply ? '' : (item.due ?? ''))
  // 開いたときの値と比べて、触った欄だけ送る。もう1台が直した欄を、古い文で上書きしない。
  const [opened] = useState({ summary, original, part, owner, due })
  // やること（v3.6）：知らせる時刻と分類は端末にだけ持つ。期限は日付と時刻で選べる（自由な文字のときはそのまま）。
  const task = isTask(item) && !fromLinkEdit()
  const stored = loadTasks(code)
  const [remind, setRemind] = useState(stored.remind[item.id] ?? '')
  const [category, setCategory] = useState<Category | ''>(stored.category[item.id] ?? '')

  const saveTask = () => {
    if (!task) return
    const next = loadTasks(code)
    const before = `${next.remind[item.id] ?? ''}|${next.category[item.id] ?? ''}`
    if (remind) next.remind[item.id] = remind
    else delete next.remind[item.id]
    if (category) next.category[item.id] = category
    else delete next.category[item.id]
    // 時刻を変えたら、閉じた知らせをもう一度出せるようにする。
    if (remind !== (stored.remind[item.id] ?? '')) delete next.closed[item.id]
    if (`${next.remind[item.id] ?? ''}|${next.category[item.id] ?? ''}` !== before) {
      saveTasks(code, next)
      onTaskSaved?.()
    }
  }

  const save = () => {
    saveTask()
    const changes: ItemChanges = {}
    if (item.task_summary !== null && summary.trim() !== opened.summary) {
      changes.task_summary = summary
    }
    if (originalEditable && original.trim() !== opened.original) {
      changes.text_original = original
    }
    if (part !== opened.part) changes.part = part
    if (hasWork && owner.trim() !== opened.owner) changes.owner = owner
    if (hasWork && !isTimeReply && due.trim() !== opened.due) changes.due = due
    if (Object.keys(changes).length === 0) {
      onClose()
      return
    }
    void onSave(changes).then((saved) => {
      if (saved) onClose()
    })
  }

  return (
    <div className="edit-panel" data-edit>
      {item.task_summary !== null && (
        <label>
          {WORDS.row.editSummary}
          <textarea
            rows={2}
            maxLength={NOTE_MAX}
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
          />
        </label>
      )}
      {originalEditable && (
        <label>
          {fromProposal
            ? WORDS.row.editSend
            : item.task_summary !== null
              ? WORDS.row.editOriginal
              : WORDS.row.editText}
          <textarea
            rows={fromProposal ? 6 : 3}
            maxLength={NOTE_MAX}
            value={original}
            onChange={(event) => setOriginal(event.target.value)}
          />
        </label>
      )}
      {textFixed && <p className="detail">{WORDS.row.editSentFixed}</p>}
      {/* 直せない原文は隠さず読むだけで出す。用件が無ければ題が原文なので重ねない。 */}
      {!textFixed && item.original_fixed && (
        <>
          {item.task_summary !== null && (
            <p className="original">
              {originalLead(item)}
              {item.text_original}
            </p>
          )}
          <p className="detail">{item.original_fixed}</p>
        </>
      )}
      {item.notice && <p className="detail">{WORDS.row.editNoOriginal}</p>}
      {!fromProposal && !ownNote && !textFixed && (
        <label>
          {WORDS.row.editPart}
          <select value={part} onChange={(event) => setPart(event.target.value)}>
            {item.part === OWN_PART && <option value={OWN_PART}>{WORDS.board.parent}</option>}
            {COLUMNS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}
      {hasWork && (
        <div className="edit-pair">
          <label>
            {WORDS.board.owner}
            <input
              maxLength={OWNER_MAX}
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
            />
          </label>
          {!isTimeReply && (
            <label>
              {WORDS.board.due}
              <input
                maxLength={DUE_MAX}
                value={due}
                data-edit-due
                onChange={(event) => setDue(event.target.value)}
              />
            </label>
          )}
          {task && !isTimeReply && (
            <label>
              {WORDS.tasks.pickDeadline}
              <input
                type="datetime-local"
                data-edit-due-picker
                onChange={(event) => setDue(deadlineFromPicker(event.target.value))}
              />
            </label>
          )}
        </div>
      )}
      {task && (
        <>
          <label>
            {WORDS.tasks.remind}
            <span className="detail">{WORDS.tasks.remindDetail}</span>
            <input
              type="datetime-local"
              value={remind}
              data-edit-remind
              onChange={(event) => setRemind(event.target.value)}
            />
          </label>
          <label>
            {WORDS.tasks.category}
            <select
              value={category}
              data-edit-category
              onChange={(event) =>
                setCategory(event.target.value ? (Number(event.target.value) as Category) : '')
              }
            >
              <option value="">
                {WORDS.tasks.categoryAuto}（{WORDS.tasks.groups[categoryOf(item, { ...stored, category: {} })]}）
              </option>
              {CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {WORDS.tasks.groups[value]}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {fromProposal && !textFixed && <p className="detail">{WORDS.row.editSendNote}</p>}
      {!fromProposal && !ownNote && !item.original_fixed && (
        <p className="detail">{WORDS.row.editNote}</p>
      )}
      <div className="edit-actions">
        <button type="button" disabled={busy} onClick={save}>
          {WORDS.row.editSave}
        </button>
        <button type="button" className="quiet" onClick={onClose}>
          {WORDS.add.back}
        </button>
      </div>
    </div>
  )
}

// 職場・保育園・支援はアプリを使わないので、親が返事を記録する。AIは使わない。
function ReplyPanel({
  busy,
  onReply,
  onClose,
}: {
  busy: boolean
  onReply: (status: string, due?: string) => void
  onClose: () => void
}) {
  const [timeOpen, setTimeOpen] = useState(false)
  const [time, setTime] = useState('')

  if (timeOpen) {
    return (
      <div className="reply-panel">
        <label>
          {WORDS.board.replyTimeLabel}
          <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
        </label>
        <button
          type="button"
          disabled={busy || !time}
          onClick={() => onReply('時刻つきで可', time)}
        >
          {WORDS.board.replySave}
        </button>
        <button type="button" onClick={() => setTimeOpen(false)}>
          {WORDS.add.back}
        </button>
      </div>
    )
  }
  return (
    <div className="reply-panel">
      {WORDS.board.replyChoices.map((choice) => (
        <button key={choice} type="button" disabled={busy} onClick={() => onReply(choice)}>
          {choice}
        </button>
      ))}
      <button type="button" disabled={busy} onClick={() => setTimeOpen(true)}>
        {WORDS.board.replyTime}
      </button>
      <button type="button" onClick={onClose}>
        {WORDS.add.back}
      </button>
    </div>
  )
}

// リストの1行。左に丸か送る印、真ん中に題と状態と時刻。押すと行の下に中身を開く。
export function ItemRow({
  item,
  send: sendOfRow,
  fromLink = false,
  code,
  items,
  shared,
  showPart,
  hideSentDays,
  today,
  isNew,
  open,
  busy,
  photos,
  answer,
  source,
  canSearchVault,
  searching,
  onToggle,
  onSearch,
  onOpenPhoto,
  onDone,
  onUndone,
  onReply,
  onShared,
  onAsked,
  onEdit,
  onRemove,
  onReshared,
  layout = 'row',
  errand,
  onOpenDrafts,
  extra,
  task,
  onTaskSaved,
}: {
  item: Item
  send: Send | null
  fromLink?: boolean
  code: string
  items: Item[]
  shared: boolean
  showPart: boolean
  hideSentDays: boolean
  today: string | undefined
  isNew: boolean
  open: boolean
  busy: boolean
  photos: Photo[]
  answer: VaultAnswer | undefined
  source: Photo | undefined
  canSearchVault: boolean
  searching: boolean
  onToggle: () => void
  onSearch: () => void
  onOpenPhoto: (photo: Photo) => void
  onDone: (status: string) => void
  onUndone: () => void
  onReply: (status: string, due?: string) => void
  onShared: () => void
  onAsked: () => void
  onEdit: (changes: ItemChanges) => Promise<boolean>
  onRemove: () => void
  onReshared?: () => void
  // card：⑪の1列のカード（相手・題・札・2段目・右の操作）。押すと同じ中身を開く。
  layout?: 'row' | 'card'
  errand?: string
  onOpenDrafts?: () => void
  // 行の操作を足す場所（職場への文の［送る文を見る］など）。開かなくても押せる。
  extra?: ReactNode
  // やることの一覧の2段目（v3.6）：期限・期限を過ぎた・知らせる時刻。あるときは「期日」の文の代わりに出す。
  task?: TaskLine
  // 端末に持つ知らせ・分類を直したとき（一覧を描き直す）。
  onTaskSaved?: () => void
}) {
  const [replyOpen, setReplyOpen] = useState(false)
  // もう一度送る：同じ文・同じリンクを共有（無い端末はコピー）。取り消したときは記録しない。
  const reshare = async () => {
    const text = messageOf(item, code)
    try {
      if (typeof navigator.share === 'function') await navigator.share({ text })
      else await navigator.clipboard.writeText(text)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        return
      }
    }
    onReshared?.()
  }
  const [editing, setEditing] = useState(false)
  const word = stateWord(item.status_label)
  const finished = word === '済'
  const isMedical = !!item.medical_reply
  const isReply = word === '返事あり' && !item.released
  const ownNote = item.part === OWN_PART && item.kind === '情報'
  // 医療の問いは畳まない。自分の問いと固定文を並べて読めるようにする。
  const expanded = open || isMedical
  const title = finished || item.released ? item.short : titleOf(item)
  const showOriginal = !!item.text_original && item.text_original !== title
  const showCertainty =
    item.part !== OWN_PART &&
    (item.kind === '予定' || (item.kind === '情報' && item.certainty !== '確定'))
  // 夫の入口では、親の下書きの行は読むだけ。送る操作も、済にする丸も出さない。
  const parentsDraft = fromLink && isSentMessage(item)
  const send = parentsDraft ? null : sendOfRow
  const next = send || parentsDraft ? null : doneStatus(item)
  const waiting =
    !item.released && item.kind === '行動' && (item.status === '頼んだ' || item.status === '保留')
  const searchable = canSearchVault && !answer?.found
  const when = whenOf(item)
  const hint = parentsDraft ? null : hintOf(item, !!send)
  // 戻す先はサーバーが記録から決める。
  const sentMessage = isOutgoing(item) && item.status === '頼んだ'
  const canUndo = (finished || isReply || sentMessage) && !item.released
  const dueShown = item.status !== '時刻つきで可' ? item.due : null
  const ownerInReply = isReply && item.part === '家族'

  // 夫のタブでは出さない。頼まれた本人に日数を見せると催促になる。
  const sentDays = waiting && !hideSentDays ? daysSinceSent(item, today) : 0

  const main = (
    <>
      <span className="row-title">{title}</span>
      <span className="row-meta">
        {isNew && <span className="chip new">{WORDS.board.newMark}</span>}
        {showPart && (
          <span className="part-head row-part">
            <PartIcon part={item.part} />
            {item.part === OWN_PART ? WORDS.board.parent : item.part}
          </span>
        )}
        {item.released && <span className="chip">{WORDS.board.released}</span>}
        {isReply && !waiting && <span className="chip state reply">{replyText(item)}</span>}
        {showCertainty && (
          <span className={item.certainty === '確定' ? 'chip confirmed' : 'chip likely'}>
            {item.certainty}
          </span>
        )}
        {item.superseded && !item.released && (
          <span className="chip">{WORDS.row.supersededMark}</span>
        )}
        {send && !item.superseded && <span className="row-fact">{WORDS.row.sendText}</span>}
        {/* 返事の札がある行と、列で意味が分かる進捗別は、時刻だけにする。 */}
        {when && (
          <time className="row-fact" dateTime={when.at}>
            {isReply || showPart ? shortTime(when.at) : `${when.label} ${shortTime(when.at)}`}
          </time>
        )}
        {/* 使っている親自身の用事は、担当を出さなくても分かる。 */}
        {item.owner && item.owner !== OWN_PART && !ownerInReply && (
          <span className="row-fact">
            {WORDS.board.owner}：{personWord(item.owner)}
          </span>
        )}
        {!task && dueShown && (
          <span className="row-fact">
            {WORDS.board.due}：{dueShown}
          </span>
        )}
        {task?.overdue && (
          <span className="chip review" data-task-overdue>
            {WORDS.tasks.overdue}
          </span>
        )}
        {task && !task.overdue && task.deadline && (
          <span className="row-fact" data-task-deadline>
            {WORDS.tasks.until(task.deadline)}
          </span>
        )}
        {task?.remind && (
          <span className="row-fact task-remind" data-task-remind>
            <BellIcon />
            {task.remind}
          </span>
        )}
        {photos.length > 0 && <span className="row-fact">{WORDS.row.photos(photos.length)}</span>}
      </span>
    </>
  )

  // 固定文と、資料から見つけた箇所は畳まない。
  const extras = (
    <>
      {item.medical_reply && <p className="medical">{item.medical_reply}</p>}
      {answer?.found && (
        <div className="vault-answer">
          <p>
            <strong>{WORDS.photos.foundIn(answer.category)}</strong>
          </p>
          <p className="quote">「{answer.quote}」</p>
          {source && (
            <button type="button" onClick={() => onOpenPhoto(source)}>
              {WORDS.photos.openSource}
            </button>
          )}
        </div>
      )}
      {answer && !answer.found && <p className="detail row-note">{WORDS.photos.notFound}</p>}
    </>
  )

  const replyPanel = waiting && replyOpen && (
    <ReplyPanel
      busy={busy}
      onReply={(status, due) => {
        setReplyOpen(false)
        onReply(status, due)
      }}
      onClose={() => setReplyOpen(false)}
    />
  )

  const searchButton = searchable && (
    <button type="button" disabled={searching} onClick={onSearch}>
      {searching ? WORDS.photos.searching : WORDS.photos.search}
    </button>
  )

  // 開いた中身。カードでは「左の丸」の案内を出さない（操作はカードの右にある）。
  const shownHint = layout === 'card' ? null : hint
  const body = expanded && (
    <div className="row-body">
      {editing ? (
        <EditPanel
          item={item}
          code={code}
          busy={busy}
          onSave={onEdit}
          onClose={() => setEditing(false)}
          onTaskSaved={onTaskSaved}
        />
      ) : (
        <>
          {shownHint && <p className="row-hint">{shownHint}</p>}
          {item.superseded && !item.released && (
            <p className="row-hint">{WORDS.row.supersededHint}</p>
          )}
          {item.outdated && <p className="detail">{WORDS.one.outdated}</p>}
          {item.notice && <p className="detail">{item.notice}</p>}
          {send ? (
            <SendBody
              send={send}
              code={code}
              items={items}
              shared={shared}
              busy={busy}
              onShared={onShared}
              onAsked={onAsked}
            />
          ) : (
            <>
              {showOriginal && (
                <p className="original">
                  {originalLead(item)}
                  {item.text_original}
                </p>
              )}
              {item.location && (
                <p className="detail">
                  {WORDS.board.location}：{item.location}
                </p>
              )}
              {photos.length > 0 && (
                <div className="photo-row">
                  {photos.map((photo) => (
                    <button
                      key={photo.id}
                      type="button"
                      className="photo-thumb"
                      aria-label={WORDS.photos.show(photo.category)}
                      onClick={() => onOpenPhoto(photo)}
                    >
                      <PhotoImage blob={photo.thumb} alt="" />
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
          {/* 2026-11-07 PO：返事待ちの送った連絡を、同じ文・同じリンクでもう一度送る。押したときだけ。
              道具の列と並べると 360px で幅を超えるので、その上に1行で置く（UX Design の P1）。 */}
          {!fromLink &&
            onReshared &&
            isSentMessage(item) &&
            item.status === '頼んだ' &&
            !item.released && (
              <div className="row-reshare">
                <button
                  type="button"
                  className="reshare"
                  data-reshare
                  disabled={busy}
                  onClick={() => void reshare()}
                >
                  {WORDS.row.reshare}
                </button>
              </div>
            )}
          <div className="row-foot">
            <Timeline item={item} />
            <div className="row-tools">
              {canUndo && !parentsDraft && (
                <button type="button" disabled={busy} onClick={onUndone}>
                  {WORDS.row.undo}
                </button>
              )}
              {!item.released && !parentsDraft && (
                <button
                  type="button"
                  className="icon-button"
                  aria-label={WORDS.row.edit}
                  disabled={busy}
                  onClick={() => setEditing(true)}
                >
                  <PencilIcon />
                </button>
              )}
              {!parentsDraft && <TrashButton disabled={busy} onClick={onRemove} />}
            </div>
          </div>
        </>
      )}
    </div>
  )

  if (layout === 'card') {
    const place = placeOf(item, !!sendOfRow)
    const chip = chipOf(item, place)
    const replied = isReply || place?.group === 'held' || place?.group === 'difficult'
    // 2段目：返事の中身か、最後に起きたこと。どちらも時刻を消さない（夫のひとことの時刻は出さない）。
    const replyWord = wantsToTalk(item) ? TALK_ANSWER : (item.status_label ?? '')
    const replyEvent =
      replied && !item.released && replyWord
        ? [...item.events].reverse().find((event) => event.label.endsWith(replyWord))
        : undefined
    // 返事待ちは、送った時刻（後から直した・戻した時刻ではなく）。
    const sentEvent =
      place?.group === 'waiting'
        ? [...item.events].reverse().find((event) => event.label === '送った')
        : undefined
    const event = replyEvent ?? sentEvent ?? when
    // 家族の返事の記録は「夫：引き受けた」。相手はカードの上に出ているので、2段目では重ねない。
    // 札と同じ語なら2段目は時刻だけ（UX Design の P2：「引き受けた」が二重になる）。
    const lineWord = replied
      ? replyWord === chip?.word
        ? ''
        : replyWord
      : (event?.label.replace(HAS_SPEAKER, '') ?? '')
    // 右の操作。送る前は下書きへ、送った後は［返事を記録］（AIを使わない）、家族の対応中は［受け取った］。
    const action: { kind: string; label: string; run: () => void; link?: boolean } | null =
      parentsDraft || item.released || finished
        ? null
        : send
          ? // 下書きを決めて送るのは親。夫の入口には下書きの画面が無いので出さない。
            !item.superseded && !fromLink && onOpenDrafts
            ? { kind: 'drafts', label: WORDS.progress.seeDraft, run: onOpenDrafts, link: true }
            : null
          : waiting
            ? { kind: 'reply', label: WORDS.board.replied, run: () => setReplyOpen(true) }
            : // 対応中も［済にする］。［受け取ったら完了にする］は⑨の返事のカードだけ（UX Design の P1-3）。
              next
                ? {
                    kind: 'done',
                    label: isMedical ? WORDS.board.askedWard : WORDS.row.markDone,
                    run: () => onDone(next),
                  }
                : null
    // やることの一覧（v3.6・UX Design §2.1／§16）：持ち主と状態を1つの札「夫：引き受けた」にし、
    // 2段目は期限と知らせ。出来事の時刻は期限が無いときだけ。親の用事の札は「自分」。
    const taskWho = task
      ? isOwnTask(item)
        ? WORDS.tasks.own
        : item.part === '家族'
          ? personWord(item.owner) || whoWord(item.part, errand)
          : whoWord(item.part, errand)
      : ''
    // 札が無い行（送る前の家族の用事など）も、やることの一覧では「夫：これから」を出す。
    const taskWord = chip?.word ?? (finished ? '済' : WORDS.tasks.notYet)
    const taskChip = task
      ? taskWho === WORDS.tasks.own
        ? taskWord === '済'
          ? taskWord
          : WORDS.tasks.own
        : `${taskWho}：${taskWord === '未送信' ? WORDS.tasks.notYet : taskWord}`
      : null
    const showEvent = !task || (!task.deadline && !task.overdue)
    const cardMain = (
      <>
        {!task && (
        <span className="part-head card-who">
          <PartIcon part={item.part} />
          {item.part === OWN_PART
            ? task
              ? WORDS.tasks.own
              : WORDS.board.parent
            : whoWord(item.part, errand)}
        </span>
        )}
        <span className="row-title">{task?.title ?? (item.ask || title)}</span>
        <span className="row-meta card-line">
          {/* やることの一覧：持ち主と状態の札は2段目の先頭（UX Design §2.1）。 */}
          {taskChip && (
            <span className={`chip ${chip?.tone ?? ''}`} data-task-chip>
              {taskChip}
            </span>
          )}
          {isNew && <span className="chip new">{WORDS.board.newMark}</span>}
          {item.superseded && !item.released && <span>{WORDS.row.supersededMark}</span>}
          {showEvent && (lineWord || event) && (
            <span className="card-event">
              {lineWord}
              {lineWord && event && '・'}
              {event && <time dateTime={event.at}>{shortTime(event.at)}</time>}
            </span>
          )}
          {!task && dueShown && (
            <span className="row-fact">
              {WORDS.board.due}：{dueShown}
            </span>
          )}
          {task?.overdue && (
            <span className="chip review" data-task-overdue>
              {WORDS.tasks.overdue}
            </span>
          )}
          {task && !task.overdue && task.deadline && (
            <span className="row-fact" data-task-deadline>
              {WORDS.tasks.until(task.deadline)}
            </span>
          )}
          {task?.remind && (
            <span className="row-fact task-remind" data-task-remind>
              <BellIcon />
              {task.remind}
            </span>
          )}
          {sentDays > 0 && <span className="row-fact">{WORDS.row.sentDays(sentDays)}</span>}
        </span>
      </>
    )
    return (
      <article
        className={`row progress-card ${toneOf(item.part)}${finished ? ' finished' : ''}${
          item.released ? ' released' : ''
        }`}
        data-item={item.id}
        data-send={send ? item.part : undefined}
        data-progress-card={chip?.word}
      >
        <div className="card-head">
          {isMedical ? (
            <div className="row-main card-main still">{cardMain}</div>
          ) : (
            <button
              type="button"
              className="row-main card-main"
              aria-expanded={expanded}
              onClick={onToggle}
            >
              {cardMain}
            </button>
          )}
          <div className="card-side">
            {chip && !task && <span className={`chip card-chip ${chip.tone}`}>{chip.word}</span>}
            {action && !replyOpen && (
              <button
                type="button"
                className={action.link ? 'link-button' : 'card-action'}
                data-card-action={action.kind}
                disabled={busy && !action.link}
                onClick={action.run}
              >
                {action.label}
              </button>
            )}
          </div>
        </div>
        {extras}
        {extra}
        {searchButton && <div className="row-action card-search">{searchButton}</div>}
        {replyPanel}
        {body}
      </article>
    )
  }

  return (
    <article
      className={`row ${toneOf(item.part)}${finished ? ' finished' : ''}${
        ownNote ? ' own' : ''
      }${item.released ? ' released' : ''}`}
      data-item={item.id}
      data-send={send ? item.part : undefined}
    >
      <div className="row-head">
        {send && (
          <button
            type="button"
            className="row-lead send"
            aria-label={WORDS.row.openSend}
            aria-expanded={expanded}
            onClick={onToggle}
          >
            <SendIcon />
          </button>
        )}
        {!send && next && (
          <button
            type="button"
            className="row-lead check"
            aria-label={isMedical ? WORDS.board.askedWard : WORDS.row.markDone}
            disabled={busy}
            onClick={() => onDone(next)}
          >
            <span className="check-circle" />
          </button>
        )}
        {!send && !next && finished && (
          <span className="row-lead done" aria-label={WORDS.board.done} role="img">
            <span className="check-circle">
              <CheckIcon />
            </span>
          </span>
        )}
        {!send && !next && !finished && !ownNote && <span className="row-lead dot" />}
        {isMedical ? (
          <div className="row-main still">{main}</div>
        ) : (
          <button type="button" className="row-main" aria-expanded={expanded} onClick={onToggle}>
            {main}
            <span className="row-chevron">
              <Chevron to={expanded ? 'up' : 'down'} />
            </span>
          </button>
        )}
      </div>

      {extras}
      {extra}

      {/* その行の一番の操作は、開かなくても押せるようにする。 */}
      {(waiting || searchable) && !replyOpen && !parentsDraft && (
        <div className="row-action">
          {waiting && (
            <>
              <span className="chip state">
                {/* 行の中は幅が狭いので、誰の答えかは行の相手の札に任せる。 */}
                {wantsToTalk(item)
                  ? TALK_ANSWER
                  : item.status === '保留'
                    ? replyText(item)
                    : WORDS.row.waitingMark}
              </span>
              {sentDays > 0 && <span className="row-fact">{WORDS.row.sentDays(sentDays)}</span>}
              <button
                type="button"
                className="reply-open"
                disabled={busy}
                onClick={() => setReplyOpen(true)}
              >
                {WORDS.board.replied}
              </button>
            </>
          )}
          {searchButton}
        </div>
      )}
      {replyPanel}
      {body}
    </article>
  )
}
