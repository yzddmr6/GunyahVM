#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Extract, check and compile the GTK frontend's gettext catalogues without extra tools.

  translations.py pot              rewrite po/gunyahvm.pot from the sources
  translations.py merge            add new strings to every po/*.po, drop removed ones
  translations.py check            fail on missing, empty, fuzzy or stale translations
  translations.py compile OUTDIR   write OUTDIR/<lang>/LC_MESSAGES/gunyahvm.mo
"""

import ast
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "src/gunyahvm"
PO = ROOT / "po"
DOMAIN = "gunyahvm"
HEADER = (
    "Project-Id-Version: GunyahVM\n"
    "Report-Msgid-Bugs-To: https://github.com/yzddmr6/GunyahVM/issues\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
)
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def extract():
    """{(msgid, plural or None): [locations]} for _(), N_() and ngettext() literals."""
    found = {}
    for path in sorted(SOURCES.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name, args = node.func.id, node.args
            literal = [isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args]
            if name in ("_", "N_") and args and literal[0]:
                key = (args[0].value, None)
            elif name == "ngettext" and len(args) >= 2 and literal[0] and literal[1]:
                key = (args[0].value, args[1].value)
            else:
                continue
            found.setdefault(key, []).append((str(path.relative_to(ROOT)), node.lineno))
    return {key: sorted(locations) for key, locations in found.items()}


def quote(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    lines = escaped.split("\n")
    parts = [line + "\\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    if len(parts) <= 1:
        return '"' + (parts[0] if parts else "") + '"'
    return '""\n' + "\n".join('"' + part + '"' for part in parts)


def unquote(text):
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse(path):
    """Entries with msgid, plural, msgstr (list), flags, comments; header is entry ''."""
    entries, entry, field = [], None, None

    def finish():
        if entry is not None and "msgid" in entry:
            entries.append(entry)

    for raw in path.read_text().splitlines() + [""]:
        line = raw.strip()
        if not line:
            finish()
            entry, field = None, None
            continue
        if entry is None:
            entry = {"comments": [], "flags": set(), "msgstr": {}, "obsolete": False}
        if line.startswith("#~"):
            entry["obsolete"] = True
            continue
        if line.startswith("#,"):
            entry["flags"].update(f.strip() for f in line[2:].split(","))
            continue
        if line.startswith("#"):
            entry["comments"].append(raw)
            continue
        match = re.match(r'(msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+"(.*)"$', line)
        if match:
            keyword, index, value = match.groups()
            field = ("msgstr", int(index or 0)) if keyword.startswith("msgstr") else keyword
            if isinstance(field, tuple):
                entry["msgstr"][field[1]] = unquote(value)
            else:
                entry[field] = unquote(value)
            continue
        if line.startswith('"') and line.endswith('"') and field:
            if isinstance(field, tuple):
                entry["msgstr"][field[1]] += unquote(line[1:-1])
            else:
                entry[field] += unquote(line[1:-1])
            continue
        raise ValueError(f"{path}: cannot parse {raw!r}")
    return entries


def render(entries):
    out = []
    for entry in entries:
        out.extend(entry["comments"])
        if entry["flags"]:
            out.append("#, " + ", ".join(sorted(entry["flags"])))
        out.append("msgid " + quote(entry["msgid"]))
        if entry.get("msgid_plural") is not None:
            out.append("msgid_plural " + quote(entry["msgid_plural"]))
            for index in sorted(entry["msgstr"]):
                out.append(f"msgstr[{index}] " + quote(entry["msgstr"][index]))
        else:
            out.append("msgstr " + quote(entry["msgstr"].get(0, "")))
        out.append("")
    return "\n".join(out)


def template_entries(found):
    entries = [{
        "comments": ["# SPDX-License-Identifier: GPL-2.0-only", "# Copyright (C) 2026 yzddMr6"],
        "flags": set(), "msgid": "", "msgstr": {0: HEADER},
    }]
    for (msgid, plural), locations in sorted(found.items(), key=lambda item: item[1][0]):
        entries.append({
            "comments": ["#: " + " ".join(f"{file}:{line}" for file, line in locations)],
            "flags": {"python-brace-format"} if PLACEHOLDER.search(msgid) else set(),
            "msgid": msgid,
            "msgid_plural": plural,
            "msgstr": {0: "", 1: ""} if plural else {0: ""},
        })
    return entries


def catalogues():
    return sorted(PO.glob("*.po"))


def merge():
    found = extract()
    template = template_entries(found)
    for path in catalogues():
        old = {(e["msgid"], e.get("msgid_plural")): e for e in parse(path) if not e["obsolete"]}
        header = old.get(("", None))
        merged = [header] if header else []
        for entry in template[1:]:
            previous = old.get((entry["msgid"], entry.get("msgid_plural")))
            if previous:
                entry = {**entry, "msgstr": previous["msgstr"], "flags": entry["flags"] | (previous["flags"] & {"fuzzy"})}
            elif entry.get("msgid_plural") is not None:
                entry = {**entry, "msgstr": {0: ""}}
            merged.append(entry)
        path.write_text(render(merged))


def problems():
    found = extract()
    errors = []
    pot = PO / f"{DOMAIN}.pot"
    if not pot.is_file() or pot.read_text() != render(template_entries(found)):
        errors.append("po/gunyahvm.pot is out of date: run tools/translations.py pot")
    if not catalogues():
        errors.append("no translations in po/")
    for path in catalogues():
        entries = [e for e in parse(path) if e["msgid"]]
        if any(e["obsolete"] for e in entries):
            errors.append(f"{path.name}: obsolete entries")
        keys = {(e["msgid"], e.get("msgid_plural")): e for e in entries}
        for key in found:
            if key not in keys:
                errors.append(f"{path.name}: missing {key[0]!r}")
        for key, entry in keys.items():
            msgid = key[0]
            if key not in found:
                errors.append(f"{path.name}: stale {msgid!r}")
                continue
            if "fuzzy" in entry["flags"]:
                errors.append(f"{path.name}: fuzzy {msgid!r}")
            values = list(entry["msgstr"].values())
            if not values or not all(v.strip() for v in values):
                errors.append(f"{path.name}: untranslated {msgid!r}")
                continue
            for value in values:
                wanted = set(PLACEHOLDER.findall(msgid) + PLACEHOLDER.findall(key[1] or ""))
                if set(PLACEHOLDER.findall(value)) != wanted:
                    errors.append(f"{path.name}: placeholders differ in {msgid!r}")
    return errors


def compile_mo(path):
    """Deterministic GNU .mo; the header entry carries charset and plural forms."""
    pairs = {}
    for entry in parse(path):
        if entry["obsolete"] or "fuzzy" in entry["flags"]:
            continue
        values = [entry["msgstr"][i] for i in sorted(entry["msgstr"])]
        if entry["msgid"] and not all(values):
            continue
        key = entry["msgid"]
        if entry.get("msgid_plural") is not None:
            key += "\0" + entry["msgid_plural"]
        pairs[key.encode()] = "\0".join(values).encode()
    keys = sorted(pairs)
    count = len(keys)
    ids_offset = 28
    strs_offset = ids_offset + count * 8
    data_offset = strs_offset + count * 8
    ids, strs, blob = [], [], b""
    for key in keys:
        ids.append((len(key), data_offset + len(blob)))
        blob += key + b"\0"
    for key in keys:
        value = pairs[key]
        strs.append((len(value), data_offset + len(blob)))
        blob += value + b"\0"
    out = struct.pack("<7I", 0x950412DE, 0, count, ids_offset, strs_offset, 0, data_offset)
    for length, offset in ids + strs:
        out += struct.pack("<2I", length, offset)
    return out + blob


def compile_all(outdir):
    written = []
    for path in catalogues():
        target = Path(outdir) / path.stem / "LC_MESSAGES" / f"{DOMAIN}.mo"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(compile_mo(path))
        written.append(target)
    return written


def main(argv):
    if argv[:1] == ["pot"]:
        (PO / f"{DOMAIN}.pot").write_text(render(template_entries(extract())))
    elif argv[:1] == ["merge"]:
        merge()
    elif argv[:1] == ["check"]:
        errors = problems()
        print("\n".join(errors) if errors else f"{len(extract())} strings translated in {len(catalogues())} catalogue(s).")
        return 1 if errors else 0
    elif argv[:1] == ["compile"] and len(argv) == 2:
        for target in compile_all(argv[1]):
            print("wrote", target)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
