"""Content security policy reports as browsers send them, each shape with
its content type and the directives the backend finds in it."""

# report-uri's one report, as most browsers send it.
REPORT_URI = {
    "csp-report": {
        "document-uri": "https://inventory.example/scan",
        "blocked-uri": "https://elsewhere.example/x.js",
        "effective-directive": "script-src-elem",
        "violated-directive": "script-src-elem",
        "original-policy": "default-src 'self'",
    }
}
# report-uri's one report as WebKit sends it, in the Reporting API's shape.
WEBKIT_REPORT_URI = {
    "type": "csp-violation",
    "url": "https://inventory.example/scan",
    "body": {
        "documentURL": "https://inventory.example/scan",
        "effectiveDirective": "style-src-attr",
        "blockedURL": "inline",
        "disposition": "enforce",
        "statusCode": 200,
    },
}
# The Reporting API's list, which carries reports of other types too.
REPORTING_API = [
    {
        "type": "csp-violation",
        "url": "https://inventory.example/admin",
        "user_agent": "Mozilla/5.0 (Phone)",
        "body": {
            "documentURL": "https://inventory.example/admin",
            "blockedURL": "inline",
            "effectiveDirective": "style-src-attr",
            "sample": "color: red",
        },
    },
    {"type": "deprecation", "body": {"id": "x"}},
    {"type": "csp-violation", "body": {"effectiveDirective": "made-up-src"}},
]

# (report, content type, directives found), one for each shape above.
SHAPES: list[tuple[object, str, list[str]]] = [
    (REPORT_URI, "application/csp-report", ["script-src-elem"]),
    (WEBKIT_REPORT_URI, "application/csp-report", ["style-src-attr"]),
    (REPORTING_API, "application/reports+json", ["style-src-attr", "other"]),
]
