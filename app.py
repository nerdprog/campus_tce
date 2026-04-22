from collections import defaultdict
from functools import lru_cache
from flask import Flask, jsonify, render_template, request
import heapq
import math
import os
import xml.etree.ElementTree as ET

app = Flask(__name__)

OSM_FILE = "map.osm"
KML_FILE = "map.kml"

BLACKLIST = [
    "avaniyapuram",
    "poriryal kalloori",
    "thiyagarajar porir",
    "tce road",
    "multi core lab",
    "multicore lab",
    "path to main building",
    "kendra vidhyalaya",
    "kalloori",
    "college mens hostel",
    "architecture dept road",
    "cse road",
    "\u0b95\u0bc7\u0ba8\u0bcd\u0ba4\u0bbf\u0bb0\u0bbf\u0baf",
    "t s srinivasan",
]

KML_NAME_ALIASES = {
    "TCE Parking Lot": "TCE Parking",
    "TCE LIBRARY": "Library",
    "Department of ECE": "Department of Electronics and Communication",
    "Department of Computer Science and Engineering": "Department of Computer Science",
    "Department of IT": "Department of Information Technology",
    "B Halls": "B-Halls",
    "M Halls": "M-Halls",
    "TCE Main Entarance": "Tce main gate",
    "TCE  Main Entarance": "Tce main gate",
    "TCE SOUTH ENTRANCE": "TCE South Entrance",
    "T S Srinivasan Centre for Automotive Research": "TS Srinivasan Center for Automotive Research",
}

DISPLAY_NAME_OVERRIDES = {
    "Tce main gate": "TCE Main Gate",
    "Department of Electronics and Communication": "Department of ECE",
    "Department of Information Technology": "Department of IT",
    "Department of Computer Science": "Department of CSE",
}

KML_ALWAYS_LANDMARKS = {
    "Fountain",
    "TCE Name Board",
    "Saraswathi statue",
    "TCE Parking",
}

NON_LANDMARK_LOCATIONS = {
    "Tce main gate",
    "Gate 2",
    "Mens Hostel Gate 1",
    "Mens Hostel Gate 2",
}

ROUTING_ONLY_LANDMARKS = {"TCE Parking"}
ROUTE_COLORS = ["#16a34a", "#f97316", "#dc2626"]
COMPONENT_LINK_THRESHOLD = 35
LANDMARK_SEARCH_RADIUS = 18
FINAL_LEG_LANDMARK_RADIUS = 14
LANDMARK_FALLBACK_RADIUS = 30
LOCATION_ACCESS_MAX_DISTANCE = 90
MAX_ROUTE_RATIO = 1.35
MAX_ROUTE_OVERLAP = 0.88
START_NEIGHBORHOOD_AVOID_RADIUS = 35
MANUAL_SHORTCUTS = [
    ((9.8841222, 78.0807678), (9.8837742, 78.0807706)),
]
MAJOR_LABELS = {
    "Library",
    "Main Building",
    "Department of IT",
    "Department of CSE",
    "Department of ECE",
    "Architecture Dept",
    "TCE South Entrance",
    "Fountain",
    "TCE Name Board",
}
MEDIUM_LABEL_KEYWORDS = ("Department", "Auditorium", "Hostel", "Gate", "Library", "Building", "Canteen")
FRIENDLY_REFERENCE_POINTS = {
    "Library",
    "Main Building",
    "Fountain",
    "TCE Name Board",
    "Saraswathi statue",
    "TCE Parking",
    "Open Air Auditorium",
    "Placement Building",
    "Trotters Ground",
}
INTERNAL_PLACE_KEYWORDS = (
    "hall",
    "block",
    "classroom",
    "lab",
    "laboratory",
    "department",
    "auditorium",
)


def distance(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    avg_lat = math.radians((lat1 + lat2) / 2)
    meters_per_lat = 111320
    meters_per_lon = 111320 * math.cos(avg_lat)
    dx = (lon2 - lon1) * meters_per_lon
    dy = (lat2 - lat1) * meters_per_lat
    return math.hypot(dx, dy)


def path_distance(path):
    return sum(distance(path[i], path[i + 1]) for i in range(len(path) - 1))


def normalize_name(name):
    cleaned = " ".join((name or "").split())
    return KML_NAME_ALIASES.get(cleaned, cleaned)


def display_name(name):
    return DISPLAY_NAME_OVERRIDES.get(name, name)


def ordinal_name(index):
    return {1: "first", 2: "second", 3: "third", 4: "fourth"}.get(index, f"{index}th")


@lru_cache(maxsize=4)
def parse_kml_cached(file_path, mtime):
    if not os.path.exists(file_path):
        return {}, []

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        ns = {"kml": "http://www.opengis.net/kml/2.2"}
        landmarks = {}
        lines = []

        for placemark in root.findall(".//kml:Placemark", ns):
            raw_name = placemark.findtext("kml:name", default="", namespaces=ns)
            name = normalize_name(raw_name)
            description = placemark.findtext("kml:description", default="", namespaces=ns).strip().lower()

            point = placemark.find(".//kml:Point", ns)
            if point is not None:
                coords_node = placemark.find(".//kml:coordinates", ns)
                if coords_node is None:
                    continue
                lon, lat, *_ = coords_node.text.strip().split(",")
                landmarks[name] = {
                    "coords": (float(lat), float(lon)),
                    "description": description,
                    "raw_name": raw_name,
                }
                continue

            line = placemark.find(".//kml:LineString", ns)
            if line is not None:
                coords_node = placemark.find(".//kml:coordinates", ns)
                if coords_node is None:
                    continue
                coords = []
                for item in coords_node.text.strip().split():
                    lon, lat, *_ = item.split(",")
                    coords.append((float(lat), float(lon)))
                if len(coords) >= 2:
                    lines.append({"type": "kml_path", "coords": coords, "name": raw_name or "KML Path"})

        return landmarks, lines
    except Exception:
        return {}, []


def parse_kml(file_path):
    if not os.path.exists(file_path):
        return {}, []
    return parse_kml_cached(file_path, os.path.getmtime(file_path))


def label_tier(name, meta):
    shown_name = display_name(name)
    if shown_name in MAJOR_LABELS or meta.get("is_landmark"):
        return 0
    if any(word in shown_name for word in MEDIUM_LABEL_KEYWORDS):
        return 1
    return 2


@lru_cache(maxsize=4)
def parse_osm_cached(file_path, osm_mtime, kml_mtime):
    if not os.path.exists(file_path):
        return {}, [], {}, []

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}

        points = {}
        metadata = {}
        ways_data = []

        for node in root.findall("node"):
            name_tag = node.find("tag[@k='name']")
            if name_tag is None:
                continue
            name = normalize_name(name_tag.get("v"))
            if any(b in name.lower() for b in BLACKLIST):
                continue
            points[name] = nodes[node.get("id")]
            is_landmark = name not in NON_LANDMARK_LOCATIONS
            metadata[name] = {
                "is_landmark": is_landmark,
                "routing_landmark": is_landmark,
                "routing_only": False,
                "type": "node",
                "display_name": display_name(name),
            }

        for way in root.findall("way"):
            tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
            way_nodes = [nd.get("ref") for nd in way.findall("nd")]

            if "highway" in tags:
                coords = [nodes[ref] for ref in way_nodes if ref in nodes]
                if coords:
                    ways_data.append({"type": "highway", "coords": coords, "name": tags.get("name", "Path")})

                if "Architecture Dept" in tags.get("name", "") and "Architecture Dept" not in points and coords:
                    points["Architecture Dept"] = coords[-1]
                    metadata["Architecture Dept"] = {
                        "is_landmark": False,
                        "routing_landmark": False,
                        "routing_only": False,
                        "type": "way",
                        "display_name": "Architecture Dept",
                    }

            if "name" not in tags:
                continue

            name = normalize_name(tags["name"])
            if any(b in name.lower() for b in BLACKLIST):
                continue

            way_coords = [nodes[ref] for ref in way_nodes if ref in nodes]
            if not way_coords:
                continue

            avg_lat = sum(c[0] for c in way_coords) / len(way_coords)
            avg_lon = sum(c[1] for c in way_coords) / len(way_coords)
            points[name] = (avg_lat, avg_lon)
            metadata[name] = {
                "is_landmark": (name == "Main Building") or ("building" not in tags),
                "routing_landmark": "building" not in tags or name == "Main Building",
                "routing_only": False,
                "type": "way",
                "display_name": display_name(name),
                "geometry": way_coords,
                "tags": tags,
            }

        kml_landmarks, kml_lines = parse_kml(KML_FILE)
        ways_data.extend(kml_lines)

        for name, item in kml_landmarks.items():
            coords = item["coords"]
            description = item["description"]
            is_landmark = name not in NON_LANDMARK_LOCATIONS and (
                name in KML_ALWAYS_LANDMARKS or "landmark" in description
            )

            if name in points and not is_landmark and name not in ROUTING_ONLY_LANDMARKS:
                continue

            points[name] = coords
            metadata[name] = {
                "is_landmark": is_landmark,
                "routing_landmark": is_landmark or name in ROUTING_ONLY_LANDMARKS,
                "routing_only": name in ROUTING_ONLY_LANDMARKS,
                "type": "kml",
                "display_name": display_name(name),
                "geometry": [coords],
            }

        for name, meta in metadata.items():
            meta["label_tier"] = label_tier(name, meta)

        lines = [w["coords"] for w in ways_data]
        return points, lines, metadata, ways_data
    except Exception as exc:
        print(f"OSM Parse Error: {exc}")
        return {}, [], {}, []


def parse_osm(file_path):
    if not os.path.exists(file_path):
        return {}, [], {}, []
    osm_mtime = os.path.getmtime(file_path)
    kml_mtime = os.path.getmtime(KML_FILE) if os.path.exists(KML_FILE) else 0
    return parse_osm_cached(file_path, osm_mtime, kml_mtime)


def bearing(p1, p2):
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = math.radians(p2[1] - p1[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(target, ref):
    return (target - ref + 180) % 360 - 180


def edge_preference(way):
    way_type = way.get("type")
    tags = way.get("tags", {})
    highway = tags.get("highway", "")
    name = (way.get("name") or "").lower()

    if way_type == "component_link":
        return 1.7
    if way_type == "access_link":
        return 1.35
    if "gate 1 to gate 2" in name:
        return 1.85
    if way_type == "kml_path":
        return 1.08
    if highway in {"service", "residential", "unclassified"}:
        return 1.0
    if highway in {"footway", "path", "pedestrian"}:
        return 0.96
    if "main" in name or "entrance" in name or "gate" in name:
        return 0.94
    return 1.0


def build_osm_graph(ways_data, points):
    graph = defaultdict(list)
    for way in ways_data:
        coords = way["coords"]
        factor = edge_preference(way)
        for i in range(len(coords) - 1):
            p1, p2 = coords[i], coords[i + 1]
            dist = distance(p1, p2)
            cost = dist * factor
            graph[p1].append((p2, cost, dist))
            graph[p2].append((p1, cost, dist))

    add_manual_shortcuts(graph)
    connect_close_components(graph, threshold=COMPONENT_LINK_THRESHOLD)
    return graph


def add_manual_shortcuts(graph):
    for a, b in MANUAL_SHORTCUTS:
        if a not in graph or b not in graph:
            continue
        if any(neighbor == b for neighbor, _, _ in graph[a]):
            continue
        gap = distance(a, b)
        cost = gap * 0.95
        graph[a].append((b, cost, gap))
        graph[b].append((a, cost, gap))


def graph_components(graph):
    remaining = set(graph.keys())
    components = []

    while remaining:
        start = remaining.pop()
        stack = [start]
        component = {start}

        while stack:
            node = stack.pop()
            for neighbor, _, _ in graph.get(node, []):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)

        components.append(component)

    return components


def connect_close_components(graph, threshold):
    while True:
        components = graph_components(graph)
        if len(components) <= 1:
            return

        best_link = None
        for i in range(len(components)):
            for j in range(i + 1, len(components)):
                for a in components[i]:
                    for b in components[j]:
                        gap = distance(a, b)
                        if gap > threshold:
                            continue
                        if best_link is None or gap < best_link[0]:
                            best_link = (gap, a, b)

        if best_link is None:
            return

        gap, a, b = best_link
        cost = gap * edge_preference({"type": "component_link"})
        graph[a].append((b, cost, gap))
        graph[b].append((a, cost, gap))


def nearest_graph_node(target, graph_nodes):
    best_node = None
    best_distance = float("inf")
    for candidate in graph_nodes:
        gap = distance(target, candidate)
        if gap < best_distance:
            best_node = candidate
            best_distance = gap
    return best_node, best_distance


def location_access_nodes(graph, points, metadata):
    graph_nodes = list(graph.keys())
    access_nodes = {}

    for name, pt in points.items():
        if not graph_nodes:
            access_nodes[name] = pt
            continue

        candidates = metadata.get(name, {}).get("geometry") or [pt]
        best_node = None
        best_distance = float("inf")
        for candidate in candidates:
            node, gap = nearest_graph_node(candidate, graph_nodes)
            if node is not None and gap < best_distance:
                best_node = node
                best_distance = gap

        if best_node is None:
            access_nodes[name] = pt
            continue

        if best_distance > LOCATION_ACCESS_MAX_DISTANCE:
            access_nodes[name] = pt
            cost = best_distance * edge_preference({"type": "access_link"})
            graph[pt].append((best_node, cost, best_distance))
            graph[best_node].append((pt, cost, best_distance))
            graph_nodes.append(pt)
        else:
            access_nodes[name] = best_node

    return access_nodes


def nodes_within_radius(graph, origin, radius):
    return {node for node in graph if node != origin and distance(node, origin) < radius}


def edge_key(a, b):
    return tuple(sorted((a, b)))


def dijkstra(graph, start, end, banned_nodes=None, banned_edges=None):
    banned_nodes = banned_nodes or set()
    banned_edges = banned_edges or set()
    pq = [(0, start)]
    distances = {start: 0}
    previous = {}

    while pq:
        cost, node = heapq.heappop(pq)
        if cost > distances.get(node, float("inf")):
            continue
        if node == end:
            path = [end]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            path.reverse()
            return path, cost

        for neighbor, weight, _ in graph.get(node, []):
            if neighbor in banned_nodes and neighbor != end:
                continue
            if edge_key(node, neighbor) in banned_edges:
                continue
            new_cost = cost + weight
            if new_cost < distances.get(neighbor, float("inf")):
                distances[neighbor] = new_cost
                previous[neighbor] = node
                heapq.heappush(pq, (new_cost, neighbor))

    return [], float("inf")


def canonical_path_key(path):
    return tuple(path)


def path_prefix_cost(path, upto_index):
    if upto_index <= 0:
        return 0
    return sum(distance(path[i], path[i + 1]) for i in range(upto_index))


def build_alternative_routes(graph, start, end, max_routes=3):
    first_path, first_cost = dijkstra(graph, start, end)
    if not first_path:
        return []

    best_paths = [(first_cost, first_path)]
    candidates = []
    seen_paths = {canonical_path_key(first_path)}

    for best_cost, base_path in list(best_paths):
        for spur_index in range(len(base_path) - 1):
            spur_node = base_path[spur_index]
            root_path = base_path[: spur_index + 1]

            banned_edges = set()
            for _, path in best_paths:
                if len(path) > spur_index and path[: spur_index + 1] == root_path:
                    banned_edges.add(edge_key(path[spur_index], path[spur_index + 1]))

            banned_nodes = set(root_path[:-1])
            spur_path, spur_cost = dijkstra(graph, spur_node, end, banned_nodes=banned_nodes, banned_edges=banned_edges)
            if not spur_path:
                continue

            total_path = root_path[:-1] + spur_path
            if len(total_path) != len(set(total_path)):
                continue

            path_key = canonical_path_key(total_path)
            if path_key in seen_paths:
                continue

            total_cost = path_prefix_cost(root_path, len(root_path) - 1) + spur_cost
            heapq.heappush(candidates, (total_cost, total_path))
            seen_paths.add(path_key)

        if not candidates:
            break

        next_cost, next_path = heapq.heappop(candidates)
        best_paths.append((next_cost, next_path))
        if len(best_paths) >= max_routes * 3:
            break

    candidate_paths = []
    for _, path in sorted(best_paths + candidates, key=lambda item: path_distance(item[1])):
        if all(path != existing for existing in candidate_paths):
            candidate_paths.append(path)

    return select_route_set(candidate_paths, max_routes=max_routes)


def route_edge_set(path):
    return {edge_key(path[i], path[i + 1]) for i in range(len(path) - 1)}


def route_overlap_ratio(path_a, path_b):
    edges_a = route_edge_set(path_a)
    edges_b = route_edge_set(path_b)
    if not edges_a or not edges_b:
        return 0
    shared = len(edges_a & edges_b)
    return shared / min(len(edges_a), len(edges_b))


def select_route_set(paths, max_routes=3):
    if not paths:
        return []

    ordered = sorted(paths, key=path_distance)
    chosen = [ordered[0]]
    shortest = path_distance(ordered[0])

    for path in ordered[1:]:
        dist = path_distance(path)
        if dist > shortest * MAX_ROUTE_RATIO:
            continue
        if any(route_overlap_ratio(path, kept) > MAX_ROUTE_OVERLAP for kept in chosen):
            continue
        chosen.append(path)
        if len(chosen) >= max_routes:
            break

    if len(chosen) == 1 and len(ordered) > 1:
        fallback = next((path for path in ordered[1:] if path_distance(path) <= shortest * (MAX_ROUTE_RATIO + 0.1)), None)
        if fallback:
            chosen.append(fallback)

    return chosen


def build_seeded_start_routes(graph, start_point, end_point, max_routes=3):
    blocked_near_start = nodes_within_radius(graph, start_point, START_NEIGHBORHOOD_AVOID_RADIUS)
    candidates = []

    for neighbor, edge_cost, edge_dist in graph.get(start_point, []):
        if edge_dist < 5:
            continue

        banned_nodes = set(blocked_near_start)
        banned_nodes.add(start_point)
        banned_nodes.discard(neighbor)

        path, rest_cost = dijkstra(graph, neighbor, end_point, banned_nodes=banned_nodes)
        if not path:
            continue

        full_path = [start_point] + path
        if len(full_path) != len(set(full_path)):
            continue

        candidates.append((edge_cost + rest_cost, full_path))

    candidate_paths = []
    for _, path in sorted(candidates, key=lambda item: path_distance(item[1])):
        if all(path != existing for existing in candidate_paths):
            candidate_paths.append(path)

    return select_route_set(candidate_paths, max_routes=max_routes)


def get_poi_near(pt, points, metadata, tol=5, include_routing_only=False):
    closest_name = None
    closest_dist = float("inf")

    for name, coords in points.items():
        meta = metadata.get(name, {})
        if meta.get("routing_only") and not include_routing_only:
            continue
        dist = distance(pt, coords)
        if dist < tol and dist < closest_dist:
            closest_name = name
            closest_dist = dist

    return closest_name


def is_instruction_landmark(name, metadata, destination_name=None):
    meta = metadata.get(name, {})
    display = meta.get("display_name", name)
    if not (meta.get("routing_landmark") or display in FRIENDLY_REFERENCE_POINTS):
        return False
    lowered = name.lower()
    if destination_name and name == destination_name:
        return False
    if any(word in lowered for word in INTERNAL_PLACE_KEYWORDS) and not meta.get("is_landmark"):
        return False
    return True


def point_segment_distance(point, seg_start, seg_end):
    lat_scale = 111320
    lon_scale = 111320 * math.cos(math.radians((seg_start[0] + seg_end[0]) / 2 or seg_start[0]))

    ax, ay = seg_start[1] * lon_scale, seg_start[0] * lat_scale
    bx, by = seg_end[1] * lon_scale, seg_end[0] * lat_scale
    px, py = point[1] * lon_scale, point[0] * lat_scale

    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.hypot(px - ax, py - ay), 0

    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / denom))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y), t


def landmark_before_turn(path, turn_index, points, metadata, used_landmarks, destination_name=None):
    best_name = None
    best_dist = float("inf")
    best_side = "left"

    for idx in range(max(0, turn_index - 2), turn_index):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < LANDMARK_SEARCH_RADIUS and t >= 0.15 and dist < best_dist:
                best_name = name
                best_dist = dist
                best_side = get_side_of_path(p1, p2, coords)

    return best_name, best_side


def fallback_landmark_near_segment(path, seg_start, seg_end, points, metadata, used_landmarks, destination_name=None):
    best_name = None
    best_dist = float("inf")
    best_side = "left"

    for idx in range(seg_start, max(seg_start + 1, seg_end)):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < LANDMARK_FALLBACK_RADIUS and 0.05 <= t <= 0.95 and dist < best_dist:
                best_name = name
                best_dist = dist
                best_side = get_side_of_path(p1, p2, coords)

    return best_name, best_side


def landmark_on_final_leg(path, start_index, points, metadata, used_landmarks, destination_name=None):
    best_name = None
    best_dist = float("inf")
    best_side = "left"

    for idx in range(start_index, len(path) - 1):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < FINAL_LEG_LANDMARK_RADIUS and 0.1 <= t <= 0.9 and dist < best_dist:
                best_name = name
                best_dist = dist
                best_side = get_side_of_path(p1, p2, coords)

    return best_name, best_side


def turn_phrase(path, turn_index, graph):
    if turn_index <= 0 or turn_index >= len(path) - 1:
        return None

    incoming = bearing(path[turn_index - 1], path[turn_index])
    outgoing = bearing(path[turn_index], path[turn_index + 1])
    diff = angle_diff(outgoing, incoming)

    if abs(diff) < 30:
        return None

    exits = []
    for neighbor, _, _ in graph.get(path[turn_index], []):
        if neighbor == path[turn_index - 1]:
            continue
        exit_angle = angle_diff(bearing(path[turn_index], neighbor), incoming)
        if abs(exit_angle) >= 20:
            exits.append(exit_angle)

    if diff > 0:
        side_exits = sorted(angle for angle in exits if angle > 20)
        direction = "right"
    else:
        side_exits = sorted((angle for angle in exits if angle < -20), reverse=True)
        direction = "left"

    if len(side_exits) > 1:
        rank = min(range(len(side_exits)), key=lambda i: abs(side_exits[i] - diff)) + 1
        return f"take the {ordinal_name(rank)} {direction}"

    if abs(diff) > 135:
        return f"make a sharp {direction}"

    return f"turn {direction}"


def get_side_of_path(p1, p2, landmark):
    if distance(p1, p2) < 0.1 or distance(p1, landmark) < 0.1:
        return "left"
    b_path = bearing(p1, p2)
    b_landmark = bearing(p1, landmark)
    diff = angle_diff(b_landmark, b_path)
    return "left" if diff < 0 else "right"


def soften_action(action):
    if "sharp left" in action:
        return "keep following the path as it bends left"
    if "sharp right" in action:
        return "keep following the path as it bends right"
    if "take the" in action:
        return action
    if "left" in action:
        return "keep following the path as it bends left"
    if "right" in action:
        return "keep following the path as it bends right"
    return action


def segment_instruction(action, segment_length):
    if segment_length <= 55:
        return f"{soften_action(action).capitalize()}."
    if segment_length <= 120:
        return f"Walk straight for a while, then {action}."
    if segment_length <= 220:
        return f"Continue along the path, then {action}."
    return f"Continue for about {segment_length} meters, then {action}."


def narrate_route(path, points, metadata, graph, start_name=None, end_name=None):
    if len(path) < 2:
        return ["You are already at your destination."]

    start_poi = start_name or get_poi_near(path[0], points, metadata, tol=12)
    end_poi = end_name or get_poi_near(path[-1], points, metadata, tol=12, include_routing_only=True)
    used_landmarks = {name for name in (start_poi, end_poi) if name}

    turn_indices = [idx for idx in range(1, len(path) - 1) if turn_phrase(path, idx, graph)]
    instructions = [f"Start from {display_name(start_poi) if start_poi else 'your location'} and head straight."]

    segment_start = 0
    turn_data = []
    for turn_index in turn_indices:
        action = turn_phrase(path, turn_index, graph)
        landmark_name, side = landmark_before_turn(
            path,
            turn_index,
            points,
            metadata,
            used_landmarks,
            destination_name=end_poi,
        )
        if not landmark_name:
            landmark_name, side = fallback_landmark_near_segment(
                path,
                segment_start,
                turn_index,
                points,
                metadata,
                used_landmarks,
                destination_name=end_poi,
            )

        turn_data.append({
            "index": turn_index,
            "action": action,
            "landmark_name": landmark_name,
            "side": side,
            "segment_start": segment_start,
            "segment_length": round(path_distance(path[segment_start: turn_index + 1])),
        })
        segment_start = turn_index

    segment_start = 0
    for pos, item in enumerate(turn_data):
        turn_index = item["index"]
        action = item["action"]
        landmark_name = item["landmark_name"]
        side = item["side"]
        segment_length = item["segment_length"]

        if landmark_name in used_landmarks:
            landmark_name = None

        next_item = turn_data[pos + 1] if pos + 1 < len(turn_data) else None
        if (
            not landmark_name
            and next_item
            and next_item["landmark_name"]
            and segment_length <= 90
        ):
            segment_start = turn_index
            continue

        if landmark_name:
            used_landmarks.add(landmark_name)
            instructions.append(
                f"Walk straight until you see {display_name(landmark_name)} on your {side}, then {action}."
            )
        else:
            instructions.append(segment_instruction(action, segment_length))

        segment_start = turn_index

    final_landmark, final_side = landmark_on_final_leg(
        path,
        segment_start,
        points,
        metadata,
        used_landmarks,
        destination_name=end_poi,
    )
    if not final_landmark:
        final_landmark, final_side = fallback_landmark_near_segment(
            path,
            segment_start,
            len(path) - 1,
            points,
            metadata,
            used_landmarks,
            destination_name=end_poi,
        )
    if end_poi:
        end_meta = metadata.get(end_poi, {})
        is_precise_side_destination = end_meta.get("is_landmark") or end_meta.get("type") == "node"
        if final_landmark and final_landmark != end_poi:
            instructions.append(
                f"Keep going past {display_name(final_landmark)} on your {final_side} to reach {display_name(end_poi)}."
            )
        elif is_precise_side_destination and end_poi in points:
            destination_side = get_side_of_path(path[-2], path[-1], points[end_poi])
            instructions.append(f"Continue straight and you will find {display_name(end_poi)} on your {destination_side}.")
        else:
            instructions.append(f"Continue straight to reach {display_name(end_poi)}.")
    elif final_landmark:
        instructions.append(f"Keep going past {display_name(final_landmark)} on your {final_side} to reach the destination.")

    cleaned = []
    for text in instructions:
        if not cleaned or cleaned[-1] != text:
            cleaned.append(text[0].upper() + text[1:] if text else text)
    cleaned.append("Reached your destination.")
    return cleaned


def serialize_route(path, points, metadata, graph, route_index, start_name, end_name):
    display_path = list(path)
    start_point = points.get(start_name)
    end_point = points.get(end_name)

    if start_point and display_path and distance(start_point, display_path[0]) > 0.5:
        display_path = [start_point] + display_path
    if end_point and display_path and distance(end_point, display_path[-1]) > 0.5:
        display_path = display_path + [end_point]

    return {
        "id": route_index,
        "name": f"Route {route_index + 1}",
        "color": ROUTE_COLORS[route_index % len(ROUTE_COLORS)],
        "path": [[lat, lon] for lat, lon in display_path],
        "start_marker": list(start_point) if start_point else list(display_path[0]),
        "end_marker": list(end_point) if end_point else list(display_path[-1]),
        "total_dist": round(path_distance(display_path), 1),
        "directions": narrate_route(display_path, points, metadata, graph, start_name=start_name, end_name=end_name),
    }


@app.route("/")
def home():
    points, _, metadata, _ = parse_osm(OSM_FILE)
    destinations = sorted(
        [
            metadata[name].get("display_name", name)
            for name in points
            if not metadata.get(name, {}).get("is_landmark") and not metadata.get(name, {}).get("routing_only")
        ]
    )
    return render_template("index.html", locations=destinations)


@app.route("/map_data")
def map_data():
    points, lines, metadata, _ = parse_osm(OSM_FILE)
    display_points = {metadata[name].get("display_name", name): coords for name, coords in points.items()}
    display_meta = {
        metadata[name].get("display_name", name): {
            **meta,
            "display_name": meta.get("display_name", name),
        }
        for name, meta in metadata.items()
    }
    return jsonify({"points": display_points, "lines": lines, "metadata": display_meta})


@app.route("/route", methods=["POST"])
def route():
    data = request.json or {}
    requested_start = normalize_name(data.get("start"))
    requested_end = normalize_name(data.get("end"))

    points, _, metadata, ways_data = parse_osm(OSM_FILE)
    display_lookup = {display_name(name).lower(): name for name in points}
    start_name = display_lookup.get(requested_start.lower(), requested_start)
    end_name = display_lookup.get(requested_end.lower(), requested_end)

    if start_name == end_name:
        return jsonify({"error": "Already at destination"}), 200
    if start_name not in points or end_name not in points:
        return jsonify({"error": "Location error"}), 400

    graph = build_osm_graph(ways_data, points)
    location_nodes = location_access_nodes(graph, points, metadata)
    start_meta = metadata.get(start_name, {})
    start_point = points[start_name]
    end_point = location_nodes[end_name]

    route_paths = []
    if start_point in graph and start_meta.get("type") in {"node", "kml"}:
        route_paths = build_seeded_start_routes(graph, start_point, end_point, max_routes=3)

    if not route_paths:
        route_paths = build_alternative_routes(graph, location_nodes[start_name], end_point, max_routes=3)

    if not route_paths:
        return jsonify({"error": "No path"}), 404

    routes = [
        serialize_route(path, points, metadata, graph, index, start_name, end_name)
        for index, path in enumerate(route_paths)
    ]
    return jsonify({"routes": routes, "start": display_name(start_name), "end": display_name(end_name)})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
