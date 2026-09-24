import re

if __package__:
    from .config import *
    from .utils import norm, matches, snapshot
else:
    from config import *
    from utils import norm, matches, snapshot

"""TTD Screen-2 pilgrim/contact form handling."""

def field_context(page, c):
    parts = []
    try:
        cid = c.get_attribute("id")
        if cid:
            lab = page.locator(f'label[for="{cid}"]').first
            if lab.count(): parts.append(lab.inner_text())
    except Exception: pass
    for attr in ["aria-label", "placeholder", "name", "id", "title"]:
        try: parts.append(c.get_attribute(attr) or "")
        except Exception: pass
    for xp in ["xpath=..", "xpath=../.."]:
        try: parts.append(c.locator(xp).inner_text(timeout=500)[:500])
        except Exception: pass
    return " ".join(parts)

def _find_labeled_control(page, label):
    """Find visible form controls by their own field-label wrapper.

    Older TTD builds are less consistent about the `name` attribute. In those
    builds a broad field-context search can confuse Age with Photo ID Number
    because both controls live inside the same Pilgrim Details row.

    We therefore inspect the DOM hierarchy for the *smallest* ancestor that:
      - contains the exact field label, and
      - contains only a small number of form controls.

    This keeps Age attached to the Age input, Name to Name, etc., even when
    the old form has weak/missing semantic attributes.
    """
    wanted = norm(label)
    controls = page.locator(
        "input:visible, textarea:visible, select:visible, "
        "[role='combobox']:visible, [contenteditable='true']:visible"
    )
    found = []

    for i in range(controls.count()):
        c = controls.nth(i)
        try:
            matched = c.evaluate(
                """
                (el, wanted) => {
                    const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    let node = el;
                    let best = null;
                    let bestScore = -1;
                    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                        const text = norm(node.innerText || node.textContent || '');
                        if (!text.includes(wanted)) continue;

                        const fields = node.querySelectorAll(
                            'input, textarea, select, [role="combobox"], [contenteditable="true"]'
                        );
                        const fieldCount = fields.length;

                        // A field wrapper normally contains one control. Allow
                        // up to three for older custom TTD wrappers.
                        if (fieldCount < 1 || fieldCount > 3) continue;

                        // Prefer the deepest/smallest matching wrapper.
                        const score = (100 - depth * 10) - fieldCount;
                        if (score > bestScore) {
                            best = true;
                            bestScore = score;
                        }
                    }
                    return !!best;
                }
                """,
                wanted,
            )
            if matched:
                found.append(c)
        except Exception:
            pass

    return found

def _find_pilgrim_fields_by_label(page, key):
    labels = {
        "name": "name",
        "age": "age",
        "gender": "gender",
        "id_type": "photo id proof",
        "id_number": "photo id number",
    }
    label = labels.get(key)
    if not label:
        return []
    return _find_labeled_control(page, label)

def find_fields(page, key):
    """Find TTD fields across old and new Screen-2 implementations.

    Strategy for pilgrim fields:
      1. Exact semantic DOM names from either known TTD form.
      2. Exact field-label wrapper matching for older/weak DOM builds.
      3. Very narrow attribute fallback.

    We deliberately do NOT use broad ancestor text matching for pilgrim
    fields. That was the source of the old-form bug where Photo ID Number
    (e.g. 441...) could be selected as Age.
    """
    name_aliases = {
        "name": ["fname", "name"],
        "age": ["age"],
        "gender": ["gender", "sex"],
        "id_type": ["photoIdType", "idType", "photoIDType", "photo_id_type"],
        "id_number": ["idProofNumber", "idNumber", "photoIdNumber", "photoIDNumber", "photo_id_number"],
        "email": ["pilgrimEmail", "email", "emailId"],
        "city": ["pilgrimCity", "city"],
        "state": ["pilgrimState", "state"],
        "country": ["pilgrimCountry", "country"],
        "pincode": ["pilgrimPincode", "pincode", "pinCode", "postalCode"],
    }

    aliases = name_aliases.get(key, [])

    # Exact semantic names first.
    for exact in aliases:
        selectors = [
            f'[name="{exact}"]:visible',
            f'[data-name="{exact}"]:visible',
            f'[data-field="{exact}"]:visible',
        ]
        if key in {"email", "city", "state", "country", "pincode"}:
            selectors.append(f'[id="{exact}"]:visible')

        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = loc.count()
                if count:
                    return [loc.nth(i) for i in range(count)]
            except Exception:
                pass

    # Critical compatibility layer for the older TTD form.
    if key in {"name", "age", "gender", "id_type", "id_number"}:
        labeled = _find_pilgrim_fields_by_label(page, key)
        if labeled:
            return labeled

    # Narrow attribute fallback only; never inspect broad parent text for age or
    # ID fields.
    result = []
    controls = page.locator(
        "input:visible, textarea:visible, select:visible, "
        "[role='combobox']:visible, [contenteditable='true']:visible"
    )
    field_attr_patterns = {
        "name": re.compile(r"^(fname|name)$", re.I),
        "age": re.compile(r"^age$", re.I),
        "gender": re.compile(r"^(gender|sex)$", re.I),
        "id_type": re.compile(r"^(photoidtype|idtype|photo[_-]?id[_-]?type)$", re.I),
        "id_number": re.compile(r"^(idproofnumber|idnumber|photoidnumber|photo[_-]?id[_-]?number)$", re.I),
    }

    pattern = field_attr_patterns.get(key)
    if pattern:
        for i in range(controls.count()):
            c = controls.nth(i)
            try:
                attrs = " ".join(
                    (c.get_attribute(a) or "")
                    for a in ("name", "placeholder", "aria-label", "title", "label")
                )
                if pattern.search(norm(attrs)):
                    result.append(c)
            except Exception:
                pass
        return result

    for i in range(controls.count()):
        c = controls.nth(i)
        try:
            if matches(field_context(page, c), FIELD_PATTERNS[key]):
                result.append(c)
        except Exception:
            pass
    return result

def _visible_dropdown_options(page, control=None):
    """Find dropdown options across old and new TTD dropdown implementations."""
    selectors = [
        "li.floatingDropdown_listItem__tU_5x:visible",  # newer observed
        "li[class*='floatingDropdown_listItem']:visible",
        "[role='option']:visible",
        "li:visible",
    ]

    # Prefer options attached to the control's immediate wrapper. If that
    # wrapper has no options, TTD may render the menu in a portal; then search
    # the page for visible option-like elements.
    for selector in selectors:
        try:
            if control is not None:
                scoped = control.locator("xpath=..").locator(selector)
                if scoped.count():
                    return scoped
        except Exception:
            pass

    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count():
                return loc
        except Exception:
            pass

    return page.locator("li:visible")

def _dropdown_value_matches(control, expected):
    """Check a custom dropdown using value, text, aria/value attributes."""
    wanted = norm(str(expected))
    for attr in ("value", "aria-label", "data-value", "title"):
        try:
            actual = norm(control.get_attribute(attr) or "")
            if actual == wanted or wanted in actual:
                return True
        except Exception:
            pass
    try:
        actual = norm(control.input_value())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass
    try:
        actual = norm(control.inner_text())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass
    return False

def select_custom_dropdown(page, c, value):
    """Select Gender/Photo ID for both old and new TTD dropdowns."""
    target = norm(str(value))
    aliases = {target}

    if target in {"male", "m"}:
        aliases.update({"male", "m"})
    elif target in {"female", "f"}:
        aliases.update({"female", "f"})
    elif target in {"transgender", "other"}:
        aliases.update({"transgender", "other"})
    elif target in {"aadhaar", "aadhar", "aadhaar card", "aadhar card"}:
        aliases.update({"aadhaar", "aadhar", "aadhaar card", "aadhar card"})

    try:
        c.scroll_into_view_if_needed()
    except Exception:
        pass

    # Re-check the exact control immediately before clicking. React may have
    # replaced the input after the previous field was populated.
    try:
        c.click(force=True, timeout=1500)
    except Exception:
        try:
            c.locator("xpath=..").click(force=True, timeout=1500)
        except Exception:
            return False

    page.wait_for_timeout(100)

    options = _visible_dropdown_options(page, c)
    count = options.count()
    debug(f"[DEBUG] TTD dropdown option count: {count}")

    # Search exact visible text first. This avoids selecting an unrelated
    # option such as a hidden/stale dropdown belonging to another row.
    exact_matches = []
    seen_option_keys = set()
    for i in range(count):
        opt = options.nth(i)
        try:
            if not opt.is_visible():
                continue
            txt = norm(opt.inner_text())
        except Exception:
            continue
        if txt in aliases:
            try:
                key = opt.evaluate("e => e.outerHTML.slice(0,1200)")
            except Exception:
                key = f"option:{i}:{txt}"
            if key not in seen_option_keys:
                seen_option_keys.add(key)
                exact_matches.append(opt)

    # Older TTD builds can render dropdown choices in a portal using div/span
    # rather than li or role=option. Use Playwright's text engine as a final
    # option-discovery fallback.
    if not exact_matches:
        for alias in aliases:
            try:
                text_matches = page.get_by_text(alias, exact=True)
                for i in range(text_matches.count()):
                    opt = text_matches.nth(i)
                    if opt.is_visible():
                        exact_matches.append(opt)
            except Exception:
                pass

    debug(f"[DEBUG] Exact dropdown options matching requested value: {len(exact_matches)}")

    for opt in exact_matches:
        try:
            debug(f"[DEBUG] Selecting TTD dropdown option: '{norm(opt.inner_text())}'")
            opt.scroll_into_view_if_needed()
            opt.click(force=True, timeout=1500)
            page.wait_for_timeout(100)
        except Exception:
            continue

        if _dropdown_value_matches(c, value):
            debug(f"[OK] TTD dropdown selection verified: {value}")
            return True

        # React can replace the control after selection. Re-find the same
        # semantic field and check whether the selected row now contains value.
        try:
            key = None
            nm = norm(c.get_attribute("name") or "")
            if nm in {"gender", "sex"}:
                key = "gender"
            elif nm in {"photoidtype", "idtype", "photo_id_type", "photoidtype"}:
                key = "id_type"
            else:
                # Old TTD controls may have no useful name at all. Infer the
                # semantic field from its visible label/wrapper.
                for candidate_key, candidate_label in (("gender", "gender"), ("id_type", "photo id proof")):
                    try:
                        wrapper_text = norm(c.locator("xpath=..").inner_text())
                        if candidate_label in wrapper_text:
                            key = candidate_key
                            break
                    except Exception:
                        pass
            if key:
                fresh = find_fields(page, key)
                for fresh_control in fresh:
                    if _dropdown_value_matches(fresh_control, value):
                        debug(f"[OK] TTD dropdown selection verified after React refresh: {value}")
                        return True
        except Exception:
            pass

    snapshot(page, f"{target.replace(' ', '_')}_dropdown_options_not_found")
    return False

def set_control(page, c, value, custom=False):
    if custom:
        return select_custom_dropdown(page, c, value)

    try:
        c.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        c.fill(str(value))
    except Exception:
        try:
            c.click(force=True)
            c.press("Control+A")
            c.type(str(value))
        except Exception:
            return False

    return verify(c, value)

def verify(c, expected):
    wanted = norm(str(expected))

    try:
        actual = norm(c.input_value())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    try:
        actual = norm(c.get_attribute("value") or "")
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    try:
        actual = norm(c.inner_text())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    return False

def fill_one(page, c, value, label="", custom=False):
    ok = set_control(page, c, value, custom=custom)

    if ok:
        debug(f"[OK] {label}: verified")
    else:
        print(f"[MANUAL] {label}: verification failed")

    return ok

def fill_pilgrims(page, pilgrims, unresolved):
    """Populate pilgrims safely on both old and new TTD Screen-2 forms."""
    if not isinstance(pilgrims, list):
        return

    total = len(pilgrims)
    print(f"[PILGRIMS] Filling {total} pilgrim(s).")

    # Detect the schema once from the currently visible DOM. This prevents a
    # React re-render from making one field use the new names and another field
    # use the old fallback mapping during the same row.
    schema = {
        "name": "fname" if page.locator('[name="fname"]:visible').count() else "name",
        "age": "age",
        "gender": "gender" if page.locator('[name="gender"]:visible').count() else "sex",
        "id_type": "photoIdType" if page.locator('[name="photoIdType"]:visible').count() else "idType",
        "id_number": "idProofNumber" if page.locator('[name="idProofNumber"]:visible').count() else "idNumber",
    }
    debug(f"[PILGRIMS] Detected TTD field schema: {schema}")

    for idx, p in enumerate(pilgrims, 1):
        debug(f"[INFO] Pilgrim {idx}/{total}: {p.get('name', '')}")

        ordered = [
            ("name", "Name", p.get("name")),
            ("age", "Age", p.get("age")),
            ("gender", "Gender", p.get("gender") or "Male"),
            ("id_type", "Photo ID Proof", p.get("id_type") or "Aadhaar Card"),
            ("id_number", "Photo ID Number", p.get("id_number")),
        ]

        for key, label, value in ordered:
            # Use the schema-specific selector first, then the alias-aware
            # finder. Always take the nth occurrence for pilgrim idx.
            fields = find_fields(page, key)
            debug(
                f"[PILGRIMS] Pilgrim {idx} {label}: "
                f"found {len(fields)} matching visible field(s)"
            )

            if len(fields) < idx:
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else value,
                    "not_found",
                ))
                print(
                    f"[MANUAL] Pilgrim {idx} - {label}: "
                    f"not_found (found {len(fields)}, need row {idx})"
                )
                continue

            field = fields[idx - 1]

            if value in (None, ""):
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else "",
                    "not_configured",
                ))
                print(f"[MANUAL] Pilgrim {idx} - {label}: not configured")
                continue

            if key in ("id_type", "id_number"):
                try:
                    if field.is_disabled():
                        unresolved.append((
                            f"Pilgrim {idx} - {label}",
                            "REDACTED" if key == "id_number" else value,
                            "disabled",
                        ))
                        print(f"[MANUAL] Pilgrim {idx} - {label}: field is disabled")
                        continue
                except Exception:
                    pass

            ok = fill_one(
                page,
                field,
                value,
                f"Pilgrim {idx} - {label}",
                custom=(key in ("gender", "id_type")),
            )

            if not ok:
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else value,
                    "verification_failed",
                ))
                continue

            try:
                if "/temples" in page.url:
                    print("[STOP] TTD navigated to /temples. Stopping field automation to preserve the session.")
                    return
            except Exception:
                pass

    print(f"[OK] Processed all {total} configured pilgrim row(s).")

def fill_general(page, contact, unresolved):
    """Populate the optional Generic/General Details section.

    TTD Screen 2 can appear in two forms:
      1. Pilgrim Details only.
      2. Pilgrim Details + Generic/General Details.

    Generic details are OPTIONAL from the automation-flow perspective:
    if none of the generic fields are present on the current Screen 2,
    skip the section and continue with the pilgrim fields.

    If the generic section is present, populate the fields that are both
    present and configured. Missing configured generic fields are reported
    for manual attention, but the entire section is not treated as mandatory
    when TTD does not render it.
    """
    generic_fields = {
        key: find_fields(page, key)
        for key in ("email", "city", "state", "country", "pincode")
    }

    present_keys = [key for key, fields in generic_fields.items() if fields]

    if not present_keys:
        print("[GENERAL] Generic/General Details form not present; skipping it.")
        return

    debug(
        "[GENERAL] Generic/General Details form detected; "
        f"available fields: {', '.join(present_keys)}"
    )

    for key, label in [
        ("email", "Email"), ("city", "City"), ("state", "State"),
        ("country", "Country"), ("pincode", "Pincode")
    ]:
        fields = generic_fields[key]

        # Field is not rendered on this version of Screen 2.
        # Do not fail the whole flow because the section may be partial.
        if not fields:
            print(f"[GENERAL] {label}: field not present; skipping.")
            continue

        value = contact.get(key)

        if not value or str(value).startswith("YOUR_"):
            unresolved.append((label, "", "not_configured"))
            print(f"[MANUAL] {label}: not configured")
            continue

        if fill_one(page, fields[0], value):
            debug(f"[OK] {label}: verified")
        else:
            unresolved.append((label, value, "verification_failed"))
            print(f"[MANUAL] {label}: verification failed")

