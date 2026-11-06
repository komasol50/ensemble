import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Board, type ImpactNode, type Week } from './api'
import { reviewLines } from './Decide'
import { device } from './device'
import { inQuotes, PartIcon, stateText, stateWord, Timeline, toneOf } from './parts'
import { isSentMessage, SendCard, sendsOf, type Send } from './Send'
import { WORDS } from './words'

// 線は固定の配置で描く（グラフのライブラリは入れない）。カードの高さと合わせる。
const NODE_HEIGHT = 100
const NODE_GAP = 12
const TRUNK_X = 10
const LINE_END = 44
const DOUBLE_OFFSET = 8
const LIGHT_STEP_SECONDS = 1
const POLL_MS = 3000

const EFFECT_CLASS: Record<string, string> = {
  変える: 'change',
  外す: 'release',
  そのまま: 'keep',
}

// 前→後の1行。文が長ければ文のほうを省略し、「→ 取り消し」は切らない。
function ChangeLine({ text }: { text: string }) {
  const end = WORDS.map.changeEnd
  const cancels = text.endsWith(end)
  return (
    <span className="map-node-change">
      <span className="map-node-change-text">{cancels ? text.slice(0, -end.length) : text}</span>
      {cancels && <span className="map-node-change-end">{end}</span>}
    </span>
  )
}

// 下の帯。週の表の「退院」の列を1行のマスにする。取り消した日は薄く残す。
export function WeekStrip({ week, delay }: { week: Week | null; delay: number }) {
  if (!week) return null
  return (
    <section
      aria-label={WORDS.map.strip}
      className="map-strip"
      style={{ animationDelay: `${delay}s` }}
    >
      <ol className="week-strip">
        {week.rows.map((row, index) => {
          const entry = row.cells[0][0]
          return (
            <li key={index} className={entry ? `strip-day ${entry.style}` : 'strip-day'}>
              <span className="strip-label">{row.label}</span>
              <span className="strip-mark">{entry ? WORDS.map.mark(entry.text) : ''}</span>
            </li>
          )
        })}
      </ol>
      {week.others.map((text) => (
        <p className="detail" key={text}>
          {WORDS.week.other(text)}
        </p>
      ))}
    </section>
  )
}

// 1人分の「やること」。その場で送れる。
function NodeCard({
  node,
  sends,
  board,
  code,
  busy,
  message,
  shared,
  onShared,
  onAsked,
  onBack,
}: {
  node: ImpactNode
  sends: Send[]
  board: Board
  code: string
  busy: boolean
  message: string | null
  shared: string[]
  onShared: (id: string) => void
  onAsked: (id: string) => void
  onBack: () => void
}) {
  const alreadySent = node.after.filter((entry) => entry.state && entry.state !== '未依頼')
  return (
    <main className="map-screen">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{node.task}</h1>
      </header>
      {message && (
        <p className="message" role="alert">
          {message}
        </p>
      )}
      {sends.map((send) => (
        <SendCard
          key={send.item.id}
          send={send}
          code={code}
          items={board.items}
          shared={shared.includes(send.item.id)}
          busy={busy}
          onShared={() => onShared(send.item.id)}
          onAsked={() => onAsked(send.item.id)}
        />
      ))}
      {alreadySent.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.sent}</h2>
          {alreadySent.map((entry) => {
            const sentItem = board.items.find(
              (item) =>
                isSentMessage(item) && item.part === node.part && item.text_original === entry.text,
            )
            return (
              <article className="card" key={entry.text}>
                <div className="chips">
                  <span className="chip">{stateText(entry.state)}</span>
                </div>
                <p className="original">{entry.text}</p>
                {sentItem && <Timeline item={sentItem} />}
              </article>
            )
          })}
        </section>
      )}
      {node.before.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.before}</h2>
          {node.before.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </section>
      )}
      {node.kept.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.kept}</h2>
          {node.kept.map((entry) => (
            <p key={entry.text}>
              {entry.text}
              {entry.state && `：${stateText(entry.state)}`}
            </p>
          ))}
        </section>
      )}
      {/* 送るカードに根拠が出ているときは、重ねて出さない。 */}
      {sends.length === 0 && node.basis.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.basis}</h2>
          {node.basis.map((entry) => (
            <p className="quote glow" key={entry.text}>
              {entry.text}
            </p>
          ))}
        </section>
      )}
    </main>
  )
}

// 線とカード。案を進める前も後も同じ形で描く。データの無い線は描かない（サーバーが決める）。
export function ImpactTree({
  nodes,
  unsentOf,
  stateOf,
  onOpen,
}: {
  nodes: ImpactNode[]
  unsentOf: (part: string) => number
  stateOf?: (node: ImpactNode) => string | null
  onOpen: (part: string) => void
}) {
  const centerOf = (index: number) => index * (NODE_HEIGHT + NODE_GAP) + NODE_HEIGHT / 2
  const height = nodes.length * (NODE_HEIGHT + NODE_GAP) - NODE_GAP
  return (
    <div className="map-tree" style={{ height }}>
      <svg className="map-lines" width={LINE_END} height={height} aria-hidden="true">
        <path className="map-trunk" d={`M${TRUNK_X} 0V${centerOf(nodes.length - 1)}`} />
        {nodes.flatMap((node, index) =>
          node.effects.map((effect, position) => {
            const offset =
              node.effects.length > 1 ? position * 2 * DOUBLE_OFFSET - DOUBLE_OFFSET : 0
            return (
              <path
                key={`${node.part}-${effect}`}
                className={`map-line ${EFFECT_CLASS[effect]}`}
                data-effect={effect}
                d={`M${TRUNK_X} ${centerOf(index) + offset}H${LINE_END}`}
                style={{ animationDelay: `${index * LIGHT_STEP_SECONDS}s` }}
              />
            )
          }),
        )}
      </svg>
      <ol className="map-nodes">
        {nodes.map((node, index) => {
          const word = stateOf?.(node)
          return (
            <li key={node.part} style={{ height: NODE_HEIGHT }}>
              <button
                type="button"
                className={`map-node ${toneOf(node.part)}${
                  node.effects.includes('そのまま') ? ' kept' : ''
                }`}
                data-part={node.part}
                data-effects={node.effects.join(',')}
                style={{ animationDelay: `${index * LIGHT_STEP_SECONDS}s` }}
                onClick={() => onOpen(node.part)}
              >
                <span className="map-node-head">
                  <span className="part-head">
                    <PartIcon part={node.part} />
                    {node.who}
                  </span>
                  {unsentOf(node.part) > 0 ? (
                    <span className="chip unsent">{WORDS.map.unsent}</span>
                  ) : (
                    word && <span className="chip">{word}</span>
                  )}
                </span>
                <span className="map-node-task">{node.task}</span>
                {node.change && <ChangeLine text={node.change} />}
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// 押す前の1人分。変わる前と後、根拠を読む。送るのは進めた後。
export function PreviewCard({ node, onBack }: { node: ImpactNode; onBack: () => void }) {
  return (
    <main className="map-screen">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{node.task}</h1>
      </header>
      {node.after.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.draft}</h2>
          {node.after.map((entry) => (
            <p className="original" key={entry.text}>
              {entry.text}
            </p>
          ))}
        </section>
      )}
      {node.before.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.before}</h2>
          {node.before.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </section>
      )}
      {node.kept.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.kept}</h2>
          {node.kept.map((entry) => (
            <p key={entry.text}>
              {entry.text}
              {entry.state && `：${stateText(entry.state)}`}
            </p>
          ))}
        </section>
      )}
      {node.basis.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.basis}</h2>
          {node.basis.map((entry) => (
            <p className="quote glow" key={entry.text}>
              {entry.text}
            </p>
          ))}
        </section>
      )}
    </main>
  )
}

// 案を進めたあとの画面。「次に何をするか」を相手ごとに出し、その場で送れる。
// 線・カード・帯は同じ台帳から描く。データの無い線は描かない（サーバーが決める）。
export function MapScreen({ code, onBack }: { code: string; onBack: () => void }) {
  const [board, setBoard] = useState<Board | null>(null)
  const [failed, setFailed] = useState(false)
  const [openPart, setOpenPart] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [shared, setShared] = useState<string[]>(() => device.shared(code))
  const shownVersion = useRef(0)

  const receive = useCallback((next: Board) => {
    shownVersion.current = next.version
    setBoard(next)
  }, [])

  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((next) => {
        if (active) receive(next)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    // 夫の答えが、開いたままでも戻るように見に行く。
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      api
        .getBoard(code)
        .then((next) => {
          if (active && next.version > shownVersion.current) receive(next)
        })
        .catch(() => undefined)
    }, POLL_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [code, receive])

  const impact = board?.impact
  const nodes = impact?.nodes ?? []
  const sends = board ? sendsOf(board) : []
  // 医療の問いになった文は、ボードの上段と同じく前の案の文に数えない。
  const leftover = (board?.items ?? []).filter(
    (item) => item.superseded && !item.released && !item.medical_reply,
  ).length
  const sendsFor = (part: string) => sends.filter((send) => send.item.part === part)
  const nextPart = (from: Board) => {
    const remaining = sendsOf(from)
    return from.impact?.nodes.find((node) =>
      remaining.some((send) => send.item.part === node.part),
    )?.part
  }

  // 送ったら、次の相手へ進む。全部済んだら一覧へ戻る。
  const markAsked = async (id: string) => {
    if (!board || busy) return
    setBusy(true)
    setMessage(null)
    try {
      const next = await api.changeStatus(code, id, shownVersion.current, '頼んだ')
      receive(next)
      setOpenPart(nextPart(next) ?? null)
    } catch (error) {
      setMessage(error instanceof ApiError && error.message ? error.message : WORDS.board.failed)
      api.getBoard(code).then(receive).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  const opened = nodes.find((node) => node.part === openPart)
  if (opened && board) {
    return (
      <NodeCard
        node={opened}
        sends={sendsFor(opened.part)}
        board={board}
        code={code}
        busy={busy}
        message={message}
        shared={shared}
        onShared={(id) => {
          const next = [...new Set([...shared, id])]
          device.setShared(code, next)
          setShared(next)
        }}
        onAsked={(id) => void markAsked(id)}
        onBack={() => setOpenPart(null)}
      />
    )
  }

  const firstPart = board ? nextPart(board) : undefined
  const review = board?.doctor
    ? reviewLines(board.doctor.candidates, board.doctor.paths)
    : []
  const anySent = nodes.some((node) =>
    node.after.some((entry) => entry.state && entry.state !== '未依頼'),
  )

  return (
    <div className="with-dock">
      <main className="map-screen">
        <header className="add-head">
          <button type="button" className="quiet" onClick={onBack}>
            {WORDS.add.back}
          </button>
          <h1>{WORDS.map.title}</h1>
        </header>

        {/* ⑥と同じ黄の「見直し」。進めた案の候補日と次の段から、コードで作る。 */}
        {review.length > 0 && (
          <section className="review-card change-review" data-review>
            <span className="chip">{WORDS.decide.reviewChip}</span>
            <dl className="review-lines">
              {review.map((line) => (
                <div key={`${line.label}${line.text}`} className="review-line" data-review-line>
                  <dt>{line.label}</dt>
                  <dd>{line.text}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {failed && (
          <p className="message" role="alert">
            {WORDS.board.offline}
          </p>
        )}

        {impact && (
          <>
            <section className="map-change">
              {impact.cause && (
                <p className="map-cause">
                  {inQuotes(impact.cause)}
                  {WORDS.map.yourInput}
                </p>
              )}
              <h2>{impact.headline}</h2>
              <p className="map-todo" role="status">
                {sends.length > 0
                  ? WORDS.map.count(sends.length)
                  : leftover > 0
                    ? WORDS.map.allSentLeftover(leftover)
                    : anySent
                      ? WORDS.map.allSent
                      : WORDS.map.noSends}
              </p>
            </section>

            {/* 帯は線が灯り終わった後に出す。 */}
            <WeekStrip week={board?.week ?? null} delay={nodes.length * LIGHT_STEP_SECONDS} />

            <ImpactTree
              nodes={nodes}
              unsentOf={(part) => sendsFor(part).length}
              stateOf={(node) => stateWord(node.after.find((entry) => entry.state)?.state)}
              onOpen={setOpenPart}
            />

            <details className="map-why">
              <summary>{WORDS.map.why}</summary>
              {impact.basis.map((entry) => (
                <p className="quote" key={entry.text}>
                  {entry.text}
                </p>
              ))}
            </details>
          </>
        )}
      </main>
      {impact && (
        <div className="dock">
          <div className="dock-row">
            <button type="button" onClick={onBack}>
              {WORDS.map.toBoard}
            </button>
            {firstPart && (
              <button
                type="button"
                className="primary"
                data-primary
                onClick={() => setOpenPart(firstPart)}
              >
                {WORDS.map.send(sends.length)}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
