import { useMemo, useState } from 'react'
import { MAX_PRODUCTS_PER_RUN, type StompboxProduct } from '../api'
import { money } from '../format'
import ProductThumb from '../components/ProductThumb'

interface Props {
  products: StompboxProduct[] | null
  error: string | null
  selected: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
}

const haystack = (p: StompboxProduct) =>
  [p.product_id, p.product_name, p.brand, p.manufacturer_model, p.category, p.subcategory].join(' ').toLowerCase()

export default function CataloguePicker({ products, error, selected, onChange, disabled = false }: Props) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')

  const categories = useMemo(
    () => [...new Set((products ?? []).map((p) => p.category))].sort(),
    [products],
  )
  const byId = useMemo(() => new Map((products ?? []).map((p) => [p.product_id, p])), [products])
  const shown = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
    return (products ?? []).filter(
      (p) => (!category || p.category === category) && terms.every((t) => haystack(p).includes(t)),
    )
  }, [products, query, category])

  const full = selected.length >= MAX_PRODUCTS_PER_RUN
  const toggle = (id: string) => {
    if (selected.includes(id)) onChange(selected.filter((s) => s !== id))
    else if (!full) onChange([...selected, id])
  }

  return (
    <section className="picker" aria-labelledby="catalogue-heading">
      <h3 id="catalogue-heading">Products</h3>

      <div className="selection" aria-live="polite">
        <strong>
          {selected.length} / {MAX_PRODUCTS_PER_RUN} selected
        </strong>
        {selected.length === 0 && <span className="muted"> · choose up to {MAX_PRODUCTS_PER_RUN} products for one run</span>}
        {full && <span className="warn-text"> · maximum reached: remove a product to choose another</span>}
        {selected.length > 0 && (
          <ul className="chips" aria-label="Selected products">
            {selected.map((id) => (
              <li key={id} className="chip">
                {id} {byId.get(id)?.product_name ?? ''}
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`Remove ${id}`}
                  disabled={disabled}
                  onClick={() => toggle(id)}
                >
                  ×
                </button>
              </li>
            ))}
            <li>
              <button type="button" className="link" disabled={disabled} onClick={() => onChange([])}>
                Clear selection
              </button>
            </li>
          </ul>
        )}
      </div>

      {error && <p role="alert" className="error">Catalogue unavailable: {error}</p>}
      {!products && !error && <p className="muted">Loading catalogue…</p>}
      {products && (
        <>
          <div className="toolbar">
            <input
              type="search"
              aria-label="Search catalogue"
              placeholder="Search by ID, name, brand or model"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <span className="muted small">
              {shown.length} of {products.length} products
            </span>
          </div>
          {shown.length === 0 ? (
            <p className="muted">No products match this search.</p>
          ) : (
            <div className="table-scroll">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col"><span className="sr-only">Select</span></th>
                    <th scope="col"><span className="sr-only">Image</span></th>
                    <th scope="col">ID</th>
                    <th scope="col">Product</th>
                    <th scope="col">Brand</th>
                    <th scope="col">Model</th>
                    <th scope="col">Subcategory</th>
                    <th scope="col" className="num">Stompbox price</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => {
                    const checked = selected.includes(p.product_id)
                    return (
                      <tr key={p.product_id} className={checked ? 'selected' : undefined}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ${p.product_id}`}
                            checked={checked}
                            disabled={disabled || (full && !checked)}
                            onChange={() => toggle(p.product_id)}
                          />
                        </td>
                        <td className="col-thumb">
                          <ProductThumb image={p.image} alt={p.product_name} size="sm" />
                        </td>
                        <td>{p.product_id}</td>
                        <td>
                          <a href={p.product_url} target="_blank" rel="noreferrer noopener">{p.product_name}</a>
                        </td>
                        <td>{p.brand}</td>
                        <td>{p.manufacturer_model}</td>
                        <td>{p.subcategory}</td>
                        <td className="num">
                          {p.displayed_price === null ? (
                            <span className="warn-text">price missing</span>
                          ) : (
                            money(p.displayed_price, p.currency)
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}
