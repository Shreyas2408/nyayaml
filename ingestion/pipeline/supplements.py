"""Keep the schedules, forms and statements that sit outside section bodies."""

import re

from .common import join_lines, require
from .tables import schedule_columns, schedule_page


def first_schedule(pages, lines):
    source = [line for line in lines if 173 <= line["p"] <= 219]
    schedule = {"title": source[0]["t"], "text": join_lines(source[1:])}
    parts = []
    for first_page, page_numbers, header_top, header_bottom in [
        (173, range(173, 219), 271, 298),
        (219, [219], 104, 132),
    ]:
        title = next(
            line["t"]
            for line in source
            if line["p"] == first_page and re.match(r"^(I\.|II\.)", line["t"])
        )
        starts = schedule_columns(first_page)
        header = schedule_page(
            pages[first_page - 1], starts, header_top, header_bottom
        )["rows"]
        columns = [
            "\n".join(row[column] for row in header if row[column])
            for column in range(len(starts))
        ]
        fragments = []
        for page_number in page_numbers:
            top = 311 if page_number == 173 else (155 if page_number == 219 else 84)
            fragment = schedule_page(
                pages[page_number - 1], schedule_columns(page_number), top
            )
            low, high = (
                (298, 301)
                if page_number == 173
                else ((136, 140) if page_number == 219 else (0, 110))
            )
            numbers = " ".join(
                line["t"]
                for line in source
                if line["p"] == page_number
                and low <= line["y"] <= high
                and re.fullmatch(r"[1-6](?: [1-6])*", line["t"])
            ).split()
            require(
                numbers == [str(number) for number in range(1, len(starts) + 1)],
                f"BNSS page {page_number}: table column numbers changed.",
            )
            fragment["column_numbers"] = numbers
            fragments.append(fragment)
        parts.append(
            {"title": title, "table": {"columns": columns, "pages": fragments}}
        )
    schedule["parts"] = parts
    return schedule


def second_schedule(lines):
    source = [line for line in lines if 220 <= line["p"] <= 279]
    starts = [
        index
        for index, line in enumerate(source)
        if re.fullmatch(r"FORM No\.\s*\d+", line["t"])
    ]
    forms = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(source)
        chunk = source[start:end]
        heading = chunk[0]["t"]
        title = []
        cursor = 1
        while (
            cursor < len(chunk)
            and chunk[cursor]["t"].isupper()
            and not chunk[cursor]["t"].startswith(("To ", "WHEREAS"))
        ):
            title.append(chunk[cursor]["t"])
            cursor += 1
        forms.append(
            {
                "form_number": re.search(r"\d+$", heading)[0],
                "heading": heading,
                "title": " ".join(title),
                "text": join_lines(chunk[cursor:]),
            }
        )
    require(
        [form["form_number"] for form in forms]
        == [str(number) for number in range(1, 59)],
        "BNSS: expected all 58 forms.",
    )
    return {
        "title": source[0]["t"],
        "text": join_lines(source[1 : starts[0]]),
        "forms": forms,
    }


def add_supplements(document, pages, lines, act):
    if act == "BNS":
        source = [line for line in lines if line["p"] == 112]
        document["additional_content"] = [
            {"title": source[0]["t"], "text": join_lines(source[1:])}
        ]
    elif act == "BSA":
        schedule = [line for line in lines if 52 <= line["p"] <= 53]
        document["schedules"] = [
            {"title": schedule[0]["t"], "text": join_lines(schedule[1:])}
        ]
        source = [line for line in lines if line["p"] == 54]
        document["additional_content"] = [
            {"title": source[0]["t"], "text": join_lines(source[1:])}
        ]
    else:
        document["schedules"] = [first_schedule(pages, lines), second_schedule(lines)]
