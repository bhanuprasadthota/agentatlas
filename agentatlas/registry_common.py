import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

DEFAULT_TASK_KEY = "generic_extract"
DEFAULT_VARIANT_KEY = "desktop_enUS_loggedout"
DEFAULT_REGISTRY_SCOPE = "auto"
VALID_ROLE_NAMES = {
    "alert", "alertdialog", "application", "article", "banner", "blockquote", "button",
    "caption", "cell", "checkbox", "code", "columnheader", "combobox", "complementary",
    "contentinfo", "definition", "deletion", "dialog", "directory", "document", "emphasis",
    "feed", "figure", "form", "generic", "grid", "gridcell", "group", "heading", "img",
    "insertion", "link", "list", "listbox", "listitem", "log", "main", "marquee", "math",
    "meter", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "navigation",
    "none", "note", "option", "paragraph", "presentation", "progressbar", "radio",
    "radiogroup", "region", "row", "rowgroup", "rowheader", "scrollbar", "search",
    "searchbox", "separator", "slider", "spinbutton", "status", "strong", "subscript",
    "superscript", "switch", "tab", "table", "tablist", "tabpanel", "term", "textbox",
    "time", "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem"
}


def path_signature(path: str) -> str:
    if not path:
        return "/"
    normalized_segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if re.fullmatch(r"\d+", segment):
            normalized_segments.append(":id")
        elif re.fullmatch(r"[0-9a-fA-F-]{8,}", segment):
            normalized_segments.append(":id")
        else:
            normalized_segments.append(segment.lower())
    return "/" + "/".join(normalized_segments) if normalized_segments else "/"


def build_route_fingerprint(acc_nodes: list[dict], url: str) -> dict:
    parsed = urlparse(url)
    compact_nodes = []
    for node in acc_nodes[:50]:
        compact_nodes.append({
            "role": (node.get("role") or "").strip().lower(),
            "name": " ".join((node.get("name") or "").strip().lower().split())[:80],
            "level": node.get("level", 0),
        })
    basis = {
        "path_signature": path_signature(parsed.path),
        "nodes": compact_nodes,
    }
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    return {
        "algorithm": "acc_tree_v1",
        "value": digest,
        "path_signature": basis["path_signature"],
        "node_count": len(acc_nodes),
    }


def path_pattern_from_signature(signature: str) -> str:
    parts = []
    for segment in signature.strip("/").split("/"):
        if not segment:
            continue
        if segment == ":id":
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(segment))
    return r"^/" + "/".join(parts) + r"/?$" if parts else r"^/$"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
