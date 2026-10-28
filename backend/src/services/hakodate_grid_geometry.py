"""Physical cell bounds shared by assignment and review."""


def cell_bbox(*, row_index: int, col_index: int, row_edges: list[float],
              column_edges: list[float]) -> list[float]:
    return [float(column_edges[col_index]), float(row_edges[row_index]),
            float(column_edges[col_index + 1]), float(row_edges[row_index + 1])]
