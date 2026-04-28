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
ACCESS_CANDIDATE_LIMIT = 4
ACCESS_CANDIDATE_MAX_DISTANCE = 45
MAX_ROUTE_RATIO = 1.25
MAX_ROUTE_OVERLAP = 0.70
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
MIN_TURN_SEGMENT_METERS = 15
MIN_BEND_SEGMENT_METERS = 28
MIN_INSTRUCTION_SPACING_METERS = 18


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


def nearest_graph_nodes(target, graph_nodes, limit=ACCESS_CANDIDATE_LIMIT, max_distance=ACCESS_CANDIDATE_MAX_DISTANCE):
    ranked = sorted(
        ((candidate, distance(target, candidate)) for candidate in graph_nodes),
        key=lambda item: item[1],
    )
    filtered = [(node, gap) for node, gap in ranked if gap <= max_distance]
    if filtered:
        return filtered[:limit]
    return ranked[:1]


def location_access_candidates(graph, points, metadata):
    graph_nodes = list(graph.keys())
    access_nodes = {}

    for name, pt in points.items():
        if not graph_nodes:
            access_nodes[name] = [(pt, 0.0)]
            continue

        candidates = metadata.get(name, {}).get("geometry") or [pt]
        ranked_nodes = []
        for candidate in candidates:
            ranked_nodes.extend(nearest_graph_nodes(candidate, graph_nodes))

        deduped = {}
        for node, gap in ranked_nodes:
            if node not in deduped or gap < deduped[node]:
                deduped[node] = gap

        ordered = sorted(deduped.items(), key=lambda item: item[1])
        if ordered:
            access_nodes[name] = ordered[:ACCESS_CANDIDATE_LIMIT]
            continue

        best_node, best_distance = nearest_graph_node(pt, graph_nodes)
        if best_node is None:
            access_nodes[name] = [(pt, 0.0)]
            continue

        if best_distance > LOCATION_ACCESS_MAX_DISTANCE:
            cost = best_distance * edge_preference({"type": "access_link"})
            graph[pt].append((best_node, cost, best_distance))
            graph[best_node].append((pt, cost, best_distance))
            graph_nodes.append(pt)
            access_nodes[name] = [(pt, 0.0)]
        else:
            access_nodes[name] = [(best_node, best_distance)]

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


def path_has_hairpin(path):
    for idx in range(1, len(path) - 1):
        incoming = bearing(path[idx - 1], path[idx])
        outgoing = bearing(path[idx], path[idx + 1])
        if abs(angle_diff(outgoing, incoming)) >= 165:
            return True
    return False


def build_candidate_routes(graph, start_candidates, end_candidates, max_routes=3):
    if not start_candidates or not end_candidates:
        return []

    # Identify the single best entry/exit pair first
    best_pair = None
    best_initial_cost = float("inf")

    for start_node, start_gap in start_candidates:
        for end_node, end_gap in end_candidates:
            path, route_cost = dijkstra(graph, start_node, end_node)
            if not path or len(path) < 2:
                continue
            if path_has_hairpin(path):
                continue

            access_penalty = (start_gap * 0.35) + (end_gap * 0.55)
            total_initial_cost = route_cost + access_penalty
            if total_initial_cost < best_initial_cost:
                best_initial_cost = total_initial_cost
                best_pair = (start_node, end_node)

    if not best_pair:
        return []

    # Generate alternatives strictly between these two nodes
    start_node, end_node = best_pair
    alternatives = build_alternative_routes(graph, start_node, end_node, max_routes=max_routes * 2)

    all_paths = []
    for path in alternatives:
        if not path_has_hairpin(path) and all(path != existing for existing in all_paths):
            all_paths.append(path)

    return select_route_set(all_paths, max_routes=max_routes)


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
    best_info = None
    best_dist = float("inf")

    # Look back up to 3 segments for a landmark
    for idx in range(max(0, turn_index - 3), turn_index):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < LANDMARK_SEARCH_RADIUS and 0.1 <= t <= 0.9 and dist < best_dist:
                best_dist = dist
                best_info = {
                    "name": name,
                    "side": get_side_of_path(p1, p2, coords),
                    "index": idx,
                    "t": t
                }

    return best_info


def fallback_landmark_near_segment(path, start_index, end_index, points, metadata, used_landmarks, destination_name=None):
    best_info = None
    best_dist = float("inf")

    for idx in range(start_index, max(start_index + 1, end_index)):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < LANDMARK_FALLBACK_RADIUS and 0.05 <= t <= 0.95 and dist < best_dist:
                best_dist = dist
                best_info = {
                    "name": name,
                    "side": get_side_of_path(p1, p2, coords),
                    "index": idx,
                    "t": t
                }

    return best_info


def landmark_on_final_leg(path, start_index, points, metadata, used_landmarks, destination_name=None):
    best_info = None
    best_dist = float("inf")

    for idx in range(start_index, len(path) - 1):
        p1 = path[idx]
        p2 = path[idx + 1]
        for name, coords in points.items():
            if name in used_landmarks or not is_instruction_landmark(name, metadata, destination_name):
                continue
            dist, t = point_segment_distance(coords, p1, p2)
            if dist < FINAL_LEG_LANDMARK_RADIUS and 0.1 <= t <= 0.9 and dist < best_dist:
                best_dist = dist
                best_info = {
                    "name": name,
                    "side": get_side_of_path(p1, p2, coords),
                    "index": idx,
                    "t": t
                }

    return best_info


def describe_maneuver(path, turn_index, graph):
    if turn_index <= 0 or turn_index >= len(path) - 1:
        return None

    incoming = bearing(path[turn_index - 1], path[turn_index])
    outgoing = bearing(path[turn_index], path[turn_index + 1])
    diff = angle_diff(outgoing, incoming)
    magnitude = abs(diff)

    # Ignore very subtle shifts
    if magnitude < 18:
        return None

    exits = []
    for neighbor, _, _ in graph.get(path[turn_index], []):
        if neighbor == path[turn_index - 1]:
            continue
        exit_angle = angle_diff(bearing(path[turn_index], neighbor), incoming)
        # Identify valid branching paths
        if abs(exit_angle) >= 20:
            exits.append(exit_angle)

    direction = "right" if diff > 0 else "left"
    if diff > 0:
        side_exits = sorted(angle for angle in exits if angle > 20)
    else:
        side_exits = sorted((angle for angle in exits if angle < -20), reverse=True)

    ordinal = None
    if len(side_exits) > 1:
        # Numbering only where necessary (more than 2 options or specific areas)
        # We use a threshold of 3 or more exits to use ordinals globally,
        # or if we are near "Open Air Auditorium" (which we can check via graph node coords)
        ordinal = min(range(len(side_exits)), key=lambda i: abs(side_exits[i] - diff)) + 1
        
        # Simple heuristic: only use ordinal if there are 3+ options or if it's the second/third option
        if len(side_exits) < 3 and ordinal == 1:
            ordinal = None

    maneuver_type = "bend" if magnitude < 65 else "turn"
    if magnitude >= 145:
        action = f"make a sharp {direction}"
        maneuver_type = "sharp_turn"
    elif ordinal and maneuver_type == "turn":
        action = f"take the {ordinal_name(ordinal)} {direction}"
    elif maneuver_type == "bend":
        action = f"bear {direction}"
    else:
        action = f"turn {direction}"

    return {
        "index": turn_index,
        "diff": diff,
        "magnitude": magnitude,
        "direction": direction,
        "ordinal": ordinal,
        "type": maneuver_type,
        "action": action,
    }


def get_side_of_path(p1, p2, landmark):
    if distance(p1, p2) < 0.1 or distance(p1, landmark) < 0.1:
        return "left"
    b_path = bearing(p1, p2)
    b_landmark = bearing(p1, landmark)
    diff = angle_diff(b_landmark, b_path)
    return "left" if diff < 0 else "right"


def final_leg_instruction(end_name, final_landmark=None, final_side="left", destination_side=None):
    destination_label = display_name(end_name)
    if final_landmark and final_landmark != end_name:
        return f"Pass {display_name(final_landmark)} on your {final_side}; your destination, {destination_label}, will be just ahead."
    if destination_side:
        return f"Keep going straight; {destination_label} will be on your {destination_side}."
    return f"Continue straight to reach {destination_label}."


def collect_maneuvers(path, graph):
    maneuvers = []
    previous_maneuver_index = 0
    for idx in range(1, len(path) - 1):
        maneuver = describe_maneuver(path, idx, graph)
        if not maneuver:
            continue
        segment_dist = path_distance(path[previous_maneuver_index : idx + 1])
        if segment_dist < 12 and maneuvers:
            if maneuver["magnitude"] > maneuvers[-1]["magnitude"]:
                prev_start = maneuvers[-1]["segment_start_idx"]
                maneuvers[-1] = maneuver
                maneuvers[-1]["segment_start_idx"] = prev_start
            continue
        maneuver["segment_start_idx"] = previous_maneuver_index
        maneuvers.append(maneuver)
        previous_maneuver_index = idx
    return maneuvers, previous_maneuver_index


def get_library_oaa_override(path, points, graph):
    library = points.get("Library")
    name_board = points.get("TCE Name Board")
    auditorium = points.get("Open Air Auditorium")
    if not library or not name_board or not auditorium:
        return None

    maneuvers, _ = collect_maneuvers(path, graph)
    for maneuver_idx, library_turn in enumerate(maneuvers):
        if library_turn["direction"] != "left":
            continue

        name_board_turn = next(
            (
                maneuver
                for maneuver in maneuvers[maneuver_idx + 1 :]
                if maneuver["direction"] == "right" and distance(path[maneuver["index"]], name_board) < 30
            ),
            None,
        )
        if not name_board_turn:
            continue

        library_idx = min(
            range(library_turn["index"], name_board_turn["index"] + 1),
            key=lambda idx: distance(path[idx], library),
        )
        if distance(path[library_idx], library) >= 30:
            continue

        auditorium_idx = min(
            range(library_idx, name_board_turn["index"] + 1),
            key=lambda idx: distance(path[idx], auditorium),
        )
        if distance(path[auditorium_idx], auditorium) >= 40:
            continue

        return {
            "turn_left_index": library_turn["index"],
            "library_index": library_idx,
            "auditorium_index": auditorium_idx,
            "name_board_turn_index": name_board_turn["index"],
            "library_distance": round(path_distance(path[library_turn["index"] : library_idx + 1])),
            "name_board_distance": round(path_distance(path[library_idx : name_board_turn["index"] + 1])),
        }

    return None


def build_display_path(path, override=None):
    if not override:
        return list(path)

    simplified = list(path[: override["turn_left_index"] + 1])
    pivot_indices = [
        override["library_index"],
        override["auditorium_index"],
        override["name_board_turn_index"],
    ]

    for idx in pivot_indices:
        point = path[idx]
        if point != simplified[-1]:
            simplified.append(point)

    for point in path[override["name_board_turn_index"] + 1 :]:
        if point != simplified[-1]:
            simplified.append(point)

    return simplified


def narrate_route(path, points, metadata, graph, start_name=None, end_name=None, route_index=None):
    if not path or len(path) < 2:
        return ["You are already at your destination."]

    start_poi = start_name or get_poi_near(path[0], points, metadata, tol=12)
    end_poi = end_name or get_poi_near(path[-1], points, metadata, tol=12, include_routing_only=True)
    used_landmarks = {name for name in (start_poi, end_poi) if name}

    override = get_library_oaa_override(path, points, graph)
    if override:
        start_label = display_name(start_poi) if start_poi else "your location"
        initial_distance = round(path_distance(path[: override["turn_left_index"] + 1]))
        instructions = [f"Head out from {start_label}"]
        if initial_distance > 3:
            instructions.append(f"Walk straight for {initial_distance} meters")
        instructions.append("Turn left")
        instructions.append(
            f"Walk straight for {override['library_distance']} meters and you will see the Library on your right"
        )
        instructions.append(f"Continue straight for {override['name_board_distance']} meters")
        instructions.append("Take the right near the TCE Name Board")
        instructions.append("Continue straight")

        remaining = narrate_route_segment(
            path[override["name_board_turn_index"] :],
            points,
            metadata,
            graph,
            start_name=None,
            end_name=end_poi,
            used_landmarks=used_landmarks,
        )
        if remaining and "head out" in remaining[0].lower():
            remaining.pop(0)

        instructions.extend(remaining)
        return normalize_instructions(instructions)

    # Standard Narration
    instructions = narrate_route_segment(path, points, metadata, graph, start_name=start_poi, end_name=end_poi, used_landmarks=used_landmarks)
    return normalize_instructions(instructions)


def narrate_route_segment(path, points, metadata, graph, start_name=None, end_name=None, used_landmarks=None):
    if len(path) < 2: return []
    if used_landmarks is None: used_landmarks = set()
    
    start_poi = start_name
    end_poi = end_name
    
    maneuvers, previous_maneuver_index = collect_maneuvers(path, graph)

    start_label = display_name(start_poi) if start_poi else "your location"
    instructions = [f"Head out from {start_label}"]

    for maneuver in maneuvers:
        turn_index = maneuver["index"]
        leg_start_idx = maneuver["segment_start_idx"]
        l_info = landmark_before_turn(path, turn_index, points, metadata, used_landmarks, destination_name=end_poi)
        if not l_info:
            l_info = fallback_landmark_near_segment(path, leg_start_idx, turn_index, points, metadata, used_landmarks, destination_name=end_poi)

        if l_info:
            used_landmarks.add(l_info["name"])
            dist_to_landmark = round(path_distance(path[leg_start_idx : l_info["index"] + 1]))
            dist_from_landmark = round(path_distance(path[l_info["index"] : turn_index + 1]))
            if dist_to_landmark > 3:
                instructions.append(f"Walk straight for {dist_to_landmark} meters")
            instructions.append(f"You will see {display_name(l_info['name'])} on your {l_info['side']}")
            if dist_from_landmark > 5:
                instructions.append(f"Continue straight for {dist_from_landmark} meters")
            instructions.append(f"{maneuver['action']}")
        else:
            dist_to_turn = round(path_distance(path[leg_start_idx : turn_index + 1]))
            if dist_to_turn > 3:
                instructions.append(f"Walk straight for {dist_to_turn} meters")
            instructions.append(f"{maneuver['action']}")

    # Final Leg
    dist_to_end = round(path_distance(path[previous_maneuver_index:]))
    if dist_to_end > 3:
        instructions.append(f"Continue straight for {dist_to_end} meters")

    if end_poi:
        instructions.append(f"You will reach your destination, {display_name(end_poi)}")
    
    return instructions


def normalize_instructions(instructions):
    cleaned = []
    for text in instructions:
        if not text: continue
        # Capitalize first letter
        normalized = text[0].upper() + text[1:]
        if not normalized.endswith("."):
            normalized += "."
        if not cleaned or cleaned[-1] != normalized:
            cleaned.append(normalized)
    if not cleaned or "reached your destination" not in cleaned[-1].lower():
        cleaned.append("You have reached your destination.")
    return cleaned


def serialize_route(path, points, metadata, graph, route_index, start_name, end_name, start_anchor=None, end_anchor=None):
    override = get_library_oaa_override(path, points, graph)
    display_path = build_display_path(path, override=override)
    # Snap markers to the actual path nodes (on the road) instead of the POI anchors
    start_marker = list(display_path[0])
    end_marker = list(display_path[-1])

    return {
        "id": route_index,
        "name": f"Route {route_index + 1}",
        "color": ROUTE_COLORS[route_index % len(ROUTE_COLORS)],
        "path": [[lat, lon] for lat, lon in display_path],
        "start_marker": start_marker,
        "end_marker": end_marker,
        "total_dist": round(path_distance(path), 1),
        "directions": narrate_route(
            path,
            points,
            metadata,
            graph,
            start_name=start_name,
            end_name=end_name,
            route_index=route_index,
        ),
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
    raw_start = data.get("start", "")
    raw_end = data.get("end", "")

    points, _, metadata, ways_data = parse_osm(OSM_FILE)
    display_lookup = {display_name(name).lower(): name for name in points}

    # Handle Source as Coordinates
    if "," in raw_start and any(c.isdigit() for c in raw_start):
        try:
            lat_s, lon_s = raw_start.split(",")
            start_coords = (float(lat_s), float(lon_s))
            start_name = "Your Location"
            points[start_name] = start_coords
            metadata[start_name] = {
                "is_landmark": False,
                "routing_landmark": False,
                "routing_only": False,
                "type": "custom",
                "display_name": "Your Location",
                "label_tier": 0
            }
        except (ValueError, TypeError):
            start_name = display_lookup.get(normalize_name(raw_start).lower(), normalize_name(raw_start))
    else:
        start_name = display_lookup.get(normalize_name(raw_start).lower(), normalize_name(raw_start))

    # Destination handling (remains name-based)
    end_name = display_lookup.get(normalize_name(raw_end).lower(), normalize_name(raw_end))

    if start_name == end_name:
        return jsonify({"error": "Already at destination"}), 200
    if start_name not in points or end_name not in points:
        return jsonify({"error": "Location error"}), 400

    graph = build_osm_graph(ways_data, points)
    access_candidates = location_access_candidates(graph, points, metadata)
    start_candidates = access_candidates.get(start_name, [])
    end_candidates = access_candidates.get(end_name, [])

    route_paths = build_candidate_routes(graph, start_candidates, end_candidates, max_routes=3)

    if not route_paths:
        return jsonify({"error": "No path"}), 404

    start_anchor = points[start_name]
    end_anchor = points[end_name]
    routes = [
        serialize_route(
            path,
            points,
            metadata,
            graph,
            index,
            start_name,
            end_name,
            start_anchor=start_anchor,
            end_anchor=end_anchor,
        )
        for index, path in enumerate(route_paths)
    ]
    return jsonify({"routes": routes, "start": display_name(start_name), "end": display_name(end_name)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
