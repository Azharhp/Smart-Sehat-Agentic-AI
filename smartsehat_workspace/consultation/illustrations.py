"""Code-authored observation diagrams, not clinical photographs or diagnosis."""
import html

_PERSON = '<circle cx="110" cy="44" r="21" fill="#c99670"/><path d="M75 150v-43q0-33 35-33t35 33v43" fill="#cbe6ef" stroke="#234b60" stroke-width="4"/>'
_SCENES = {
    "breathing": _PERSON + '<path d="M103 92q-27-19-27 17t27 14M117 92q27-19 27 17t-27 14" fill="#fff" stroke="#087d82" stroke-width="4"/><path d="M43 92h26m-9-8 9 8-9 8M177 128h-26m9-8-9 8 9 8" fill="none" stroke="#c13d49" stroke-width="5"/>',
    "drink": _PERSON + '<path d="M28 58h32l-5 44H33z" fill="#e2f4ff" stroke="#087d82" stroke-width="4"/><path d="M60 70q18-1 8 18H57M64 63l22-13" fill="none" stroke="#087d82" stroke-width="4"/>',
    "vomit": _PERSON + '<path d="M88 52Q60 59 58 82" fill="none" stroke="#bc8325" stroke-width="5"/><path d="M25 97q27 45 62 0z" fill="#fff" stroke="#087d82" stroke-width="4"/>',
    "alert": '<path d="M29 131h161" stroke="#234b60" stroke-width="5"/><circle cx="58" cy="103" r="21" fill="#c99670"/><path d="M82 123V93h75l25 30" fill="#cbe6ef" stroke="#234b60" stroke-width="4"/><path d="m68 22 12 18H56zM67 49v10" fill="#fff0c2" stroke="#b26b13" stroke-width="4"/>',
    "brain": _PERSON + '<path d="m101 23 13 16-12 8 15 16" fill="none" stroke="#c13d49" stroke-width="5"/><path d="m56 46-8 12 9 10m108-22 8 12-9 10" fill="none" stroke="#087d82" stroke-width="4"/>',
    "heart": _PERSON + '<path d="M110 124 87 104q-14-22 7-25 11-1 16 11 5-12 16-11 21 3 7 25z" fill="#c13d49"/>',
    "blood": _PERSON + '<path d="M45 71q-29 37 0 39 29-2 0-39" fill="#c13d49"/><path d="m150 106 26 0" stroke="#c13d49" stroke-width="5"/>',
    "maternal": _PERSON + '<ellipse cx="120" cy="122" rx="27" ry="25" fill="#fff" stroke="#087d82" stroke-width="4"/><circle cx="119" cy="118" r="9" fill="#c99670"/><path d="M117 128q16 4 15-9" fill="none" stroke="#234b60" stroke-width="4"/>',
    "eye": '<path d="M30 84q80-79 160 0-80 79-160 0" fill="#fff" stroke="#234b60" stroke-width="5"/><circle cx="110" cy="84" r="24" fill="#5d9caa"/><circle cx="110" cy="84" r="10" fill="#234b60"/><path d="M41 135h31m-10-9 10 9-10 9" fill="none" stroke="#087d82" stroke-width="4"/>',
    "hand": '<path d="M63 138V76q0-16 12-13V36q0-15 12-10v39-44q8-15 15 0v44-46q9-12 15 0v48-34q12-14 15 2v61l13-24q17-8 15 8l-21 50-22 23H83z" fill="#c99670" stroke="#234b60" stroke-width="4"/>',
    "skin": _PERSON + '<g fill="#c13d49"><circle cx="90" cy="98" r="4"/><circle cx="117" cy="92" r="4"/><circle cx="131" cy="121" r="4"/><circle cx="97" cy="131" r="4"/></g>',
    "device": '<rect x="34" y="24" width="150" height="120" rx="13" fill="#e0edf1" stroke="#234b60" stroke-width="5"/><rect x="49" y="39" width="120" height="65" rx="5" fill="#fff"/><path d="m60 73 19 0 8-18 11 36 10-24 19 6h27" fill="none" stroke="#087d82" stroke-width="4"/><circle cx="110" cy="126" r="9" fill="#087d82"/>',
    "tape": '<path d="M25 57h170v50H25z" fill="#e0edf1" stroke="#234b60" stroke-width="4"/><path d="M40 57v28m20-28v16m20-16v28m20-28v16m20-16v28m20-28v16m20-16v28m20-28v16" stroke="#234b60" stroke-width="3"/><text x="80" y="140" font-size="22" fill="#234b60">cm / kg</text>',
}
_FAMILIES = {
    "cannot_drink": "drink", "dehydration": "drink", "vomits_everything": "vomit",
    "convulsions": "brain", "confusion": "brain", "lethargic": "alert", "fainting": "alert", "shock": "alert",
    "chest_pain": "heart", "heavy_bleeding": "blood", "vaginal_bleeding": "blood",
    "severe_headache_blurred_vision": "eye", "reduced_fetal_movement": "maternal", "leaking_fluid": "maternal",
    "pallor": "hand", "oedema": "hand", "jaundice": "eye", "rash": "skin",
    "muac": "tape", "weight": "tape", "temperature": "device", "spo2": "device", "pulse": "device", "systolic": "device", "diastolic": "device", "fever": "device",
}


def illustration(key: str, title: str) -> str:
    family = _FAMILIES.get(key, "breathing")
    return '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + html.escape(title, quote=True) + '" viewBox="0 0 220 174"><title>' + html.escape(title) + '</title><rect width="220" height="174" rx="15" fill="#f3f8fa"/>' + _SCENES[family] + '</svg>'
