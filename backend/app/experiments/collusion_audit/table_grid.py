"""Expand HTML cell spans without judging or altering any source text."""


def table_grid(table):
    rows = []
    carry = {}
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        values = {column: text for column, (remaining, text) in carry.items()}
        next_carry = {column: (remaining - 1, text) for column, (remaining, text) in carry.items() if remaining > 1}
        cursor = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while cursor in values:
                cursor += 1
            colspan = int(cell.get("colspan", 1))
            rowspan = int(cell.get("rowspan", 1))
            if not 1 <= colspan <= 100 or not 1 <= rowspan <= 1000:
                raise ValueError("Invalid table span")
            text = cell.get_text(" ", strip=True)
            for column in range(cursor, cursor + colspan):
                if column in values:
                    raise ValueError("Overlapping table spans")
                values[column] = text
                if rowspan > 1:
                    next_carry[column] = (rowspan - 1, text)
            cursor += colspan
        rows.append([values.get(column, "") for column in range(max(values, default=-1) + 1)])
        carry = next_carry
    return rows
