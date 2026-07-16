import Link from 'next/link'

const links = [
  { href: '/', label: 'Home' },
  { href: '/pipeline', label: 'Pipeline' },
  { href: '/listen', label: 'Listen' },
  { href: '/modules', label: 'Modules' },
  { href: '/insights', label: 'Insights' },
]

export function SiteNav() {
  return (
    <nav className="flex gap-6 border-b border-zinc-800 px-6 py-4 text-sm">
      <span className="font-semibold tracking-tight">Modular Mind</span>
      {links.map((l) => (
        <Link key={l.href} href={l.href} className="text-zinc-400 hover:text-zinc-100">
          {l.label}
        </Link>
      ))}
    </nav>
  )
}
