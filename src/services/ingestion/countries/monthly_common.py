"""Pure intermediate formatting shared by monthly source parsers."""
import csv
import io


def normalized_csv(rows, *, add_disease=True):
    rows = [{**row, "Disease": row.get("Disease") or row.get("RawDiseaseLabel", "")} for row in rows] if add_disease else list(rows)
    fields = sorted({key for row in rows for key in row})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")
