import csv
import io


def total(text):
    return sum(int(row["count"]) for row in csv.DictReader(io.StringIO(text)))
