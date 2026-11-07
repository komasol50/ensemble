import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, type Item } from './api'
import { device, type AskedEntry, type Quoted } from './device'
import { Chevron, shortDay, shortTime, whoWord } from './parts'
import {
  deletePhoto,
  isReadDocument,
  listPhotos,
  READABLE,
  readPhoto,
  searchFreely,
  setPhotoCategory,
  type FreeAnswer,
  type Photo,
} from './photos'
import { RemoveButton } from './RemoveButton'
import { MEDICAL_FIXED_TEXT, WORDS } from './words'

// 写真のURLは表示している間だけ作り、終わったら手放す。
export function useBlobUrl(blob: Blob): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    const made = URL.createObjectURL(blob)
    setUrl(made)
    return () => URL.revokeObjectURL(made)
  }, [blob])
  return url
}

function dayOf(iso: string): string {
  const date = new Date(iso)
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

export function PhotoImage({ blob, alt, className }: { blob: Blob; alt: string; className?: string }) {
  const url = useBlobUrl(blob)
  return url ? <img src={url} alt={alt} className={className} /> : null
}

// 写真を大きく見る画面。読み取りや解釈はしない。
export function PhotoViewer({
  code,
  photo,
  items,
  onClose,
  onChanged,
}: {
  code: string
  photo: Photo
  items: Item[]
  onClose: () => void
  onChanged: () => void
}) {
  const [category, setCategory] = useState(photo.category)
  const [failed, setFailed] = useState(false)
  const [text, setText] = useState(photo.text)
  const [refused, setRefused] = useState(!!photo.refused)
  const [reading, setReading] = useState(false)
  const [readFailed, setReadFailed] = useState(false)
  const linked = photo.itemIds
    .map((id) => items.find((item) => item.id === id))
    .filter((item): item is Item => !!item)

  const change = async (next: string) => {
    setFailed(false)
    try {
      await setPhotoCategory({ ...photo, category }, next)
      setCategory(next)
      if (!READABLE.includes(next)) {
        setText(undefined)
        setRefused(false)
      }
      onChanged()
    } catch {
      setFailed(true)
    }
  }

  const read = async () => {
    setReading(true)
    setReadFailed(false)
    try {
      const result = await readPhoto(code, { ...photo, category })
      setText(result.text ?? undefined)
      setRefused(result.refused)
      setReadFailed(!result.text && !result.refused)
      onChanged()
    } catch {
      setReadFailed(true)
    } finally {
      setReading(false)
    }
  }

  const remove = async () => {
    setFailed(false)
    try {
      await deletePhoto(photo.id)
      onChanged()
      onClose()
    } catch {
      setFailed(true)
    }
  }

  return (
    <main className="viewer">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onClose}>
          {WORDS.add.back}
        </button>
        <h1>{dayOf(photo.at)}</h1>
      </header>
      <PhotoImage
        blob={photo.full}
        alt={WORDS.photos.alt(category, dayOf(photo.at))}
        className="viewer-image"
      />
      {linked.length > 0 && (
        <section className="viewer-texts">
          <h2>{WORDS.photos.linked}</h2>
          {linked.map((item) => (
            <p className="quote" key={item.id}>
              {item.text_original ?? item.task_summary}
            </p>
          ))}
        </section>
      )}
      {text && (
        <details className="read-text">
          <summary>{WORDS.photos.readText}</summary>
          <p className="original">{text}</p>
        </details>
      )}
      {refused && <p className="detail">{WORDS.photos.readRefused}</p>}
      {readFailed && (
        <p className="message" role="alert">
          {WORDS.photos.readFailed}
        </p>
      )}
      {READABLE.includes(category) && !text && !refused && (
        <div className="choices">
          <p className="detail">{WORDS.photos.readNote}</p>
          <button type="button" disabled={reading} onClick={() => void read()}>
            {reading ? WORDS.photos.reading : WORDS.photos.readNow}
          </button>
        </div>
      )}
      <div className="choices" role="group" aria-label={WORDS.photos.categoryLabel}>
        <p className="choice-label">{WORDS.photos.categoryLabel}</p>
        <div className="choice-wrap">
          {WORDS.photos.categories.map((name) => (
            <button
              key={name}
              type="button"
              className="choice"
              aria-pressed={category === name}
              onClick={() => void change(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </div>
      {failed && (
        <p className="message" role="alert">
          {WORDS.photos.failed}
        </p>
      )}
      <div className="actions">
        <RemoveButton
          icon
          armedLabel={WORDS.photos.removeAgain}
          onConfirm={() => void remove()}
        />
      </div>
    </main>
  )
}

// 読ませた資料が無いときの答え。AIを呼ばずに出す。
type Shown = FreeAnswer | { kind: 'nodocs' }

function LineIcon({ d, size = 22 }: { d: string; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  )
}

const MAGNIFIER = 'M8.5 3.5a5 5 0 1 1 0 10a5 5 0 0 1 0-10zM12.2 12.2l4.3 4.3'
const DOCUMENT = 'M5 2.5h6.5L15 6v11.5H5zM11.5 2.5V6H15'
const OPEN_PAGE = 'M4 2.5h7l3 3V8M4 2.5v15h5M12.5 10a2.8 2.8 0 1 1 0 5.6a2.8 2.8 0 0 1 0-5.6zM14.6 15.1l2.4 2.4'

// 前に聞いたことの時計の絵（UX Design：20px、線の太さ1.6）。
function ClockIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="10" cy="10" r="7.25" />
      <path d="M10 6v4.2l2.6 1.6" />
    </svg>
  )
}

const PARENT_SPEAKERS = ['親', '母', '父', '私', '自分']

// 出どころの語。項目の speaker・part・要約かどうかから作る（AIの言葉ではない）。
function sourceOf(quoted: Quoted, errand?: string): string {
  const words = WORDS.ask
  if (quoted.source === 'doc') return `${words.fromDoc(quoted.category)} ${shortDay(quoted.at)}`
  const who = quoted.speaker ?? whoWord(quoted.part, errand)
  let label: string
  if (quoted.summary) label = words.fromSummary(who)
  else if (quoted.itemId.startsWith('s-')) label = words.fromSent(whoWord(quoted.part, errand))
  else if (quoted.itemId.startsWith('n-')) label = words.fromNote(who)
  else if (!quoted.speaker) label = words.fromRecord(whoWord(quoted.part, errand))
  else if (PARENT_SPEAKERS.includes(quoted.speaker)) label = words.fromYou
  else if (quoted.part === '家族') label = words.fromFamily(quoted.speaker)
  else label = words.fromWords(quoted.speaker)
  const time = shortTime(quoted.recordedAt)
  if (!time) return label
  return label.endsWith('）') ? `${label}${time}` : `${label} ${time}`
}

// 見つかった引用1件。引用はそのまま、下に出どころ。確定でなければ確かさの札を付ける。
// ledger が false（今までの④）のときは、今までと同じ形で出す。
function QuoteView({
  quoted,
  ledger,
  errand,
  onOpenPhoto,
  onOpenRow,
}: {
  quoted: Quoted
  ledger: boolean
  errand?: string
  onOpenPhoto?: () => void
  onOpenRow?: () => void
}) {
  let body: ReactNode
  if (quoted.source === 'doc') {
    body = (
      <>
        {/* しおりの文そのまま。言い換えない。 */}
        <p className="quote search-quote">{quoted.quote}</p>
        <p className="detail" data-search-source>
          {ledger ? sourceOf(quoted) : `${quoted.category}・${shortDay(quoted.at)}`}
        </p>
        {onOpenPhoto && (
          <button type="button" className="search-open" data-search-open onClick={onOpenPhoto}>
            <LineIcon d={OPEN_PAGE} />
            {WORDS.photos.openPage}
          </button>
        )}
      </>
    )
  } else {
    body = (
      <>
        {/* やりとりの文そのまま（第三者の項目は、原文を残していないので用件の要約）。 */}
        <p className="quote search-quote">{quoted.quote}</p>
        <p className="detail search-source" data-search-source>
          <span>{sourceOf(quoted, errand)}</span>
          {quoted.certainty && quoted.certainty !== '確定' && (
            <span className="chip likely" data-search-certainty>
              {quoted.certainty}
            </span>
          )}
        </p>
        {onOpenRow && (
          <button type="button" className="quiet search-row" data-search-row onClick={onOpenRow}>
            {WORDS.ask.openRow}
          </button>
        )}
      </>
    )
  }
  return ledger ? (
    <div className="search-answer" data-search-answer={quoted.source}>
      {body}
    </div>
  ) : (
    body
  )
}

// 前に聞いたことの1行。押すと開き、保存した答えをそのまま出す（AIを呼び直さない）。
function AskedRow({
  entry,
  errand,
  photoOf,
  rowOf,
  onOpenPhoto,
  onOpenItem,
  onAskAgain,
  onRemove,
}: {
  entry: AskedEntry
  errand?: string
  photoOf: (id: string) => Photo | undefined
  rowOf: (quoted: Quoted) => Item | undefined
  onOpenPhoto: (id: string) => void
  onOpenItem?: (item: Item) => void
  onAskAgain: () => void
  onRemove: () => void
}) {
  const words = WORDS.ask
  const [open, setOpen] = useState(false)
  const answer = entry.answer
  const first = answer.kind === 'found' ? answer.quotes[0] : undefined
  const line =
    answer.kind === 'medical'
      ? words.historyAnswer(words.historyMedical)
      : first
        ? words.historyQuote(first.quote)
        : words.historyAnswer(words.historyMissing)
  const meta = [first ? sourceOf(first, errand) : '', words.askedAt(shortTime(entry.at))]
    .filter(Boolean)
    .join('・')
  return (
    <li className="asked-row" data-asked-row>
      <button
        type="button"
        className="asked-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="asked-text">
          <strong className="asked-question">{entry.question}</strong>
          <span className="asked-answer">{line}</span>
          <span className="asked-meta">{meta}</span>
        </span>
        <Chevron to={open ? 'up' : 'down'} />
      </button>
      {open && (
        <div className="asked-body" data-asked-body>
          {answer.kind === 'found' &&
            answer.quotes.map((quoted, index) => {
              const row = quoted.source === 'item' ? rowOf(quoted) : undefined
              return (
                <QuoteView
                  key={index}
                  quoted={quoted}
                  ledger
                  errand={errand}
                  onOpenPhoto={
                    quoted.source === 'doc' && photoOf(quoted.photoId)
                      ? () => onOpenPhoto(quoted.photoId)
                      : undefined
                  }
                  onOpenRow={row && onOpenItem ? () => onOpenItem(row) : undefined}
                />
              )
            })}
          {/* 医療の問いは、固定文を返した印だけを残している。 */}
          {answer.kind === 'medical' && <p className="medical">{MEDICAL_FIXED_TEXT}</p>}
          {answer.kind === 'missing' && <p>{words.notFound}</p>}
          <div className="asked-actions">
            <button type="button" className="quiet" data-asked-again onClick={onAskAgain}>
              {words.askAgain}
            </button>
            <RemoveButton
              className="quiet"
              label={words.remove}
              armedLabel={WORDS.board.removeAgain}
              onConfirm={onRemove}
            />
          </div>
        </div>
      )}
    </li>
  )
}

// しおりから探す（図の④）。上に探す欄と候補の語、下に入れた写真の一覧（畳む）。
// 戻るは下のナビで（見出しの「戻る」は置かない）。onBack は呼び出し側の型のために残す。
export function VaultScreen({
  code,
  onAdd,
  onChanged,
  onOpenItem,
}: {
  code: string
  onBack: () => void
  onAdd: () => void
  onChanged: () => void
  // 見つかったやりとりの行を開く（App の revealItem と同じ道）。
  onOpenItem?: (item: Item) => void
}) {
  const [photos, setPhotos] = useState<Photo[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [shelf, setShelf] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [items, setItems] = useState<Item[]>([])
  // 夫がリンクから開いた画面では、やりとりを探さず、前に聞いたことも出さない。
  const [fromLink] = useState(() => device.fromLink(code))
  // サーバーの切り替え（やりとりも探すか）。読めるまでは探す欄を出さない（語が入れ替わらないように）。
  const [scope, setScope] = useState<'loading' | 'ledger' | 'docs'>('loading')
  const [errand, setErrand] = useState<string | undefined>(undefined)
  const [history, setHistory] = useState<AskedEntry[]>(() => device.asked(code))
  const historyRef = useRef<HTMLElement>(null)
  const ledger = scope === 'ledger' && !fromLink

  const read = () => {
    listPhotos(code)
      .then((next) => {
        setPhotos(next)
        setFailed(false)
      })
      .catch(() => setFailed(true))
  }

  useEffect(() => {
    read()
    // 一緒に入れた文を出すため、やりとりの行を開くため、切り替えを知るために読む。
    // 読めなくても写真は見られ、資料だけを探す（今までの④）。
    api
      .getBoard(code)
      .then((board) => {
        setItems(board.items)
        setErrand(board.profile.errand_people[0]?.label)
        setScope(board.ledger_search ? 'ledger' : 'docs')
      })
      .catch(() => setScope('docs'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  // 見つかったやりとりの行。夫のひとことは行に描かないので、頼んだ用事の行を開く。
  const rowOf = (quoted: Quoted): Item | undefined => {
    if (quoted.source !== 'item') return undefined
    const item = items.find((entry) => entry.id === quoted.itemId)
    const target = item?.id.startsWith('n-') && item.refers_to ? item.refers_to : quoted.itemId
    return items.find((entry) => entry.id === target)
  }
  const photoOf = (id: string) => photos?.find((photo) => photo.id === id)

  const remember = (question: string, result: FreeAnswer) => {
    if (fromLink) return
    const entry: AskedEntry = {
      id: `${Date.now()}-${Math.random()}`,
      question,
      at: new Date().toISOString(),
      // 医療の問いは、固定文を返した印だけ。
      answer:
        result.kind === 'medical'
          ? { kind: 'medical' }
          : result.kind === 'found'
            ? { kind: 'found', quotes: result.quotes }
            : { kind: 'missing', hospital: result.hospital },
    }
    // 同じ問いを聞き直したら、前の答えは新しい答えに置き換える。
    const next = [entry, ...device.asked(code).filter((old) => old.question !== question)]
    device.setAsked(code, next)
    setHistory(next.slice(0, 50))
  }

  const forget = (id: string) => {
    const next = device.asked(code).filter((entry) => entry.id !== id)
    device.setAsked(code, next)
    setHistory(next)
  }

  // 写真を開いて戻ったときも、畳んだ一覧を開いたままにする。
  const [listOpen, setListOpen] = useState(false)
  const [question, setQuestion] = useState('')
  // 探した問い。答えの後に欄を書き換えても、残すのは探した問い。
  const [asked, setAsked] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<Shown | null>(null)
  const [searchNote, setSearchNote] = useState<string | null>(null)
  const readDocs = (photos ?? []).filter(isReadDocument)
  const tags = [...new Set(readDocs.map((photo) => photo.category))].map((category) => ({
    category,
    count: readDocs.filter((photo) => photo.category === category).length,
  }))

  // 医療の問いの仕分けとAIの呼び出しは今のまま（searchFreely → /api/board/search）。
  const search = async (text: string = question) => {
    const trimmed = text.trim()
    if (!trimmed || asking) return
    setAnswer(null)
    setSearchNote(null)
    setAsked(trimmed)
    setAsking(true)
    try {
      // 資料が0枚でも送る。医療の語はサーバーが先に見て固定文を返し、AIは呼ばない。
      const result = await searchFreely(code, trimmed, ledger)
      setAnswer(result)
      remember(trimmed, result)
    } catch (error) {
      // 探すものが無ければ（資料が0枚で、やりとりも探さないか、探せる行が無い）、資料の追加へ案内する。
      const nothingElse = !ledger || items.every((item) => item.part === '親' && item.kind === '情報')
      if (
        readDocs.length === 0 &&
        nothingElse &&
        error instanceof ApiError &&
        error.status === 422
      ) {
        setAnswer({ kind: 'nodocs' })
      } else {
        setSearchNote(error instanceof ApiError && error.message ? error.message : WORDS.photos.searchFailed)
      }
    } finally {
      setAsking(false)
    }
  }

  const keepQuestion = async () => {
    setAsking(true)
    try {
      const board = await api.getBoard(code)
      await api.addQuestion(code, board.version, asked)
      setAnswer(null)
      setSearchNote(WORDS.photos.kept)
      onChanged()
    } catch {
      setSearchNote(WORDS.board.failed)
    } finally {
      setAsking(false)
    }
  }

  const opened = photos?.find((photo) => photo.id === openId)
  if (opened) {
    return (
      <PhotoViewer
        code={code}
        photo={opened}
        items={items}
        onClose={() => setOpenId(null)}
        onChanged={() => {
          read()
          onChanged()
        }}
      />
    )
  }

  const shown = (photos ?? []).filter((photo) => !shelf || photo.category === shelf)

  const words = WORDS.photos
  const ask = WORDS.ask
  // 切り替えが on で親の画面なら「質問する」の語、off なら今までの④の語のまま。
  const title = ledger ? ask.title : words.searchTitle
  const lead = ledger ? ask.lead : words.searchLead
  const chips = ledger ? ask.chips : words.searchChips
  const showHistory = !fromLink
  return (
    <main className="vault" data-ledger={scope === 'loading' ? undefined : ledger ? 'on' : 'off'}>
      {scope === 'loading' ? null : (
        <>
          <header className="search-head">
            <div className="search-title-row">
              <h1>{title}</h1>
              {showHistory && (
                <button
                  type="button"
                  className="icon-button search-history"
                  data-asked-open
                  aria-label={ask.historyButton}
                  onClick={() =>
                    historyRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }
                >
                  <ClockIcon />
                </button>
              )}
            </div>
            <p className="detail">{lead}</p>
          </header>
          {searchArea()}
        </>
      )}
      {showHistory && (
        <section className="asked" data-asked ref={historyRef} aria-labelledby="asked-title">
          <h2 id="asked-title" className="asked-title">
            <ClockIcon />
            {ask.history}
          </h2>
          {history.length === 0 ? (
            <p className="asked-none" data-asked-none>
              {ask.historyNone}
            </p>
          ) : (
            <ul className="asked-list">
              {history.map((entry) => (
                <AskedRow
                  key={entry.id}
                  entry={entry}
                  errand={errand}
                  photoOf={photoOf}
                  rowOf={rowOf}
                  onOpenPhoto={setOpenId}
                  onOpenItem={onOpenItem}
                  onAskAgain={() => {
                    setQuestion(entry.question)
                    window.scrollTo({ top: 0, behavior: 'smooth' })
                    void search(entry.question)
                  }}
                  onRemove={() => forget(entry.id)}
                />
              ))}
            </ul>
          )}
          <p className="detail">{ask.historyNote}</p>
        </section>
      )}
      <details className="fold search-help" data-search-help>
        <summary>
          <span className="search-help-head">
            <span>{words.helpTitle}</span>
            <span className="search-help-sub">{words.helpSub}</span>
          </span>
        </summary>
        <p className="detail">{words.helpBody}</p>
      </details>
      {failed && (
        <p className="message" role="alert">
          {words.cannotUse}
        </p>
      )}
      {/* 「資料を追加する」は画面に1つだけ（0枚の答えに出している間は、ここに出さない）。 */}
      {answer?.kind !== 'nodocs' && (
        <button type="button" className="quiet search-add" data-search-add onClick={onAdd}>
          {words.addDocument}
        </button>
      )}
      {photos && photos.length > 0 && (
        <details
          className="fold small"
          data-search-list
          open={listOpen}
          onToggle={(event) => setListOpen(event.currentTarget.open)}
        >
          <summary>{words.photoList(photos.length)}</summary>
          <p className="detail">{words.onlyHere}</p>
          <div className="choice-wrap" role="group" aria-label={words.categoryLabel}>
            <button
              type="button"
              className="choice"
              aria-pressed={shelf === null}
              onClick={() => setShelf(null)}
            >
              {words.all}
            </button>
            {words.categories.map((name) => (
              <button
                key={name}
                type="button"
                className="choice"
                aria-pressed={shelf === name}
                onClick={() => setShelf(name)}
              >
                {name}
              </button>
            ))}
          </div>
          {shown.length === 0 && <p>{words.none}</p>}
          <div className="photo-grid">
            {shown.map((photo) => (
              <button
                key={photo.id}
                type="button"
                className="photo-tile"
                aria-label={words.alt(photo.category, dayOf(photo.at))}
                onClick={() => setOpenId(photo.id)}
              >
                <PhotoImage blob={photo.thumb} alt="" />
                <span>
                  {dayOf(photo.at)} {photo.category}
                </span>
              </button>
            ))}
          </div>
        </details>
      )}
    </main>
  )

  // 探す欄、候補の語、答え。答えは候補の語のすぐ下に出す（前に聞いたことが長くても、答えが隠れないように）。
  function searchArea() {
    return (
      <>
      {tags.length > 0 && (
        <ul className="search-docs">
          {tags.map((tag) => (
            <li key={tag.category} className="search-doc" data-search-doc={tag.category}>
              <LineIcon d={DOCUMENT} />
              {words.docTag(tag.category, tag.count)}
            </li>
          ))}
        </ul>
      )}
      {/* 資料が0枚でも出す。0枚で探すと、AIを呼ばずに資料の追加へ案内する。 */}
      <form
        className="search-box"
        data-search-box
        onSubmit={(event) => {
          event.preventDefault()
          void search()
        }}
      >
        <input
          type="text"
          maxLength={200}
          aria-label={ledger ? ask.label : words.searchLabel}
          placeholder={ledger ? ask.placeholder : words.searchPlaceholder}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button
          type="submit"
          className="search-go"
          aria-label={ledger ? ask.go : words.searchGo}
          aria-busy={asking}
          disabled={asking || !question.trim()}
        >
          <LineIcon d={MAGNIFIER} size={24} />
        </button>
      </form>
      <div className="search-chips" role="group" aria-label={words.chipsLabel}>
        {chips.map((chip) => (
          <button
            key={chip.label}
            type="button"
            className="search-chip"
            data-search-chip={chip.label}
            disabled={asking}
            onClick={() => {
              setQuestion(chip.question)
              void search(chip.question)
            }}
          >
            {chip.label}
          </button>
        ))}
      </div>
      {asking && (
        <p className="detail" role="status">
          {words.searching}
        </p>
      )}
      {searchNote && (
        <p className="detail" role="status">
          {searchNote}
        </p>
      )}
      {answer?.kind === 'nodocs' && (
        <section className="now-card" data-search-nodocs>
          <p>{photos && photos.length > 0 ? words.notReadYet : words.none}</p>
          <button type="button" onClick={onAdd}>
            {words.addDocument}
          </button>
        </section>
      )}
      {/* 医療の問いのときだけ固定文。資料は出さない。 */}
      {answer?.kind === 'medical' && (
        <p className="medical" data-search-medical>
          {answer.text}
        </p>
      )}
      {answer?.kind === 'found' && (
        <section className="now-card" data-search-found>
          <h2 className="search-found-title">{ledger ? ask.found : words.written}</h2>
          {answer.quotes.map((quoted, index) => {
            const row = rowOf(quoted)
            return (
              <QuoteView
                key={index}
                quoted={quoted}
                ledger={ledger}
                errand={errand}
                onOpenPhoto={
                  quoted.source === 'doc' ? () => setOpenId(quoted.photoId) : undefined
                }
                onOpenRow={row && onOpenItem ? () => onOpenItem(row) : undefined}
              />
            )
          })}
        </section>
      )}
      {answer?.kind === 'missing' && (
        <section className="now-card" data-search-missing>
          <p>{ledger ? ask.notFound : words.notFoundShiori}</p>
          {/* やりとりも探すときは、病院のことだけ残せる（ほかは読むだけ）。 */}
          {ledger && answer.hospital && (
            <p className="detail" data-search-hospital>
              {ask.hospitalHint}
            </p>
          )}
          {(!ledger || answer.hospital) && (
            <button type="button" disabled={asking} onClick={() => void keepQuestion()}>
              {words.keepQuestion}
            </button>
          )}
        </section>
      )}
      </>
    )
  }
}
