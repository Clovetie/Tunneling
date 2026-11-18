"""Offline checks for caller-supplied localization entries; never reads Studio."""

import re

# Supported subset of Roblox's documented localization parameter syntax.
# https://github.com/Roblox/creator-docs/blob/main/content/en-us/production/localization/translate-dynamic-content.md
_PARAMETER = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|[0-9]+)(?::([A-Za-z][A-Za-z0-9]*))?\Z")
_SPECIFIERS = frozenset(("int", "fixed", "num", "HEX", "hex", "datetime", "iso8601",
                         "shorttime", "shortdatetime", "shortdate", "translate"))


def _parameters(text):
    """Return name -> specifiers and offsets of unrecognized brace constructs.

    Double braces are ignored for backwards compatibility; their Roblox runtime
    interpretation is not asserted. This deliberately does not parse nested or
    arbitrary formatting languages.
    """
    params, unsupported = {}, []
    i = 0
    while i < len(text):
        if text.startswith("{{", i) or text.startswith("}}", i):
            i += 2
            continue
        if text[i] == "}":
            unsupported.append(i)
            i += 1
            continue
        if text[i] != "{":
            i += 1
            continue
        end = text.find("}", i + 1)
        if end < 0:
            unsupported.append(i)
            break
        match = _PARAMETER.fullmatch(text[i + 1:end])
        if not match or (match.group(2) and match.group(2) not in _SPECIFIERS):
            unsupported.append(i)
        else:
            params.setdefault(match.group(1), set()).add(match.group(2) or "")
        i = end + 1
    return params, unsupported


def audit_localization(entries, locales, cursor=0, limit=50):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
        raise ValueError("entries must contain 1 to 200 entries")
    if not isinstance(locales, list) or not 1 <= len(locales) <= 20:
        raise ValueError("locales must contain 1 to 20 locale strings")
    if any(not isinstance(locale, str) or not locale.strip() or len(locale) > 100 for locale in locales):
        raise ValueError("each locale must be a non-empty string of at most 100 characters")
    if len(set(locales)) != len(locales):
        raise ValueError("duplicate requested locales are not allowed")
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")

    issues = []
    total_issues = 0

    def emit(issue):
        nonlocal total_issues
        if cursor <= total_issues < cursor + limit:
            issues.append(issue)
        total_issues += 1

    seen = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {index} must be an object")
        key, source, translations = entry.get("key"), entry.get("source"), entry.get("translations")
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError(f"entry {index} key must be a non-empty string of at most 200 characters")
        if not isinstance(source, str) or not source.strip() or len(source) > 4000:
            raise ValueError(f"entry {index} source must be a non-empty string of at most 4000 characters")
        if not isinstance(translations, dict) or len(translations) > 20:
            raise ValueError(f"entry {index} translations must be an object with at most 20 locales")
        for locale, text in translations.items():
            if not isinstance(locale, str) or not locale.strip() or len(locale) > 100 or not isinstance(text, str) or len(text) > 4000:
                raise ValueError(f"entry {index} has an invalid translation locale or text")
        if key in seen:
            emit({"code": "duplicate_key", "entryIndex": index, "key": key, "firstIndex": seen[key]})
        else:
            seen[key] = index
        expected, source_unsupported = _parameters(source)
        for offset in source_unsupported:
            emit({"code": "unsupported_placeholder", "entryIndex": index, "key": key,
                           "field": "source", "offset": offset})
        for locale in locales:
            text = translations.get(locale)
            if text is None or not text.strip():
                emit({"code": "missing_translation", "entryIndex": index, "key": key, "locale": locale})
                continue
            actual, unsupported = _parameters(text)
            for offset in unsupported:
                emit({"code": "unsupported_placeholder", "entryIndex": index, "key": key,
                               "field": "translation", "locale": locale, "offset": offset})
            missing, extra = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
            if missing or extra:
                emit({"code": "placeholder_mismatch", "entryIndex": index, "key": key,
                               "locale": locale, "missing": missing, "extra": extra})
            for parameter in sorted(expected.keys() & actual.keys()):
                if expected[parameter] != actual[parameter]:
                    emit({"code": "format_mismatch", "entryIndex": index, "key": key,
                                   "locale": locale, "parameter": parameter,
                                   "sourceFormats": sorted(expected[parameter]),
                                   "translationFormats": sorted(actual[parameter])})
    return {"ok": total_issues == 0, "entriesChecked": len(entries), "localesChecked": locales,
            "totalIssues": total_issues, "issues": issues,
            "nextCursor": cursor + limit if cursor + limit < total_issues else None,
            "limitations": ["Only supplied entries and requested exact locale keys are checked; no Studio or translation tables were read.",
                            "Named/positional parameters and documented specifiers are compared as sets; format differences require review and may be intentional. Repeated occurrences, types, mixed parameter modes and double braces are not validated.",
                            "Does not verify translation accuracy, locale fallback, layout, font coverage, or runtime behavior."]}
