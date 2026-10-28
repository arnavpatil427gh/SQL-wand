/**
 * Helpers for mapping Postgres error positions to Monaco editor coordinates.
 *
 * Postgres reports errors as a 1-based character offset into the SQL string.
 * Monaco markers need 1-based {lineNumber, column} pairs.
 */

/**
 * Convert a 1-based character offset into 1-based line + column numbers.
 */
export function offsetToPosition(
  sql: string,
  offset: number,
): { lineNumber: number; column: number } {
  let line = 1;
  let col = 1;
  for (let i = 0; i < Math.min(offset - 1, sql.length); i++) {
    if (sql[i] === "\n") {
      line++;
      col = 1;
    } else {
      col++;
    }
  }
  return { lineNumber: line, column: col };
}

/**
 * Build a full Monaco marker range for a Postgres error position.
 * Underlines the token at the error position (extends to the next whitespace).
 */
export function offsetToMarkerRange(
  sql: string,
  offset: number,
): {
  startLineNumber: number;
  startColumn: number;
  endLineNumber: number;
  endColumn: number;
} {
  const { lineNumber, column } = offsetToPosition(sql, offset);
  const lines = sql.split("\n");
  const line = lines[lineNumber - 1] || "";

  // Walk forward from the error column to the end of the token
  let endCol = column;
  while (endCol - 1 < line.length && /\S/.test(line[endCol - 1])) {
    endCol++;
  }
  // Guarantee at least one character is underlined
  if (endCol <= column) endCol = column + 1;

  return {
    startLineNumber: lineNumber,
    startColumn: column,
    endLineNumber: lineNumber,
    endColumn: endCol,
  };
}
