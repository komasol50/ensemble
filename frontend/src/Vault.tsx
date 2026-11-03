import { useEffect, useState } from 'react'
import { api, type Item } from './api'
import {
  deletePhoto,
  listPhotos,
  READABLE,
  readPhoto,
  setPhotoCategory,
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
        <RemoveButton icon onConfirm={() => void remove()} />
      </div>
    </main>
  )
}

// 保管庫。全部の履歴と、種類ごとの棚。
export function VaultScreen({
  code,
  onBack,
  onChanged,
}: {
  code: string
  onBack: () => void
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

  return (
    <main className="vault">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.photos.vault}</h1>
      </header>
      <p className="detail">{WORDS.photos.onlyHere}</p>
      <div className="choice-wrap" role="group" aria-label={WORDS.photos.categoryLabel}>
        <button
          type="button"
          className="choice"
          aria-pressed={shelf === null}
          onClick={() => setShelf(null)}
        >
          {WORDS.photos.all}
        </button>
        {WORDS.photos.categories.map((name) => (
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
      {failed && (
        <p className="message" role="alert">
          {WORDS.photos.cannotUse}
        </p>
      )}
      {photos && shown.length === 0 && !failed && <p>{WORDS.photos.none}</p>}
      <div className="photo-grid">
        {shown.map((photo) => (
          <button
            key={photo.id}
            type="button"
            className="photo-tile"
            aria-label={WORDS.photos.alt(photo.category, dayOf(photo.at))}
            onClick={() => setOpenId(photo.id)}
          >
            <PhotoImage blob={photo.thumb} alt="" />
            <span>
              {dayOf(photo.at)} {photo.category}
            </span>
          </button>
        ))}
      </div>
    </main>
  )
}
