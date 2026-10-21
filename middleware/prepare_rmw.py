#!/usr/bin/env python3
"""Prepare the pinned Humble RMW source with narrow Chimaera integration hooks."""

import argparse
import hashlib
from pathlib import Path
import tarfile
import urllib.request

VERSION = "6.2.10"
URL = f"https://github.com/ros2/rmw_fastrtps/archive/refs/tags/{VERSION}.tar.gz"
SHA256 = "b2b99db5d4d9827cd3366e6ae6a5c7ef4a7404430242ffecfb729edd9dcd8073"


def replace(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"{path}: expected exactly one integration point: {old!r}")
    path.write_text(text.replace(old, new))


def prepare(archive, output):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("RMW source archive checksum mismatch")
    if output.exists():
        raise ValueError("RMW output must be new; preserve previous build provenance")
    output.mkdir(parents=True)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk():
                raise ValueError("unsafe RMW source archive member")
        bundle.extractall(output)
    source = output / f"rmw_fastrtps-{VERSION}"
    for package in ("rmw_fastrtps_cpp", "rmw_fastrtps_shared_cpp"):
        root = source / package
        replace(root / "CMakeLists.txt", "find_package(rmw REQUIRED)",
                "find_package(rmw REQUIRED)\nfind_package(chimaera_dds_transport REQUIRED)")
        replace(root / "CMakeLists.txt", f"add_library({package}\n",
                f"set(CMAKE_CXX_STANDARD 17)\nadd_library({package}\n")
        replace(root / "CMakeLists.txt", f"target_include_directories({package}\n"
                if package == "rmw_fastrtps_shared_cpp" else "# specific order: dependents before dependencies\nament_target_dependencies(rmw_fastrtps_cpp",
                f"target_link_libraries({package} chimaera_dds_transport::chimaera_dds_transport)\n"
                + (f"target_include_directories({package}\n"
                   if package == "rmw_fastrtps_shared_cpp" else
                   "# specific order: dependents before dependencies\nament_target_dependencies(rmw_fastrtps_cpp"))
        replace(root / "package.xml", "</package>",
                "  <depend>chimaera_dds_transport</depend>\n</package>")
    participant = source / "rmw_fastrtps_shared_cpp/src/participant.cpp"
    replace(participant, "#include <string>",
            "#include <chimaera_dds_transport/transport.hpp>\n#include <string>")
    replace(participant, "  return __create_participant(\n",
            "  chimaera_dds::configure_participant(domainParticipantQos);\n"
            "  return __create_participant(\n")
    shared = source / "rmw_fastrtps_shared_cpp/src"
    demangle = shared / "demangle.cpp"
    replace(demangle, '#include "demangle.hpp"',
            '#include "demangle.hpp"\n#include <chimaera_dds_transport/transport.hpp>')
    replace(demangle, "return _strip_ros_prefix_if_exists(topic_name);",
            "return chimaera_dds::local_topic(_strip_ros_prefix_if_exists(topic_name));")
    replace(demangle, "return _resolve_prefix(topic_name, ros_topic_prefix);",
            "return chimaera_dds::local_topic(_resolve_prefix(topic_name, ros_topic_prefix));")
    for filename in ("rmw_count.cpp", "rmw_get_topic_endpoint_info.cpp"):
        path = shared / filename
        original = path.read_text()
        needle = "_mangle_topic_name(ros_topic_prefix, topic_name).to_string()"
        if original.count(needle) != 2:
            raise ValueError(f"{path}: expected two graph query integration points")
        path.write_text('#include <chimaera_dds_transport/transport.hpp>\n' + original.replace(
            needle, "_mangle_topic_name(ros_topic_prefix, "
            "chimaera_dds::wire_topic(topic_name).c_str()).to_string()"))
    for name in ("publisher", "subscription"):
        path = source / f"rmw_fastrtps_cpp/src/{name}.cpp"
        replace(path, "#include <string>",
                "#include <chimaera_dds_transport/transport.hpp>\n#include <string>")
        replace(path, "_create_topic_name(qos_policies, ros_topic_prefix, topic_name).to_string()",
                "_create_topic_name(qos_policies, ros_topic_prefix, "
                "chimaera_dds::wire_topic(topic_name, type_name).c_str()).to_string()")
        if name == "publisher":
            replace(path, "  // Creates DataWriter with a mask",
                    "  chimaera_dds::configure_writer(topic_name, writer_qos);\n\n"
                    "  // Creates DataWriter with a mask")
            replace(path, "  topic.should_be_deleted = false;",
                    "  chimaera_dds::endpoint_created(topic_name, true);\n\n"
                    "  topic.should_be_deleted = false;")
        else:
            replace(path, "  info->datareader_qos_ = reader_qos;",
                    "  chimaera_dds::configure_reader(topic_name, reader_qos);\n"
                    "  info->datareader_qos_ = reader_qos;")
            replace(path, "  topic.should_be_deleted = false;",
                    "  chimaera_dds::endpoint_created(topic_name, false);\n\n"
                    "  topic.should_be_deleted = false;")
        destroy = shared / f"{name}.cpp"
        original = destroy.read_text()
        handle = "publisher" if name == "publisher" else "subscription"
        needle = f"  rmw_free(const_cast<char *>({handle}->topic_name));"
        if original.count(needle) != 1:
            raise ValueError(f"{destroy}: missing endpoint disposal integration point")
        destroy.write_text('#include <chimaera_dds_transport/transport.hpp>\n' + original.replace(
            needle, f"  chimaera_dds::endpoint_destroyed({handle}->topic_name, "
            + ("true" if name == "publisher" else "false") + ");\n" + needle))
    publisher = source / "rmw_fastrtps_cpp/src/publisher.cpp"
    publisher.write_text(publisher.read_text() + '\nextern "C" '
                         '__attribute__((visibility("default"))) '
                         'int chimaera_rmw_transport_abi() { return 1; }\n')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    archive = args.archive or args.output.parent / f"rmw_fastrtps-{VERSION}.tar.gz"
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, archive)
    print(prepare(archive, args.output))


if __name__ == "__main__":
    main()
