import { api, type Item } from './api'
import { device, type VaultAnswer } from './device'

// 写真はこの端末の中（IndexedDB）にだけ置く。サーバーには残さない。
// AIへ送るのは、資料の写真で、親が「読ませる」を選んだものだけ。
// 検査の結果は読ませない。実在の情報が写りやすく、値の解釈に近づくため。

const DB_NAME = 'ensemble-photos'
const STORE = 'photos'
const FULL_MAX = 2000 // 資料の字が読める大きさ
const THUMB_MAX = 400
const QUALITY = 0.85
const MAX_DOCS = 30
const MAX_DOC_CHARS = 20_000
const MAX_TOTAL_CHARS = 60_000

export const READABLE = ['入院のしおり', '病院の資料']

export type Photo = {
  id: string
  code: string
  category: string
  at: string
  full: Blob
  thumb: Blob
  // 一緒に入れた文が仕分けられた項目。写真だけのときは空。
  itemIds: string[]
  pendingId?: string
  // 読み取った文。AIに読ませた資料だけにある。
  text?: string
  refused?: boolean
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(DB_NAME, 1)
    opening.onupgradeneeded = () => {
      const store = opening.result.createObjectStore(STORE, { keyPath: 'id' })
      store.createIndex('code', 'code')
    }
    opening.onsuccess = () => resolve(opening.result)
    opening.onerror = () => reject(opening.error)
  })
}

async function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => Promise<T>,
): Promise<T> {
  const db = await openDb()
  try {
    const transaction = db.transaction(STORE, mode)
    const finished = new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
      transaction.onabort = () => reject(transaction.error)
    })
    const result = await work(transaction.objectStore(STORE))
    await finished
    return result
  } finally {
    db.close()
  }
}

function resize(image: CanvasImageSource, width: number, height: number, max: number) {
  const scale = Math.min(1, max / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width * scale))
  canvas.height = Math.max(1, Math.round(height * scale))
  const context = canvas.getContext('2d')
  if (!context) return Promise.resolve<Blob | null>(null)
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY))
}

function decode(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      URL.revokeObjectURL(url)
      resolve(image)
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('decode'))
    }
    image.src = url
  })
}

// 端末の容量を食わないよう、読める大きさに縮める。縮められない形式は、そのまま残す。
async function shrink(file: File): Promise<{ full: Blob; thumb: Blob }> {
  try {
    const image = await decode(file)
    const full = await resize(image, image.naturalWidth, image.naturalHeight, FULL_MAX)
    const thumb = await resize(image, image.naturalWidth, image.naturalHeight, THUMB_MAX)
    if (full && thumb) return { full, thumb }
  } catch {
    // 下でそのまま残す。
  }
  return { full: file, thumb: file }
}

// 消されにくくするよう頼む。断られても動く。
function askToKeep(): void {
  void navigator.storage?.persist?.().catch(() => undefined)
}

export async function savePhotos(
  code: string,
  files: File[],
  category: string,
  pendingId?: string,
): Promise<Photo[]> {
  askToKeep()
  // 縮める処理は時間がかかるので、保存の取引の外で先に済ませる。
  const prepared: Photo[] = []
  for (const file of files) {
    const { full, thumb } = await shrink(file)
    prepared.push({
      id: `${Date.now()}-${Math.random()}`,
      code,
      category,
      at: new Date().toISOString(),
      full,
      thumb,
      itemIds: [],
      pendingId,
    })
  }
  await withStore('readwrite', async (store) => {
    for (const photo of prepared) await request(store.put(photo))
  })
  return prepared
}

// 読み取りや項目との結びが同時に進んでも上書きし合わないよう、保存の中で読み直して変える。
async function changePhoto(id: string, change: (photo: Photo) => Photo): Promise<void> {
  await withStore('readwrite', async (store) => {
    const current = (await request(store.get(id))) as Photo | undefined
    if (current) await request(store.put(change(current)))
  })
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

// 親が選んだ資料の写真を読み取り、読み取った文を端末に残す。
export async function readPhoto(
  code: string,
  photo: Photo,
): Promise<{ text: string | null; refused: boolean }> {
  const image = await toBase64(photo.full)
  const result = await api.readPhoto(code, image, photo.full.type, photo.category)
  await changePhoto(photo.id, (current) => ({
    ...current,
    text: result.text ?? undefined,
    refused: result.refused,
  }))
  return { text: result.text, refused: result.refused }
}

// 保管庫から探せる質問か。医療の問いと、人に聞く質問は探さない。
export function canSearch(item: Item): boolean {
  return (
    item.kind === '質問' &&
    !item.medical_reply &&
    item.status !== '答えあり' &&
    (item.part === '病院' || item.part === '親')
  )
}

export function isReadDocument(photo: Photo): boolean {
  return !!photo.text && READABLE.includes(photo.category)
}

// 親の質問に答える箇所を、読み取った資料から探す。読んだ資料が無ければ何もしない。
export async function searchVault(code: string, itemId: string): Promise<VaultAnswer | null> {
  const documents = (await listPhotos(code)).filter(isReadDocument)
  const docs: { id: string; category: string; text: string }[] = []
  let total = 0
  for (const photo of documents.slice(0, MAX_DOCS)) {
    const text = (photo.text ?? '').slice(0, MAX_DOC_CHARS)
    if (total + text.length > MAX_TOTAL_CHARS) break
    total += text.length
    docs.push({ id: photo.id, category: photo.category, text })
  }
  if (docs.length === 0) return null
  const result = await api.searchItem(code, itemId, docs)
  const source = documents.find((photo) => photo.id === result.doc_id)
  const answer: VaultAnswer =
    result.found && source && result.quote
      ? { found: true, photoId: source.id, category: source.category, quote: result.quote }
      : { found: false }
  device.setAnswer(code, itemId, answer)
  return answer
}

export async function listPhotos(code: string): Promise<Photo[]> {
  const photos = await withStore('readonly', (store) =>
    request(store.index('code').getAll(code) as IDBRequest<Photo[]>),
  )
  return photos.sort((a, b) => b.at.localeCompare(a.at))
}

// 一緒に入れた文の仕分けが終わったら、項目と結ぶ。
export async function linkPending(
  code: string,
  pendingId: string,
  itemIds: string[],
): Promise<boolean> {
  const photos = await listPhotos(code)
  const targets = photos.filter((photo) => photo.pendingId === pendingId)
  if (targets.length === 0) return false
  for (const photo of targets) {
    await changePhoto(photo.id, (current) => ({ ...current, itemIds, pendingId: undefined }))
  }
  return true
}

// 資料でない種類へ変えたら、読み取った文も消す。探す対象に残さないため。
export async function setPhotoCategory(photo: Photo, category: string): Promise<void> {
  await changePhoto(photo.id, (current) => ({
    ...current,
    category,
    text: READABLE.includes(category) ? current.text : undefined,
    refused: READABLE.includes(category) ? current.refused : undefined,
  }))
}

export async function deletePhoto(id: string): Promise<void> {
  await withStore('readwrite', async (store) => {
    await request(store.delete(id))
  })
}

// 「このボードを消す」と一緒に消す。
export async function deleteBoardPhotos(code: string): Promise<void> {
  const photos = await listPhotos(code)
  await withStore('readwrite', async (store) => {
    for (const photo of photos) await request(store.delete(photo.id))
  })
}
