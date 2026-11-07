// ボタンに添える小さな絵（2026-11-07 PO「ボタンはアイコン付き」）。文字の代わりにはしない。
// 線の太さと大きさはメニューの絵と同じ作法（20px、線 1.6）。読み上げでは読まない。

function Icon({ children, size = 18 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      className="btn-icon"
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  )
}

export function ShareIcon() {
  return (
    <Icon>
      <path d="M11 3h6v6M17 3l-8 8" />
      <path d="M15 12v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h4" />
    </Icon>
  )
}

export function EditIcon() {
  return (
    <Icon>
      <path d="M13.5 3.5l3 3L7 16H4v-3z" />
    </Icon>
  )
}

export function CheckIcon() {
  return (
    <Icon>
      <path d="M4 10.5l4 4 8-9" />
    </Icon>
  )
}

export function PlusIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M10 4v12M4 10h12" />
    </Icon>
  )
}
