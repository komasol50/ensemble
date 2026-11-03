// 写真はこの端末の中（IndexedDB）にだけ置く。サーバーにもAIにも送らない。
// 実在の検査結果が写りやすいので、外へ出さないことを守りにする。AIに読ませないので、医療の線も越えない。

const DB_NAME = 'ensemble-photos'
const STORE = 'photos'
const FULL_MAX = 2000 // 資料の字が読める大きさ
const THUMB_MAX = 400
const QUALITY = 0.85

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
): Promise<void> {
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
  await withStore('readwrite', async (store) => {
    for (const photo of targets) {
      await request(store.put({ ...photo, itemIds, pendingId: undefined }))
    }
  })
  return true
}

export async function setPhotoCategory(photo: Photo, category: string): Promise<void> {
  await withStore('readwrite', async (store) => {
    await request(store.put({ ...photo, category }))
  })
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
