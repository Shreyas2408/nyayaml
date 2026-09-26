"""Split section bodies into their printed provision hierarchy."""

import copy
import re

from .common import require

MARK = re.compile("^\\(([0-9]+|[a-zA-Z]+)\\)\\s*")
RESIDUAL = re.compile(
    "^(?:Explanation(?:\\s|\\.|[—–:]|$)|Illustrations?(?:\\.|\\s|$)|Exception(?:\\s|\\.|[—–:]|$)|Provided\\b|Proviso\\b)"
)
ROMANS = [
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
    "xxi",
    "xxii",
    "xxiii",
    "xxiv",
    "xxv",
    "xxvi",
    "xxvii",
    "xxviii",
    "xxix",
    "xxx",
]


def paragraphs(lines, stem):
    # Indentation and line spacing distinguish labels from wrapped references.
    out = []
    left = 72 if stem == "Bnss_2023" else 56.6
    expanded = []
    for line in lines:
        match = MARK.match(line["t"])
        if match and MARK.match(line["t"][match.end() :]):
            a = copy.deepcopy(line)
            b = copy.deepcopy(line)
            a["t"] = match.group().strip()
            b["t"] = line["t"][match.end() :]
            b["x"] = line["x"] + 9
            b["force_new"] = True
            expanded.extend([a, b])
        else:
            expanded.append(line)
    lines = expanded
    for i, line in enumerate(lines):
        prev = lines[i - 1] if i else None
        match = MARK.match(line["t"])
        gap = line["y"] - prev["y"] if prev and prev["p"] == line["p"] else None
        new = (
            not out
            or line.get("force_new")
            or line.get("header_inline")
            or (gap is not None and gap >= 15.7)
            or (
                gap is None
                and (
                    match
                    or RESIDUAL.match(line["t"])
                    or (
                        line["x"] >= left + 17
                        and prev
                        and re.search("[.;:—–]$", prev["t"])
                    )
                )
            )
        )
        if (
            match
            and line["x"] >= left + 17
            and (gap is None or gap >= 12)
            and (
                not re.match(
                    "^\\(\\d+\\)\\s+(?:of|and|or|to|in|above|below|shall|as|may)\\b",
                    line["t"],
                )
            )
        ):
            new = True
        if RESIDUAL.match(line["t"]):
            new = True
        if new:
            out.append(
                {"lines": [line], "x": line["x"], "p": line["p"], "text": line["t"]}
            )
        else:
            out[-1]["lines"].append(line)
            out[-1]["text"] += "\n" + line["t"]
    return out


def next_label(kind, label):
    if kind == "num":
        return str(int(label) + 1)
    if kind in ("alpha", "upper"):
        if len(label) == 1:
            return chr(ord(label) + 1)
        if label == "z":
            return "aa"
    if kind == "roman" and label in ROMANS:
        i = ROMANS.index(label)
        return ROMANS[i + 1] if i + 1 < len(ROMANS) else None
    return None


class Provision:

    def __init__(self, label="", kind="root", x=0, parent=None):
        self.label = label
        self.kind = kind
        self.x = x
        self.parent = parent
        self.events = []
        self.level = parent.level + 1 if parent else 0

    def addtext(self, text, res=False):
        self.events.append(("text", text, res))

    def full(self):
        return "\n".join(
            (
                (
                    event[1]
                    if event[0] == "text"
                    else "("
                    + event[1].label
                    + ")"
                    + (" " + event[1].full() if event[1].full() else "")
                )
                for event in self.events
            )
        ).strip()

    def export(self):
        obj = {"id": "(" + self.label + ")", "text": self.full()}
        children = [event[1] for event in self.events if event[0] == "child"]
        first_child = next(
            (i for i, event in enumerate(self.events) if event[0] == "child"),
            len(self.events),
        )
        prefix = "\n".join(
            (event[1] for event in self.events[:first_child] if event[0] == "text")
        ).strip()
        residual = "\n".join(
            (
                event[1]
                for i, event in enumerate(self.events)
                if event[0] == "text"
                and (i >= first_child or (not children and event[2]))
            )
        ).strip()
        if self.kind == "num" and self.parent.kind == "root":
            obj.update(
                intro_text=prefix if children else "",
                clauses=[child.export() for child in children],
                residual_text=residual,
            )
        elif self.parent.kind == "root" or (
            self.parent.kind == "num" and self.parent.parent.kind == "root"
        ):
            obj.update(
                intro_text=prefix if children else "",
                sub_clauses=[child.export() for child in children],
                residual_text=residual,
            )
        else:
            if children:
                obj.update(
                    intro_text=prefix,
                    sub_clauses=[child.export() for child in children],
                )
            obj["residual_text"] = residual
        return obj


def structure(section, lines, stem, diagnostics):
    root = Provision(x=90 if stem == "Bnss_2023" else 74.7)
    stack = [root]
    residual_owner = None
    # The stack tracks the current provision and its ancestors.
    paragraph_list = paragraphs(lines, stem)
    is_definition = section["title"] == "Definitions."
    residual_mode = None
    for paragraph_index, p in enumerate(paragraph_list):
        text = p["text"]
        match = MARK.match(text)
        x = p["x"]
        if match and re.match(
            "^\\(\\d+\\)\\s+(?:of|and|or|to|in|above|below|as)\\b", text
        ):
            match = None
        if match:
            label = match[1]
            resume = None
            candidates = [
                ancestor
                for ancestor in stack[1:]
                if next_label(ancestor.kind, ancestor.label) == label
            ]
            if candidates:
                resume = min(candidates, key=lambda a: abs(a.x - x))
            if (
                residual_owner is not None
                and residual_mode == "illustration"
                and resume
                and (
                    not (
                        resume.kind == "num"
                        or (is_definition and resume.kind == "alpha")
                    )
                )
            ):
                resume = None
            # Numbered examples stay inside their Explanation or Illustration.
            if residual_owner is not None:
                if resume is None:
                    residual_owner.addtext(text, True)
                    diagnostics["residual_markers"].append(
                        [
                            section["section_number"],
                            p["p"],
                            "(" + label + ")",
                            text[:110],
                        ]
                    )
                    continue
                if (
                    resume.parent is not residual_owner
                    and resume is not residual_owner
                    and (resume.level > residual_owner.level)
                ):
                    residual_owner.addtext(text, True)
                    diagnostics["residual_markers"].append(
                        [
                            section["section_number"],
                            p["p"],
                            "(" + label + ")",
                            text[:110],
                        ]
                    )
                    continue
            if resume:
                kind = resume.kind
                parent = resume.parent
            elif label.isdigit():
                kind = "num"
                parent = root
            elif label.isupper():
                kind = "upper"
                parent = stack[-1]
            elif label in ROMANS and (
                len(label) > 1 or label == "i" or stack[-1].kind == "roman"
            ):
                kind = "roman"
                parent = stack[-1]
                if parent.kind == "roman":
                    parent = parent.parent
            else:
                kind = "alpha"
                parent = next(
                    (a for a in reversed(stack) if a.kind in ["num", "root"]), root
                )
                if stack[-1].kind in ("roman", "upper") and (
                    x >= stack[-1].x - 1
                    or (
                        paragraph_index
                        and paragraph_list[paragraph_index - 1]["text"]
                        == f"({stack[-1].label})"
                    )
                ):
                    parent = stack[-1]
                elif (
                    stack[-1].kind == "alpha" and x > stack[-1].x + 7 and (label == "a")
                ):
                    parent = stack[-1]
            if residual_owner is not None and resume is None:
                residual_owner.addtext(text, True)
                continue
            while stack[-1] is not parent:
                stack.pop()
            node = Provision(label, kind, x, parent)
            parent.events.append(("child", node))
            stack.append(node)
            residual_owner = None
            node.addtext(text[match.end() :])
            diagnostics["nodes"] += 1
        else:
            is_residual = bool(RESIDUAL.match(text))
            is_centered = p["x"] > 150 and text.startswith("Illustration")
            if is_residual:
                residual_mode = (
                    "illustration"
                    if text.startswith("Illustration")
                    else "explanation" if text.startswith("Explanation") else "proviso"
                )
                if is_definition and residual_mode in ("illustration", "explanation"):
                    owner = next(
                        (
                            a
                            for a in reversed(stack)
                            if a.kind == "alpha"
                            and a.parent.kind == "num"
                            or (a.kind == "num" and stem == "Bns_2023")
                        ),
                        stack[-1],
                    )
                    while stack[-1] is not owner:
                        stack.pop()
                elif is_centered:
                    if residual_owner:
                        owner = residual_owner
                    else:
                        owner = next(
                            (a for a in reversed(stack) if a.kind in ("num", "root")),
                            root,
                        )
                    while stack[-1] is not owner:
                        stack.pop()
                else:
                    while (
                        len(stack) > 1
                        and stack[-1].kind != "num"
                        and (x < stack[-1].x - 7)
                    ):
                        stack.pop()
                    owner = stack[-1]
                residual_owner = owner
                owner.addtext(text, True)
            elif residual_owner:
                residual_owner.addtext(text, True)
            else:
                while (
                    len(stack) > 1 and stack[-1].kind != "num" and (x < stack[-1].x - 7)
                ):
                    stack.pop()
                stack[-1].addtext(
                    text,
                    len(stack[-1].events) > 1
                    and any((event[0] == "child" for event in stack[-1].events)),
                )
    require(
        re.sub(r"\s", "", root.full()) == re.sub(r"\s", "", section["text"]),
        f'{stem} section {section["section_number"]}: hierarchy changed the source text.',
    )
    children = [event[1] for event in root.events if event[0] == "child"]
    section["subsections"] = [node.export() for node in children if node.kind == "num"]
    direct = [node.export() for node in children if node.kind != "num"]
    if direct:
        section["clauses"] = direct
    section["residual_text"] = "\n".join(
        (
            event[1]
            for event in root.events
            if event[0] == "text" and (children or event[2])
        )
    ).strip()

    def review(node):
        children = [event[1] for event in node.events if event[0] == "child"]
        labels = [x.label for x in children]
        if len(labels) != len(set(labels)):
            diagnostics["duplicate_ids"].append(
                [section["section_number"], node.label, labels]
            )
        for a, b in zip(children, children[1:]):
            if a.kind == b.kind and next_label(a.kind, a.label) != b.label:
                diagnostics["sequence_gaps"].append(
                    [section["section_number"], node.label, a.label, b.label]
                )
        for child in children:
            review(child)

    review(root)
    return root
