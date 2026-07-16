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
    <nav className="flex gap-6 border-b border-zinc-800 px-6 py-4 text-sm">
      <Link href="/" className="font-semibold tracking-tight">
        Modular Mind
      </Link>
      {links.map((l) => {
        const isActive = pathname === l.href
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={isActive ? 'page' : undefined}
            className="text-zinc-400 hover:text-zinc-100"
          >
            {l.label}
          </Link>
        )
      })}
    </nav>
  )
}
