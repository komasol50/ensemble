import { useEffect, useState } from 'react'
import { WORDS } from './words'

// 元に戻す仕組みがまだ無いので、押し間違いで消えないよう2回押しにする。
export function RemoveButton({
  disabled,
  onConfirm,
  label = WORDS.board.remove,
  armedLabel = WORDS.board.removeAgain,
}: {
  disabled?: boolean
  onConfirm: () => void
  label?: string
  armedLabel?: string
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = window.setTimeout(() => setArmed(false), 4000)
    return () => window.clearTimeout(timer)
  }, [armed])
  return (
    <button
      type="button"
      disabled={disabled}
      className={armed ? 'armed' : undefined}
      onClick={() => (armed ? onConfirm() : setArmed(true))}
    >
      {armed ? armedLabel : label}
    </button>
  )
}
