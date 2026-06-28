import { ReactNode } from "react"
import { EmptyState } from "./EmptyState"

export interface Column<T> {
  key: string
  header: string
  className?: string
  headerClassName?: string
  render: (row: T) => ReactNode
}

interface DenseTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  getKey: (row: T) => string | number
  emptyMessage?: string
  emptyDescription?: string
  className?: string
  onRowClick?: (row: T) => void
}

export function DenseTable<T>({
  columns,
  rows,
  getKey,
  emptyMessage,
  emptyDescription,
  className = "",
  onRowClick,
}: DenseTableProps<T>) {
  if (rows.length === 0) {
    return <EmptyState title={emptyMessage} description={emptyDescription} />
  }

  return (
    <div className={`overflow-x-auto rounded-xl border border-white/[0.07] ${className}`}>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-white/[0.07] bg-white/[0.02]">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`px-3 py-2.5 text-left font-semibold uppercase tracking-widest text-muted/60 ${col.headerClassName ?? ""}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04]">
          {rows.map((row) => (
            <tr
              key={getKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`transition-colors ${onRowClick ? "cursor-pointer hover:bg-white/[0.03]" : ""}`}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={`px-3 py-2.5 text-[#E8EDF7] ${col.className ?? ""}`}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
