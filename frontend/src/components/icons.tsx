// Inline icons (no icon library). Decorative: always aria-hidden; the text label carries meaning.
import type { ReactNode } from 'react'

function Svg({ children, size = 20 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export const OverviewIcon = () => (
  <Svg>
    <path d="M4 13h6V4H4zM14 20h6v-9h-6zM14 4v3h6V4zM4 20h6v-3H4z" />
  </Svg>
)
export const ProductIcon = () => (
  <Svg>
    <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" />
    <circle cx="7.5" cy="7.5" r="1.5" />
  </Svg>
)
export const RunsIcon = () => (
  <Svg>
    <path d="M3 12h4l3-8 4 16 3-8h4" />
  </Svg>
)
export const DomainsIcon = () => (
  <Svg>
    <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </Svg>
)
export const MenuIcon = () => (
  <Svg>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Svg>
)
export const CloseIcon = () => (
  <Svg>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
)
export const ChevronDownIcon = () => (
  <Svg size={16}>
    <path d="m6 9 6 6 6-6" />
  </Svg>
)
export const ExternalIcon = () => (
  <Svg size={14}>
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Svg>
)
export const PriceIcon = () => (
  <Svg size={24}>
    <path d="M7 15h7M9 11h5M16 7.5A4 4 0 0 0 9 10v4a3 3 0 0 1-2 3h10" />
  </Svg>
)
export const SearchIcon = () => (
  <Svg size={16}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Svg>
)
export const CheckIcon = () => (
  <Svg size={24}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
)

/** Brand mark: a stompbox pedal outline with a footswitch. */
export const BrandMark = () => (
  <svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true" focusable="false">
    <rect width="36" height="36" rx="9" fill="#2f5bea" />
    <rect x="10" y="7" width="16" height="22" rx="3.5" fill="none" stroke="#fff" strokeWidth="2" />
    <circle cx="18" cy="13" r="2.4" fill="#5ec8ff" />
    <circle cx="18" cy="22.5" r="3.2" fill="none" stroke="#fff" strokeWidth="2" />
  </svg>
)
