"""Small, validated study diagrams rendered as PDF vectors, never executable SVG.

Only layered acyclic graphs are redrawn. Complex notation, cycles and dense
graphs must use a source crop instead of silently losing their meaning.
"""

import math
import textwrap

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors


def validate(spec):
    if not isinstance(spec, dict):
        raise ValueError("Diagram must be an object")
    nodes, edges = spec.get("nodes"), spec.get("edges")
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 9:
        raise ValueError("Use 2-9 nodes")
    if not isinstance(edges, list) or not 1 <= len(edges) <= 12:
        raise ValueError("Use 1-12 edges")
    ids, clean_nodes = set(), []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Invalid node")
        ident, label = node.get("id"), node.get("label")
        if not isinstance(ident, str) or not ident or len(ident) > 16 or ident in ids:
            raise ValueError("Invalid/duplicate node ID")
        if not isinstance(label, str) or not label.strip() or len(label) > 64:
            raise ValueError("Invalid label")
        ids.add(ident)
        clean_nodes.append({"id": ident, "label": " ".join(label.split())})
    clean_edges, seen, used = [], set(), set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Invalid edge")
        a, b, label = edge.get("from"), edge.get("to"), edge.get("label", "")
        if (
            not isinstance(a, str)
            or not isinstance(b, str)
            or a not in ids
            or b not in ids
            or a == b
        ):
            raise ValueError("Invalid edge endpoint")
        if not isinstance(label, str) or len(label) > 28:
            raise ValueError("Invalid edge label")
        if (a, b) in seen:
            raise ValueError("Duplicate edge")
        seen.add((a, b))
        used.update((a, b))
        clean_edges.append({"from": a, "to": b, "label": " ".join(label.split())})
    if used != ids:
        raise ValueError("Disconnected node")
    clean = {"nodes": clean_nodes, "edges": clean_edges}
    layers = _layers(clean)
    if len(layers) > 5 or max(map(len, layers)) > 3:
        raise ValueError("Too dense to draw readably")
    ranks = {node: rank for rank, layer in enumerate(layers) for node in layer}
    if any(ranks[e["to"]] != ranks[e["from"]] + 1 for e in clean_edges):
        raise ValueError("Long edges could cross intermediate nodes")
    # Avoid crossing many-to-many connectors. A source crop is more faithful.
    positions = {n: i for layer in layers for i, n in enumerate(layer)}
    for a in clean_edges:
        for b in clean_edges:
            if ranks[a["from"]] == ranks[b["from"]] and (
                (positions[a["from"]] - positions[b["from"]])
                * (positions[a["to"]] - positions[b["to"]])
                < 0
            ):
                raise ValueError("Crossing edges require a source crop")
    return clean


def _layers(spec):
    pending = [n["id"] for n in spec["nodes"]]
    done, layers = set(), []
    while pending:
        layer = [
            n for n in pending if all(e["from"] in done for e in spec["edges"] if e["to"] == n)
        ]
        if not layer:
            raise ValueError("Cyclic diagram")
        layers.append(layer)
        done.update(layer)
        pending = [n for n in pending if n not in done]
    return layers


def drawing(spec, font="Helvetica", width=470):
    spec = validate(spec)
    layers = _layers(spec)
    # A simple chain fits horizontally. Branches use centered vertical layers.
    horizontal = (
        len(layers) <= 3
        and all(len(layer) == 1 for layer in layers)
        and not any(e["label"] for e in spec["edges"])
    )
    height = 76 if horizontal else 24 + len(layers) * 76
    result = Drawing(width, height)
    accent = colors.HexColor("#245880")
    locations = {}
    node_w, node_h = min(134, width / 3 - 16), 44
    for rank, layer in enumerate(layers):
        for i, ident in enumerate(layer):
            x = (rank + 0.5) * width / len(layers) if horizontal else (i + 0.5) * width / len(layer)
            y = height / 2 if horizontal else height - 38 - rank * 76
            locations[ident] = (x, y)
    for edge in spec["edges"]:
        x1, y1 = locations[edge["from"]]
        x2, y2 = locations[edge["to"]]
        if horizontal:
            x1 += node_w / 2
            x2 -= node_w / 2
        else:
            y1 -= node_h / 2
            y2 += node_h / 2
        result.add(Line(x1, y1, x2, y2, strokeColor=accent, strokeWidth=1.2))
        theta = math.atan2(y2 - y1, x2 - x1)
        arrow = [x2, y2]
        for angle in (theta + 2.7, theta - 2.7):
            arrow += [x2 + 6 * math.cos(angle), y2 + 6 * math.sin(angle)]
        result.add(Polygon(arrow, fillColor=accent, strokeColor=accent))
        if edge["label"]:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            # White-backed labels remain readable over a connector.
            label_width = max(18, len(edge["label"]) * 4.4)
            result.add(
                Rect(
                    mx - label_width / 2,
                    my + 3,
                    label_width,
                    12,
                    fillColor=colors.white,
                    strokeColor=None,
                )
            )
            result.add(
                String(
                    mx,
                    my + 5,
                    edge["label"],
                    fontName=font,
                    fontSize=8,
                    textAnchor="middle",
                    fillColor=accent,
                )
            )
    for node in spec["nodes"]:
        x, y = locations[node["id"]]
        result.add(
            Rect(
                x - node_w / 2,
                y - node_h / 2,
                node_w,
                node_h,
                rx=6,
                ry=6,
                fillColor=colors.HexColor("#eef4fb"),
                strokeColor=accent,
                strokeWidth=1,
            )
        )
        lines = textwrap.wrap(node["label"], width=22)
        for i, line in enumerate(lines):
            result.add(
                String(
                    x,
                    y + (len(lines) - 1) * 5.5 - i * 11 - 3,
                    line,
                    fontName=font,
                    fontSize=9,
                    textAnchor="middle",
                    fillColor=accent,
                )
            )
    return result
