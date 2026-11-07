import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Board, type PackingSuggestion } from './api'
import {
  linkedItem,
  normName,
  thingsOf,
  type Proposal,
  type Saved,
  type Thing,
} from './checklist'
import { Chevron } from './parts'
import { shioriDocs } from './photos'
import { WORDS } from './words'

// 持ち物の案（v3.6.1）。初めて持ち物を開いたときに1回、その後は親が［設定に合わせて案を足す］を押したときだけ作る。
// 案は親が押した物だけ一覧に入る（自動で並べ替えない・消さない）。親が足した・直した・消した・断った物は案に出さない。
// しおりの引用は端末の中（ensemble.packingQuotes.<code>）にだけ置き、サーバーの控えには入れない（Plan main の案(a)）。

function quotesKey(code: string): string {
  return `ensemble.packingQuotes.${code}`
}

export function loadQuotes(code: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(quotesKey(code))
    const value = raw ? JSON.parse(raw) : null
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch {
    return {}
  }
}

function saveQuotes(code: string, quotes: Record<string, string>): void {
  try {
    localStorage.setItem(quotesKey(code), JSON.stringify(quotes))
  } catch {
    // 残せなくても、案は使える（出どころは「しおりから」だけになる）。
  }
}

// 品名の下の出どころの1行。型の物には出さない。色や記号でなく語で見分ける。
export function sourceLine(thing: Thing, quotes: Record<string, string>): string | null {
  const words = WORDS.packing
  if (thing.edited) return words.editedByYou
  if (thing.source === 'template') return null
  if (thing.source === 'user') return words.byYou
  if (thing.source === 'setting') return words.fromSetting(thing.basis)
  return words.fromShiori(quotes[thing.id])
}

function proposalLine(proposal: Proposal, quotes: Record<string, string>): string {
  const words = WORDS.packing
  return proposal.source === 'shiori'
    ? words.fromShiori(quotes[proposal.id])
    : words.fromSetting(proposal.basis)
}

// 届いた案のうち、今の一覧に入れられるもの。親が優先：足した・直した・消した・断った物は出さない。
function freshProposals(saved: Saved, board: Board, suggestions: PackingSuggestion[]): Proposal[] {
  const things = thingsOf(saved)
  const names = new Set([
    ...things.map((thing) => normName(thing.name)),
    ...saved.rejected,
    ...saved.proposals.filter((p) => p.action === 'add').map((p) => normName(p.name)),
  ])
  const targets = new Set(saved.proposals.map((p) => p.target_id).filter(Boolean))
  const titles = WORDS.checklist.groups.map((group) => group.title)
  const fresh: Proposal[] = []
  for (const suggestion of suggestions) {
    const group = titles.indexOf(suggestion.group)
    if (group < 0 || saved.proposals.some((p) => p.id === suggestion.id)) continue
    if (suggestion.action === 'add') {
      const key = normName(suggestion.name)
      if (!key || names.has(key)) continue
      names.add(key)
    } else {
      const target = things.find((thing) => thing.id === suggestion.target_id)
      if (
        !target ||
        target.source !== 'template' ||
        target.edited ||
        saved.kept.includes(target.id) ||
        saved.unneeded.includes(target.id) ||
        saved.marks[target.id] ||
        linkedItem(saved, target.id, board) ||
        targets.has(target.id)
      )
        continue
      targets.add(target.id)
    }
    fresh.push({
      id: suggestion.id,
      action: suggestion.action,
      name: suggestion.name,
      group,
      target_id: suggestion.target_id ?? null,
      source: suggestion.source,
      basis: suggestion.basis || undefined,
    })
  }
  return fresh
}

type Status = 'idle' | 'making' | 'failed' | 'none'

export type Packing = {
  enabled: boolean
  status: Status
  open: boolean
  changed: boolean
  quotes: Record<string, string>
  run: () => void
  toggle: () => void
  accept: (proposal: Proposal) => void
  decline: (proposal: Proposal) => void
  acceptAll: () => void
}

type Options = {
  code: string
  board: Board
  saved: Saved
  update: (change: (current: Saved) => Saved) => void
  readOnly?: boolean
}

export function usePacking({ code, board, saved, update, readOnly }: Options): Packing {
  const enabled = board.packing_ai === true && !readOnly
  const [status, setStatus] = useState<Status>('idle')
  const [open, setOpen] = useState(false)
  const [quotes, setQuotes] = useState<Record<string, string>>(() => loadQuotes(code))
  const savedRef = useRef(saved)
  savedRef.current = saved
  const boardRef = useRef(board)
  boardRef.current = board
  const running = useRef(false)

  const run = useCallback(() => {
    if (running.current) return
    running.current = true
    setStatus('making')
    void (async () => {
      try {
        const docs = await shioriDocs(code)
        const result = await api.packing(code, docs)
        if (result.enabled === false) {
          setStatus('idle')
          return
        }
        if (result.fell_back) {
          setStatus('failed')
          return
        }
        const fresh = freshProposals(savedRef.current, boardRef.current, result.suggestions)
        const shiori = result.suggestions.filter(
          (s) => s.quote && fresh.some((p) => p.id === s.id),
        )
        if (shiori.length > 0) {
          const next = { ...loadQuotes(code) }
          for (const s of shiori) next[s.id] = s.quote as string
          saveQuotes(code, next)
          setQuotes(next)
        }
        const first = savedRef.current.packingRounds === 0
        update((current) => ({
          ...current,
          proposals: [...current.proposals, ...fresh],
          packingRounds: current.packingRounds + 1,
          packingSetting: boardRef.current.packing_setting ?? null,
        }))
        setOpen(fresh.length > 0)
        setStatus(fresh.length === 0 && !first ? 'none' : 'idle')
      } catch {
        setStatus('failed')
      } finally {
        running.current = false
      }
    })()
  }, [code, update])

  // 初めて開いたときに1回だけ。作れなかったときは、次に開いたときにもう一度作る。
  const started = useRef(false)
  useEffect(() => {
    if (!enabled || started.current || saved.packingRounds > 0) return
    started.current = true
    run()
  }, [enabled, saved.packingRounds, run])

  const drop = (current: Saved, id: string) => current.proposals.filter((p) => p.id !== id)

  const accept = (proposal: Proposal) =>
    update((current) => {
      if (proposal.action === 'remove') {
        const target = proposal.target_id ?? ''
        return {
          ...current,
          proposals: drop(current, proposal.id),
          unneeded: current.unneeded.includes(target)
            ? current.unneeded
            : [...current.unneeded, target],
        }
      }
      return {
        ...current,
        proposals: drop(current, proposal.id),
        added: [
          ...current.added,
          {
            id: proposal.id,
            name: proposal.name,
            group: proposal.group,
            source: proposal.source,
            basis: proposal.basis,
          },
        ],
      }
    })

  const decline = (proposal: Proposal) =>
    update((current) =>
      proposal.action === 'remove'
        ? {
            ...current,
            proposals: drop(current, proposal.id),
            kept: [...current.kept, proposal.target_id ?? ''],
          }
        : {
            ...current,
            proposals: drop(current, proposal.id),
            rejected: [...current.rejected, normName(proposal.name)],
          },
    )

  const acceptAll = () => {
    for (const proposal of saved.proposals) if (proposal.action === 'add') accept(proposal)
  }

  const changed =
    enabled &&
    saved.packingRounds > 0 &&
    !!saved.packingSetting &&
    !!board.packing_setting &&
    saved.packingSetting !== board.packing_setting

  return {
    enabled,
    status,
    open,
    changed,
    quotes,
    run,
    toggle: () => setOpen((value) => !value),
    accept,
    decline,
    acceptAll,
  }
}

// 一覧の一番上：作っている1行／案のカード／作れなかった1行／設定が変わった1行。
export function PackingTop({ packing, saved }: { packing: Packing; saved: Saved }) {
  const words = WORDS.packing
  if (!packing.enabled) return null
  const proposals = saved.proposals
  const adds = proposals.filter((p) => p.action === 'add')
  const count = proposals.length
  const title = saved.packingRounds > 1 ? words.newTitle(count) : words.cardTitle(count)
  return (
    <>
      {packing.status === 'making' && (
        <p className="pk-line" role="status" data-packing-making>
          <span className="pk-spin" aria-hidden="true" />
          {words.making}
        </p>
      )}
      {packing.status === 'failed' && (
        <p className="pk-line" role="status" data-packing-failed>
          <span>{words.failed}</span>
          <button type="button" className="quiet pk-retry" onClick={packing.run}>
            {words.retry}
          </button>
        </p>
      )}
      {packing.status === 'none' && (
        <p className="pk-line" role="status" data-packing-none>
          {words.none}
        </p>
      )}
      {packing.changed && packing.status !== 'making' && (
        <p className="pk-line" data-packing-changed>
          <span>{words.changed}</span>
          <button type="button" className="quiet pk-retry" onClick={packing.run}>
            {words.more}
          </button>
        </p>
      )}
      {count > 0 && packing.status !== 'making' && (
        <div className="pk-card" data-packing-card>
          <button
            type="button"
            className="quiet pk-head"
            aria-expanded={packing.open}
            onClick={packing.toggle}
          >
            <span>{packing.open ? title : words.newTitle(count)}</span>
            <Chevron to={packing.open ? 'up' : 'down'} />
          </button>
          {packing.open && (
            <>
              <ul className="pk-rows">
                {proposals.map((proposal) => (
                  <li
                    className="pk-row"
                    key={proposal.id}
                    data-proposal={proposal.name}
                    data-action={proposal.action}
                  >
                    <div className="pk-text">
                      <span className="pk-name">
                        <span aria-hidden="true">
                          {proposal.action === 'add' ? words.addSign : words.removeSign}{' '}
                        </span>
                        {proposal.name}
                      </span>
                      <span className="pk-source">{proposalLine(proposal, packing.quotes)}</span>
                    </div>
                    <div className="pk-tools">
                      {proposal.action === 'add' ? (
                        <>
                          <button
                            type="button"
                            aria-label={words.addLabel(proposal.name)}
                            onClick={() => packing.accept(proposal)}
                          >
                            {words.add}
                          </button>
                          <button
                            type="button"
                            className="quiet"
                            aria-label={words.skipLabel(proposal.name)}
                            onClick={() => packing.decline(proposal)}
                          >
                            {words.skip}
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            aria-label={words.dropLabel(proposal.name)}
                            onClick={() => packing.accept(proposal)}
                          >
                            {words.drop}
                          </button>
                          <button
                            type="button"
                            className="quiet"
                            aria-label={words.keepLabel(proposal.name)}
                            onClick={() => packing.decline(proposal)}
                          >
                            {words.keep}
                          </button>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              {adds.length > 1 && (
                <button type="button" className="pk-all" onClick={packing.acceptAll}>
                  {words.addAll}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </>
  )
}

// 一覧の一番下（＋持ち物を足す の下）：［設定に合わせて案を足す］。押したときだけ作る。
export function PackingBottom({ packing }: { packing: Packing }) {
  if (!packing.enabled) return null
  return (
    <button
      type="button"
      className="quiet pk-more"
      disabled={packing.status === 'making'}
      onClick={packing.run}
      data-packing-more
    >
      {WORDS.packing.more}
    </button>
  )
}
