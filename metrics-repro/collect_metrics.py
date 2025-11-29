#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

MAVEN_CMD = [
    "mvn",
    "clean",
    "install",
    "-DskipTests",
    "-Dmaven.repo.local=.m2repo",
]
HEALTH_ENDPOINTS = {
    "gateway": "http://localhost:8080/actuator/health",
    "pricing": "http://localhost:8081/actuator/health",
    "inventory": "http://localhost:8082/actuator/health",
    "finance": "http://localhost:8083/actuator/health",
    "loyalty": "http://localhost:8085/actuator/health",
    "reporting": "http://localhost:8086/actuator/health",
}
NS = {"m": "http://maven.apache.org/POM/4.0.0"}
HEADERS = {"Content-Type": "application/json"}


def read_text(path):
    return path.read_text(encoding="utf-8")


def run_command(args, cwd, check=True, capture_output=True):
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def parse_root_pom(repo_root):
    return ET.parse(repo_root / "pom.xml").getroot()


def list_module_poms(repo_root, modules):
    return [repo_root / module / "pom.xml" for module in modules if (repo_root / module / "pom.xml").exists()]


def discover_tech_stack(repo_root, module_poms):
    dependency_map = {
        "org.springframework.boot:spring-boot-starter-web": "Spring Web",
        "org.springframework.boot:spring-boot-starter-webflux": "Spring WebFlux",
        "org.springframework.boot:spring-boot-starter-data-jpa": "Spring Data JPA",
        "org.springframework.boot:spring-boot-starter-actuator": "Spring Boot Actuator",
        "org.springframework.kafka:spring-kafka": "Spring Kafka",
        "org.springframework.cloud:spring-cloud-starter-gateway": "Spring Cloud Gateway",
        "org.postgresql:postgresql": "PostgreSQL",
    }
    found = {"Java 17", "Maven", "Docker Compose"}
    root_pom = parse_root_pom(repo_root)
    parent_version = root_pom.find("./m:parent/m:version", NS)
    cloud_version = root_pom.find("./m:properties/m:spring-cloud.version", NS)
    if parent_version is not None:
        found.add(f"Spring Boot {parent_version.text.strip()}")
    if cloud_version is not None:
        found.add(f"Spring Cloud {cloud_version.text.strip()}")

    for pom_path in module_poms:
        tree = ET.parse(pom_path).getroot()
        for dependency in tree.findall(".//m:dependencies/m:dependency", NS):
            group_id = dependency.find("m:groupId", NS)
            artifact_id = dependency.find("m:artifactId", NS)
            if group_id is None or artifact_id is None:
                continue
            coord = f"{group_id.text}:{artifact_id.text}"
            label = dependency_map.get(coord)
            if label:
                found.add(label)

    compose_text = read_text(repo_root / "docker-compose.yml")
    if "confluentinc/cp-kafka" in compose_text:
        found.add("Kafka")
    return sorted(found)


def count_compose_services(repo_root):
    count = 0
    in_services = False
    for line in read_text(repo_root / "docker-compose.yml").splitlines():
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services:
            if line and not line.startswith(" "):
                break
            if re.match(r"^  [A-Za-z0-9_-]+:$", line):
                count += 1
    return count


def normalize_endpoint_path(base_path, method_path):
    if not method_path:
        path = base_path or "/"
    elif not base_path:
        path = method_path
    else:
        path = f"{base_path.rstrip('/')}/{method_path.lstrip('/')}"
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def collect_endpoints(repo_root):
    mapping_re = re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*(\(([^)]*)\))?")
    endpoints = []
    for path in sorted(repo_root.glob("**/*Controller.java")):
        text = read_text(path)
        if "@RestController" not in text:
            continue
        class_match = re.search(r"@RequestMapping\(([^)]*)\)\s*public class", text, re.S)
        base_path = ""
        if class_match:
            string_match = re.search(r'"([^"]*)"', class_match.group(1))
            if string_match:
                base_path = string_match.group(1)
        class_index = class_match.start() if class_match else -1
        for match in mapping_re.finditer(text):
            annotation = match.group(1)
            args = match.group(3) or ""
            if annotation == "RequestMapping" and match.start() == class_index:
                continue
            method = annotation.replace("Mapping", "").upper()
            if annotation == "RequestMapping":
                method_match = re.search(r"method\s*=\s*RequestMethod\.([A-Z]+)", args)
                method = method_match.group(1) if method_match else "REQUEST"
            path_match = re.search(r'"([^"]*)"', args)
            method_path = path_match.group(1) if path_match else ""
            endpoints.append(
                {
                    "file": str(path.relative_to(repo_root)),
                    "method": method,
                    "path": normalize_endpoint_path(base_path, method_path),
                }
            )
    deduped = []
    seen = set()
    for endpoint in endpoints:
        key = (endpoint["file"], endpoint["method"], endpoint["path"])
        if key not in seen:
            seen.add(key)
            deduped.append(endpoint)
    return deduped


def collect_topics(repo_root):
    declarations = []
    for path in sorted(repo_root.glob("**/src/main/resources/application.yml")):
        lines = read_text(path).splitlines()
        in_destore = False
        in_topics = False
        destore_indent = 0
        topics_indent = 0
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if stripped == "destore:":
                in_destore = True
                in_topics = False
                destore_indent = indent
                continue
            if in_destore and indent <= destore_indent and stripped != "destore:":
                in_destore = False
                in_topics = False
            if in_destore and stripped == "topics:":
                in_topics = True
                topics_indent = indent
                continue
            if in_topics:
                if indent <= topics_indent:
                    in_topics = False
                    continue
                match = re.match(r"([A-Za-z0-9_.-]+):\s*(.+)$", stripped)
                if match:
                    declarations.append(
                        {
                            "file": str(path.relative_to(repo_root)),
                            "key": match.group(1),
                            "value": match.group(2).strip().strip("\"'"),
                        }
                    )
    unique_values = []
    for item in declarations:
        if item["value"] not in unique_values:
            unique_values.append(item["value"])
    return declarations, unique_values


def collect_java_counts(repo_root):
    java_files = sorted(repo_root.glob("**/*.java"))
    total_loc = 0
    for path in java_files:
        text = read_text(path)
        total_loc += text.count("\n") + (0 if not text or text.endswith("\n") else 1)
    return len(java_files), total_loc


def collect_code_markers(repo_root):
    producer_files = set()
    producer_occurrences = 0
    consumer_occurrences = 0
    entities = []
    repositories = []
    for path in sorted(repo_root.glob("**/*.java")):
        text = read_text(path)
        if "KafkaTemplate<" in text:
            producer_files.add(str(path.relative_to(repo_root)))
            producer_occurrences += text.count("KafkaTemplate<")
        consumer_occurrences += text.count("@KafkaListener")
        if "@Entity" in text:
            entities.append(str(path.relative_to(repo_root)))
        if path.name.endswith("Repository.java"):
            repositories.append(str(path.relative_to(repo_root)))
    return {
        "producer_components": sorted(producer_files),
        "producer_usage_occurrences": producer_occurrences,
        "consumer_usage_count": consumer_occurrences,
        "entities": entities,
        "repositories": repositories,
    }


def http_request(method, url, payload=None, timeout=10):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = HEADERS if payload is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return response.status, body


def health_is_up(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            body = response.read().decode("utf-8", errors="replace")
        return '"status":"UP"' in body
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def percentile(sorted_values, pct):
    if not sorted_values:
        return None
    index = math.ceil((pct / 100.0) * len(sorted_values)) - 1
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def benchmark(url, requests_count=1000, concurrency=20):
    def one_request(_):
        start = time.perf_counter()
        ok = False
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
                ok = 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            ok = False
        duration_ms = (time.perf_counter() - start) * 1000.0
        return duration_ms, ok

    for _ in range(10):
        one_request(None)

    latencies = []
    errors = 0
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for duration_ms, ok in pool.map(one_request, range(requests_count)):
            latencies.append(duration_ms)
            if not ok:
                errors += 1
    elapsed = time.perf_counter() - started
    latencies.sort()
    return {
        "n": requests_count,
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 3),
        "rps": round(requests_count / elapsed, 2) if elapsed else None,
        "p50_ms": round(percentile(latencies, 50), 3),
        "p95_ms": round(percentile(latencies, 95), 3),
        "p99_ms": round(percentile(latencies, 99), 3),
        "error_rate_pct": round((errors / requests_count) * 100.0, 3),
        "errors": errors,
    }


def normalize_docker_timestamp(timestamp):
    stamp = timestamp.strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    if "." in stamp:
        head, tail = stamp.split(".", 1)
        digits = []
        suffix = []
        digit_mode = True
        for char in tail:
            if digit_mode and char.isdigit():
                digits.append(char)
            else:
                digit_mode = False
                suffix.append(char)
        stamp = f"{head}.{(''.join(digits) + '000000')[:6]}{''.join(suffix)}"
    return datetime.fromisoformat(stamp)


def parse_compose_log_line(line):
    match = re.match(r"^\S+\s+\|\s+(\S+)\s+(.*)$", line)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def measure_event_latency(repo_root):
    base_url = "http://localhost:8080"
    since = datetime.now(timezone.utc).replace(microsecond=0)
    skus = []
    for index in range(200):
        sku = f"evt-bench-{index:03d}"
        skus.append(sku)
        amount = round(20.0 + (index / 100.0), 2)
        http_request(
            "POST",
            f"{base_url}/pricing/{sku}",
            {"currency": "GBP", "amount": amount, "offerType": "Promo"},
        )

    pricing_pattern = re.compile(r"Price set for sku=(evt-bench-\d{3})\b")
    notify_pattern = re.compile(r"price changed for (evt-bench-\d{3})\b")
    pricing_times = {}
    notify_times = {}

    for _ in range(15):
        pricing_logs = run_command(
            [
                "docker",
                "compose",
                "logs",
                "--since",
                since.isoformat(),
                "--timestamps",
                "pricing-service",
            ],
            cwd=repo_root,
        ).stdout.splitlines()
        notify_logs = run_command(
            [
                "docker",
                "compose",
                "logs",
                "--since",
                since.isoformat(),
                "--timestamps",
                "notification-worker",
            ],
            cwd=repo_root,
        ).stdout.splitlines()

        for line in pricing_logs:
            timestamp, message = parse_compose_log_line(line)
            if not timestamp:
                continue
            match = pricing_pattern.search(message)
            if match and match.group(1) not in pricing_times:
                pricing_times[match.group(1)] = normalize_docker_timestamp(timestamp)

        for line in notify_logs:
            timestamp, message = parse_compose_log_line(line)
            if not timestamp:
                continue
            match = notify_pattern.search(message)
            if match and match.group(1) not in notify_times:
                notify_times[match.group(1)] = normalize_docker_timestamp(timestamp)

        if len(notify_times) >= len(skus):
            break
        time.sleep(1)

    deltas_ms = []
    missing = []
    for sku in skus:
        pricing_time = pricing_times.get(sku)
        notify_time = notify_times.get(sku)
        if pricing_time is None or notify_time is None:
            missing.append(sku)
            continue
        deltas_ms.append((notify_time - pricing_time).total_seconds() * 1000.0)

    deltas_ms.sort()
    return {
        "generated": len(skus),
        "matched": len(deltas_ms),
        "missing": len(missing),
        "median_ms": round(percentile(deltas_ms, 50), 3) if deltas_ms else None,
        "p95_ms": round(percentile(deltas_ms, 95), 3) if deltas_ms else None,
    }


def measure_runtime(repo_root):
    runtime = {
        "measured": False,
        "not_measured_reason": None,
    }
    if shutil.which("docker") is None:
        runtime["not_measured_reason"] = "docker_not_installed"
        return runtime
    try:
        run_command(["docker", "ps"], cwd=repo_root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        runtime["not_measured_reason"] = "docker_unavailable_or_no_daemon_access"
        return runtime

    build_start = time.perf_counter()
    run_command(MAVEN_CMD, cwd=repo_root)
    runtime["build_time_seconds"] = round(time.perf_counter() - build_start, 3)

    compose_start = time.perf_counter()
    compose_up = run_command(["docker", "compose", "up", "-d"], cwd=repo_root)
    statuses = {}
    for _ in range(120):
        statuses = {name: ("UP" if health_is_up(url) else "NOT_UP") for name, url in HEALTH_ENDPOINTS.items()}
        if all(status == "UP" for status in statuses.values()):
            break
        time.sleep(1)
    runtime["compose_up_return_code"] = compose_up.returncode
    runtime["compose_up_elapsed_seconds"] = round(time.perf_counter() - compose_start, 3)
    runtime["health_statuses"] = statuses

    seed = {
        "price": http_request(
            "POST",
            "http://localhost:8080/pricing/resume-bench-001",
            {"currency": "GBP", "amount": 9.99, "offerType": "Promo"},
        )[0],
        "inventory": http_request(
            "POST",
            "http://localhost:8080/inventory/upload",
            {"sku": "resume-bench-001", "quantity": 25, "storeId": "store-1"},
        )[0],
    }
    runtime["seed_status_codes"] = seed

    pricing_via_gateway = benchmark("http://localhost:8080/pricing/resume-bench-001")
    reports_via_gateway = benchmark("http://localhost:8080/reports/summary")
    pricing_direct = benchmark("http://localhost:8081/pricing/resume-bench-001")
    runtime["benchmarks"] = {
        "pricing_via_gateway": pricing_via_gateway,
        "reports_via_gateway": reports_via_gateway,
        "pricing_direct": pricing_direct,
        "gateway_overhead_pricing": {
            "p50_ms_delta": round(pricing_via_gateway["p50_ms"] - pricing_direct["p50_ms"], 3),
            "p95_ms_delta": round(pricing_via_gateway["p95_ms"] - pricing_direct["p95_ms"], 3),
            "p99_ms_delta": round(pricing_via_gateway["p99_ms"] - pricing_direct["p99_ms"], 3),
            "rps_delta": round(pricing_via_gateway["rps"] - pricing_direct["rps"], 2),
        },
    }
    runtime["eventing"] = measure_event_latency(repo_root)
    runtime["measured"] = True
    return runtime


def collect_static(repo_root):
    root_pom = parse_root_pom(repo_root)
    modules = [item.text.strip() for item in root_pom.findall("./m:modules/m:module", NS)]
    module_poms = list_module_poms(repo_root, modules)
    endpoints = collect_endpoints(repo_root)
    topic_declarations, topic_values = collect_topics(repo_root)
    markers = collect_code_markers(repo_root)
    java_file_count, java_loc = collect_java_counts(repo_root)
    readme_title = read_text(repo_root / "README.md").splitlines()[0].lstrip("# ").strip()

    return {
        "project": {
            "readme_title": readme_title,
            "root_name": root_pom.find("./m:name", NS).text.strip(),
            "root_artifact_id": root_pom.find("./m:artifactId", NS).text.strip(),
            "root_description": root_pom.find("./m:description", NS).text.strip(),
        },
        "tech_stack": discover_tech_stack(repo_root, module_poms),
        "modules": {
            "count": len(modules),
            "names": modules,
        },
        "compose_services": {
            "count": count_compose_services(repo_root),
        },
        "endpoints": {
            "count": len(endpoints),
            "items": endpoints,
        },
        "topics": {
            "declaration_count": len(topic_declarations),
            "module_count": len({Path(item["file"]).parts[0] for item in topic_declarations}),
            "unique_count": len(topic_values),
            "values": topic_values,
            "items": topic_declarations,
        },
        "kafka": {
            "producer_component_count": len(markers["producer_components"]),
            "producer_usage_occurrences": markers["producer_usage_occurrences"],
            "producer_files": markers["producer_components"],
            "consumer_usage_count": markers["consumer_usage_count"],
        },
        "persistence": {
            "entity_count": len(markers["entities"]),
            "entities": markers["entities"],
            "repository_count": len(markers["repositories"]),
            "repositories": markers["repositories"],
        },
        "java": {
            "file_count": java_file_count,
            "loc": java_loc,
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Collect DE-Store source and runtime metrics.")
    parser.add_argument("--repo-root", default=".", help="Path to the repository root.")
    parser.add_argument("--skip-runtime", action="store_true", help="Skip Docker/Maven runtime measurements.")
    parser.add_argument("--output-json", help="Write the collected metrics JSON to this file.")
    return parser.parse_args()


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "static": collect_static(repo_root),
        "runtime": {"measured": False, "not_measured_reason": "skipped_by_flag"} if args.skip_runtime else measure_runtime(repo_root),
    }

    payload = json.dumps(metrics, indent=2)
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")


if __name__ == "__main__":
    main()
