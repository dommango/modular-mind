import type { Metadata } from 'next'
import { SiteNav } from '@/components/site-nav'
import './globals.css'

export const metadata: Metadata = {
  title: 'Modular Mind',
  description: 'An AI that learns to build modular synth patches — explore the pipeline and listen to what it makes.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        <SiteNav />
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  )
}
