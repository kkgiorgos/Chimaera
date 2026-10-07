#!/usr/bin/env python3
"""Export URDF visuals and kinematics for a self-contained offline replay.

Only the Python standard library is required. Collada scene transforms, units,
and diffuse materials are retained; textures are represented by their diffuse
fallback color. Mesh vertices are in visual-local metres, while URDF visual
origins and joint origins are exported separately. Optional vertex clustering
is for display only and is reported in ``notes``; it never changes simulation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET


IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
DEFAULT_COLOR = [0.75, 0.78, 0.82, 1.0]


def _numbers(value):
    return [float(x) for x in value.split()]


def _origin(element):
    return {
        "xyz": _numbers(element.get("xyz", "0 0 0")) if element is not None else [0., 0., 0.],
        "rpy": _numbers(element.get("rpy", "0 0 0")) if element is not None else [0., 0., 0.],
    }


def _multiply(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
            for r in range(4) for c in range(4)]


def _point(matrix, vertex):
    return [sum(matrix[r * 4 + k] * vertex[k] for k in range(3)) + matrix[r * 4 + 3]
            for r in range(3)]


def _transform(element):
    kind = element.tag
    values = _numbers(element.text or "")
    matrix = IDENTITY.copy()
    if kind == "matrix":
        if len(values) != 16:
            raise ValueError("Collada matrix must have sixteen values")
        # Collada XML matrices use row-major text order (translation at 3, 7, 11).
        return values
    if kind == "translate":
        matrix[3], matrix[7], matrix[11] = values
    elif kind == "scale":
        matrix[0], matrix[5], matrix[10] = values
    elif kind == "rotate":
        x, y, z, degrees = values
        length = math.sqrt(x*x + y*y + z*z)
        if length == 0:
            raise ValueError("Collada rotation axis has zero length")
        x, y, z = x/length, y/length, z/length
        c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
        t = 1-c
        matrix = [t*x*x+c, t*x*y-s*z, t*x*z+s*y, 0.,
                  t*x*y+s*z, t*y*y+c, t*y*z-s*x, 0.,
                  t*x*z-s*y, t*y*z+s*x, t*z*z+c, 0., 0., 0., 0., 1.]
    else:
        raise ValueError(f"Unsupported Collada transform: {kind}")
    return matrix


def _compact(vertices, triangles):
    remap, compact_vertices, compact_triangles = {}, [], []
    for triangle in triangles:
        indices = []
        for old in triangle:
            if old not in remap:
                remap[old] = len(compact_vertices)
                compact_vertices.append([round(v, 7) for v in vertices[old]])
            indices.append(remap[old])
        compact_triangles.append(indices)
    return compact_vertices, compact_triangles


def _dae_parts(path):
    root = ET.parse(path).getroot()
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    effects = {}
    for effect in root.findall("./library_effects/effect"):
        color = effect.find(".//diffuse/color")
        effects[effect.get("id")] = _numbers(color.text) if color is not None else DEFAULT_COLOR
    materials = {}
    for material in root.findall("./library_materials/material"):
        effect = material.find("instance_effect")
        materials[material.get("id")] = effects.get(effect.get("url", "").lstrip("#"), DEFAULT_COLOR)
    geometries = {}
    for geometry in root.findall("./library_geometries/geometry"):
        mesh = geometry.find("mesh")
        if mesh is None:
            raise ValueError(f"Non-mesh Collada geometry in {path}")
        sources = {}
        for source in mesh.findall("source"):
            array = source.find("float_array")
            accessor = source.find("./technique_common/accessor")
            if array is not None and accessor is not None:
                values = _numbers(array.text or "")
                stride, offset = int(accessor.get("stride", "1")), int(accessor.get("offset", "0"))
                count = int(accessor.get("count", "0"))
                sources[source.get("id")] = [values[offset+i*stride:offset+i*stride+3]
                                              for i in range(count)]
        vertex_sources = {}
        for vertices in mesh.findall("vertices"):
            position = vertices.find("input[@semantic='POSITION']")
            if position is not None:
                vertex_sources[vertices.get("id")] = position.get("source").lstrip("#")
        parts = []
        for primitive in mesh:
            if primitive.tag in ("source", "vertices", "extra"):
                continue
            if primitive.tag not in ("triangles", "polylist", "polygons"):
                raise ValueError(f"Unsupported Collada primitive {primitive.tag} in {path}")
            inputs = primitive.findall("input")
            stride = max(int(i.get("offset", "0")) for i in inputs) + 1
            position = next(i for i in inputs if i.get("semantic") in ("VERTEX", "POSITION"))
            offset = int(position.get("offset", "0"))
            source = position.get("source").lstrip("#")
            if position.get("semantic") == "VERTEX":
                source = vertex_sources[source]
            triangles = []
            for p in primitive.findall("p"):
                raw = [int(v) for v in (p.text or "").split()]
                indices = raw[offset::stride]
                if primitive.tag == "triangles":
                    counts = [3] * (len(indices)//3)
                elif primitive.tag == "polylist":
                    counts = [int(v) for v in primitive.findtext("vcount", "").split()]
                else:
                    counts = [len(indices)]
                cursor = 0
                for count in counts:
                    face = indices[cursor:cursor+count]
                    triangles.extend([[face[0], face[j], face[j+1]] for j in range(1, count-1)])
                    cursor += count
                if cursor != len(indices):
                    raise ValueError(f"Malformed Collada index count in {path}")
            vertices, triangles = _compact(sources[source], triangles)
            parts.append({"vertices": vertices, "triangles": triangles,
                          "material_symbol": primitive.get("material", "")})
        geometries[geometry.get("id")] = parts
    unit = root.find("./asset/unit")
    meters = float(unit.get("meter", "1")) if unit is not None else 1.
    # Browser replay, URDF, and Gazebo all use Z up.
    axis = root.findtext("./asset/up_axis", "Z_UP").strip()
    basis = IDENTITY.copy()
    if axis == "Y_UP":
        basis = [1., 0., 0., 0., 0., 0., -1., 0., 0., 1., 0., 0., 0., 0., 0., 1.]
    elif axis == "X_UP":
        basis = [0., 0., -1., 0., 0., 1., 0., 0., 1., 0., 0., 0., 0., 0., 0., 1.]
    elif axis != "Z_UP":
        raise ValueError(f"Unsupported Collada up axis {axis}")
    basis = _multiply([meters, 0., 0., 0., 0., meters, 0., 0., 0., 0., meters, 0., 0., 0., 0., 1.], basis)
    parts = []

    def append_geometry(instance, matrix):
        bindings = {x.get("symbol"): x.get("target", "").lstrip("#")
                    for x in instance.findall(".//instance_material")}
        for part in geometries[instance.get("url", "").lstrip("#")]:
            symbol = part["material_symbol"]
            parts.append({"vertices": [[round(x, 7) for x in _point(matrix, v)]
                                       for v in part["vertices"]],
                          "triangles": part["triangles"],
                          "color": materials.get(bindings.get(symbol, symbol), DEFAULT_COLOR)})

    def visit(node, parent_matrix):
        matrix = parent_matrix
        for element in node:
            if element.tag in ("matrix", "translate", "rotate", "scale"):
                matrix = _multiply(matrix, _transform(element))
            elif element.tag in ("lookat", "skew", "instance_node"):
                raise ValueError(f"Unsupported Collada scene element {element.tag} in {path}")
        for instance in node.findall("instance_geometry"):
            append_geometry(instance, matrix)
        for child in node.findall("node"):
            visit(child, matrix)

    scene_ref = root.find("./scene/instance_visual_scene")
    scenes = root.findall("./library_visual_scenes/visual_scene")
    scene = next((s for s in scenes if scene_ref is not None and
                  s.get("id") == scene_ref.get("url", "").lstrip("#")), scenes[0] if scenes else None)
    if scene is not None:
        for node in scene.findall("node"):
            visit(node, basis)
    else:
        for geometry_id in geometries:
            append_geometry(ET.Element("instance_geometry", {"url": "#"+geometry_id}), basis)
    return parts


def _stl_parts(path):
    data = path.read_bytes()
    triangles = []
    vertices = []
    if len(data) >= 84 and 84 + 50 * struct.unpack_from("<I", data, 80)[0] == len(data):
        for start in range(84, len(data), 50):
            face = struct.unpack_from("<9f", data, start+12)
            triangles.append([len(vertices)+i for i in range(3)])
            vertices.extend([list(face[i:i+3]) for i in (0, 3, 6)])
    else:
        for line in data.decode("ascii").splitlines():
            words = line.split()
            if words and words[0].lower() == "vertex":
                vertices.append([float(v) for v in words[1:4]])
        if len(vertices) % 3:
            raise ValueError(f"Malformed STL {path}")
        triangles = [[i, i+1, i+2] for i in range(0, len(vertices), 3)]
    return [{"vertices": vertices, "triangles": triangles, "color": DEFAULT_COLOR}]


def _primitive_parts(shape):
    vertices, triangles = [], []
    if shape.tag == "box":
        x, y, z = [v/2 for v in _numbers(shape.get("size"))]
        vertices = [[a*x, b*y, c*z] for a, b, c in
                    [(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                     (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)]]
        for a, b, c, d in [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]:
            triangles.extend([[a,b,c], [a,c,d]])
    elif shape.tag == "cylinder":
        radius, half = float(shape.get("radius")), float(shape.get("length"))/2
        count = 32
        vertices = [[radius*math.cos(i*2*math.pi/count), radius*math.sin(i*2*math.pi/count), z]
                    for z in (-half, half) for i in range(count)] + [[0.,0.,-half], [0.,0.,half]]
        for i in range(count):
            j = (i+1) % count
            triangles.extend([[i,j,j+count], [i,j+count,i+count], [2*count,j,i], [2*count+1,i+count,j+count]])
    elif shape.tag == "sphere":
        radius = float(shape.get("radius"))
        rows, columns = 12, 24
        vertices = [[radius*math.sin(i*math.pi/rows)*math.cos(j*2*math.pi/columns),
                     radius*math.sin(i*math.pi/rows)*math.sin(j*2*math.pi/columns),
                     radius*math.cos(i*math.pi/rows)]
                    for i in range(rows+1) for j in range(columns)]
        for i in range(rows):
            for j in range(columns):
                a, b = i*columns+j, i*columns+(j+1)%columns
                if i > 0:
                    triangles.append([a,a+columns,b])
                if i < rows-1:
                    triangles.append([b,a+columns,b+columns])
    else:
        raise ValueError(f"Unsupported URDF geometry {shape.tag}")
    return [{"vertices": vertices, "triangles": triangles, "color": DEFAULT_COLOR}]


def _cluster(parts, maximum):
    """Merge nearby vertices, remove collapsed and duplicate faces; keep surfaces."""
    original = sum(len(part["triangles"]) for part in parts)
    if maximum is None or original <= maximum:
        return parts, original, original
    if maximum < 64:
        raise ValueError("max_triangles_per_mesh must be at least 64")
    all_vertices = [v for part in parts for v in part["vertices"]]
    lower = [min(v[i] for v in all_vertices) for i in range(3)]
    span = max(max(v[i] for v in all_vertices)-lower[i] for i in range(3))
    if span == 0:
        return parts, original, original
    cell = span/256
    for _ in range(24):
        result = []
        for part in parts:
            cells, accumulators, remap = {}, [], []
            for vertex in part["vertices"]:
                key = tuple(math.floor((vertex[i]-lower[i])/cell) for i in range(3))
                if key not in cells:
                    cells[key] = len(accumulators)
                    accumulators.append([0., 0., 0., 0])
                index = cells[key]
                for i in range(3):
                    accumulators[index][i] += vertex[i]
                accumulators[index][3] += 1
                remap.append(index)
            vertices = [[v[i]/v[3] for i in range(3)] for v in accumulators]
            seen, triangles = set(), []
            for triangle in part["triangles"]:
                face = [remap[i] for i in triangle]
                key = tuple(sorted(face))
                if len(set(face)) == 3 and key not in seen:
                    triangles.append(face)
                    seen.add(key)
            vertices, triangles = _compact(vertices, triangles)
            result.append({"vertices": vertices, "triangles": triangles, "color": part["color"]})
        count = sum(len(part["triangles"]) for part in result)
        if count <= maximum:
            return result, original, count
        cell *= 1.3
    raise ValueError("Unable to meet display mesh triangle budget")


def _mesh_path(uri, urdf_path, package_paths):
    if uri.startswith("file://"):
        return Path(uri[7:])
    if not uri.startswith("package://"):
        path = Path(uri)
        return path if path.is_absolute() else urdf_path.parent/path
    package, relative = uri[10:].split("/", 1)
    candidates = []
    if package in package_paths:
        candidates.append(Path(package_paths[package])/relative)
    for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
        if prefix:
            candidates.append(Path(prefix)/"share"/package/relative)
    for parent in urdf_path.parents:
        candidates.extend([parent/"install"/package/"share"/package/relative,
                           parent/"install"/"share"/package/relative,
                           parent/"src"/package/relative])
    return next((path for path in candidates if path.is_file()), candidates[0] if candidates else Path(uri))


def export_robot_assets(urdf_path, package_paths=None, max_triangles_per_mesh=None):
    """Return JSON-friendly stock visual meshes and all URDF joint transforms.

    ``package_paths`` maps ROS package names to their share/source directories.
    ``max_triangles_per_mesh`` is optional display simplification; omit it to
    preserve every stock triangle. Missing/unsupported assets raise an error,
    preventing a report from silently presenting replacement robot shapes.
    """
    urdf_path = Path(urdf_path).resolve()
    root = ET.parse(urdf_path).getroot()
    notes, meshes, links, joints = [], {}, [], []
    materials = {m.get("name"): _numbers(m.find("color").get("rgba"))
                 for m in root.findall("material") if m.find("color") is not None}
    for link in root.findall("link"):
        visuals = []
        for visual in link.findall("visual"):
            geometry = visual.find("geometry")
            if geometry is None or len(geometry) != 1:
                raise ValueError(f"Missing/ambiguous visual geometry in {link.get('name')}")
            shape = geometry[0]
            if shape.tag == "mesh":
                uri = shape.get("filename")
                path = _mesh_path(uri, urdf_path, package_paths or {})
                if path not in meshes:
                    if path.suffix.lower() == ".dae":
                        parts = _dae_parts(path)
                    elif path.suffix.lower() == ".stl":
                        parts = _stl_parts(path)
                    else:
                        raise ValueError(f"Unsupported visual mesh format: {path}")
                    parts, original, reduced = _cluster(parts, max_triangles_per_mesh)
                    meshes[path] = (parts, original, reduced)
                    if reduced < original:
                        notes.append(f"Display-only vertex clustering: {path.name}: {original} to {reduced} triangles.")
                parts, original, reduced = meshes[path]
                scale = _numbers(shape.get("scale", "1 1 1"))
            else:
                parts = _primitive_parts(shape)
                original = reduced = sum(len(p["triangles"]) for p in parts)
                scale, uri = [1.,1.,1.], None
            material = visual.find("material")
            color = None
            if material is not None:
                element = material.find("color")
                color = _numbers(element.get("rgba")) if element is not None else materials.get(material.get("name"))
                if material.find("texture") is not None:
                    notes.append(f"Visual texture in {link.get('name')} uses diffuse fallback color.")
            rendered_parts = [{**p, "color": color if color is not None else p["color"]} for p in parts]
            visuals.append({"name": visual.get("name", ""), "origin": _origin(visual.find("origin")),
                            "material": {"name": material.get("name", "") if material is not None else "", "color": color},
                            "geometry": {"type": "mesh", "source_type": shape.tag, "source": uri,
                                         "scale": scale, "parts": rendered_parts,
                                         "original_triangles": original, "display_triangles": reduced}})
        links.append({"name": link.get("name"), "visuals": visuals})
    for joint in root.findall("joint"):
        limit = joint.find("limit")
        mimic = joint.find("mimic")
        joints.append({"name": joint.get("name"), "type": joint.get("type"),
                       "parent": joint.find("parent").get("link"), "child": joint.find("child").get("link"),
                       "origin": _origin(joint.find("origin")),
                       "axis": _numbers(joint.find("axis").get("xyz", "1 0 0")) if joint.find("axis") is not None else [1.,0.,0.],
                       "limits": {key: float(value) for key, value in limit.attrib.items()} if limit is not None else {},
                       "mimic": {"joint": mimic.get("joint"), "multiplier": float(mimic.get("multiplier", "1")),
                                 "offset": float(mimic.get("offset", "0"))} if mimic is not None else None})
    children = {j["child"] for j in joints}
    return {"schema_version": 1, "name": root.get("name", "robot"),
            "root_links": [link["name"] for link in links if link["name"] not in children],
            "links": links, "joints": joints, "notes": notes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-triangles-per-mesh", type=int)
    args = parser.parse_args()
    assets = export_robot_assets(args.urdf, max_triangles_per_mesh=args.max_triangles_per_mesh)
    args.output.write_text(json.dumps(assets, separators=(",", ":")) + "\n")
    print(f"Exported {len(assets['links'])} links and {len(assets['joints'])} joints to {args.output}")
    for note in assets["notes"]:
        print(note)


if __name__ == "__main__":
    main()
