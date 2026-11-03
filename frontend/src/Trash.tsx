import { useEffect, useState } from 'react'
import { api, ApiError, type TrashEntry } from './api'
import {
  listDeletedPhotos,
  purgePhoto,
  restorePhoto,
  type Photo,
} from './photos'
import { RemoveButton } from './RemoveButton'
import { PhotoImage } from './Vault'
import { WORDS } from './words'

// ゴミ箱。消した項目と写真を戻せる。完全に消すと戻せない（2回押し）。
export function TrashScreen({
  code,
  onBack,
  onRestored,
}: {
  code: string
  onBack: () => void
  onRestored: () => void
}) {
  const [entries, setEntries] = useState<TrashEntry[] | null>(null)
  const [photos, setPhotos] = useState<Photo[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const read = async () => {
    try {
      const body = await api.getTrash(code)
      setEntries(body.entries)
    } catch {
      setMessage(WORDS.board.offline)
    }
    // 写真を保存できないブラウザでも、項目は戻せる。
    listDeletedPhotos(code)
      .then(setPhotos)
      .catch(() => setPhotos([]))
  }

  useEffect(() => {
    void read()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  const act = async (work: () => Promise<unknown>, done: string) => {
    if (busy) return
    setBusy(true)
    setMessage(null)
    try {
      await work()
      setMessage(done)
      await read()
    } catch (error) {
      setMessage(error instanceof ApiError && error.message ? error.message : WORDS.trash.failed)
    } finally {
      setBusy(false)
    }
  }

  const isEmpty = entries?.length === 0 && photos.length === 0

  return (
    <main className="trash">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.trash.title}</h1>
      </header>

      {message && (
        <p className="message" role="status">
          {message}
        </p>
      )}
      {isEmpty && <p>{WORDS.trash.none}</p>}

      {entries?.map((entry) => (
        <article className="card" key={entry.id} data-trash={entry.item_id}>
          <div className="chips">
            <span className="chip">{entry.part}</span>
            {entry.status_label && <span className="chip">{entry.status_label}</span>}
          </div>
          <h3>{entry.text}</h3>
          {entry.withdrew && <p className="detail">{WORDS.trash.withdrew}</p>}
          <div className="trash-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await api.restoreTrash(code, entry.id)
                  onRestored()
                }, WORDS.trash.restored)
              }
            >
              {WORDS.trash.restore}
            </button>
            <RemoveButton
              disabled={busy}
              label={WORDS.trash.purge}
              armedLabel={WORDS.trash.purgeAgain}
              onConfirm={() =>
                void act(() => api.purgeTrash(code, entry.id), WORDS.trash.purged)
              }
            />
          </div>
        </article>
      ))}

      {photos.map((photo) => (
        <article className="card" key={photo.id} data-trash-photo={photo.id}>
          <div className="chips">
            <span className="chip">{photo.category}</span>
          </div>
          <div className="photo-row">
            <span className="photo-thumb-static">
              <PhotoImage blob={photo.thumb} alt={WORDS.trash.photoAlt(photo.category)} />
            </span>
          </div>
          <div className="trash-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await restorePhoto(photo.id)
                  onRestored()
                }, WORDS.trash.restored)
              }
            >
              {WORDS.trash.restore}
            </button>
            <RemoveButton
              disabled={busy}
              label={WORDS.trash.purge}
              armedLabel={WORDS.trash.purgeAgain}
              onConfirm={() => void act(() => purgePhoto(photo.id), WORDS.trash.purged)}
            />
          </div>
        </article>
      ))}
    </main>
  )
}
