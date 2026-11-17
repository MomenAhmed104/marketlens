/** Letter avatar for a company or domain. No network fetch (no favicons). */
export default function Avatar({ name }: { name: string }) {
  const letter = name.replace(/^www\./, '').charAt(0).toUpperCase() || '?'
  return (
    <span className="avatar" aria-hidden="true">
      {letter}
    </span>
  )
}
