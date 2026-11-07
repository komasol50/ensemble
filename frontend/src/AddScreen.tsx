import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type Item } from './api'
import { Consent } from './Consent'
import { Chevron, inQuotes } from './parts'
import { listPhotos, READABLE, type Photo } from './photos'
import { useBlobUrl } from './Vault'
import { WORDS } from './words'

const MAX_CHARS = 2000
// 字数は上限が近づいてから出す（図に無い数字で、入れる欄の周りを混ませない）。
const COUNT_FROM = 1500
const RECENT_SHOWN = 3
const WEEKDAYS = '日月火水木金土'
const OWN_PART = '親'

function Thumbnail({ file, label, onRemove }: { file: File; label: string; onRemove: () => void }) {
  const url = useBlobUrl(file)
  return (
    <button type="button" className="photo-chosen" aria-label={label} onClick={onRemove}>
      {url && <img src={url} alt="" />}
      <span aria-hidden="true">×</span>
    </button>
  )
}

function ToolIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      width="22"
      height="22"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  )
}

const CAMERA = (
  <>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h2l1.2-2h4.6l1.2 2h2A1.5 1.5 0 0 1 17 7.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z" />
    <circle cx="10" cy="11" r="3" />
  </>
)
const MIC = (
  <>
    <rect x="7.5" y="2.5" width="5" height="9" rx="2.5" />
    <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" />
  </>
)
const CLIPBOARD = (
  <>
    <rect x="4.5" y="4" width="11" height="13.5" rx="1.5" />
    <path d="M7.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1" />
  </>
)

// 見出しの日付は台帳の「今日」（撮影では指定の日）、時刻は端末の時計。
function whenLabel(today: string | null, now: Date): string {
  const [year, month, day] = (today ?? '').split('-').map(Number)
  const date = year && month && day ? new Date(year, month - 1, day) : now
  const time = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`
  return `${date.getMonth() + 1}/${date.getDate()}（${WEEKDAYS[date.getDay()]}）${time}`
}

// これまでに入れたものの行の時刻。今日なら時刻だけ、別の日なら日付も。
function rowTime(iso: string, today: string | null): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const time = `${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`
  const [year, month, day] = (today ?? '').split('-').map(Number)
  const sameDay =
    date.getFullYear() === year && date.getMonth() + 1 === month && date.getDate() === day
  return sameDay ? time : `${date.getMonth() + 1}/${date.getDate()} ${time}`
}

type Recent =
  | { type: 'item'; at: string; item: Item }
  | { type: 'photo'; at: string; photo: Photo }

// 入れた時刻。項目は記録した時刻（無ければ最初の出来事）。
function enteredAt(item: Item): string {
  return item.recorded_at ?? item.events[0]?.at ?? ''
}

function kindOf(entry: Recent): string {
  if (entry.type === 'photo') return WORDS.add.kindPhoto
  const speaker = entry.item.speaker
  return speaker && speaker !== OWN_PART ? WORDS.add.kindFrom(speaker) : WORDS.add.kindMemo
}

function textOf(entry: Recent): string {
  if (entry.type === 'photo') return entry.photo.category
  const item = entry.item
  const text = item.text_original || item.short || item.task_summary || ''
  // 人から届いた言葉は、原文のまま「」で引く。親が「」を付けて書いた文には重ねない。
  const quoted = !!item.text_original && !!item.speaker && item.speaker !== OWN_PART
  return quoted && !/[「『]/.test(text) ? inQuotes(text) : text
}

type Props = {
  code: string
  draft: string
  onDraft: (text: string) => void
  onSubmit: (
    text: string,
    said: string | null,
    who: string | null,
    photos: File[],
    category: string,
    letRead: boolean,
  ) => Promise<void>
  // 書きかけは端末に残したまま「いま」へ戻る。
  onLater: () => void
  onOpenList: () => void
  onOpenItem: (item: Item) => void
  onOpenPhotos: () => void
  // 同意は、AIへ送る直前（最初の［整理する］）に出す。
  consented: boolean
  onConsented: () => void
  // いまのマイクから入ったとき。開いた直後から音声の案内を出す。
  voice?: boolean
}

export function AddScreen({
  code,
  draft,
  onDraft,
  onSubmit,
  onLater,
  onOpenList,
  onOpenItem,
  onOpenPhotos,
  consented,
  onConsented,
  voice = false,
}: Props) {
  const [text, setText] = useState(draft)
  const [photos, setPhotos] = useState<File[]>([])
  const [category, setCategory] = useState<string | null>(null)
  const [letRead, setLetRead] = useState(false)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [voiceHint, setVoiceHint] = useState(voice)
  const [pasteHint, setPasteHint] = useState(false)
  const [asking, setAsking] = useState(false)
  const [today, setToday] = useState<string | null>(null)
  const [now] = useState(() => new Date())
  const [items, setItems] = useState<Item[]>([])
  const [shelf, setShelf] = useState<Photo[]>([])
  const box = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const readNote = useRef<HTMLParagraphElement>(null)

  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((board) => {
        if (!active) return
        setToday(board.today)
        // 親が入れたもの。案から作った下書き（s-）、夫のひとこと（n-）、自分のことは入れない。
        setItems(
          board.items.filter(
            (item) =>
              !item.id.startsWith('s-') && !item.id.startsWith('n-') && item.part !== OWN_PART,
          ),
        )
      })
      .catch(() => undefined)
    // 写真はこの端末の中だけにある。読めない端末でも、文の行は出す。
    listPhotos(code)
      .then((list) => {
        if (active) setShelf(list)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [code])

  // 音声の案内を出すときは、入力欄に入る（キーボードが開き、マイクが押せる）。
  useEffect(() => {
    if (voiceHint) box.current?.focus()
  }, [voiceHint])

  // 送り先の注意書きは、押す前に必ず目に入るようにする。
  useEffect(() => {
    if (letRead) readNote.current?.scrollIntoView({ block: 'center' })
  }, [letRead])

  const recent: Recent[] = [
    ...items.map((item): Recent => ({ type: 'item', at: enteredAt(item), item })),
    ...shelf.map((photo): Recent => ({ type: 'photo', at: photo.at, photo })),
  ]
    .sort((a, b) => (new Date(b.at).getTime() || 0) - (new Date(a.at).getTime() || 0))
    .slice(0, RECENT_SHOWN)

  const over = text.length - MAX_CHARS
  const change = (next: string) => {
    setText(next)
    onDraft(next)
  }

  const paste = async () => {
    setPasteHint(false)
    try {
      const pasted = await navigator.clipboard.readText()
      if (!pasted) return
      change(text ? `${text}\n${pasted}` : pasted)
      box.current?.focus()
    } catch {
      // 読めない端末（許可が無い・使えないブラウザ）では、長押しの貼り付けを案内する。
      setPasteHint(true)
    }
  }

  const send = () => {
    setSaving(true)
    setFailed(null)
    // 何の話か・誰からかは選ばせない（AIが仕分ける）。
    onSubmit(text, null, null, photos, category ?? WORDS.photos.defaultCategory, letRead).catch(
      () => {
        // 文も写真も画面に残したまま、理由を出す。
        setFailed(WORDS.photos.cannotUse)
        setSaving(false)
      },
    )
  }

  // AIへ送るもの（文か、読ませる写真）があるときだけ、最初に同意を聞く。写真だけの保存は端末の中。
  const toAi = !!text.trim() || (photos.length > 0 && letRead)

  if (asking) {
    return (
      <Consent
        buttonLabel={WORDS.consent.agree}
        onClose={() => {
          onConsented()
          setAsking(false)
          send()
        }}
      />
    )
  }

  return (
    <div className="input-page" data-input-screen>
      <main className="input-screen">
        <header className="input-head">
          <h1>{WORDS.add.title}</h1>
          <p className="input-when" data-input-when>
            {whenLabel(today, now)}
          </p>
        </header>

        {/* 字数で切り捨てない（貼った文が黙って消えないように）。超えたら整理のボタンを止める。 */}
        <textarea
          ref={box}
          aria-label={WORDS.add.title}
          placeholder={WORDS.add.placeholder}
          value={text}
          data-input-text
          onChange={(event) => change(event.target.value)}
        />
        {(over > 0 || text.length >= COUNT_FROM) && (
          <p className={over > 0 ? 'count over' : 'count'} role="status">
            {over > 0 ? WORDS.add.over(over) : WORDS.add.count(text.length)}
          </p>
        )}

        <p className="detail" data-place-hint>
          {WORDS.add.placeHint}
        </p>
        <div className="input-tools" role="group" aria-label={WORDS.add.tools}>
          <button type="button" data-input-photo onClick={() => picker.current?.click()}>
            <ToolIcon>{CAMERA}</ToolIcon>
            {WORDS.add.photo}
          </button>
          {/* 録音しない。端末のキーボードの音声入力を使うよう案内するだけ。 */}
          <button
            type="button"
            data-input-voice
            aria-pressed={voiceHint}
            onClick={() => {
              setVoiceHint(true)
              box.current?.focus()
            }}
          >
            <ToolIcon>{MIC}</ToolIcon>
            {WORDS.add.voiceButton}
          </button>
          <button type="button" data-input-paste onClick={() => void paste()}>
            <ToolIcon>{CLIPBOARD}</ToolIcon>
            {WORDS.add.paste}
          </button>
        </div>
        {voiceHint && (
          <p className="detail" role="status" data-voice-hint>
            {WORDS.add.voice}
          </p>
        )}
        {pasteHint && (
          <p className="detail" role="status" data-paste-hint>
            {WORDS.add.pasteFailed}
          </p>
        )}

        {/* カメラか写真の一覧かは、端末が聞いてくれる。写真はこの端末の中だけに保存する。 */}
        <input
          ref={picker}
          className="photo-input"
          type="file"
          accept="image/*"
          multiple
          aria-label={WORDS.photos.add}
          onChange={(event) => {
            const chosen = [...(event.target.files ?? [])].filter((file) =>
              file.type.startsWith('image/'),
            )
            setPhotos((current) => [...current, ...chosen])
            setFailed(null)
            // 同じ写真をもう一度選べるように。
            event.target.value = ''
          }}
        />
        {photos.length > 0 && (
          <>
            <div className="photo-row">
              {photos.map((file, index) => (
                <Thumbnail
                  key={`${file.name}-${index}`}
                  file={file}
                  label={WORDS.photos.removePhoto(index + 1)}
                  onRemove={() => setPhotos(photos.filter((_, other) => other !== index))}
                />
              ))}
            </div>
            <p className="detail">{WORDS.photos.onlyHere}</p>
            <div className="choices" role="group" aria-label={WORDS.photos.categoryLabel}>
              <p className="choice-label">{WORDS.photos.categoryLabel}</p>
              <div className="choice-wrap">
                {WORDS.photos.categories.map((name) => (
                  <button
                    key={name}
                    type="button"
                    className="choice"
                    aria-pressed={category === name}
                    onClick={() => {
                      setCategory(category === name ? null : name)
                      setLetRead(false)
                    }}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
            {/* 資料の写真だけ。親が選んだときだけAIへ送る。検査の結果には出さない。 */}
            {category && READABLE.includes(category) && (
              <div className="choices">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={letRead}
                  onClick={() => setLetRead(!letRead)}
                >
                  {WORDS.photos.letRead}
                </button>
                {letRead && (
                  <p ref={readNote} className="detail">
                    {WORDS.photos.readNote}
                  </p>
                )}
              </div>
            )}
          </>
        )}
        {failed && (
          <p className="message" role="alert">
            {failed}
          </p>
        )}
      </main>

      {/* 図のとおり、整理するは入れ方の続き。写真を選んで長くなったら、ナビの上に張り付けて見せる。 */}
      <div className="dock input-actions">
        <button
          type="button"
          className="primary"
          data-primary
          disabled={saving || (!text.trim() && photos.length === 0) || over > 0}
          onClick={() => {
            if (toAi && !consented) setAsking(true)
            else send()
          }}
        >
          {WORDS.add.submit}
        </button>
        <button type="button" className="quiet input-later" data-input-later onClick={onLater}>
          {WORDS.add.later}
        </button>
      </div>

      {recent.length > 0 && (
        <section className="input-recent" data-recent>
          <div className="input-recent-head">
            <h2>{WORDS.add.recent}</h2>
            <button type="button" className="quiet" data-recent-all onClick={onOpenList}>
              {WORDS.add.recentAll}
            </button>
          </div>
          {recent.map((entry) => (
            <button
              type="button"
              className="input-recent-row"
              key={entry.type === 'item' ? entry.item.id : entry.photo.id}
              data-recent-row={entry.type}
              onClick={() =>
                entry.type === 'item' ? onOpenItem(entry.item) : onOpenPhotos()
              }
            >
              <span className="input-recent-main">
                <span className="input-recent-meta">
                  {entry.at && <time dateTime={entry.at}>{rowTime(entry.at, today)}</time>}
                  <span>{kindOf(entry)}</span>
                </span>
                <span className="input-recent-text">{textOf(entry)}</span>
              </span>
              <span className="input-recent-link">
                {WORDS.add.source}
                <Chevron />
              </span>
            </button>
          ))}
        </section>
      )}
    </div>
  )
}
