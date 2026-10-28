import { useMemo } from "react";
import { ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import type { QueryResult } from "../api";

export default function ResultsGrid({ result }: { result: QueryResult }) {
  const columns = useMemo<ColumnDef<unknown[]>[]>(
    () =>
      result.columns.map((name, i) => ({
        header: name,
        accessorFn: (row) => row[i],
        cell: (c) => {
          const v = c.getValue();
          return v === null ? <span className="null">NULL</span> : String(v);
        },
      })),
    [result.columns]
  );
  const table = useReactTable({ data: result.rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <table>
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => (
              <th key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
