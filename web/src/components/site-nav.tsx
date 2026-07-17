'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const links = [
  { href: '/', label: 'Home' },
  { href: '/pipeline', label: 'Pipeline' },
  { href: '/listen', label: 'Listen' },
  { href: '/modules', label: 'Modules' },
  { href: '/insights', label: 'Insights' },
]

export function SiteNav() {
  const pathname = usePathname()
  return (
    <nav className="flex items-center gap-6 border-b border-[var(--mm-border)] bg-[var(--mm-surface)] px-6 text-sm h-[var(--mm-nav-height)]">
      <Link href="/" className="font-semibold tracking-tight text-[var(--mm-accent)]">
        Modular Mind
      </Link>
      {links.map((l) => {
        const isActive = pathname === l.href
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={isActive ? 'page' : undefined}
            className={
              isActive
                ? 'text-[var(--mm-text)]'
                : 'text-[var(--mm-text-dim)] hover:text-[var(--mm-text)]'
            }
          >
            {l.label}
          </Link>
        )
      })}
    </nav>
  )
}
