import { useEffect, useState } from 'react'
import { WORDS } from './words'

function TrashIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  )
}

// ゴミ箱の印のボタン。項目を消す入口と、ゴミ箱を開く入口に使う。
export function TrashButton({
  disabled,
  onClick,
  label = WORDS.board.remove,
}: {
  disabled?: boolean
  onClick: () => void
  label?: string
}) {
  return (
    <button
      type="button"
      className="icon-button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      <TrashIcon />
    </button>
  )
}

// 元に戻せないものを消すボタン。押し間違いで消えないよう2回押しにする。
export function RemoveButton({
  disabled,
  onConfirm,
  icon = false,
  label = WORDS.board.remove,
  armedLabel = WORDS.board.removeAgain,
}: {
  disabled?: boolean
  onConfirm: () => void
  icon?: boolean
  label?: string
  armedLabel?: string
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = window.setTimeout(() => setArmed(false), 4000)
    return () => window.clearTimeout(timer)
  }, [armed])
  const showIcon = icon && !armed
  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={showIcon ? label : undefined}
      className={armed ? 'armed' : showIcon ? 'icon-button' : undefined}
      onClick={() => (armed ? onConfirm() : setArmed(true))}
    >
      {showIcon ? <TrashIcon /> : armed ? armedLabel : label}
    </button>
  )
}
