import { useEffect, useState } from 'react'
import { api, type Board, type ImpactNode, type Week } from './api'
import { sendsOf } from './Send'
import { WORDS } from './words'

// 線は固定の配置で描く（グラフのライブラリは入れない）。カードの高さと合わせる。
const NODE_HEIGHT = 84
const NODE_GAP = 12
const TRUNK_X = 8
const LINE_END = 32
const DOUBLE_OFFSET = 8
const LIGHT_STEP_SECONDS = 0.4

const EFFECT_CLASS: Record<string, string> = {
  変える: 'change',
  外す: 'release',
  そのまま: 'keep',
}

function Effects({ effects }: { effects: string[] }) {
  return (
    <span className="map-effects">
      {effects.map((effect) => (
        <span key={effect} className={`effect ${EFFECT_CLASS[effect]}`}>
          {effect}
        </span>
      ))}
    </span>
  )
}

// 下の帯。週の表の「退院」の列を1行のマスにする。取り消した日は薄く残す。
function WeekStrip({ week }: { week: Week | null }) {
  if (!week) return null
  return (
    <section aria-label={WORDS.map.strip}>
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

function NodeCard({
  node,
  sendCount,
  onBack,
  onSends,
}: {
  node: ImpactNode
  sendCount: number
  onBack: () => void
  onSends: () => void
}) {
  return (
    <main className="map-screen">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{node.part}</h1>
      </header>
      <Effects effects={node.effects} />
      {node.before.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.before}</h2>
          {node.before.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </section>
      )}
      {node.after.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.after}</h2>
          {node.after.map((entry) => (
            <article className="card" key={entry.text}>
              {entry.state && (
                <div className="chips">
                  <span className="chip">{entry.state}</span>
                </div>
              )}
              <p className="original">{entry.text}</p>
            </article>
          ))}
        </section>
      )}
      {node.kept.length > 0 && (
        <section className="map-detail">
          <h2>{WORDS.map.kept}</h2>
          {node.kept.map((entry) => (
            <p key={entry.text}>
              {entry.text}
              {entry.state && `：${entry.state}`}
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
      {sendCount > 0 && (
        <div className="map-actions">
          <button type="button" className="primary" data-primary onClick={onSends}>
            {WORDS.map.toSends(sendCount)}
          </button>
        </div>
      )}
    </main>
  )
}

// 影響マップ。線・カード・帯は同じ台帳から描く。データの無い線は描かない（サーバーが決める）。
export function MapScreen({
  code,
  onBack,
  onSends,
}: {
  code: string
  onBack: () => void
  onSends: () => void
}) {
  const [board, setBoard] = useState<Board | null>(null)
  const [failed, setFailed] = useState(false)
  const [openPart, setOpenPart] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    api
      .getBoard(code)
      .then((next) => {
        if (active) setBoard(next)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
    }
  }, [code])

  const impact = board?.impact
  const sends = board ? sendsOf(board) : []
  const opened = impact?.nodes.find((node) => node.part === openPart)
  if (opened) {
    return (
      <NodeCard
        node={opened}
        sendCount={sends.filter((send) => send.item.part === opened.part).length}
        onBack={() => setOpenPart(null)}
        onSends={onSends}
      />
    )
  }

  const nodes = impact?.nodes ?? []
  const centerOf = (index: number) => index * (NODE_HEIGHT + NODE_GAP) + NODE_HEIGHT / 2
  const height = nodes.length * (NODE_HEIGHT + NODE_GAP) - NODE_GAP

  return (
    <main className="map-screen">
      <header className="add-head">
        <button type="button" className="quiet" onClick={onBack}>
          {WORDS.add.back}
        </button>
        <h1>{WORDS.map.title}</h1>
      </header>

      {failed && (
        <p className="message" role="alert">
          {WORDS.board.offline}
        </p>
      )}

      {impact && (
        <>
          <section className="map-change">
            <h2>{impact.headline}</h2>
            {impact.basis.map((entry) => (
              <p className="quote" key={entry.text}>
                {entry.text}
              </p>
            ))}
          </section>

          <div className="map-tree" style={{ height }}>
            <svg className="map-lines" width={LINE_END} height={height} aria-hidden="true">
              <path className="map-trunk" d={`M${TRUNK_X} 0V${centerOf(nodes.length - 1)}`} />
              {nodes.flatMap((node, index) =>
                node.effects.map((effect, position) => {
                  const offset =
                    node.effects.length > 1
                      ? position * 2 * DOUBLE_OFFSET - DOUBLE_OFFSET
                      : 0
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
              {nodes.map((node, index) => (
                <li key={node.part} style={{ height: NODE_HEIGHT }}>
                  <button
                    type="button"
                    className="map-node"
                    data-part={node.part}
                    data-effects={node.effects.join(',')}
                    style={{ animationDelay: `${index * LIGHT_STEP_SECONDS}s` }}
                    onClick={() => setOpenPart(node.part)}
                  >
                    <span className="map-node-head">
                      <strong>{node.part}</strong>
                      <Effects effects={node.effects} />
                    </span>
                    <span className="map-node-summary">{node.summary}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>

          <WeekStrip week={board?.week ?? null} />

          <div className="map-actions">
            {sends.length > 0 && (
              <button type="button" className="primary" data-primary onClick={onSends}>
                {WORDS.map.toSends(sends.length)}
              </button>
            )}
            <button type="button" onClick={onBack}>
              {WORDS.map.toBoard}
            </button>
          </div>
        </>
      )}
    </main>
  )
}
