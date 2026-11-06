import { useEffect, useState } from 'react'
import { api, ApiError, type Item } from './api'
import { shortDay } from './parts'
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
import { WORDS } from './words'

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

// しおりから探す（図の④）。上に探す欄と候補の語、下に入れた写真の一覧（畳む）。
// 戻るは下のナビで（見出しの「戻る」は置かない）。onBack は呼び出し側の型のために残す。
export function VaultScreen({
  code,
  onAdd,
  onChanged,
}: {
  code: string
  onBack: () => void
  onAdd: () => void
  onChanged: () => void
}) {
  const [photos, setPhotos] = useState<Photo[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [shelf, setShelf] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [items, setItems] = useState<Item[]>([])

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
    // 一緒に入れた文を出すためだけに読む。読めなくても写真は見られる。
    api
      .getBoard(code)
      .then((board) => setItems(board.items))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

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
    // 読ませた資料が無ければ、AIを呼ばずに資料の追加へ案内する。
    if (readDocs.length === 0) {
      setAnswer({ kind: 'nodocs' })
      return
    }
    setAsking(true)
    try {
      setAnswer(await searchFreely(code, trimmed))
    } catch (error) {
      setSearchNote(error instanceof ApiError && error.message ? error.message : WORDS.photos.searchFailed)
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
  return (
    <main className="vault">
      <header className="search-head">
        <h1>{words.searchTitle}</h1>
        <p className="detail">{words.searchLead}</p>
      </header>
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
          aria-label={words.searchLabel}
          placeholder={words.searchPlaceholder}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button
          type="submit"
          className="search-go"
          aria-label={words.searchGo}
          aria-busy={asking}
          disabled={asking || !question.trim()}
        >
          <LineIcon d={MAGNIFIER} size={24} />
        </button>
      </form>
      <div className="search-chips" role="group" aria-label={words.chipsLabel}>
        {words.searchChips.map((chip) => (
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
          <h2 className="search-found-title">{words.written}</h2>
          {/* しおりの文そのまま。言い換えない。 */}
          <p className="quote search-quote">{answer.quote}</p>
          <p className="detail" data-search-source>
            {answer.photo.category}・{shortDay(answer.photo.at)}
          </p>
          <button
            type="button"
            className="search-open"
            data-search-open
            onClick={() => setOpenId(answer.photo.id)}
          >
            <LineIcon d={OPEN_PAGE} />
            {words.openPage}
          </button>
        </section>
      )}
      {answer?.kind === 'missing' && (
        <section className="now-card" data-search-missing>
          <p>{words.notFoundShiori}</p>
          <button type="button" disabled={asking} onClick={() => void keepQuestion()}>
            {words.keepQuestion}
          </button>
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
}
